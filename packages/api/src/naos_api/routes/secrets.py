from typing import Annotated, Self

from fastapi import APIRouter
from pydantic import BaseModel, Field, StrictInt

from naos_api import secrets
from naos_api.models import Secret
from naos_api.routes.deps import SessionDep
from naos_api.secrets import SecretName, SecretValue
from naos_api.spec import StrictModel


class SecretCreate(StrictModel):
    name: SecretName
    value: SecretValue
    expires_at: Annotated[StrictInt, Field(ge=0)] | None = None


class SecretRead(BaseModel):
    id: str
    name: str
    expires_at: int | None
    created_at: int

    @classmethod
    def of(cls, secret: Secret) -> Self:
        return cls(
            id=secret.id,
            name=secret.name,
            expires_at=secret.expires_at,
            created_at=secret.created_at,
        )


router = APIRouter()


@router.post("/secrets", status_code=201)
def create_secret(body: SecretCreate, session: SessionDep) -> SecretRead:
    return SecretRead.of(secrets.create_secret(session, body.name, body.value, body.expires_at))


@router.get("/secrets/{name}")
def get_secret(name: str, session: SessionDep) -> SecretRead:
    return SecretRead.of(secrets.get_secret(session, name))
