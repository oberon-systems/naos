from fastapi import APIRouter

from naos_api.routes.audit import router as _audit_router
from naos_api.routes.deps import domain_error_handler
from naos_api.routes.images import router as _images_router
from naos_api.routes.policies import router as _policies_router
from naos_api.routes.runner import router as runner_router
from naos_api.routes.runs import router as _runs_router
from naos_api.routes.secrets import router as _secrets_router

api_router = APIRouter()
api_router.include_router(_runs_router)
api_router.include_router(_policies_router)
api_router.include_router(_images_router)
api_router.include_router(_secrets_router)
api_router.include_router(_audit_router)

__all__ = ["api_router", "domain_error_handler", "runner_router"]
