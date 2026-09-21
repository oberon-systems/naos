from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from naos_web.client import ApiClient, ApiError, Dashboard, Row
from naos_web.clock import NowDep
from naos_web.format import ago
from naos_web.pages import (
    EXITS,
    FENCING,
    LIFECYCLE,
    NAV,
    PAGES,
    RUN_COLUMNS,
    RUN_FILTERS,
    STATUS_TONE,
    ListPage,
    Summary,
    TileValue,
)
from naos_web.rows import run_rows, runner_rows

State = Literal["all", "active", "queued", "waiting_merge", "failed"]
StateQuery = Annotated[State, Query()]

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


def failed(request: Request, page: ListPage, err: ApiError) -> HTMLResponse:
    return render(request, page, template="unreachable.html", reason=str(err))


@router.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse("/runs", status_code=307)


@router.get("/runs", response_class=HTMLResponse)
async def runs(request: Request, now: NowDep, state: StateQuery = "all") -> HTMLResponse:
    api: ApiClient = request.app.state.api
    fragment = request.headers.get("HX-Request") == "true"
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


@router.get("/runners", response_class=HTMLResponse)
def runners(request: Request) -> HTMLResponse:
    return render(request, PAGES["runners"])


@router.get("/images", response_class=HTMLResponse)
def images(request: Request) -> HTMLResponse:
    return render(request, PAGES["images"])


@router.get("/profiles", response_class=HTMLResponse)
def profiles(request: Request) -> HTMLResponse:
    return render(request, PAGES["profiles"])


@router.get("/audit", response_class=HTMLResponse)
def audit(request: Request) -> HTMLResponse:
    return render(request, PAGES["audit"])
