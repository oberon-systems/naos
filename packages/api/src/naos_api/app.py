import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import Engine
from sqlmodel import Session

from naos_api.auth import require_principal
from naos_api.db import make_engine
from naos_api.errors import DomainError
from naos_api.models import create_schema, utcnow
from naos_api.routes import domain_error_handler
from naos_api.routes import router as api_router
from naos_api.runner_routes import router as runner_router
from naos_api.runners import expire_leases
from naos_api.settings import Settings

log = logging.getLogger(__name__)


def build_v1_router(*routers: APIRouter) -> APIRouter:
    v1 = APIRouter(prefix="/api/v1", dependencies=[Depends(require_principal)])
    for router in routers:
        v1.include_router(router)
    return v1


def sweep_leases(engine: Engine) -> list[str]:
    with Session(engine) as session:
        return expire_leases(session, utcnow())


async def _sweep_forever(engine: Engine, interval: int) -> None:
    while True:
        try:
            expired = await asyncio.to_thread(sweep_leases, engine)
        except Exception:
            log.exception("lease sweep failed")
        else:
            if expired:
                log.warning("expired runner leases: %s", ", ".join(expired))
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    task = asyncio.create_task(
        _sweep_forever(app.state.engine, settings.lease_sweep_interval_seconds)
    )
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="naos", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = make_engine(settings)
    create_schema(app.state.engine)
    app.add_exception_handler(DomainError, domain_error_handler)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(build_v1_router(api_router))
    app.include_router(runner_router, prefix="/api/v1")
    return app
