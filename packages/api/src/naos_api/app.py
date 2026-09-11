from fastapi import APIRouter, Depends, FastAPI

from naos_api.auth import require_principal
from naos_api.db import make_engine
from naos_api.errors import DomainError
from naos_api.models import create_schema
from naos_api.routes import domain_error_handler
from naos_api.routes import router as api_router
from naos_api.settings import Settings


def build_v1_router(*routers: APIRouter) -> APIRouter:
    v1 = APIRouter(prefix="/api/v1", dependencies=[Depends(require_principal)])
    for router in routers:
        v1.include_router(router)
    return v1


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="naos", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.engine = make_engine(settings)
    create_schema(app.state.engine)
    app.add_exception_handler(DomainError, domain_error_handler)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(build_v1_router(api_router))
    return app
