from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from naos_web.pages import NAV, PAGES, ListPage, Summary

router = APIRouter()


def render(request: Request, page: ListPage, summary: Summary | None = None) -> HTMLResponse:
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "list_page.html",
        {
            "nav": NAV,
            "page": page,
            "summary": summary or Summary(),
            "api_endpoint": request.app.state.api_endpoint,
        },
    )


@router.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse("/runs", status_code=307)


@router.get("/runs", response_class=HTMLResponse)
def runs(request: Request) -> HTMLResponse:
    return render(request, PAGES["runs"])


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
