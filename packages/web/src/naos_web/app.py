from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from naos_web import format
from naos_web.client import ApiClient, read_token
from naos_web.routes import router
from naos_web.settings import get_settings

HERE = Path(__file__).parent


def api_endpoint(api_base_url: str) -> str:
    return urlsplit(api_base_url).netloc or api_base_url


def api_attach_url(api_base_url: str) -> str:
    parts = urlsplit(api_base_url.rstrip("/"))
    scheme = "wss" if parts.scheme == "https" else "ws"
    return parts._replace(scheme=scheme).geturl()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        await app.state.api.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="naos web", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals["fmt"] = format
    app.state.templates = templates
    app.state.api_endpoint = api_endpoint(settings.api_base_url)
    app.state.api_attach_url = api_attach_url(settings.api_base_url)
    # The token is read once, held by the client and never put in a template context.
    app.state.api = ApiClient(
        settings.api_url,
        read_token(settings.operator_token_file),
        settings.api_timeout_seconds,
    )
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)
    return app
