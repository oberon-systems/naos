from collections.abc import Collection
from dataclasses import dataclass
from typing import Annotated
from uuid import uuid4

from pydantic import Field
from sqlmodel import Session, col, select

from naos_api.errors import NotFoundError, SecretConflictError
from naos_api.models import Secret

SecretName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
SecretValue = Annotated[str, Field(pattern=r"^[\x21-\x7e]{1,8192}$")]


@dataclass(frozen=True)
class IssuedCredential:
    value: str
    expires_at: int


def _find(session: Session, name: str) -> Secret | None:
    return session.exec(select(Secret).where(col(Secret.name) == name)).first()


def create_secret(session: Session, name: str, value: str, expires_at: int | None) -> Secret:
    if _find(session, name) is not None:
        raise SecretConflictError(f"secret {name} already exists")
    secret = Secret(id=f"sec_{uuid4().hex}", name=name, value=value, expires_at=expires_at)
    session.add(secret)
    try:
        session.commit()
    except Exception:
        session.rollback()
        if _find(session, name) is None:
            raise
        raise SecretConflictError(f"secret {name} already exists") from None
    return secret


def get_secret(session: Session, name: str) -> Secret:
    secret = _find(session, name)
    if secret is None:
        raise NotFoundError(f"secret {name} does not exist")
    return secret


def issue_credentials(
    session: Session, names: Collection[str], now: int, ttl: int
) -> dict[str, IssuedCredential]:
    if not names:
        return {}
    found = session.exec(select(Secret).where(col(Secret.name).in_(names))).all()
    return {
        secret.name: IssuedCredential(secret.value, min(now + ttl, secret.expires_at or now + ttl))
        for secret in found
        if secret.expires_at is None or secret.expires_at > now
    }
