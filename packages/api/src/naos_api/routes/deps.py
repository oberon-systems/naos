from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlmodel import Session

from naos_api.db import get_session
from naos_api.errors import (
    ConsoleFullError,
    IdempotencyConflictError,
    ImageConflictError,
    InvalidTransitionError,
    LeaseError,
    MergeError,
    NotFoundError,
    PolicyError,
    ProfileBusyError,
    ProfileConflictError,
    SecretConflictError,
)
from naos_api.settings import get_settings


def _lease_ttl() -> int:
    return get_settings().lease_ttl_seconds


def _token_ttl() -> int:
    return get_settings().runner_token_ttl_seconds


def _credential_ttl() -> int:
    return get_settings().run_credential_ttl_seconds


def _console_limit() -> int:
    return get_settings().console_limit_bytes


def _mount_roots() -> list[str]:
    return get_settings().allowed_mount_roots


SessionDep = Annotated[Session, Depends(get_session)]
LeaseTtlDep = Annotated[int, Depends(_lease_ttl)]
TokenTtlDep = Annotated[int, Depends(_token_ttl)]
CredentialTtlDep = Annotated[int, Depends(_credential_ttl)]
ConsoleLimitDep = Annotated[int, Depends(_console_limit)]
MountRootsDep = Annotated[list[str], Depends(_mount_roots)]
IdempotencyKey = Annotated[str, Header(pattern=r"^[A-Za-z0-9._:-]{1,128}$")]

_ERROR_STATUS: dict[type[Exception], int] = {
    NotFoundError: 404,
    InvalidTransitionError: 409,
    IdempotencyConflictError: 409,
    LeaseError: 409,
    ImageConflictError: 409,
    SecretConflictError: 409,
    ProfileConflictError: 409,
    ProfileBusyError: 409,
    PolicyError: 422,
    MergeError: 422,
    ConsoleFullError: 413,
}


def domain_error_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=_ERROR_STATUS.get(type(exc), 400))
