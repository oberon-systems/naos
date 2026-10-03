import asyncio
import json
from dataclasses import replace
from http import HTTPStatus
from typing import Annotated, Literal
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from fastapi import APIRouter, Path, Query, Request, WebSocket, WebSocketException, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from naos_web import audit as trail
from naos_web import changes, confirm, events, new_run, profiles, register, terminal
from naos_web import policies as documents
from naos_web import secrets as vault
from naos_web.client import ApiClient, ApiError, Choices, Dashboard, Row, RunDetail
from naos_web.clock import NowDep
from naos_web.format import ago
from naos_web.pages import (
    BUDGET_NOTE,
    COPY_NOTE,
    ERASED_NOTE,
    EXISTS_NOTE,
    EXITS,
    EXPIRY_NOTE,
    FENCING,
    FORM_NOTE,
    GATES_NOTE,
    HELD_NOTE,
    IDENTITY_NOTE,
    IMAGE_COLUMNS,
    IMAGE_FILTERS,
    IMAGE_NOTE,
    ISSUED_NOTE,
    LEASE_NOTE,
    LIFECYCLE,
    NAMED_NOTE,
    NAV,
    NEVER_NOTE,
    OPEN_NOTE,
    PAGES,
    POLICIES_NOTE,
    POLICY_COLUMNS,
    POLICY_FILTERS,
    POLICY_FORM_HINTS,
    POLICY_FORM_NOTE,
    POLICY_NOTE,
    PROFILE_COLUMNS,
    PROFILE_FILTERS,
    PROFILE_NOTE,
    ROTATE_NOTE,
    RUN_COLUMNS,
    RUN_FILTERS,
    RUNNER_COLUMNS,
    RUNNER_FILTERS,
    RUNNER_NOTE,
    SECRET_COLUMNS,
    SECRET_FILTERS,
    SECRETS_NOTE,
    SENT_ONCE_NOTE,
    SOURCE_NOTE,
    STATUS_TONE,
    TYPED_ONCE_NOTE,
    USED_NOTE,
    WORKSPACE_HINT,
    WRITE_ONLY_NOTE,
    ListPage,
    Summary,
    TileValue,
)
from naos_web.rows import (
    RunDetailRow,
    catalog,
    fleet,
    image_detail,
    image_rows,
    pick,
    pick_images,
    run_detail,
    run_rows,
    runner_detail,
    runner_rows,
)

State = Literal["all", "active", "queued", "waiting_merge", "failed"]
Tab = Literal["overview", "terminal", "logs", "changes"]
StateQuery = Annotated[State, Query()]
Fleet = Literal["all", "live", "stale", "revoked"]
FleetQuery = Annotated[Fleet, Query()]
Usage = Literal["all", "in_use", "unused"]
UsageQuery = Annotated[Usage, Query()]
Used = Literal["all", "used", "unused"]
UsedQuery = Annotated[Used, Query()]
SearchQuery = Annotated[str, Query(max_length=128)]

LOG_KINDS: tuple[tuple[events.Kind, str], ...] = (
    ("all", "All"),
    ("api", "API"),
    ("runner", "Runner"),
    ("errors", "Errors"),
)

router = APIRouter()


def render(
    request: Request, page: ListPage, summary: Summary | None = None, **context: object
) -> HTMLResponse:
    templates: Jinja2Templates = request.app.state.templates
    template = str(context.pop("template", "list_page.html"))
    return templates.TemplateResponse(
        request,
        template,
        {
            "nav": NAV,
            "page": page,
            "summary": summary or Summary(),
            "api_endpoint": request.app.state.api_endpoint,
            "standalone": not wants_fragment(request),
            **context,
        },
    )


# Active runs are the ones on a slot right now, which is not every open run.
def tiles(summary: Row, runners: list[Row], now: int) -> Summary:
    counts: dict[str, int] = summary["counts"]
    active = sum(counts[status] for status in ("STARTING", "STARTED", "STOPPING", "COLLECTING"))
    slots = sum(runner["capacity"] or 0 for runner in runners if runner["status"] == "live")
    oldest: int | None = summary["oldest_pending_at"]
    waiting = counts["WAITING_MERGE"]
    return Summary(
        values={
            "active": TileValue(str(active), f"of {slots} runner slots"),
            "queued": TileValue(
                str(counts["PENDING"]),
                "" if oldest is None else f"oldest waiting {ago(oldest, now).removesuffix(' ago')}",
            ),
            "waiting_merge": TileValue(str(waiting), "needs approval" if waiting else ""),
            "failed": TileValue(
                str(summary["failed_24h"]), str(summary["last_failure_reason"] or "")
            ),
        }
    )


def runs_page(
    request: Request, board: Dashboard, state: State, now: int, fragment: bool
) -> HTMLResponse:
    return render(
        request,
        PAGES["runs"],
        tiles(board.summary, board.runners, now),
        template="runs_fragment.html" if fragment else "runs.html",
        runs=run_rows(board.runs, board.runners, now),
        runners=runner_rows(board.runners, now),
        live_runners=sum(1 for runner in board.runners if runner["status"] == "live"),
        open_runs=board.summary["open"],
        run_filters=RUN_FILTERS,
        run_columns=RUN_COLUMNS,
        status_tone=STATUS_TONE,
        lifecycle=LIFECYCLE,
        exits=EXITS,
        fencing=FENCING,
        state=state,
    )


def terminal_view(request: Request, run_id: str, status: str) -> terminal.TerminalView:
    return terminal.view(run_id, status, request.app.state.api_attach_url)


# hx-boost on the nav sends HX-Request too, but that swap replaces the whole body,
# so only a request aimed inside the page may answer without the shell.
def wants_fragment(request: Request) -> bool:
    return (
        request.headers.get("HX-Request") == "true" and request.headers.get("HX-Boosted") != "true"
    )


# A failure is rendered into whatever asked for it: the page, the swapped body, or
# the overlay. Answering with the wrong one nests a whole document inside an element.
def failed(request: Request, page: ListPage, err: ApiError) -> HTMLResponse:
    template = "page_error.html" if wants_fragment(request) else "unreachable.html"
    return render(request, page, template=template, reason=str(err))


def failed_overlay(request: Request, page: ListPage, err: ApiError) -> HTMLResponse:
    template = "overlay_error.html" if wants_fragment(request) else "unreachable.html"
    return render(request, page, template=template, reason=str(err))


@router.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse("/runs", status_code=307)


@router.get("/runs", response_class=HTMLResponse)
async def runs(request: Request, now: NowDep, state: StateQuery = "all") -> HTMLResponse:
    api: ApiClient = request.app.state.api
    fragment = wants_fragment(request)
    try:
        board = await api.dashboard(None if state == "all" else state)
    except ApiError as err:
        return failed(request, PAGES["runs"], err)
    return runs_page(request, board, state, now, fragment)


@router.post("/runs/{run_id}/cancel", response_class=HTMLResponse)
async def cancel_run(
    request: Request, run_id: str, now: NowDep, state: StateQuery = "all"
) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        await api.stop_run(run_id)
        board = await api.dashboard(None if state == "all" else state)
    except ApiError as err:
        return failed(request, PAGES["runs"], err)
    return runs_page(request, board, state, now, fragment=True)


NEW_RUN_STEPS = ("Profile", "Configure", "Save & run")
PROFILE_LIST = "new-run-profiles"


def _dialog(
    request: Request, step: int, draft: new_run.Draft, template: str = "", **context: object
) -> HTMLResponse:
    fragment = wants_fragment(request)
    return render(
        request,
        PAGES["runs"],
        None,
        template=template or ("new_run_overlay.html" if fragment else "new_run_page.html"),
        step=step,
        steps=NEW_RUN_STEPS,
        draft=draft,
        **context,
    )


# The dialog posts urlencoded forms only, so the stdlib parser spares a multipart dependency.
async def _form(request: Request) -> dict[str, str]:
    return dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))


async def _profile(api: ApiClient, draft: new_run.Draft) -> Row | None:
    return await api.profile(draft.profile) if draft.profile else None


def _profile_step(
    request: Request,
    draft: new_run.Draft,
    profiles: list[Row],
    query: str = "",
    template: str = "",
    back: bool = False,
) -> HTMLResponse:
    return _dialog(
        request,
        1,
        draft,
        template,
        back=back,
        profiles=[
            {**row, "usage": new_run.usage(row), "line": new_run.runtime_line(row["spec"])}
            for row in profiles
        ],
        query=query,
    )


def _configure_step(
    request: Request,
    draft: new_run.Draft,
    profile: Row | None,
    choices: Choices,
    notice: str = "",
) -> HTMLResponse:
    return _dialog(
        request,
        2,
        draft,
        profile=profile,
        fields=new_run.configure_fields(draft, profile, choices),
        image=new_run.image_field(draft, choices.images),
        runner=new_run.runner_field(draft, choices.runners),
        edited=len(draft.edited(profile)),
        notice=notice,
    )


def _profile_label(draft: new_run.Draft, profile: Row | None) -> str:
    if draft.mode == "new" and draft.name:
        return draft.name
    return str(profile["name"]) if profile else "new profile"


def _review_step(
    request: Request,
    draft: new_run.Draft,
    profile: Row | None,
    choices: Choices,
    notice: str = "",
) -> HTMLResponse:
    lock = new_run.update_lock(profile)
    if profile is None or lock:
        draft = replace(draft, mode="new")
    image = new_run.resolve_image(choices.images, draft.image)
    runner = next((r for r in choices.runners if r["id"] == draft.runner), None)
    edited = draft.edited(profile)
    return _dialog(
        request,
        3,
        draft,
        profile=profile,
        lock=lock,
        saves=profile is None or bool(edited),
        changes=new_run.changes(draft, profile, choices.policies),
        image_label=new_run.image_label(image) if image else "no registered image",
        runner_label=runner["name"] if runner else new_run.AUTO,
        profile_label=_profile_label(draft, profile),
        notice=notice,
    )


@router.get("/runs/new", response_class=HTMLResponse)
async def new_run_dialog(
    request: Request, q: Annotated[str, Query(max_length=128)] = ""
) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        profiles = await api.profiles(q or None)
    except ApiError as err:
        return failed_overlay(request, PAGES["runs"], err)
    listing = request.headers.get("HX-Target") == PROFILE_LIST
    template = "partials/new_run_profiles.html" if listing else ""
    return _profile_step(request, new_run.Draft.fresh(), profiles, q, template)


@router.post("/runs/new/profile", response_class=HTMLResponse)
async def new_run_profile(request: Request) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    draft = new_run.Draft.of(await _form(request))
    try:
        profiles = await api.profiles()
    except ApiError as err:
        return failed_overlay(request, PAGES["runs"], err)
    return _profile_step(request, draft, profiles, back=True)


@router.post("/runs/new/configure", response_class=HTMLResponse)
async def new_run_configure(request: Request) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    form = await _form(request)
    draft = new_run.Draft.of(form)
    try:
        profile = await _profile(api, draft)
        choices = await api.choices()
    except ApiError as err:
        return failed_overlay(request, PAGES["runs"], err)
    if "cpu" not in form:
        draft = draft.based_on(profile)
    return _configure_step(request, draft, profile, choices)


@router.post("/runs/new/review", response_class=HTMLResponse)
async def new_run_review(request: Request) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    draft = new_run.Draft.of(await _form(request))
    try:
        profile = await _profile(api, draft)
        choices = await api.choices()
    except ApiError as err:
        return failed_overlay(request, PAGES["runs"], err)
    try:
        new_run.spec_of(draft.values)
    except new_run.FormError as err:
        return _configure_step(request, draft, profile, choices, notice=str(err))
    return _review_step(request, draft, profile, choices)


async def _save(api: ApiClient, draft: new_run.Draft, profile: Row | None) -> str:
    spec = new_run.spec_of(draft.values)
    if profile is not None and not draft.edited(profile):
        return str(profile["id"])
    if profile is not None and draft.mode == "update":
        await api.update_profile(profile["id"], spec)
        return str(profile["id"])
    if not draft.name:
        raise new_run.FormError("a new profile needs a name")
    saved = await api.create_profile(draft.name, spec)
    return str(saved["id"])


# Every write carries the dialog's key and replays, so a second submit lands on the same Run.
@router.post("/runs/new", response_class=HTMLResponse)
async def new_run_submit(request: Request) -> Response:
    api: ApiClient = request.app.state.api
    draft = new_run.Draft.of(await _form(request))
    try:
        profile = await _profile(api, draft)
        choices = await api.choices()
    except ApiError as err:
        return failed_overlay(request, PAGES["runs"], err)
    image = new_run.resolve_image(choices.images, draft.image)
    try:
        if image is None:
            raise new_run.FormError("no registered image to run")
        profile_id = await _save(api, draft, profile)
        runner = None if draft.runner == new_run.AUTO else draft.runner
        ref = {"id": image["id"], "digest": image["digest"]}
        await api.run_from_profile(profile_id, ref, runner, draft.key)
    except (ApiError, new_run.FormError) as err:
        return _review_step(request, draft, profile, choices, notice=str(err))
    if wants_fragment(request):
        return Response(headers={"HX-Redirect": "/runs"})
    return RedirectResponse("/runs", status_code=303)


def _detail_row(detail: RunDetail, now: int) -> RunDetailRow:
    return run_detail(
        detail.run,
        detail.events,
        detail.runner,
        detail.policies,
        detail.images,
        detail.profile,
        detail.model,
        now,
    )


def _merge_context(detail: RunDetail, now: int) -> changes.Context:
    run = detail.run
    decided = [row for row in detail.events if row["event"] == "merge_decided"]
    vms = [row["vm_id"] for row in detail.events if row.get("vm_id")]
    return changes.Context(
        run_id=run["id"],
        workspace=run["workspace"] or "",
        runner=run["runner"]["name"] if run["runner"] else "",
        runner_id=run["runner"]["id"] if run["runner"] else None,
        vm_id=vms[-1] if vms else None,
        decided=decided[-1] if decided else None,
        now=now,
    )


def _changes(
    detail: RunDetail,
    now: int,
    shown: changes.Filter,
    entry: int | None,
    ask: changes.Asked | None = None,
    error: str | None = None,
) -> changes.Changes | None:
    if detail.merge is None:
        return None
    ctx = _merge_context(detail, now)
    banner = changes.banner(
        detail.merge, detail.run["spec"]["merge"]["policy"], ctx.workspace, ctx.runner, now
    )
    return changes.changes(detail.merge, shown, entry, banner, ctx, ask, error)


async def _run_panel(
    request: Request,
    run_id: str,
    now: int,
    tab: Tab,
    kind: events.Kind = "all",
    shown: changes.Filter = "all",
    entry: int | None = None,
    ask: changes.Asked | None = None,
    error: str | None = None,
) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        detail = await api.run_detail(run_id)
    except ApiError as err:
        return failed_overlay(request, PAGES["runs"], err)
    diff = _changes(detail, now, shown, entry, ask, error)
    return render(
        request,
        PAGES["runs"],
        template="run_overlay.html" if wants_fragment(request) else "run_overlay_page.html",
        run=_detail_row(detail, now),
        tab="overview" if tab == "changes" and diff is None else tab,
        diff=diff,
        on_merge=changes.ON_MERGE,
        on_conflict=changes.ON_CONFLICT,
        conflict_notes=changes.CONFLICT_NOTES,
        report_note=changes.REPORT_NOTE,
        where_note=changes.WHERE_NOTE,
        list_note=changes.LIST_NOTE,
        entry_note=changes.ENTRY_NOTE,
        sensitive_note=changes.SENSITIVE_NOTE,
        rejected_note=changes.REJECTED_NOTE,
        terminal=terminal_view(request, run_id, detail.run["status"]),
        log_kinds=LOG_KINDS,
        log_kind=kind,
        log_rows=events.log_rows(detail.events, detail.runners, kind),
    )


# Declared after /runs/new, which this path would otherwise take for a run id.
@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def run(request: Request, run_id: str, now: NowDep) -> HTMLResponse:
    return await _run_panel(request, run_id, now, "overview")


@router.get("/runs/{run_id}/terminal", response_class=HTMLResponse)
async def run_terminal(
    request: Request, run_id: str, now: NowDep, window: bool = False
) -> HTMLResponse:
    if not window:
        return await _run_panel(request, run_id, now, "terminal")
    api: ApiClient = request.app.state.api
    try:
        detail = await api.run_detail(run_id)
    except ApiError as err:
        return failed(request, PAGES["runs"], err)
    return render(
        request,
        PAGES["runs"],
        template="run_terminal_window.html",
        run=_detail_row(detail, now),
        terminal=terminal_view(request, run_id, detail.run["status"]),
    )


@router.get("/runs/{run_id}/logs", response_class=HTMLResponse)
async def run_logs(
    request: Request, run_id: str, now: NowDep, kind: events.Kind = "all"
) -> HTMLResponse:
    return await _run_panel(request, run_id, now, "logs", kind)


@router.get("/runs/{run_id}/changes", response_class=HTMLResponse)
async def run_changes(
    request: Request,
    run_id: str,
    now: NowDep,
    change: changes.Filter = "all",
    entry: Annotated[int | None, Query(ge=0)] = None,
) -> HTMLResponse:
    ask = changes.asked(request.query_params.multi_items())
    return await _run_panel(request, run_id, now, "changes", shown=change, entry=entry, ask=ask)


async def _merge_confirm(request: Request, run_id: str, now: int, nothing: bool) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        detail = await api.run_detail(run_id)
    except ApiError as err:
        return failed_overlay(request, PAGES["runs"], err)
    if detail.merge is None:
        return await _run_panel(request, run_id, now, "changes")
    ask = changes.asked(request.query_params.multi_items())
    chosen = changes.selection(detail.merge, ask)
    ctx = _merge_context(detail, now)
    back = f"/runs/{run_id}/changes?{chosen.query()}"
    asked = (
        confirm.merge_nothing(run_id, ctx.workspace, detail.merge, back)
        if nothing
        else confirm.merge(run_id, ctx.workspace, detail.merge, chosen, back)
    )
    return render(
        request,
        PAGES["runs"],
        template="run_confirm_overlay.html" if wants_fragment(request) else "run_confirm_page.html",
        run=_detail_row(detail, now),
        confirm=asked,
    )


@router.get("/runs/{run_id}/merge", response_class=HTMLResponse)
async def merge_confirm(request: Request, run_id: str, now: NowDep) -> HTMLResponse:
    return await _merge_confirm(request, run_id, now, nothing=False)


@router.get("/runs/{run_id}/merge/reject", response_class=HTMLResponse)
async def reject_confirm(request: Request, run_id: str, now: NowDep) -> HTMLResponse:
    return await _merge_confirm(request, run_id, now, nothing=True)


# A refused selection comes back with the api's words; a stale one reloads the tab as it is now.
async def _decided(
    request: Request, run_id: str, now: int, ask: changes.Asked, err: ApiError | None
) -> Response:
    if err is not None and err.status == HTTPStatus.UNPROCESSABLE_ENTITY:
        return await _run_panel(request, run_id, now, "changes", ask=ask, error=str(err))
    if err is not None and err.status != HTTPStatus.CONFLICT:
        return failed_overlay(request, PAGES["runs"], err)
    if wants_fragment(request):
        return await _run_panel(request, run_id, now, "changes")
    return RedirectResponse(f"/runs/{run_id}/changes", status_code=303)


@router.post("/runs/{run_id}/merge", response_class=HTMLResponse)
async def merge_run(request: Request, run_id: str, now: NowDep) -> Response:
    api: ApiClient = request.app.state.api
    body = (await request.body()).decode()
    ask = replace(changes.asked(parse_qsl(body, keep_blank_values=True)), touched=True)
    try:
        await api.decide_merge(run_id, list(ask.paths), dict(ask.resolutions))
    except ApiError as err:
        return await _decided(request, run_id, now, ask, err)
    return await _decided(request, run_id, now, ask, None)


@router.post("/runs/{run_id}/merge/reject", response_class=HTMLResponse)
async def reject_run(request: Request, run_id: str, now: NowDep) -> Response:
    api: ApiClient = request.app.state.api
    try:
        await api.reject_merge(run_id)
    except ApiError as err:
        return await _decided(request, run_id, now, changes.Asked(), err)
    return await _decided(request, run_id, now, changes.Asked(), None)


@router.get("/runs/{run_id}/logs/export")
async def run_logs_export(request: Request, run_id: str) -> Response:
    api: ApiClient = request.app.state.api
    try:
        rows = await api.run_events(run_id)
    except ApiError as err:
        return failed(request, PAGES["runs"], err)
    return Response(
        json.dumps(rows, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{run_id}-events.json"'},
    )


@router.get("/runs/{run_id}/terminal/log")
async def run_terminal_log(request: Request, run_id: str) -> Response:
    api: ApiClient = request.app.state.api
    try:
        log = await api.console_log(run_id)
    except ApiError as err:
        return failed(request, PAGES["runs"], err)
    return Response(
        log,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{run_id}-console.log"'},
    )


async def _until_disconnect(websocket: WebSocket, outbox: "asyncio.Queue[bytes | str]") -> None:
    while (message := await websocket.receive())["type"] != "websocket.disconnect":
        frame = message.get("text")
        if frame is None:
            frame = message.get("bytes")
        if frame is not None:
            outbox.put_nowait(frame)


async def _relay(
    api: ApiClient, websocket: WebSocket, run_id: str, outbox: "asyncio.Queue[bytes | str]"
) -> None:
    async for frame in api.attach(run_id, outbox):
        if isinstance(frame, str):
            await websocket.send_text(frame)
        else:
            await websocket.send_bytes(frame)
    await websocket.close()


# The page has no login of its own, so a socket opened from another origin is refused.
@router.websocket("/runs/{run_id}/terminal/ws")
async def run_terminal_stream(websocket: WebSocket, run_id: str) -> None:
    origin = urlsplit(websocket.headers.get("origin", "")).netloc
    if origin and origin != websocket.headers.get("host"):
        raise WebSocketException(status.WS_1008_POLICY_VIOLATION, "foreign origin")
    await websocket.accept()
    outbox: asyncio.Queue[bytes | str] = asyncio.Queue()
    relay = asyncio.create_task(_relay(websocket.app.state.api, websocket, run_id, outbox))
    listener = asyncio.create_task(_until_disconnect(websocket, outbox))
    try:
        await asyncio.wait({relay, listener}, return_when=asyncio.FIRST_COMPLETED)
        if relay.done() and (err := relay.exception()) is not None:
            await websocket.close(status.WS_1011_INTERNAL_ERROR, str(err)[:120])
    finally:
        relay.cancel()
        listener.cancel()


def _reopen(request: Request, run_id: str) -> Response:
    if wants_fragment(request):
        return Response(headers={"HX-Redirect": f"/runs/{run_id}"})
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


async def _confirm(request: Request, run_id: str, now: int, ask: str) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        detail = await api.run_detail(run_id)
    except ApiError as err:
        return failed_overlay(request, PAGES["runs"], err)
    row = _detail_row(detail, now)
    asked = confirm.stop(row) if ask == "stop" else confirm.rerun(row, uuid4().hex)
    return render(
        request,
        PAGES["runs"],
        template="run_confirm_overlay.html" if wants_fragment(request) else "run_confirm_page.html",
        run=row,
        confirm=asked,
    )


@router.get("/runs/{run_id}/stop", response_class=HTMLResponse)
async def stop_confirm(request: Request, run_id: str, now: NowDep) -> HTMLResponse:
    return await _confirm(request, run_id, now, "stop")


# The key is rendered with the confirm, so a double submit replays onto the same new Run.
@router.get("/runs/{run_id}/rerun", response_class=HTMLResponse)
async def rerun_confirm(request: Request, run_id: str, now: NowDep) -> HTMLResponse:
    return await _confirm(request, run_id, now, "rerun")


@router.post("/runs/{run_id}/stop", response_class=HTMLResponse)
async def stop_run(request: Request, run_id: str, now: NowDep) -> Response:
    api: ApiClient = request.app.state.api
    try:
        await api.stop_run(run_id)
    except ApiError as err:
        return failed_overlay(request, PAGES["runs"], err)
    if wants_fragment(request):
        return await run(request, run_id, now)
    return _reopen(request, run_id)


@router.post("/runs/{run_id}/rerun", response_class=HTMLResponse)
async def rerun(request: Request, run_id: str) -> Response:
    api: ApiClient = request.app.state.api
    key = (await _form(request)).get("key") or uuid4().hex
    try:
        source = await api.run(run_id)
        created = await api.create_run(source["spec"], key)
    except ApiError as err:
        return failed_overlay(request, PAGES["runs"], err)
    return _reopen(request, created["id"])


# Search and filters narrow the table; the tiles always count the whole fleet.
@router.get("/runners", response_class=HTMLResponse)
async def runners(
    request: Request, now: NowDep, state: FleetQuery = "all", q: SearchQuery = ""
) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        every = await api.runners()
    except ApiError as err:
        return failed(request, PAGES["runners"], err)
    return render(
        request,
        PAGES["runners"],
        fleet(every),
        template="partials/runners_body.html" if wants_fragment(request) else "runners.html",
        runners=runner_rows(pick(every, state, q), now),
        runner_filters=RUNNER_FILTERS,
        runner_columns=RUNNER_COLUMNS,
        runner_note=RUNNER_NOTE,
        state=state,
        query=q,
    )


RunnerTab = Literal["overview", "runs", "audit"]


# The popup a runner row opens. A plain request gets it inside the shell, so the
# popup is reachable without htmx and a link to it can be shared.
async def _runner_panel(
    request: Request,
    runner_id: str,
    now: int,
    tab: RunnerTab,
    scope: events.Scope = "all",
    run: str | None = None,
) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        detail = await api.runner(runner_id)
    except ApiError as err:
        return failed_overlay(request, PAGES["runners"], err)
    row = runner_detail(detail.runner, detail.runs, now)
    seqs = {found["id"]: found["seq"] for found in detail.runs}
    return render(
        request,
        PAGES["runners"],
        fleet(detail.fleet),
        template="runner_overlay.html" if wants_fragment(request) else "runner_overlay_page.html",
        runners=runner_rows(detail.fleet, now),
        runner_filters=RUNNER_FILTERS,
        runner_columns=RUNNER_COLUMNS,
        runner_note=RUNNER_NOTE,
        state="all",
        query="",
        runner=row,
        tab=tab,
        lease_note=LEASE_NOTE,
        status_tone=STATUS_TONE,
        scopes=events.SCOPES,
        scope=scope,
        audit_run=run if run in seqs else None,
        audit_label=f"#{seqs[run]}" if run in seqs else "All runs",
        audit_rows=events.runner_audit(
            detail.events, seqs, scope, run if run in seqs else None, now
        ),
    )


@router.get("/runners/{runner_id}", response_class=HTMLResponse)
async def runner(request: Request, runner_id: str, now: NowDep) -> HTMLResponse:
    return await _runner_panel(request, runner_id, now, "overview")


@router.get("/runners/{runner_id}/runs", response_class=HTMLResponse)
async def runner_runs(request: Request, runner_id: str, now: NowDep) -> HTMLResponse:
    return await _runner_panel(request, runner_id, now, "runs")


@router.get("/runners/{runner_id}/audit", response_class=HTMLResponse)
async def runner_audit(
    request: Request,
    runner_id: str,
    now: NowDep,
    scope: events.Scope = "all",
    run: Annotated[str, Query(max_length=64)] = "",
) -> HTMLResponse:
    return await _runner_panel(request, runner_id, now, "audit", scope, run or None)


@router.get("/runners/{runner_id}/audit/export")
async def runner_audit_export(request: Request, runner_id: str) -> Response:
    api: ApiClient = request.app.state.api
    try:
        rows = await api.events(runner_id, limit=1000)
    except ApiError as err:
        return failed(request, PAGES["runners"], err)
    return Response(
        json.dumps(rows, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{runner_id}-audit.json"'},
    )


RunnerAct = Literal["revoke", "drain"]
ASKS = {"revoke": confirm.revoke, "drain": confirm.drain}


# Declared after the tabs, whose paths this one would otherwise take for an action.
@router.get("/runners/{runner_id}/{act}", response_class=HTMLResponse)
async def runner_confirm(
    request: Request, runner_id: str, act: RunnerAct, now: NowDep
) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        detail = await api.runner(runner_id)
    except ApiError as err:
        return failed_overlay(request, PAGES["runners"], err)
    row = runner_detail(detail.runner, detail.runs, now)
    return render(
        request,
        PAGES["runners"],
        template="run_confirm_overlay.html" if wants_fragment(request) else "run_confirm_page.html",
        confirm=ASKS[act](row),
    )


@router.post("/runners/{runner_id}/{act}", response_class=HTMLResponse)
async def runner_act(request: Request, runner_id: str, act: RunnerAct) -> Response:
    api: ApiClient = request.app.state.api
    try:
        if act == "revoke":
            await api.revoke_runner(runner_id)
        else:
            await api.drain_runner(runner_id)
    except ApiError as err:
        return failed_overlay(request, PAGES["runners"], err)
    target = f"/runners/{runner_id}"
    if wants_fragment(request):
        return Response(headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=303)


@router.get("/overlay/close", response_class=HTMLResponse)
def close_overlay() -> HTMLResponse:
    return HTMLResponse("")


def _catalog(
    images: list[Row], now: int, usage: Usage = "all", query: str = ""
) -> dict[str, object]:
    return {
        "images": image_rows(pick_images(images, usage, query), now),
        "image_filters": IMAGE_FILTERS,
        "image_columns": IMAGE_COLUMNS,
        "image_note": IMAGE_NOTE,
        "state": usage,
        "query": query,
    }


# Search and filters narrow the table; the tiles always count the whole catalog.
@router.get("/images", response_class=HTMLResponse)
async def images(
    request: Request, now: NowDep, state: UsageQuery = "all", q: SearchQuery = ""
) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        every = await api.images()
    except ApiError as err:
        return failed(request, PAGES["images"], err)
    return render(
        request,
        PAGES["images"],
        catalog(every),
        template="partials/images_body.html" if wants_fragment(request) else "images.html",
        **_catalog(every, now, state, q),
    )


async def _register_dialog(
    request: Request, now: int, entry: register.Draft, notice: str = ""
) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        every = await api.images()
    except ApiError as err:
        return failed_overlay(request, PAGES["images"], err)
    return render(
        request,
        PAGES["images"],
        catalog(every),
        template="image_register_overlay.html"
        if wants_fragment(request)
        else "image_register_page.html",
        **_catalog(every, now),
        entry=entry,
        notice=notice,
    )


# Declared before /images/{image_id}, which would otherwise take "register" for an id.
@router.get("/images/register", response_class=HTMLResponse)
async def register_dialog(request: Request, now: NowDep) -> HTMLResponse:
    return await _register_dialog(request, now, register.Draft())


@router.post("/images/register", response_class=HTMLResponse)
async def register_image(request: Request, now: NowDep) -> Response:
    api: ApiClient = request.app.state.api
    entry = register.draft(await _form(request))
    try:
        created = await api.register_image(register.body(entry))
    except (ApiError, ValueError) as err:
        return await _register_dialog(request, now, entry, str(err))
    target = f"/images/{created['id']}"
    if wants_fragment(request):
        return Response(headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=303)


ImageTab = Literal["overview", "runs", "audit"]


async def _image_panel(
    request: Request,
    image_id: str,
    now: int,
    tab: ImageTab,
    scope: events.ImageScope = "all",
) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        detail = await api.image(image_id)
    except ApiError as err:
        return failed_overlay(request, PAGES["images"], err)
    seqs = {found["id"]: found["seq"] for found in detail.runs}
    return render(
        request,
        PAGES["images"],
        catalog(detail.catalog),
        template="image_overlay.html" if wants_fragment(request) else "image_overlay_page.html",
        **_catalog(detail.catalog, now),
        image=image_detail(detail.image, detail.runs, now),
        tab=tab,
        source_note=SOURCE_NOTE,
        scopes=events.IMAGE_SCOPES,
        scope=scope,
        audit_rows=events.image_audit(detail.events, seqs, scope, now),
    )


@router.get("/images/{image_id}", response_class=HTMLResponse)
async def image(request: Request, image_id: str, now: NowDep) -> HTMLResponse:
    return await _image_panel(request, image_id, now, "overview")


@router.get("/images/{image_id}/runs", response_class=HTMLResponse)
async def image_runs(request: Request, image_id: str, now: NowDep) -> HTMLResponse:
    return await _image_panel(request, image_id, now, "runs")


@router.get("/images/{image_id}/audit", response_class=HTMLResponse)
async def image_audit(
    request: Request, image_id: str, now: NowDep, scope: events.ImageScope = "all"
) -> HTMLResponse:
    return await _image_panel(request, image_id, now, "audit", scope)


@router.get("/images/{image_id}/audit/export")
async def image_audit_export(request: Request, image_id: str) -> Response:
    api: ApiClient = request.app.state.api
    try:
        rows = await api.image_events(image_id, limit=1000)
    except ApiError as err:
        return failed(request, PAGES["images"], err)
    return Response(
        json.dumps(rows, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{image_id}-audit.json"'},
    )


def _shelf(
    every: list[Row], policies: list[Row], now: int, used: Used = "all", query: str = ""
) -> dict[str, object]:
    return {
        "profiles": profiles.profile_rows(profiles.pick_profiles(every, used, query), now),
        "profile_filters": PROFILE_FILTERS,
        "profile_columns": PROFILE_COLUMNS,
        "profile_note": PROFILE_NOTE,
        "state": used,
        "query": query,
    }


def _moved(request: Request, target: str) -> Response:
    if wants_fragment(request):
        return Response(headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=303)


# Search and filters narrow the table; the tiles always count every profile and policy.
@router.get("/profiles", response_class=HTMLResponse)
async def profile_shelf(
    request: Request, now: NowDep, state: UsedQuery = "all", q: SearchQuery = ""
) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        every, policies = await asyncio.gather(api.profiles(), api.policies())
    except ApiError as err:
        return failed(request, PAGES["profiles"], err)
    return render(
        request,
        PAGES["profiles"],
        profiles.shelf(every, policies),
        template="partials/profiles_body.html" if wants_fragment(request) else "profiles.html",
        **_shelf(every, policies, now, state, q),
    )


async def _profile_form(
    request: Request, now: int, form: profiles.Form, notice: str = ""
) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        every, policies = await asyncio.gather(api.profiles(), api.policies())
    except ApiError as err:
        return failed_overlay(request, PAGES["profiles"], err)
    return render(
        request,
        PAGES["profiles"],
        profiles.shelf(every, policies),
        template="profile_form_overlay.html"
        if wants_fragment(request)
        else "profile_form_page.html",
        **_shelf(every, policies, now),
        form=form,
        gates=profiles.gate_fields(form, policies),
        merges=profiles.merge_options(form),
        form_note=FORM_NOTE,
        gates_note=GATES_NOTE,
        notice=notice,
    )


# Declared before /profiles/{profile_id}, which would otherwise take "new" for an id.
@router.get("/profiles/new", response_class=HTMLResponse)
async def new_profile(request: Request, now: NowDep) -> HTMLResponse:
    return await _profile_form(request, now, profiles.new_form())


@router.post("/profiles/new", response_class=HTMLResponse)
async def create_profile(request: Request, now: NowDep) -> Response:
    api: ApiClient = request.app.state.api
    fields = await _form(request)
    form = profiles.read_form("clone" if fields.get("mode") == "clone" else "new", fields)
    try:
        created = await api.create_profile(form.name, form.spec())
    except (ApiError, new_run.FormError) as err:
        return await _profile_form(request, now, form, str(err))
    return _moved(request, f"/profiles/{created['id']}")


ProfileTab = Literal["overview", "runs", "audit"]


async def _profile_panel(
    request: Request,
    profile_id: str,
    now: int,
    tab: ProfileTab,
    scope: events.ProfileScope = "all",
) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        detail = await api.profile_detail(profile_id)
    except ApiError as err:
        return failed_overlay(request, PAGES["profiles"], err)
    seqs = {found["id"]: found["seq"] for found in detail.runs}
    return render(
        request,
        PAGES["profiles"],
        profiles.shelf(detail.profiles, detail.policies),
        template="profile_overlay.html" if wants_fragment(request) else "profile_overlay_page.html",
        **_shelf(detail.profiles, detail.policies, now),
        profile=profiles.profile_detail(detail.profile, detail.runs, detail.policies, now),
        tab=tab,
        copy_note=COPY_NOTE,
        policies_note=POLICIES_NOTE,
        open_note=OPEN_NOTE,
        scopes=events.PROFILE_SCOPES,
        scope=scope,
        audit_rows=events.profile_audit(detail.events, seqs, scope, now),
    )


@router.get("/profiles/{profile_id}", response_class=HTMLResponse)
async def profile(request: Request, profile_id: str, now: NowDep) -> HTMLResponse:
    return await _profile_panel(request, profile_id, now, "overview")


@router.get("/profiles/{profile_id}/runs", response_class=HTMLResponse)
async def profile_runs(request: Request, profile_id: str, now: NowDep) -> HTMLResponse:
    return await _profile_panel(request, profile_id, now, "runs")


@router.get("/profiles/{profile_id}/audit", response_class=HTMLResponse)
async def profile_audit(
    request: Request, profile_id: str, now: NowDep, scope: events.ProfileScope = "all"
) -> HTMLResponse:
    return await _profile_panel(request, profile_id, now, "audit", scope)


@router.get("/profiles/{profile_id}/audit/export")
async def profile_audit_export(request: Request, profile_id: str) -> Response:
    api: ApiClient = request.app.state.api
    try:
        rows = await api.profile_events(profile_id, limit=1000)
    except ApiError as err:
        return failed(request, PAGES["profiles"], err)
    return Response(
        json.dumps(rows, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{profile_id}-audit.json"'},
    )


@router.get("/profiles/{profile_id}/edit", response_class=HTMLResponse)
async def edit_profile(request: Request, profile_id: str, now: NowDep) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        found = await api.profile(profile_id)
    except ApiError as err:
        return failed_overlay(request, PAGES["profiles"], err)
    return await _profile_form(request, now, profiles.edit_form(found))


# The api refuses the write while a run from the profile is open; the form shows why.
@router.post("/profiles/{profile_id}/edit", response_class=HTMLResponse)
async def update_profile(request: Request, profile_id: str, now: NowDep) -> Response:
    api: ApiClient = request.app.state.api
    try:
        found = await api.profile(profile_id)
    except ApiError as err:
        return failed_overlay(request, PAGES["profiles"], err)
    form = profiles.read_form("edit", await _form(request), found)
    try:
        await api.update_profile(profile_id, form.spec())
    except (ApiError, new_run.FormError) as err:
        return await _profile_form(request, now, form, str(err))
    return _moved(request, f"/profiles/{profile_id}")


@router.get("/profiles/{profile_id}/clone", response_class=HTMLResponse)
async def clone_profile(request: Request, profile_id: str, now: NowDep) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        found = await api.profile(profile_id)
    except ApiError as err:
        return failed_overlay(request, PAGES["profiles"], err)
    return await _profile_form(request, now, profiles.clone_form(found))


def _delete_dialog(request: Request, found: Row, refusal: str = "") -> HTMLResponse:
    return render(
        request,
        PAGES["profiles"],
        template="profile_delete_overlay.html"
        if wants_fragment(request)
        else "profile_delete_page.html",
        profile=found,
        refusal=refusal,
    )


# A profile a run still holds is refused up front; the api decides again on the write.
@router.get("/profiles/{profile_id}/delete", response_class=HTMLResponse)
async def delete_confirm(request: Request, profile_id: str) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        found, held = await asyncio.gather(
            api.profile(profile_id), api.runs(limit=100, profile=profile_id)
        )
    except ApiError as err:
        return failed_overlay(request, PAGES["profiles"], err)
    refusal = profiles.busy_note(found, held) if found["active_runs"] else ""
    return _delete_dialog(request, found, refusal)


@router.post("/profiles/{profile_id}/delete", response_class=HTMLResponse)
async def delete_profile(request: Request, profile_id: str) -> Response:
    api: ApiClient = request.app.state.api
    try:
        found = await api.profile(profile_id)
    except ApiError as err:
        return failed_overlay(request, PAGES["profiles"], err)
    try:
        await api.delete_profile(profile_id)
    except ApiError as err:
        if err.status != 409:
            return failed_overlay(request, PAGES["profiles"], err)
        return _delete_dialog(request, found, str(err))
    return _moved(request, "/profiles")


KindFilter = Literal["all", "mount", "network", "shell", "mcp", "model"]
KindQuery = Annotated[KindFilter, Query()]
PolicyTab = Literal["document", "used"]


def _policy_list(
    policies: list[Row], now: int, kind: KindFilter = "all", query: str = ""
) -> dict[str, object]:
    return {
        "policies": documents.policy_rows(policies, now),
        "policy_filters": POLICY_FILTERS,
        "policy_columns": POLICY_COLUMNS,
        "policy_note": POLICY_NOTE,
        "state": kind,
        "query": query,
    }


# Search and filters narrow the table through the api; the tiles always count every policy.
@router.get("/policies", response_class=HTMLResponse)
async def policy_shelf(
    request: Request, now: NowDep, kind: KindQuery = "all", q: SearchQuery = ""
) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        every = await api.policies()
        shown = (
            await api.policies(None if kind == "all" else kind, q or None)
            if kind != "all" or q
            else every
        )
    except ApiError as err:
        return failed(request, PAGES["policies"], err)
    return render(
        request,
        PAGES["policies"],
        documents.shelf(every),
        template="partials/policies_body.html" if wants_fragment(request) else "policies.html",
        **_policy_list(shown, now, kind, q),
    )


async def _policy_overlay(
    request: Request, now: int, overlay: str, page: str, **context: object
) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        every = await api.policies()
    except ApiError as err:
        return failed_overlay(request, PAGES["policies"], err)
    return render(
        request,
        PAGES["policies"],
        documents.shelf(every),
        template=overlay if wants_fragment(request) else page,
        **_policy_list(every, now),
        **context,
    )


async def _policy_form(
    request: Request, now: int, form: documents.PolicyForm, notice: str = ""
) -> HTMLResponse:
    return await _policy_overlay(
        request,
        now,
        "policy_form_overlay.html",
        "policy_form_page.html",
        form=form,
        kinds=documents.KIND_LABELS,
        capabilities=documents.CAPABILITIES,
        protocols=documents.PROTOCOLS,
        modes=documents.MODES,
        dialects=documents.DIALECTS,
        limits=documents.LIMITS,
        form_note=POLICY_FORM_NOTE,
        form_hint=POLICY_FORM_HINTS[form.kind],
        workspace_hint=WORKSPACE_HINT,
        notice=notice,
    )


# Declared before /policies/{policy_id}, which would otherwise take "new" for an id.
@router.get("/policies/new", response_class=HTMLResponse)
async def new_policy(request: Request, now: NowDep, kind: str = "mount") -> HTMLResponse:
    return await _policy_form(request, now, documents.new_form(kind))


# A step adds or drops a row and redraws the form; only a plain submit reaches the api.
@router.post("/policies/new", response_class=HTMLResponse)
async def create_policy(request: Request, now: NowDep) -> Response:
    api: ApiClient = request.app.state.api
    fields = await _form(request)
    form = documents.read_form(fields)
    if fields.get("step"):
        return await _policy_form(request, now, form.stepped(fields["step"]))
    try:
        created, fresh = await api.create_policy(form.kind, form.document())
    except (ApiError, new_run.FormError) as err:
        return await _policy_form(request, now, form, str(err))
    if fresh:
        return _moved(request, f"/policies/{created['id']}")
    return await _policy_overlay(
        request,
        now,
        "policy_exists_overlay.html",
        "policy_exists_page.html",
        existing=created,
        fields=form.fields(),
        exists_note=EXISTS_NOTE,
    )


async def _policy_panel(request: Request, policy_id: str, now: int, tab: PolicyTab) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    secrets: dict[str, Row | None] = {}
    used = None
    try:
        found = await api.policy(policy_id)
        if tab == "used":
            named, runs = await asyncio.gather(
                api.profiles(policy=policy_id), api.runs(limit=100, policy=policy_id)
            )
            used = documents.used_by(found, named, runs, now)
        else:
            names = documents.secret_names(found)
            stored = await asyncio.gather(*(api.secret(name) for name in names))
            secrets = dict(zip(names, stored, strict=True))
    except ApiError as err:
        return failed_overlay(request, PAGES["policies"], err)
    return await _policy_overlay(
        request,
        now,
        "policy_overlay.html",
        "policy_overlay_page.html",
        policy=documents.policy_detail(found, secrets, now),
        used=used,
        tab=tab,
        identity_note=IDENTITY_NOTE,
        secrets_note=SECRETS_NOTE,
        budget_note=BUDGET_NOTE,
        used_note=USED_NOTE,
    )


@router.get("/policies/{policy_id}", response_class=HTMLResponse)
async def policy(request: Request, policy_id: str, now: NowDep) -> HTMLResponse:
    return await _policy_panel(request, policy_id, now, "document")


@router.get("/policies/{policy_id}/used", response_class=HTMLResponse)
async def policy_used(request: Request, policy_id: str, now: NowDep) -> HTMLResponse:
    return await _policy_panel(request, policy_id, now, "used")


@router.get("/policies/{policy_id}/new", response_class=HTMLResponse)
async def policy_from(request: Request, policy_id: str, now: NowDep) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        found = await api.policy(policy_id)
    except ApiError as err:
        return failed_overlay(request, PAGES["policies"], err)
    return await _policy_form(request, now, documents.from_document(found))


SecretName = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
SecretTab = Literal["overview", "used", "events"]
VaultQuery = Annotated[vault.State, Query()]
# A secret may be named "new", so the dialog lives at a path no name can take.
VAULT_NEW = "/secrets/_new"


def _secret_list(
    secrets: list[Row], now: int, state: vault.State = "all", query: str = ""
) -> dict[str, object]:
    return {
        "secrets": vault.secret_rows(secrets, now),
        "secret_filters": SECRET_FILTERS,
        "secret_columns": SECRET_COLUMNS,
        "secret_note": TYPED_ONCE_NOTE,
        "state": state,
        "query": query,
    }


# Search and filters narrow the table through the api; the tiles always count every secret.
@router.get("/secrets", response_class=HTMLResponse)
async def secret_shelf(
    request: Request, now: NowDep, state: VaultQuery = "all", q: SearchQuery = ""
) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        every = await api.secrets()
        shown = (
            await api.secrets(*vault.api_filter(state), q or None) if state != "all" or q else every
        )
    except ApiError as err:
        return failed(request, PAGES["secrets"], err)
    return render(
        request,
        PAGES["secrets"],
        vault.shelf(every),
        template="partials/secrets_body.html" if wants_fragment(request) else "secrets.html",
        **_secret_list(shown, now, state, q),
    )


def _secret_overlay(
    request: Request, every: list[Row], now: int, partial: str, **context: object
) -> HTMLResponse:
    return render(
        request,
        PAGES["secrets"],
        vault.shelf(every),
        template="secret_overlay.html" if wants_fragment(request) else "secret_overlay_page.html",
        **_secret_list(every, now),
        partial=f"partials/{partial}",
        never_note=NEVER_NOTE,
        **context,
    )


# Only the name and the term go back into a form; nothing here reads the value.
def _term_context(fields: dict[str, str], now: int) -> dict[str, object]:
    return {
        "terms": vault.terms(now, vault.shown_term(fields)),
        "confirmed": vault.confirmed(fields),
    }


async def _new_secret(
    request: Request, now: int, fields: dict[str, str], notice: str = ""
) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        every = await api.secrets()
    except ApiError as err:
        return failed_overlay(request, PAGES["secrets"], err)
    return _secret_overlay(
        request,
        every,
        now,
        "secret_form.html",
        name=fields.get("name", "").strip(),
        **_term_context(fields, now),
        form_note=SENT_ONCE_NOTE,
        notice=notice,
    )


def _refusal(err: ApiError | new_run.FormError) -> str:
    unprocessable = isinstance(err, ApiError) and err.status == HTTPStatus.UNPROCESSABLE_ENTITY
    return vault.REFUSED_NOTE if unprocessable else str(err)


@router.get(VAULT_NEW, response_class=HTMLResponse)
async def new_secret(request: Request, now: NowDep) -> HTMLResponse:
    return await _new_secret(request, now, {})


# The value is read from the body, posted once and dropped: a refused form comes back without it.
@router.post(VAULT_NEW, response_class=HTMLResponse)
async def create_secret(request: Request, now: NowDep) -> Response:
    api: ApiClient = request.app.state.api
    fields = await _form(request)
    name = fields.get("name", "").strip()
    try:
        created = await api.create_secret(
            name, vault.value_of(fields), vault.expires_at(fields, now)
        )
    except (ApiError, new_run.FormError) as err:
        return await _new_secret(request, now, fields, _refusal(err))
    return _moved(request, f"/secrets/{created['name']}")


async def _secret_panel(
    request: Request,
    name: str,
    now: int,
    tab: SecretTab,
    scope: events.SecretScope = "all",
) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        detail = await api.secret_detail(name)
    except ApiError as err:
        return failed_overlay(request, PAGES["secrets"], err)
    seqs = {found["run_id"]: found["seq"] for found in detail.secret["runs"]}
    return _secret_overlay(
        request,
        detail.secrets,
        now,
        "secret_panel.html",
        secret=vault.secret_detail(
            detail.secret, detail.events, detail.policies, detail.profiles, now
        ),
        tab=tab,
        write_only_note=WRITE_ONLY_NOTE,
        named_note=NAMED_NOTE,
        held_note=HELD_NOTE,
        issued_note=ISSUED_NOTE,
        scopes=events.SECRET_SCOPES,
        scope=scope,
        audit_rows=events.secret_audit(detail.events, seqs, scope, name, now),
    )


@router.get("/secrets/{name}", response_class=HTMLResponse)
async def secret(request: Request, name: SecretName, now: NowDep) -> HTMLResponse:
    return await _secret_panel(request, name, now, "overview")


@router.get("/secrets/{name}/used", response_class=HTMLResponse)
async def secret_used(request: Request, name: SecretName, now: NowDep) -> HTMLResponse:
    return await _secret_panel(request, name, now, "used")


@router.get("/secrets/{name}/events", response_class=HTMLResponse)
async def secret_events(
    request: Request, name: SecretName, now: NowDep, scope: events.SecretScope = "all"
) -> HTMLResponse:
    return await _secret_panel(request, name, now, "events", scope)


@router.get("/secrets/{name}/events/export")
async def secret_events_export(request: Request, name: SecretName) -> Response:
    api: ApiClient = request.app.state.api
    try:
        rows = await api.secret_events(name, limit=1000)
    except ApiError as err:
        return failed(request, PAGES["secrets"], err)
    return Response(
        json.dumps(rows, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}-events.json"'},
    )


async def _secret_dialog(
    request: Request, name: str, now: int, partial: str, **context: object
) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        detail = await api.secret_detail(name)
    except ApiError as err:
        return failed_overlay(request, PAGES["secrets"], err)
    return _secret_overlay(
        request,
        detail.secrets,
        now,
        partial,
        secret=vault.secret_detail(
            detail.secret, detail.events, detail.policies, detail.profiles, now
        ),
        **context,
    )


@router.get("/secrets/{name}/rotate", response_class=HTMLResponse)
async def rotate_dialog(request: Request, name: SecretName, now: NowDep) -> HTMLResponse:
    return await _secret_dialog(
        request, name, now, "secret_rotate.html", form_note=ROTATE_NOTE, notice=""
    )


@router.post("/secrets/{name}/rotate", response_class=HTMLResponse)
async def rotate_secret(request: Request, name: SecretName, now: NowDep) -> Response:
    api: ApiClient = request.app.state.api
    try:
        await api.rotate_secret(name, vault.value_of(await _form(request)))
    except (ApiError, new_run.FormError) as err:
        return await _secret_dialog(
            request, name, now, "secret_rotate.html", form_note=ROTATE_NOTE, notice=_refusal(err)
        )
    return _moved(request, f"/secrets/{name}")


def _expiry_context(fields: dict[str, str], now: int, notice: str = "") -> dict[str, object]:
    return _term_context(fields, now) | {"form_note": EXPIRY_NOTE, "notice": notice}


@router.get("/secrets/{name}/expiry", response_class=HTMLResponse)
async def expiry_dialog(request: Request, name: SecretName, now: NowDep) -> HTMLResponse:
    context = _expiry_context({}, now)
    return await _secret_dialog(request, name, now, "secret_expiry.html", **context)


@router.post("/secrets/{name}/expiry", response_class=HTMLResponse)
async def set_secret_expiry(request: Request, name: SecretName, now: NowDep) -> Response:
    api: ApiClient = request.app.state.api
    fields = await _form(request)
    try:
        await api.set_secret_expiry(name, vault.expires_at(fields, now))
    except (ApiError, new_run.FormError) as err:
        context = _expiry_context(fields, now, _refusal(err))
        return await _secret_dialog(request, name, now, "secret_expiry.html", **context)
    return _moved(request, f"/secrets/{name}")


@router.get("/secrets/{name}/delete", response_class=HTMLResponse)
async def delete_secret_confirm(request: Request, name: SecretName, now: NowDep) -> HTMLResponse:
    return await _secret_dialog(
        request, name, now, "secret_delete.html", delete_note=ERASED_NOTE, refusal=""
    )


# The api decides and audits the refusal; the confirm only asks, it never answers for it.
@router.post("/secrets/{name}/delete", response_class=HTMLResponse)
async def delete_secret(request: Request, name: SecretName, now: NowDep) -> Response:
    api: ApiClient = request.app.state.api
    try:
        await api.delete_secret(name)
    except ApiError as err:
        if err.status != HTTPStatus.CONFLICT:
            return failed_overlay(request, PAGES["secrets"], err)
        return await _secret_dialog(
            request, name, now, "secret_delete.html", delete_note="", refusal=str(err)
        )
    return _moved(request, "/secrets")


AuditQuery = Annotated[trail.Category, Query()]
AfterQuery = Annotated[int, Query(ge=0)]
AuditTab = Literal["overview", "logs"]


async def _trail(api: ApiClient, state: trail.Category, query: str, limit: int) -> list[Row]:
    found = trail.params(state, query)
    if found is None:
        return []
    return await api.trail({**found, "limit": limit, "order": "desc"})


async def _held(api: ApiClient, row: Row) -> Row | None:
    run_id = row.get("run_id")
    if not run_id:
        return None
    try:
        return await api.run(run_id)
    except ApiError as err:
        if err.status == 404:
            return None
        raise


# A run's own timeline when the event has one, else the latest events of its runner.
async def _timeline(api: ApiClient, row: Row, run: Row | None) -> list[Row]:
    if run is not None:
        return await api.run_events(run["id"])
    if row.get("runner_id"):
        return await api.events(row["runner_id"])
    return []


# The whole trail answers the tiles; the filter and the search narrow only the table.
async def _audit_page(
    request: Request,
    now: int,
    state: trail.Category,
    query: str,
    template: str = "audit.html",
    **popup: object,
) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        rows, summary, runners, runs = await asyncio.gather(
            _trail(api, state, query, trail.PAGE_SIZE),
            api.trail_summary(),
            api.runners(),
            api.runs(limit=100),
        )
    except ApiError as err:
        return failed(request, PAGES["audit"], err)
    names = {runner["id"]: runner["name"] for runner in runners}
    seqs = {found["id"]: found["seq"] for found in runs}
    return render(
        request,
        PAGES["audit"],
        trail.tiles(summary),
        template="partials/audit_body.html" if wants_fragment(request) else template,
        audit_rows=trail.audit_rows(rows, seqs, names, now),
        audit_filters=trail.AUDIT_FILTERS,
        audit_columns=trail.AUDIT_COLUMNS,
        audit_note=trail.AUDIT_NOTE,
        after=max((row["seq"] for row in rows), default=summary["last_seq"] or 0),
        state=state,
        query=query,
        **popup,
    )


@router.get("/audit", response_class=HTMLResponse)
async def audit(
    request: Request, now: NowDep, state: AuditQuery = "all", q: SearchQuery = ""
) -> HTMLResponse:
    return await _audit_page(request, now, state, q)


# Newer rows go on top of the table; the page keeps the last seq it has shown.
@router.get("/audit/tail", response_class=HTMLResponse)
async def audit_tail(
    request: Request, now: NowDep, after: AfterQuery, state: AuditQuery = "all", q: SearchQuery = ""
) -> Response:
    api: ApiClient = request.app.state.api
    found = trail.params(state, q)
    if found is None:
        return Response(status_code=204)
    try:
        rows, runners, runs = await asyncio.gather(
            api.trail({**found, "after": after, "limit": trail.PAGE_SIZE}),
            api.runners(),
            api.runs(limit=100),
        )
    except ApiError:
        return Response(status_code=204)
    if not rows:
        return Response(status_code=204)
    seqs = {run["id"]: run["seq"] for run in runs}
    names = {runner["id"]: runner["name"] for runner in runners}
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "partials/audit_tail.html",
        {"audit_rows": trail.audit_rows(rows[::-1], seqs, names, now), "after": rows[-1]["seq"]},
    )


@router.get("/audit/export")
async def audit_export(
    request: Request, state: AuditQuery = "all", q: SearchQuery = ""
) -> Response:
    api: ApiClient = request.app.state.api
    try:
        rows = await _trail(api, state, q, trail.EXPORT_SIZE)
    except ApiError as err:
        return failed(request, PAGES["audit"], err)
    return Response(
        json.dumps(rows, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="audit.json"'},
    )


# The popup an event row opens. A plain request gets it over the trail, so a link
# to one event can be shared.
async def _audit_popup(request: Request, event_id: str, now: int, tab: AuditTab) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        picked, runners = await asyncio.gather(api.trail_event(event_id), api.runners())
        run = await _held(api, picked)
        lines = await _timeline(api, picked, run) if tab == "logs" else []
    except ApiError as err:
        return failed_overlay(request, PAGES["audit"], err)
    names = {runner["id"]: runner["name"] for runner in runners}
    popup = {
        "detail": trail.detail(picked, run, names),
        "tab": tab,
        "log_lines": trail.timeline(lines, names, event_id),
        "denial_note": trail.DENIAL_NOTE,
    }
    if wants_fragment(request):
        return render(request, PAGES["audit"], None, template="audit_overlay.html", **popup)
    return await _audit_page(request, now, "all", "", "audit_overlay_page.html", **popup)


# Declared after /audit/tail and /audit/export, which this path would otherwise take for an id.
@router.get("/audit/{event_id}", response_class=HTMLResponse)
async def audit_event(request: Request, event_id: str, now: NowDep) -> HTMLResponse:
    return await _audit_popup(request, event_id, now, "overview")


@router.get("/audit/{event_id}/logs", response_class=HTMLResponse)
async def audit_event_logs(request: Request, event_id: str, now: NowDep) -> HTMLResponse:
    return await _audit_popup(request, event_id, now, "logs")
