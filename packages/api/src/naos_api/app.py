import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import APIRouter, Depends, FastAPI
from sqlmodel import Session

from naos_api.auth import require_principal
from naos_api.clock import now_ts
from naos_api.db import Database
from naos_api.errors import DomainError
from naos_api.routes import api_router, domain_error_handler, runner_router
from naos_api.runners import expire_leases
from naos_api.settings import get_settings

log = logging.getLogger(__name__)


def build_v1_router(*routers: APIRouter) -> APIRouter:
    v1 = APIRouter(prefix="/api/v1", dependencies=[Depends(require_principal)])
    for router in routers:
        v1.include_router(router)
    return v1


def sweep_leases(db: Database) -> list[str]:
    with Session(db.engine) as session:
        return expire_leases(session, now_ts())


async def _sweep_forever(db: Database, interval: int) -> None:
    while True:
        try:
            expired = await asyncio.to_thread(sweep_leases, db)
        except Exception:
            log.exception("lease sweep failed")
        else:
            if expired:
                log.warning("expired runner leases: %s", ", ".join(expired))
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    interval = get_settings().lease_sweep_interval_seconds
    sweep = asyncio.create_task(_sweep_forever(app.state.db, interval))
    try:
        yield
    finally:
        sweep.cancel()
        with suppress(asyncio.CancelledError):
            await sweep


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="naos", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.db = Database(settings.database_url)
    app.state.db.create_schema()
    app.add_exception_handler(DomainError, domain_error_handler)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(build_v1_router(api_router))
    app.include_router(runner_router, prefix="/api/v1")
    return app
