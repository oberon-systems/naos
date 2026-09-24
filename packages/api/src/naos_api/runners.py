import hashlib
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Self
from uuid import uuid4

from sqlmodel import Session, col, func, or_, select, update

from naos_api import audit, runs
from naos_api.errors import InvalidTransitionError, LeaseError, NotFoundError
from naos_api.images.service import check_image
from naos_api.lifecycle import TERMINAL, RunStatus
from naos_api.models import Lease, Merge, Policy, Run, Runner
from naos_api.secrets import IssuedCredential, issue_credentials
from naos_api.spec import PolicyKind, RunSpec

S = RunStatus
LEASE_EXPIRED_REASON = "runner lease expired"
REVOKED_REASON = "runner revoked"
LEASE_BOUND = frozenset({S.STARTING, S.STARTED, S.STOPPING, S.COLLECTING})
CREDENTIAL_BOUND = frozenset({S.PENDING, S.STARTING, S.STARTED})
RUNNER_TRANSITIONS = frozenset(
    {
        (S.PENDING, S.STARTING),
        (S.STARTING, S.STARTED),
        (S.STARTED, S.STOPPING),
        (S.STOPPING, S.COLLECTING),
        (S.WAITING_MERGE, S.FAILED),
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
class Placement:
    host: str | None
    zone: str | None
    platform: str
    version: str
    labels: list[str]


def _placed(placement: Placement | None, address: str | None) -> dict[str, Any]:
    values: dict[str, Any] = {"address": address}
    if placement is not None:
        values |= {
            "host": placement.host,
            "zone": placement.zone,
            "platform": placement.platform,
            "version": placement.version,
            "labels": placement.labels,
        }
    return values


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
    image_url: str
    policies: dict[PolicyKind, dict[str, Any] | None]
    credentials: dict[str, IssuedCredential]
    merge: dict[str, Any] | None


def _live_lease(session: Session, runner_id: str) -> Lease | None:
    statement = select(Lease).where(
        col(Lease.runner_id) == runner_id, col(Lease.expired_at).is_(None)
    )
    return session.exec(statement).first()


@dataclass(frozen=True)
class HeldRun:
    id: str
    seq: int
    status: RunStatus


@dataclass(frozen=True)
class RunnerState:
    runner: Runner
    lease: Lease | None
    runs: list[HeldRun]
    lapsed: Lease | None = None


def _holding(session: Session, lease_id: str) -> list[HeldRun]:
    statement = (
        select(Run.id, Run.seq, Run.status)
        .where(col(Run.lease_id) == lease_id, col(Run.status).not_in(TERMINAL))
        .order_by(col(Run.seq))
    )
    return [
        HeldRun(id=run_id, seq=seq, status=S(status))
        for run_id, seq, status in session.exec(statement).all()
    ]


def list_runners(session: Session, now: int, limit: int, offset: int) -> list[RunnerState]:
    statement = (
        select(Runner)
        .order_by(col(Runner.created_at).desc(), col(Runner.id))
        .offset(offset)
        .limit(limit)
    )
    return [runner_state(session, runner, now) for runner in session.exec(statement).all()]


def runner_state(session: Session, runner: Runner, now: int) -> RunnerState:
    lease = _live_lease(session, runner.id)
    # A lease is only marked expired by the sweep, so its deadline decides here.
    if lease is not None and lease.expires_at <= now:
        lease = None
    held = _holding(session, lease.id) if lease is not None else []
    lapsed = None if lease is not None else _latest_lease(session, runner.id)
    return RunnerState(runner=runner, lease=lease, runs=held, lapsed=lapsed)


def _latest_lease(session: Session, runner_id: str) -> Lease | None:
    statement = (
        select(Lease)
        .where(col(Lease.runner_id) == runner_id)
        .order_by(col(Lease.acquired_at).desc(), col(Lease.id).desc())
    )
    return session.exec(statement).first()


def register_runner(
    session: Session,
    name: str,
    now: int,
    token_ttl: int,
    lease_ttl: int,
    placement: Placement | None = None,
    address: str | None = None,
) -> tuple[Runner, IssuedToken, Lease]:
    token = IssuedToken.new(now + token_ttl)
    runner = Runner(
        id=f"rnr_{uuid4().hex}",
        name=name,
        token_hash=token.digest,
        token_expires_at=token.expires_at,
        created_at=now,
        **_placed(placement, address),
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
    audit.record(session, "runner_registered", actor="runner", runner_id=runner.id)
    audit.record(session, "lease_acquired", actor="runner", runner_id=runner.id, lease_id=lease.id)
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


def _fail_bound(
    session: Session,
    bound: Any,
    statuses: frozenset[RunStatus],
    runner_id: str,
    now: int,
    reason: str,
    actor: audit.Actor,
) -> None:
    statement = select(Run.id, Run.status).where(bound, col(Run.status).in_(statuses))
    for run_id, status in session.exec(statement).all():
        failed = session.exec(
            update(Run)
            .where(col(Run.id) == run_id, col(Run.status) == status, bound)
            .values(
                status=S.FAILED,
                status_reason=reason,
                updated_at=now,
                **runs.stamps(S.FAILED, now),
            )
        )
        if failed.rowcount == 1:
            audit.transitioned(
                session, run_id, S(status), S.FAILED, reason, actor=actor, runner_id=runner_id
            )


def _lapse(
    session: Session,
    lease_id: str,
    runner_id: str,
    now: int,
    reason: str,
    actor: audit.Actor,
    *where: Any,
) -> bool:
    claimed = session.exec(
        update(Lease)
        .where(col(Lease.id) == lease_id, col(Lease.expired_at).is_(None), *where)
        .values(expired_at=now, live_runner_id=None)
    )
    if claimed.rowcount != 1:
        return False
    audit.record(session, "lease_expired", actor=actor, runner_id=runner_id, lease_id=lease_id)
    session.exec(
        update(Run)
        .where(col(Run.lease_id) == lease_id, col(Run.status) == S.PENDING)
        .values(lease_id=None, updated_at=now)
    )
    bound = col(Run.lease_id) == lease_id
    _fail_bound(session, bound, LEASE_BOUND, runner_id, now, reason, actor)
    return True


def expire_leases(session: Session, now: int) -> list[str]:
    lapsed = (col(Lease.expired_at).is_(None), col(Lease.expires_at) <= now)
    expired: list[str] = []
    for lease_id, runner_id in session.exec(select(Lease.id, Lease.runner_id).where(*lapsed)).all():
        due = col(Lease.expires_at) <= now
        if _lapse(session, lease_id, runner_id, now, LEASE_EXPIRED_REASON, "system", due):
            expired.append(lease_id)
    session.commit()
    return expired


def _known(session: Session, runner_id: str) -> Runner:
    runner = session.get(Runner, runner_id)
    if runner is None:
        raise NotFoundError(f"runner {runner_id} does not exist")
    return runner


# A Run waiting for its merge outlives the lease, but its changes sit on a host
# that can never take a lease again, so it fails with the rest.
def revoke_runner(session: Session, runner_id: str, now: int) -> Runner:
    runner = _known(session, runner_id)
    revoked = session.exec(
        update(Runner)
        .where(col(Runner.id) == runner_id, col(Runner.revoked_at).is_(None))
        .values(revoked_at=now)
    )
    if revoked.rowcount == 1:
        audit.record(session, "runner_revoked", actor="operator", runner_id=runner_id)
        lease = _live_lease(session, runner_id)
        if lease is not None:
            _lapse(session, lease.id, runner_id, now, REVOKED_REASON, "operator")
        leases = select(Lease.id).where(col(Lease.runner_id) == runner_id)
        waiting = frozenset({S.WAITING_MERGE})
        bound = col(Run.lease_id).in_(leases)
        _fail_bound(session, bound, waiting, runner_id, now, REVOKED_REASON, "operator")
    session.commit()
    session.refresh(runner)
    return runner


def drain_runner(session: Session, runner_id: str, now: int) -> Runner:
    runner = _known(session, runner_id)
    if runner.revoked_at is not None:
        raise InvalidTransitionError(f"runner {runner_id} is revoked")
    drained = session.exec(
        update(Runner)
        .where(
            col(Runner.id) == runner_id,
            col(Runner.revoked_at).is_(None),
            col(Runner.drained_at).is_(None),
        )
        .values(drained_at=now)
    )
    if drained.rowcount == 1:
        audit.record(session, "runner_drained", actor="operator", runner_id=runner_id)
    session.commit()
    session.refresh(runner)
    return runner


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
            audit.record(
                session,
                "lease_acquired",
                actor="runner",
                runner_id=runner_id,
                lease_id=lease_id,
            )
            try:
                session.commit()
            except Exception:
                session.rollback()
                continue
            _rebind_waiting(session, runner_id, lease_id, now)
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


def _rebind_waiting(session: Session, runner_id: str, lease_id: str, now: int) -> None:
    # A Run waiting for its merge outlives the lease that ran it; its changes stay with the runner.
    lapsed = select(Lease.id).where(
        col(Lease.runner_id) == runner_id, col(Lease.expired_at).is_not(None)
    )
    waiting = (col(Run.status) == S.WAITING_MERGE, col(Run.lease_id).in_(lapsed))
    for run_id in session.exec(select(Run.id).where(*waiting)).all():
        rebound = session.exec(
            update(Run)
            .where(col(Run.id) == run_id, *waiting)
            .values(lease_id=lease_id, updated_at=now)
        )
        if rebound.rowcount == 1:
            audit.record(
                session,
                "waiting_rebound",
                actor="system",
                run_id=run_id,
                runner_id=runner_id,
                lease_id=lease_id,
            )
    session.commit()


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
    if rotated.rowcount == 1:
        audit.record(session, "token_rotated", actor="runner", runner_id=principal.runner_id)
    session.commit()
    return token if rotated.rowcount == 1 else None


def _candidates(session: Session, runner_id: str, limit: int) -> Sequence[str]:
    pinned = or_(col(Run.runner_id).is_(None), col(Run.runner_id) == runner_id)
    statement = (
        select(Run.id)
        .where(col(Run.status) == S.PENDING, col(Run.lease_id).is_(None), pinned)
        .order_by(col(Run.seq))
        .limit(limit)
    )
    return session.exec(statement).all()


def _held(session: Session, lease_id: str) -> int:
    count = session.exec(
        select(func.count())
        .select_from(Run)
        .where(col(Run.lease_id) == lease_id, col(Run.status).not_in(TERMINAL))
    ).one()
    return int(count)


def _assign(session: Session, runner_id: str, lease_id: str, capacity: int, now: int) -> None:
    runner = session.get(Runner, runner_id)
    if runner is None or runner.revoked_at is not None or runner.drained_at is not None:
        return
    held = _held(session, lease_id)
    if capacity - held <= 0:
        return
    for run_id in _candidates(session, runner_id, capacity - held):
        assigned = session.exec(
            update(Run)
            .where(col(Run.id) == run_id, col(Run.status) == S.PENDING, col(Run.lease_id).is_(None))
            .values(lease_id=lease_id, updated_at=now)
        )
        if assigned.rowcount == 1:
            held += 1
            audit.record(
                session,
                "run_assigned",
                actor="system",
                run_id=run_id,
                runner_id=runner_id,
                lease_id=lease_id,
                slot=held,
                slots=capacity,
            )
    session.commit()


def heartbeat(
    session: Session,
    principal: RunnerPrincipal,
    capacity: int,
    now: int,
    lease_ttl: int,
    token_ttl: int,
    interval: int | None = None,
    placement: Placement | None = None,
    address: str | None = None,
) -> Heartbeat:
    expire_leases(session, now)
    lease_id = _renew_lease(session, principal.runner_id, now, lease_ttl)
    token = _rotate_token(session, principal, now, token_ttl)
    reported = _placed(placement, address)
    if interval is not None:
        reported["heartbeat_seconds"] = interval
    session.exec(
        update(Runner)
        .where(col(Runner.id) == principal.runner_id)
        .values(last_heartbeat_at=now, capacity=capacity, **reported)
    )
    session.commit()
    _assign(session, principal.runner_id, lease_id, capacity, now)
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
    for kind, policy_id in refs.items():
        policy = session.get(Policy, policy_id) if policy_id else None
        policies[kind] = policy.document if policy else None
    return policies


def _credentials(
    session: Session,
    runner_id: str,
    run: Run,
    mcp: dict[str, Any] | None,
    now: int,
    ttl: int,
) -> dict[str, IssuedCredential]:
    if mcp is None or run.status not in CREDENTIAL_BOUND:
        return {}
    names = {server["credential"] for server in mcp["servers"] if server["credential"]}
    issued = issue_credentials(session, names, now, ttl)
    if issued:
        audit.record(
            session,
            "credentials_issued",
            actor="runner",
            run_id=run.id,
            runner_id=runner_id,
            names=sorted(issued),
            ttl=ttl,
        )
    return issued


def _decision(session: Session, run: Run) -> dict[str, Any] | None:
    if run.status is not S.WAITING_MERGE:
        return None
    merge = session.get(Merge, run.id)
    return merge.decision if merge else None


def desired_state(
    session: Session, runner_id: str, now: int, credential_ttl: int
) -> tuple[str, list[DesiredRun]]:
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
    desired: list[DesiredRun] = []
    for run in assigned:
        policies = _policies(session, run)
        desired.append(
            DesiredRun(
                run=run,
                image_url=check_image(session, RunSpec.model_validate(run.spec).image).url,
                policies=policies,
                credentials=_credentials(
                    session, runner_id, run, policies[PolicyKind.MCP], now, credential_ttl
                ),
                merge=_decision(session, run),
            )
        )
    session.commit()
    return lease.id, desired


def owned_run(session: Session, runner_id: str, run_id: str, lease_id: str, now: int) -> Run:
    expire_leases(session, now)
    run = session.get(Run, run_id)
    owner = session.get(Lease, run.lease_id) if run is not None and run.lease_id else None
    if run is None or owner is None or owner.runner_id != runner_id:
        raise NotFoundError(f"run {run_id} does not exist")
    if run.lease_id != lease_id or owner.expired_at is not None:
        raise LeaseError(f"lease {lease_id} does not hold run {run_id}")
    return run


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
    owned_run(session, runner_id, run_id, lease_id, now)
    return runs.transition_run(
        session,
        run_id,
        expected,
        target,
        reason,
        lease_id=lease_id,
        actor="runner",
        runner_id=runner_id,
    )
