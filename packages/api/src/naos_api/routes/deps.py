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
from naos_api.images.service import ImageSource
from naos_api.settings import get_settings


def _lease_ttl() -> int:
    return get_settings().lease_ttl_seconds


def _token_ttl() -> int:
    return get_settings().runner_token_ttl_seconds


def _mount_roots() -> list[str]:
    return get_settings().allowed_mount_roots


def _image_source() -> ImageSource:
    settings = get_settings()
    return ImageSource(
        url=settings.image_source_url,
        allowed_hosts=frozenset(settings.image_source_allowed_hosts),
        max_bytes=settings.image_max_bytes,
        timeout_seconds=settings.image_download_timeout_seconds,
    )


SessionDep = Annotated[Session, Depends(get_session)]
LeaseTtlDep = Annotated[int, Depends(_lease_ttl)]
TokenTtlDep = Annotated[int, Depends(_token_ttl)]
MountRootsDep = Annotated[list[str], Depends(_mount_roots)]
ImageSourceDep = Annotated[ImageSource, Depends(_image_source)]
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
