from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import Field, StrictInt

from naos_api import secrets
from naos_api.clock import NowDep
from naos_api.models import Secret
from naos_api.routes.deps import SessionDep
from naos_api.secrets import SecretDetail, SecretName, SecretState, SecretValue, SecretView
from naos_api.spec import StrictModel

Expiry = Annotated[StrictInt, Field(ge=0)] | None


class SecretCreate(StrictModel):
    name: SecretName
    value: SecretValue
    expires_at: Expiry = None


class SecretRotate(StrictModel):
    value: SecretValue


class SecretExpiry(StrictModel):
    expires_at: Expiry


def _view(session: SessionDep, secret: Secret, now: int) -> SecretView:
    return secrets.view_secrets(session, [secret], now)[0]


router = APIRouter()


@router.post("/secrets", status_code=201)
def create_secret(body: SecretCreate, session: SessionDep, now: NowDep) -> SecretView:
    secret = secrets.create_secret(session, body.name, body.value, body.expires_at)
    return _view(session, secret, now)


@router.get("/secrets")
def list_secrets(
    session: SessionDep,
    now: NowDep,
    q: Annotated[str | None, Query(max_length=128)] = None,
    state: SecretState | None = None,
    used: bool | None = None,
) -> list[SecretView]:
    return secrets.list_secrets(session, now, q, state, used)


@router.get("/secrets/{name}")
def get_secret(name: str, session: SessionDep, now: NowDep) -> SecretDetail:
    return secrets.detail_secret(session, name, now)


@router.post("/secrets/{name}/rotate")
def rotate_secret(name: str, body: SecretRotate, session: SessionDep, now: NowDep) -> SecretView:
    return _view(session, secrets.rotate_secret(session, name, body.value, now), now)


@router.patch("/secrets/{name}")
def set_expiry(name: str, body: SecretExpiry, session: SessionDep, now: NowDep) -> SecretView:
    return _view(session, secrets.set_expiry(session, name, body.expires_at), now)


@router.delete("/secrets/{name}", status_code=204)
def delete_secret(name: str, session: SessionDep, now: NowDep) -> None:
    secrets.delete_secret(session, name, now)
