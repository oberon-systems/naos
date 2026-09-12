import hashlib
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Self
from uuid import uuid4

from sqlmodel import Session, col, func, or_, select, update

from naos_api import runs
from naos_api.errors import InvalidTransitionError, LeaseError, NotFoundError
from naos_api.lifecycle import TERMINAL, ImageStatus, RunStatus
from naos_api.models import Image, Lease, PolicySnapshot, Run, Runner
from naos_api.settings import Settings
from naos_api.spec import PolicyKind, RunSpec

S = RunStatus
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
class DesiredRun:
    run: Run
    policies: dict[PolicyKind, dict[str, Any] | None]


def _live_lease(session: Session, runner_id: str) -> Lease | None:
    statement = select(Lease).where(
        col(Lease.runner_id) == runner_id, col(Lease.expired_at).is_(None)
    )
    return session.exec(statement).first()


def register_runner(
    session: Session, settings: Settings, name: str, now: int
) -> tuple[Runner, IssuedToken, Lease]:
    token = IssuedToken.new(now + settings.runner_token_ttl_seconds)
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
        expires_at=now + settings.lease_ttl_seconds,
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
    release = {"expired_at": now, "live_runner_id": None}
    # MySQL has no UPDATE ... RETURNING, so there the ids are read before the write.
    if session.get_bind().dialect.update_returning:
        expired = list(
            session.exec(update(Lease).where(*lapsed).values(**release).returning(col(Lease.id)))
            .scalars()
            .all()
        )
    else:
        expired = list(session.exec(select(Lease.id).where(*lapsed)).all())
        if expired:
            session.exec(update(Lease).where(col(Lease.id).in_(expired)).values(**release))
    if expired:
        bound = col(Run.lease_id).in_(expired)
        session.exec(
            update(Run)
            .where(bound, col(Run.status) == S.PENDING)
            .values(lease_id=None, updated_at=now)
        )
        session.exec(
            update(Run)
            .where(bound, col(Run.status).in_(LEASE_BOUND))
            .values(status=S.FAILED, status_reason=LEASE_EXPIRED_REASON, updated_at=now)
        )
    session.commit()
    return expired


def _renew_lease(session: Session, runner_id: str, settings: Settings, now: int) -> str:
    expires_at = now + settings.lease_ttl_seconds
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
    session: Session, principal: RunnerPrincipal, settings: Settings, now: int
) -> IssuedToken | None:
    ttl = settings.runner_token_ttl_seconds
    if not principal.via_previous_token and principal.token_expires_at - ttl // 2 > now:
        return None

    token = IssuedToken.new(now + ttl)
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
        select(Run.id)
        .where(col(Run.status) == S.PENDING, col(Run.lease_id).is_(None))
        .order_by(col(Run.seq))
        .limit(limit)
    )
    return session.exec(statement).all()


def _assign(session: Session, lease_id: str, capacity: int, now: int) -> None:
    held = session.exec(
        select(func.count())
        .select_from(Run)
        .where(col(Run.lease_id) == lease_id, col(Run.status).not_in(TERMINAL))
    ).one()
    free = capacity - held
    if free <= 0:
        return
    for run_id in _candidates(session, free):
        session.exec(
            update(Run)
            .where(col(Run.id) == run_id, col(Run.status) == S.PENDING, col(Run.lease_id).is_(None))
            .values(lease_id=lease_id, updated_at=now)
        )
    session.commit()


def heartbeat(
    session: Session,
    settings: Settings,
    principal: RunnerPrincipal,
    capacity: int,
    now: int,
) -> Heartbeat:
    expire_leases(session, now)
    lease_id = _renew_lease(session, principal.runner_id, settings, now)
    token = _rotate_token(session, principal, settings, now)
    session.exec(
        update(Runner).where(col(Runner.id) == principal.runner_id).values(last_heartbeat_at=now)
    )
    session.commit()
    _assign(session, lease_id, capacity, now)
    lease = session.get(Lease, lease_id)
    if lease is None:
        raise LeaseError(f"lease {lease_id} disappeared")
    return Heartbeat(lease=lease, token=token)


def _policies(session: Session, run: Run) -> dict[PolicyKind, dict[str, Any] | None]:
    refs = {
        PolicyKind.MOUNT: run.mount_policy_id,
        PolicyKind.NETWORK: run.network_policy_id,
        PolicyKind.SHELL: run.shell_policy_id,
        PolicyKind.MCP: run.mcp_policy_id,
    }
    policies: dict[PolicyKind, dict[str, Any] | None] = {}
    for kind, snapshot_id in refs.items():
        snapshot = session.get(PolicySnapshot, snapshot_id) if snapshot_id else None
        policies[kind] = snapshot.document if snapshot else None
    return policies


def desired_state(session: Session, runner_id: str, now: int) -> tuple[str, list[DesiredRun]]:
    expire_leases(session, now)
    lease = _live_lease(session, runner_id)
    if lease is None:
        raise LeaseError(f"runner {runner_id} holds no live lease")
    statement = (
        select(Run)
        .where(col(Run.lease_id) == lease.id, col(Run.status).not_in(TERMINAL))
        .order_by(col(Run.seq))
    )
    assigned = session.exec(statement).all()
    return lease.id, [DesiredRun(run=run, policies=_policies(session, run)) for run in assigned]


def image_for_runner(session: Session, runner_id: str, digest: str, now: int) -> Image:
    expire_leases(session, now)
    lease = _live_lease(session, runner_id)
    ready = col(Image.status) == ImageStatus.READY
    image = session.exec(select(Image).where(col(Image.digest) == digest, ready)).first()
    if lease is not None and image is not None:
        held = select(Run).where(col(Run.lease_id) == lease.id, col(Run.status).not_in(TERMINAL))
        if any(
            RunSpec.model_validate(run.spec).image.digest == digest
            for run in session.exec(held).all()
        ):
            return image
    raise NotFoundError(f"image {digest} does not exist")


def transition(
    session: Session,
    runner_id: str,
    run_id: str,
    lease_id: str,
    expected: RunStatus,
    target: RunStatus,
    reason: str | None,
    now: int,
) -> Run:
    if (expected, target) not in RUNNER_TRANSITIONS:
        raise InvalidTransitionError(f"a runner may not move a run {expected} -> {target}")
    expire_leases(session, now)

    run = session.get(Run, run_id)
    owner = session.get(Lease, run.lease_id) if run is not None and run.lease_id else None
    if run is None or owner is None or owner.runner_id != runner_id:
        raise NotFoundError(f"run {run_id} does not exist")
    if run.lease_id != lease_id or owner.expired_at is not None:
        raise LeaseError(f"lease {lease_id} does not hold run {run_id}")

    return runs.transition_run(session, run_id, expected, target, reason, lease_id=lease_id)
