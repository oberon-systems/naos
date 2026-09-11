from fastapi import APIRouter, Depends, FastAPI

from naos_api.auth import require_principal
from naos_api.db import make_engine
from naos_api.settings import Settings


def build_v1_router(*routers: APIRouter) -> APIRouter:
    v1 = APIRouter(prefix="/api/v1", dependencies=[Depends(require_principal)])
    for router in routers:
        v1.include_router(router)
    return v1


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="naos", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine = make_engine(settings or Settings())

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(build_v1_router())
    return app
