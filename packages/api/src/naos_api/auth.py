import hmac
from typing import Annotated, NoReturn

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from naos_api import runners
from naos_api.clock import NowDep
from naos_api.db import get_session
from naos_api.runners import RunnerPrincipal, hash_token
from naos_api.settings import get_settings

_bearer = HTTPBearer(auto_error=False)
BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_principal() -> NoReturn:
    raise _unauthorized("authentication is not configured")


def _enrollment_hash() -> str | None:
    return get_settings().runner_enrollment_token_sha256


def require_enrollment(
    credentials: BearerDep, expected: Annotated[str | None, Depends(_enrollment_hash)]
) -> None:
    if (
        credentials is None
        or expected is None
        or not hmac.compare_digest(hash_token(credentials.credentials), expected)
    ):
        raise _unauthorized("runner enrollment is not authorized")


def require_runner(
    runner_id: str,
    credentials: BearerDep,
    session: Annotated[Session, Depends(get_session)],
    now: NowDep,
) -> RunnerPrincipal:
    principal = (
        None if credentials is None else runners.authenticate(session, credentials.credentials, now)
    )
    if principal is None:
        raise _unauthorized("runner credentials are not valid")
    if principal.runner_id != runner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="runner credentials do not belong to this runner",
        )
    return principal
