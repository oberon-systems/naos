from collections.abc import Sequence
from typing import Annotated, Any, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt, create_model, model_validator
from sqlmodel import Session, and_, col, or_, select

from naos_api.clock import now_ts
from naos_api.lifecycle import RunStatus
from naos_api.models import AuditEvent, Lease, Run

Actor = Literal["operator", "runner", "system"]
EventId = Annotated[str, Field(pattern=r"^evt_[0-9a-f]{32}$")]
Id = Annotated[str, Field(pattern=r"^[A-Za-z0-9_]{1,64}$")]
Name = Annotated[str, Field(max_length=256)]
Text = Annotated[str, Field(max_length=500)]
GuestPath = Annotated[str, Field(max_length=4096)]
Count = Annotated[StrictInt, Field(ge=0)]

API_EVENTS: dict[str, frozenset[str]] = {
    "run_created": frozenset({"workspace", "profile"}),
    "run_queued": frozenset({"position"}),
    "run_assigned": frozenset({"lease_id", "slot", "slots"}),
    "run_transition": frozenset({"from", "to", "reason"}),
    "run_stop_requested": frozenset({"status"}),
    "runner_registered": frozenset(),
    "runner_revoked": frozenset(),
    "runner_drained": frozenset(),
    "lease_acquired": frozenset({"lease_id"}),
    "lease_expired": frozenset({"lease_id"}),
    "token_rotated": frozenset(),
    "waiting_rebound": frozenset({"lease_id"}),
    "credentials_issued": frozenset({"names", "ttl"}),
    "diff_reported": frozenset({"entries", "rejected", "sensitive", "policy", "decided"}),
    "merge_decided": frozenset({"paths", "resolutions"}),
    "merge_reported": frozenset(
        {"outcome", "applied", "skipped", "exported", "backed_up", "conflicts"}
    ),
    "policy_created": frozenset({"policy_id", "kind"}),
    "image_registered": frozenset({"image_id", "version", "digest"}),
    "secret_created": frozenset({"name"}),
    "profile_created": frozenset({"profile_id", "name"}),
    "profile_updated": frozenset({"profile_id", "name"}),
    "profile_deleted": frozenset({"profile_id", "name"}),
    "console_attached": frozenset(),
    "console_typing": frozenset({"view"}),
}

_VM = {"vm_id": Id, "run_id": Id}
_RUN = {"run_id": Id}
_NETWORK = {"run_id": Id, "protocol": Name, "host": Name, "rule": Name}
_SHELL = {"run_id": Id, "capability": Name, "path": GuestPath}
RUNNER_EVENTS: dict[str, dict[str, Any]] = {
    "runner_registered": {"runner_id": Id},
    "runner_credentials_dropped": {"runner_id": Id},
    "run_claimed": _RUN,
    "run_failed": _RUN | {"reason": Text},
    "orphan_destroyed": _VM,
    "lease_fenced": {"vms": Count},
    "image_cached": {"digest": Name},
    "image_rejected": {"digest": Name, "reason": Text},
    "vm_created": _VM,
    "vm_stopped": _VM,
    "workspace_shared": _RUN | {"mode": Literal["ro", "rw"]},
    "workspace_collected": _RUN | {"entries": Count, "rejected": Count},
    "merge_conflict": _RUN | {"conflicts": Count},
    "merge_applied": _RUN | {"applied": Count, "backed_up": Count, "exported": Count},
    "changes_archived": _VM,
    "vm_destroyed": _VM,
    "console_attached": _VM,
    "network_policy_configured": _RUN,
    "network_allowed": _NETWORK,
    "network_denied": _NETWORK | {"reason": Text},
    "shell_policy_configured": _RUN,
    "shell_allowed": _SHELL,
    "shell_denied": _SHELL | {"reason": Text},
    "mcp_attached": _RUN,
    "mcp_rejected": _RUN | {"reason": Text},
    "mcp_policy_configured": _RUN,
    "mcp_credentials_updated": _RUN | {"names": Text},
    "mcp_call": _RUN
    | {
        "server": Name,
        "tool": Name,
        "resource": GuestPath,
        "decision": Literal["allow", "deny"],
        "duration_ms": Count,
        "category": Name,
    },
    "audit_dropped": {"dropped": Count},
}


def _schema(event: str, fields: dict[str, Any]) -> type[BaseModel]:
    required: dict[str, Any] = {name: (kind, ...) for name, kind in fields.items()}
    return create_model(
        f"{event}_fields", __config__=ConfigDict(extra="forbid", strict=True), **required
    )


_FIELDS = {event: _schema(event, fields) for event, fields in RUNNER_EVENTS.items()}


class RunnerEventIn(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    id: EventId
    at: Count
    event: str

    @model_validator(mode="after")
    def _known_fields(self) -> Self:
        fields = _FIELDS.get(self.event)
        if fields is None:
            raise ValueError(f"{self.event} is not a runner audit event")
        fields.model_validate(self.model_extra or {})
        return self

    @property
    def fields(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


def record(
    session: Session,
    event: str,
    *,
    actor: Actor,
    run_id: str | None = None,
    runner_id: str | None = None,
    **data: Any,
) -> None:
    unknown = sorted(set(data) - API_EVENTS[event])
    if unknown:
        raise ValueError(f"{event} does not carry {', '.join(unknown)}")
    session.add(
        AuditEvent(
            id=f"evt_{uuid4().hex}",
            at=now_ts(),
            source="api",
            event=event,
            actor=actor,
            run_id=run_id,
            runner_id=runner_id,
            data=data,
        )
    )


def transitioned(
    session: Session,
    run_id: str,
    expected: RunStatus,
    target: RunStatus,
    reason: str | None,
    *,
    actor: Actor,
    runner_id: str | None = None,
) -> None:
    moved = {"from": str(expected), "to": str(target), "reason": reason}
    record(
        session,
        "run_transition",
        actor=actor,
        run_id=run_id,
        runner_id=runner_id,
        **moved,
    )


def _held(session: Session, runner_id: str, run_ids: set[str]) -> set[str]:
    if not run_ids:
        return set()
    statement = (
        select(Run.id)
        .join(Lease, col(Run.lease_id) == col(Lease.id))
        .where(col(Lease.runner_id) == runner_id, col(Run.id).in_(run_ids))
    )
    return set(session.exec(statement).all())


def _stored(session: Session, ids: list[str]) -> set[str]:
    return set(session.exec(select(AuditEvent.id).where(col(AuditEvent.id).in_(ids))).all())


def ingest(
    session: Session, runner_id: str, events: Sequence[RunnerEventIn]
) -> tuple[int, list[str]]:
    held = _held(session, runner_id, {e.fields["run_id"] for e in events if "run_id" in e.fields})
    refused = [e.id for e in events if "run_id" in e.fields and e.fields["run_id"] not in held]
    for attempt in range(2):
        seen = _stored(session, [event.id for event in events]) | set(refused)
        accepted = 0
        for event in events:
            if event.id in seen:
                continue
            seen.add(event.id)
            fields = event.fields
            session.add(
                AuditEvent(
                    id=event.id,
                    at=event.at,
                    source="runner",
                    event=event.event,
                    actor="runner",
                    run_id=fields.pop("run_id", None),
                    vm_id=fields.pop("vm_id", None),
                    runner_id=runner_id,
                    data=fields,
                )
            )
            accepted += 1
        try:
            session.commit()
        except Exception:
            # A retried batch raced this one; the second pass skips what it stored.
            session.rollback()
            if attempt == 1:
                raise
            continue
        return accepted, refused
    raise AssertionError("unreachable")


def timeline(session: Session, run_id: str, limit: int) -> Sequence[AuditEvent]:
    statement = (
        select(AuditEvent)
        .where(col(AuditEvent.run_id) == run_id)
        .order_by(col(AuditEvent.at), col(AuditEvent.seq))
        .limit(limit)
    )
    return session.exec(statement).all()


def search(
    session: Session,
    *,
    runner_id: str | None,
    event: str | None,
    since: int | None,
    after: int | None,
    limit: int,
    newest_first: bool = False,
    image_id: str | None = None,
    profile_id: str | None = None,
) -> Sequence[AuditEvent]:
    statement = select(AuditEvent)
    if runner_id is not None:
        statement = statement.where(col(AuditEvent.runner_id) == runner_id)
    if image_id is not None:
        booting = select(Run.id).where(col(Run.spec)[("image", "id")].as_string() == image_id)
        registered = and_(
            col(AuditEvent.event) == "image_registered",
            col(AuditEvent.data)["image_id"].as_string() == image_id,
        )
        statement = statement.where(or_(registered, col(AuditEvent.run_id).in_(booting)))
    if profile_id is not None:
        copied = select(Run.id).where(col(Run.profile_id) == profile_id)
        own = and_(
            col(AuditEvent.event).startswith("profile_"),
            col(AuditEvent.data)["profile_id"].as_string() == profile_id,
        )
        statement = statement.where(or_(own, col(AuditEvent.run_id).in_(copied)))
    if event is not None:
        statement = statement.where(col(AuditEvent.event) == event)
    if since is not None:
        statement = statement.where(col(AuditEvent.at) >= since)
    if after is not None:
        statement = statement.where(col(AuditEvent.seq) > after)
    order = col(AuditEvent.seq).desc() if newest_first else col(AuditEvent.seq)
    return session.exec(statement.order_by(order).limit(limit)).all()
