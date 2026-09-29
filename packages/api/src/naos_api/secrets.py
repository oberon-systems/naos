import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlmodel import Session, col, func, select

from naos_api import audit
from naos_api.errors import NotFoundError, SecretBusyError, SecretConflictError
from naos_api.lifecycle import CREDENTIAL_BOUND, RunStatus
from naos_api.models import AuditEvent, Policy, Run, Secret, SecretMeta
from naos_api.spec import PolicyKind

SecretName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
SecretValue = Annotated[
    str, Field(max_length=8192, pattern=r"^[\x21-\x7e \t\r\n]*[\x21-\x7e][\x21-\x7e \t\r\n]*$")
]

EXPIRING = 7 * 86_400


class SecretState(StrEnum):
    VALID = "valid"
    EXPIRING = "expiring"
    EXPIRED = "expired"


@dataclass(frozen=True)
class IssuedCredential:
    value: str
    expires_at: int


class Usage(BaseModel):
    kind: Literal["cred"]
    id: str
    server: str | None


class Holder(BaseModel):
    run_id: str
    seq: int
    status: RunStatus
    profile_id: str | None


class RunUse(Holder):
    issued: int
    last_at: int


class SecretView(SecretMeta):
    state: SecretState
    named_by: list[Usage]
    held_by: list[Holder]

    @property
    def used(self) -> bool:
        return bool(self.named_by or self.held_by)


class SecretDetail(SecretView):
    runs: list[RunUse]


def _find(session: Session, name: str) -> Secret | None:
    return session.exec(select(Secret).where(col(Secret.name) == name)).first()


def create_secret(session: Session, name: str, value: str, expires_at: int | None) -> Secret:
    if _find(session, name) is not None:
        raise SecretConflictError(f"secret {name} already exists")
    secret = Secret(id=f"sec_{uuid4().hex}", name=name, value=value, expires_at=expires_at)
    session.add(secret)
    audit.record(session, "secret_created", actor="operator", name=name)
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


def state_of(secret: Secret, now: int) -> SecretState:
    if secret.expires_at is None or secret.expires_at > now + EXPIRING:
        return SecretState.VALID
    if secret.expires_at > now:
        return SecretState.EXPIRING
    return SecretState.EXPIRED


# Policy documents are JSON, so the secrets they name are read in Python, not queried.
def _naming(session: Session) -> dict[str, list[Usage]]:
    naming: dict[str, list[Usage]] = {}
    statement = select(Policy).where(col(Policy.kind) == PolicyKind.MCP).order_by(col(Policy.id))
    for policy in session.exec(statement).all():
        for server in policy.document["servers"]:
            if server["credential"]:
                usage = Usage(kind="cred", id=policy.id, server=server["name"])
                naming.setdefault(server["credential"], []).append(usage)
    return naming


def _holding(session: Session) -> dict[str, list[Holder]]:
    names = col(AuditEvent.data)["names"].as_string()
    statement = (
        select(Run, names)
        .join(AuditEvent, col(AuditEvent.run_id) == col(Run.id))
        .where(
            col(AuditEvent.event) == "credentials_issued",
            col(Run.status).in_(CREDENTIAL_BOUND),
        )
        .group_by(col(Run.id), names)
        .order_by(col(Run.seq))
    )
    holding: dict[str, list[Holder]] = {}
    for run, issued in session.exec(statement).all():
        holder = Holder(run_id=run.id, seq=run.seq, status=run.status, profile_id=run.profile_id)
        for name in json.loads(issued):
            held = holding.setdefault(name, [])
            if holder not in held:
                held.append(holder)
    return holding


def view_secrets(session: Session, secrets: Sequence[Secret], now: int) -> list[SecretView]:
    if not secrets:
        return []
    naming = _naming(session)
    holding = _holding(session)
    return [
        SecretView.model_validate(
            secret,
            update={
                "state": state_of(secret, now),
                "named_by": naming.get(secret.name, []),
                "held_by": holding.get(secret.name, []),
            },
        )
        for secret in secrets
    ]


def _matches(view: SecretView, needle: str) -> bool:
    words = [view.name, view.id]
    words += [usage.id for usage in view.named_by]
    words += [usage.server for usage in view.named_by if usage.server]
    return any(needle in word.lower() for word in words)


def list_secrets(
    session: Session,
    now: int,
    query: str | None = None,
    state: SecretState | None = None,
    used: bool | None = None,
) -> list[SecretView]:
    secrets = session.exec(select(Secret).order_by(col(Secret.name))).all()
    views = view_secrets(session, secrets, now)
    if query:
        views = [view for view in views if _matches(view, query.lower())]
    if state is not None:
        views = [view for view in views if view.state is state]
    if used is not None:
        views = [view for view in views if view.used is used]
    return views


def _runs_of(session: Session, name: str) -> list[RunUse]:
    last = func.max(col(AuditEvent.at))
    statement = (
        select(Run, func.count(), last)
        .join(AuditEvent, col(AuditEvent.run_id) == col(Run.id))
        .where(col(AuditEvent.event) == "credentials_issued", audit.names_secret(name))
        .group_by(col(Run.id))
        .order_by(last.desc(), col(Run.seq).desc())
    )
    return [
        RunUse(
            run_id=run.id,
            seq=run.seq,
            status=run.status,
            profile_id=run.profile_id,
            issued=int(issued),
            last_at=int(at),
        )
        for run, issued, at in session.exec(statement).all()
    ]


def detail_secret(session: Session, name: str, now: int) -> SecretDetail:
    view = view_secrets(session, [get_secret(session, name)], now)[0]
    return SecretDetail.model_validate(view, update={"runs": _runs_of(session, name)})


def rotate_secret(session: Session, name: str, value: str, now: int) -> Secret:
    secret = get_secret(session, name)
    secret.value = value
    secret.rotated_at = now
    session.add(secret)
    audit.record(session, "secret_rotated", actor="operator", name=name)
    session.commit()
    session.refresh(secret)
    return secret


def set_expiry(session: Session, name: str, expires_at: int | None) -> Secret:
    secret = get_secret(session, name)
    moved: dict[str, Any] = {"from": secret.expires_at, "to": expires_at}
    secret.expires_at = expires_at
    session.add(secret)
    audit.record(
        session,
        "secret_expiry_changed",
        actor="operator",
        name=name,
        **moved,
    )
    session.commit()
    session.refresh(secret)
    return secret


def _busy_detail(view: SecretView) -> str:
    named = [f"{usage.id} (server {usage.server})" for usage in view.named_by]
    held = [f"#{holder.seq} {holder.status}" for holder in view.held_by]
    parts = []
    if named:
        parts.append(f"named by {', '.join(named)}")
    if held:
        parts.append(f"held by {', '.join(held)}")
    return f"secret {view.name} is in use: {'; '.join(parts)}"


def delete_secret(session: Session, name: str, now: int) -> None:
    secret = get_secret(session, name)
    view = view_secrets(session, [secret], now)[0]
    if view.used:
        audit.record(
            session,
            "secret_delete_refused",
            actor="operator",
            name=name,
            named_by=len(view.named_by),
            held_by=[holder.run_id for holder in view.held_by],
        )
        session.commit()
        raise SecretBusyError(_busy_detail(view))
    session.delete(secret)
    audit.record(session, "secret_deleted", actor="operator", name=name)
    session.commit()


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
