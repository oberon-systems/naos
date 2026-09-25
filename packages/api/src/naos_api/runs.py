from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from sqlmodel import Session, col, func, or_, select, update

from naos_api import audit
from naos_api.audit import Actor
from naos_api.clock import now_ts
from naos_api.errors import (
    IdempotencyConflictError,
    InvalidTransitionError,
    NotFoundError,
    PolicyError,
)
from naos_api.images.service import booted_image, check_image
from naos_api.lifecycle import ACTIVE, TERMINAL, RunStatus, ensure_transition
from naos_api.models import Lease, Merge, Policy, Profile, Run, Runner
from naos_api.policies import check_refs
from naos_api.spec import PolicyKind, RunSpec, digest_of

MAX_REASON_LENGTH = 500
CREATE_ATTEMPTS = 3
DAY = 86400

RunState = str
# The states an operator filters by, disjoint by construction: a run waiting for a
# merge decision is no longer running, even though ACTIVE still counts its slot.
STATES: dict[RunState, frozenset[RunStatus]] = {
    "active": frozenset(ACTIVE) - {RunStatus.WAITING_MERGE},
    "queued": frozenset({RunStatus.PENDING}),
    "waiting_merge": frozenset({RunStatus.WAITING_MERGE}),
    "failed": frozenset({RunStatus.FAILED}),
}

_STOP_TARGETS = {
    RunStatus.PENDING: RunStatus.CANCELLED,
    RunStatus.STARTING: RunStatus.STOPPING,
    RunStatus.STARTED: RunStatus.STOPPING,
}


def _by_key(session: Session, idempotency_key: str) -> Run | None:
    return session.exec(select(Run).where(Run.idempotency_key == idempotency_key)).first()


def _replay(run: Run, request_digest: str) -> Run:
    if run.request_digest != request_digest:
        raise IdempotencyConflictError("Idempotency-Key was already used with a different spec")
    return run


def _check_runner(session: Session, runner_id: str | None) -> None:
    if runner_id is None:
        return
    runner = session.get(Runner, runner_id)
    if runner is None or runner.revoked_at is not None:
        raise PolicyError(f"runner {runner_id} does not exist or is revoked")
    if runner.drained_at is not None:
        raise PolicyError(f"runner {runner_id} is draining")


def _workspace(session: Session, policy_id: str | None) -> str | None:
    policy = session.get(Policy, policy_id) if policy_id else None
    return PurePosixPath(policy.document["workdir"]).name if policy else None


def _free_slots(session: Session, pinned: str | None, now: int) -> int:
    statement = (
        select(Lease.id, Runner.capacity)
        .join(Runner, col(Runner.id) == col(Lease.runner_id))
        .where(
            col(Lease.expired_at).is_(None),
            col(Lease.expires_at) > now,
            col(Runner.revoked_at).is_(None),
            col(Runner.drained_at).is_(None),
        )
    )
    if pinned is not None:
        statement = statement.where(col(Lease.runner_id) == pinned)
    free = 0
    for lease_id, capacity in session.exec(statement).all():
        held = session.exec(
            select(func.count())
            .select_from(Run)
            .where(col(Run.lease_id) == lease_id, col(Run.status).not_in(TERMINAL))
        ).one()
        free += max((capacity or 0) - held, 0)
    return free


def _waiting(session: Session, pinned: str | None) -> int:
    statement = (
        select(func.count())
        .select_from(Run)
        .where(col(Run.status) == RunStatus.PENDING, col(Run.lease_id).is_(None))
    )
    if pinned is not None:
        statement = statement.where(or_(col(Run.runner_id).is_(None), col(Run.runner_id) == pinned))
    return int(session.exec(statement).one())


def create_run(
    session: Session,
    spec: RunSpec,
    idempotency_key: str,
    profile_id: str | None = None,
    now: int | None = None,
) -> tuple[Run, bool]:
    document = spec.model_dump(mode="json")
    request_digest = digest_of({"spec": document, "profile_id": profile_id})
    existing = _by_key(session, idempotency_key)
    if existing is not None:
        return _replay(existing, request_digest), False

    check_refs(session, spec)
    check_image(session, spec.image)
    _check_runner(session, spec.runner)
    refs = spec.policy_refs()
    profile = session.get(Profile, profile_id) if profile_id else None
    created = {
        "workspace": _workspace(session, refs[PolicyKind.MOUNT]),
        "profile": profile.name if profile else None,
    }
    for attempt in range(CREATE_ATTEMPTS):
        run = Run(
            id=f"run_{uuid4().hex}",
            seq=session.exec(select(func.coalesce(func.max(Run.seq), 0))).one() + 1,
            spec=document,
            mount_policy_id=refs[PolicyKind.MOUNT],
            network_policy_id=refs[PolicyKind.NETWORK],
            shell_policy_id=refs[PolicyKind.SHELL],
            mcp_policy_id=refs[PolicyKind.MCP],
            profile_id=profile_id,
            runner_id=spec.runner,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )
        position = _waiting(session, spec.runner) + 1
        queued = _free_slots(session, spec.runner, now or now_ts()) < position
        session.add(run)
        audit.record(session, "run_created", actor="operator", run_id=run.id, **created)
        if queued:
            audit.record(session, "run_queued", actor="operator", run_id=run.id, position=position)
        try:
            session.commit()
        except Exception:
            session.rollback()
            existing = _by_key(session, idempotency_key)
            if existing is not None:
                return _replay(existing, request_digest), False
            if attempt == CREATE_ATTEMPTS - 1:
                raise
            continue
        return run, True
    raise AssertionError("unreachable")


def get_run(session: Session, run_id: str) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise NotFoundError(f"run {run_id} does not exist")
    return run


def list_runs(
    session: Session,
    status: RunStatus | None = None,
    state: RunState | None = None,
    limit: int = 50,
    offset: int = 0,
    runner: str | None = None,
    image: str | None = None,
) -> Sequence[Run]:
    statement = select(Run)
    if status is not None:
        statement = statement.where(Run.status == status)
    if state is not None:
        statement = statement.where(col(Run.status).in_(STATES[state]))
    if runner is not None:
        leases = select(Lease.id).where(col(Lease.runner_id) == runner)
        statement = statement.where(col(Run.lease_id).in_(leases))
    if image is not None:
        statement = statement.where(booted_image() == image)
    statement = statement.order_by(col(Run.seq).desc()).offset(offset).limit(limit)
    return session.exec(statement).all()


@dataclass(frozen=True)
class RunView:
    run: Run
    runner: Runner | None
    workspace: str | None
    changed: int | None
    conflicts: int | None


def _runners_of(session: Session, runs: Sequence[Run]) -> dict[str, Runner]:
    leases = {run.lease_id for run in runs if run.lease_id}
    if not leases:
        return {}
    pairs = session.exec(
        select(Lease.id, Runner)
        .join(Runner, col(Runner.id) == col(Lease.runner_id))
        .where(col(Lease.id).in_(leases))
    ).all()
    return {lease_id: runner for lease_id, runner in pairs}


def _workspaces_of(session: Session, runs: Sequence[Run]) -> dict[str, str]:
    policies = {run.mount_policy_id for run in runs if run.mount_policy_id}
    if not policies:
        return {}
    rows = session.exec(
        select(Policy.id, Policy.document).where(col(Policy.id).in_(policies))
    ).all()
    return {policy_id: PurePosixPath(document["workdir"]).name for policy_id, document in rows}


def _entries(entries: Sequence[dict[str, Any]]) -> int:
    return sum(1 for entry in entries if entry["change"] != "rejected")


def _merges_of(session: Session, runs: Sequence[Run]) -> dict[str, tuple[int, int]]:
    ids = [run.id for run in runs]
    if not ids:
        return {}
    rows = session.exec(
        select(Merge.run_id, Merge.entries, Merge.conflicts).where(col(Merge.run_id).in_(ids))
    ).all()
    return {
        run_id: (_entries(entries), len(conflicts or [])) for run_id, entries, conflicts in rows
    }


def view_runs(session: Session, runs: Sequence[Run]) -> list[RunView]:
    """Decorate a page of runs with what the operator sees beside them.

    Three queries for the whole page, so a longer list costs no more round trips.
    """
    runners = _runners_of(session, runs)
    workspaces = _workspaces_of(session, runs)
    merges = _merges_of(session, runs)
    views: list[RunView] = []
    for run in runs:
        changed, conflicts = merges.get(run.id, (None, None))
        views.append(
            RunView(
                run=run,
                runner=runners.get(run.lease_id) if run.lease_id else None,
                workspace=workspaces.get(run.mount_policy_id) if run.mount_policy_id else None,
                changed=changed,
                conflicts=conflicts,
            )
        )
    return views


@dataclass(frozen=True)
class RunSummary:
    counts: dict[RunStatus, int]
    open: int
    oldest_pending_at: int | None
    failed_24h: int
    last_failure_reason: str | None


def summary(session: Session) -> RunSummary:
    """The fleet at a glance. Reads the row clock, the one that wrote finished_at."""
    now = now_ts()
    counts = {
        RunStatus(status): int(count)
        for status, count in session.exec(
            select(Run.status, func.count()).group_by(col(Run.status))
        ).all()
    }
    oldest = session.exec(
        select(func.min(Run.created_at)).where(col(Run.status) == RunStatus.PENDING)
    ).one()
    since = now - DAY
    failed = (col(Run.status) == RunStatus.FAILED, col(Run.finished_at) >= since)
    failed_24h = int(session.exec(select(func.count()).select_from(Run).where(*failed)).one())
    reason = session.exec(
        select(Run.status_reason).where(*failed).order_by(col(Run.finished_at).desc()).limit(1)
    ).first()
    return RunSummary(
        counts=counts,
        open=sum(count for status, count in counts.items() if status not in TERMINAL),
        oldest_pending_at=oldest,
        failed_24h=failed_24h,
        last_failure_reason=reason,
    )


def stamps(target: RunStatus, now: int) -> dict[str, int]:
    """When a run started and when it stopped, so a duration is never the queue time."""
    if target is RunStatus.STARTING:
        return {"started_at": now}
    return {"finished_at": now} if target in TERMINAL else {}


def transition_run(
    session: Session,
    run_id: str,
    expected: RunStatus,
    target: RunStatus,
    reason: str | None = None,
    *,
    lease_id: str | None = None,
    actor: Actor = "operator",
    runner_id: str | None = None,
) -> Run:
    ensure_transition(expected, target)
    if target is RunStatus.FAILED and not (reason and len(reason) <= MAX_REASON_LENGTH):
        raise ValueError(f"FAILED requires a reason of 1..{MAX_REASON_LENGTH} characters")

    now = now_ts()
    statement = update(Run).where(col(Run.id) == run_id, col(Run.status) == expected)
    if lease_id is not None:
        live = select(Lease.id).where(col(Lease.id) == lease_id, col(Lease.expired_at).is_(None))
        statement = statement.where(col(Run.lease_id) == lease_id, live.exists())
    statement = statement.values(
        status=target, status_reason=reason, updated_at=now, **stamps(target, now)
    )
    result = session.exec(statement)
    if result.rowcount == 1:
        audit.transitioned(
            session, run_id, expected, target, reason, actor=actor, runner_id=runner_id
        )
    session.commit()

    run = get_run(session, run_id)
    if result.rowcount == 1 or run.status is target:
        return run
    raise InvalidTransitionError(f"run {run_id} is {run.status}, expected {expected}")


def stop_run(session: Session, run_id: str) -> Run:
    status = get_run(session, run_id).status
    audit.record(session, "run_stop_requested", actor="operator", run_id=run_id, status=str(status))
    session.commit()
    # The lifecycle is acyclic, so racing a runner can move the run at most len(RunStatus) times.
    for _ in RunStatus:
        run = get_run(session, run_id)
        current = run.status
        target = _STOP_TARGETS.get(current)
        if target is None:
            return run
        try:
            return transition_run(session, run_id, current, target)
        except InvalidTransitionError:
            continue
    raise InvalidTransitionError(f"run {run_id} kept changing while being stopped")
