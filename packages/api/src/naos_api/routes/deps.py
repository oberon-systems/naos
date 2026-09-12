from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlmodel import Session

from naos_api.db import get_session
from naos_api.errors import (
    IdempotencyConflictError,
    ImageConflictError,
    ImageError,
    InvalidTransitionError,
    LeaseError,
    NotFoundError,
    PolicyError,
)
from naos_api.settings import Settings, get_settings

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
IdempotencyKey = Annotated[str, Header(pattern=r"^[A-Za-z0-9._:-]{1,128}$")]

_ERROR_STATUS: dict[type[Exception], int] = {
    NotFoundError: 404,
    InvalidTransitionError: 409,
    IdempotencyConflictError: 409,
    LeaseError: 409,
    ImageConflictError: 409,
    PolicyError: 422,
    ImageError: 422,
}


def domain_error_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=_ERROR_STATUS.get(type(exc), 400))
