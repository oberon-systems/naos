import asyncio
import json
from dataclasses import replace
from typing import Annotated, Literal
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketException, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from naos_web import confirm, events, new_run, terminal
from naos_web.client import ApiClient, ApiError, Choices, Dashboard, Row, RunDetail
from naos_web.clock import NowDep
from naos_web.format import ago
from naos_web.pages import (
    EXITS,
    FENCING,
    LEASE_NOTE,
    LIFECYCLE,
    NAV,
    PAGES,
    RUN_COLUMNS,
    RUN_FILTERS,
    RUNNER_COLUMNS,
    RUNNER_FILTERS,
    RUNNER_NOTE,
    STATUS_TONE,
    ListPage,
    Summary,
    TileValue,
)
from naos_web.rows import (
    RunDetailRow,
    fleet,
    pick,
    run_detail,
    run_rows,
    runner_detail,
    runner_rows,
)

State = Literal["all", "active", "queued", "waiting_merge", "failed"]
Tab = Literal["overview", "terminal", "logs"]
StateQuery = Annotated[State, Query()]
Fleet = Literal["all", "live", "stale", "revoked"]
FleetQuery = Annotated[Fleet, Query()]
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
        now,
    )


async def _run_panel(
    request: Request, run_id: str, now: int, tab: Tab, kind: events.Kind = "all"
) -> HTMLResponse:
    api: ApiClient = request.app.state.api
    try:
        detail = await api.run_detail(run_id)
    except ApiError as err:
        return failed_overlay(request, PAGES["runs"], err)
    return render(
        request,
        PAGES["runs"],
        template="run_overlay.html" if wants_fragment(request) else "run_overlay_page.html",
        run=_detail_row(detail, now),
        tab=tab,
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


@router.get("/images", response_class=HTMLResponse)
def images(request: Request) -> HTMLResponse:
    return render(request, PAGES["images"])


@router.get("/profiles", response_class=HTMLResponse)
def profiles(request: Request) -> HTMLResponse:
    return render(request, PAGES["profiles"])


@router.get("/audit", response_class=HTMLResponse)
def audit(request: Request) -> HTMLResponse:
    return render(request, PAGES["audit"])
