from fastapi import APIRouter

from naos_api.routes.deps import domain_error_handler
from naos_api.routes.policies import router as _policies_router
from naos_api.routes.runner import router as runner_router
from naos_api.routes.runs import router as _runs_router

api_router = APIRouter()
api_router.include_router(_runs_router)
api_router.include_router(_policies_router)

__all__ = ["api_router", "domain_error_handler", "runner_router"]
