from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security.utils import get_authorization_scheme_param
from pydantic import BaseModel

from naos_api import audit, credentials
from naos_api.auth import operator_login, require_principal
from naos_api.clock import NowDep
from naos_api.routes.deps import SessionDep
from naos_api.settings import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


class CredentialOut(BaseModel):
    credential: str
    key: str
    expires_at: int


# The operator token logs in; a request signed with a live credential renews it.
async def _logged_in(request: Request, session: SessionDep, now: NowDep) -> None:
    scheme, token = get_authorization_scheme_param(request.headers.get("authorization"))
    if scheme.lower() != "bearer":
        await require_principal(request, session, now)
    elif not operator_login(token):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "operator is not authorized",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post("/credential", dependencies=[Depends(_logged_in)])
def credential(session: SessionDep, now: NowDep) -> CredentialOut:
    settings = get_settings()
    if settings.master_kid is None or settings.master_kid not in settings.master_keys:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "operator credentials are off")
    issued = credentials.issue(session, settings.master_keys, settings.master_kid, now)
    audit.record(
        session, "operator_credential_issued", actor="operator", expires_at=issued.expires_at
    )
    session.commit()
    return CredentialOut(credential=issued.credential, key=issued.key, expires_at=issued.expires_at)


@router.post("/logout", status_code=204, dependencies=[Depends(require_principal)])
def logout(session: SessionDep, now: NowDep) -> Response:
    credentials.bump(session, now)
    audit.record(session, "operator_logged_out", actor="operator")
    session.commit()
    return Response(status_code=204)
