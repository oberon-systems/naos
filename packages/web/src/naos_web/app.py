from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from naos_web.routes import router
from naos_web.settings import get_settings

HERE = Path(__file__).parent


def api_endpoint(api_base_url: str) -> str:
    return urlsplit(api_base_url).netloc or api_base_url


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="naos web", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.templates = Jinja2Templates(directory=HERE / "templates")
    app.state.api_endpoint = api_endpoint(settings.api_base_url)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)
    return app
