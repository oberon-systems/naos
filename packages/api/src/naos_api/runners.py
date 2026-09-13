import hashlib
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Self
from uuid import uuid4

from sqlmodel import Session, col, func, or_, select, update

from naos_api import tasks
from naos_api.errors import InvalidTransitionError, LeaseError, NotFoundError
from naos_api.images.service import check_image
from naos_api.lifecycle import TERMINAL, TaskStatus
from naos_api.models import Lease, Policy, Runner, Task
from naos_api.spec import PolicyKind, RunSpec

S = TaskStatus
LEASE_EXPIRED_REASON = "runner lease expired"
LEASE_BOUND = frozenset({S.STARTING, S.STARTED, S.STOPPING, S.COLLECTING})
RUNNER_TRANSITIONS = frozenset(
    {
        (S.PENDING, S.STARTING),
        (S.STARTING, S.STARTED),
        (S.STARTED, S.STOPPING),
        (S.STOPPING, S.COLLECTING),
        *((status, S.FAILED) for status in LEASE_BOUND),
    }
)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class IssuedToken:
    value: str
    expires_at: int

    @classmethod
    def new(cls, expires_at: int) -> Self:
        return cls(secrets.token_urlsafe(32), expires_at)

    @property
    def digest(self) -> str:
        return hash_token(self.value)


@dataclass(frozen=True)
class RunnerPrincipal:
    runner_id: str
    token_hash: str
    token_expires_at: int
    prev_token_hash: str | None
    via_previous_token: bool


@dataclass(frozen=True)
class Heartbeat:
    lease: Lease
    token: IssuedToken | None


@dataclass(frozen=True)
class DesiredTask:
    task: Task
    image_url: str
    policies: dict[PolicyKind, dict[str, Any] | None]


def _live_lease(session: Session, runner_id: str) -> Lease | None:
    statement = select(Lease).where(
        col(Lease.runner_id) == runner_id, col(Lease.expired_at).is_(None)
    )
    return session.exec(statement).first()


def register_runner(
    session: Session, name: str, now: int, token_ttl: int, lease_ttl: int
) -> tuple[Runner, IssuedToken, Lease]:
    token = IssuedToken.new(now + token_ttl)
    runner = Runner(
        id=f"rnr_{uuid4().hex}",
        name=name,
        token_hash=token.digest,
        token_expires_at=token.expires_at,
        created_at=now,
    )
    session.add(runner)
    session.flush()
    lease = Lease(
        id=f"lease_{uuid4().hex}",
        runner_id=runner.id,
        live_runner_id=runner.id,
        acquired_at=now,
        expires_at=now + lease_ttl,
    )
    session.add(lease)
    session.commit()
    return runner, token, lease


def authenticate(session: Session, token: str, now: int) -> RunnerPrincipal | None:
    digest = hash_token(token)
    statement = select(Runner).where(
        or_(col(Runner.token_hash) == digest, col(Runner.prev_token_hash) == digest)
    )
    runner = session.exec(statement).first()
    if runner is None or runner.revoked_at is not None:
        return None

    via_previous = runner.token_hash != digest
    expires_at = runner.prev_token_expires_at if via_previous else runner.token_expires_at
    if expires_at is None or expires_at <= now:
        return None
    return RunnerPrincipal(
        runner_id=runner.id,
        token_hash=runner.token_hash,
        token_expires_at=runner.token_expires_at,
        prev_token_hash=runner.prev_token_hash,
        via_previous_token=via_previous,
    )


def expire_leases(session: Session, now: int) -> list[str]:
    lapsed = (col(Lease.expired_at).is_(None), col(Lease.expires_at) <= now)
    expired = list(session.exec(select(Lease.id).where(*lapsed)).all())
    if expired:
        session.exec(
            update(Lease)
            .where(col(Lease.id).in_(expired), *lapsed)
            .values(expired_at=now, live_runner_id=None)
        )
        bound = col(Task.lease_id).in_(expired)
        session.exec(
            update(Task)
            .where(bound, col(Task.status) == S.PENDING)
            .values(lease_id=None, updated_at=now)
        )
        session.exec(
            update(Task)
            .where(bound, col(Task.status).in_(LEASE_BOUND))
            .values(status=S.FAILED, status_reason=LEASE_EXPIRED_REASON, updated_at=now)
        )
    session.commit()
    return expired


def _renew_lease(session: Session, runner_id: str, now: int, lease_ttl: int) -> str:
    expires_at = now + lease_ttl
    for _ in range(3):
        lease = _live_lease(session, runner_id)
        if lease is None:
            lease_id = f"lease_{uuid4().hex}"
            session.add(
                Lease(
                    id=lease_id,
                    runner_id=runner_id,
                    live_runner_id=runner_id,
                    acquired_at=now,
                    expires_at=expires_at,
                )
            )
            try:
                session.commit()
            except Exception:
                session.rollback()
                continue
            return lease_id

        lease_id = lease.id
        extended = session.exec(
            update(Lease)
            .where(col(Lease.id) == lease_id, col(Lease.expired_at).is_(None))
            .values(expires_at=expires_at)
        )
        session.commit()
        if extended.rowcount == 1:
            return lease_id
    raise LeaseError(f"runner {runner_id} could not acquire a lease")


def _rotate_token(
    session: Session, principal: RunnerPrincipal, now: int, token_ttl: int
) -> IssuedToken | None:
    if not principal.via_previous_token and principal.token_expires_at - token_ttl // 2 > now:
        return None

    token = IssuedToken.new(now + token_ttl)
    values: dict[str, Any] = {"token_hash": token.digest, "token_expires_at": token.expires_at}
    if not principal.via_previous_token:
        values |= {
            "prev_token_hash": principal.token_hash,
            "prev_token_expires_at": principal.token_expires_at,
        }
    rotated = session.exec(
        update(Runner)
        .where(
            col(Runner.id) == principal.runner_id,
            col(Runner.token_hash) == principal.token_hash,
            col(Runner.prev_token_hash).is_not_distinct_from(principal.prev_token_hash),
        )
        .values(**values)
    )
    session.commit()
    return token if rotated.rowcount == 1 else None


def _candidates(session: Session, limit: int) -> Sequence[str]:
    statement = (
        select(Task.id)
        .where(col(Task.status) == S.PENDING, col(Task.lease_id).is_(None))
        .order_by(col(Task.seq))
        .limit(limit)
    )
    return session.exec(statement).all()


def _assign(session: Session, lease_id: str, capacity: int, now: int) -> None:
    held = session.exec(
        select(func.count())
        .select_from(Task)
        .where(col(Task.lease_id) == lease_id, col(Task.status).not_in(TERMINAL))
    ).one()
    free = capacity - held
    if free <= 0:
        return
    for task_id in _candidates(session, free):
        session.exec(
            update(Task)
            .where(
                col(Task.id) == task_id, col(Task.status) == S.PENDING, col(Task.lease_id).is_(None)
            )
            .values(lease_id=lease_id, updated_at=now)
        )
    session.commit()


def heartbeat(
    session: Session,
    principal: RunnerPrincipal,
    capacity: int,
    now: int,
    lease_ttl: int,
    token_ttl: int,
) -> Heartbeat:
    expire_leases(session, now)
    lease_id = _renew_lease(session, principal.runner_id, now, lease_ttl)
    token = _rotate_token(session, principal, now, token_ttl)
    session.exec(
        update(Runner).where(col(Runner.id) == principal.runner_id).values(last_heartbeat_at=now)
    )
    session.commit()
    _assign(session, lease_id, capacity, now)
    lease = session.get(Lease, lease_id)
    if lease is None:
        raise LeaseError(f"lease {lease_id} disappeared")
    return Heartbeat(lease=lease, token=token)


def _policies(session: Session, task: Task) -> dict[PolicyKind, dict[str, Any] | None]:
    refs = {
        PolicyKind.MOUNT: task.mount_policy_id,
        PolicyKind.NETWORK: task.network_policy_id,
        PolicyKind.SHELL: task.shell_policy_id,
        PolicyKind.MCP: task.mcp_policy_id,
    }
    policies: dict[PolicyKind, dict[str, Any] | None] = {}
    for kind, policy_id in refs.items():
        policy = session.get(Policy, policy_id) if policy_id else None
        policies[kind] = policy.document if policy else None
    return policies


def desired_state(session: Session, runner_id: str, now: int) -> tuple[str, list[DesiredTask]]:
    expire_leases(session, now)
    lease = _live_lease(session, runner_id)
    if lease is None:
        raise LeaseError(f"runner {runner_id} holds no live lease")
    statement = (
        select(Task)
        .where(col(Task.lease_id) == lease.id, col(Task.status).not_in(TERMINAL))
        .order_by(col(Task.seq))
    )
    assigned = session.exec(statement).all()
    return lease.id, [
        DesiredTask(
            task=task,
            image_url=check_image(session, RunSpec.model_validate(task.spec).image).url,
            policies=_policies(session, task),
        )
        for task in assigned
    ]


def transition(
    session: Session,
    runner_id: str,
    task_id: str,
    lease_id: str,
    expected: TaskStatus,
    target: TaskStatus,
    reason: str | None,
    now: int,
) -> Task:
    if (expected, target) not in RUNNER_TRANSITIONS:
        raise InvalidTransitionError(f"a runner may not move a task {expected} -> {target}")
    expire_leases(session, now)

    task = session.get(Task, task_id)
    owner = session.get(Lease, task.lease_id) if task is not None and task.lease_id else None
    if task is None or owner is None or owner.runner_id != runner_id:
        raise NotFoundError(f"task {task_id} does not exist")
    if task.lease_id != lease_id or owner.expired_at is not None:
        raise LeaseError(f"lease {lease_id} does not hold task {task_id}")

    return tasks.transition_task(session, task_id, expected, target, reason, lease_id=lease_id)
