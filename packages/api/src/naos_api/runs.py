from collections.abc import Sequence
from uuid import uuid4

from sqlmodel import Session, col, func, select, update

from naos_api import audit
from naos_api.audit import Actor
from naos_api.clock import now_ts
from naos_api.errors import IdempotencyConflictError, InvalidTransitionError, NotFoundError
from naos_api.images.service import check_image
from naos_api.lifecycle import RunStatus, ensure_transition
from naos_api.models import Lease, Run
from naos_api.policies import check_refs
from naos_api.spec import PolicyKind, RunSpec, digest_of

MAX_REASON_LENGTH = 500
CREATE_ATTEMPTS = 3

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


def create_run(session: Session, spec: RunSpec, idempotency_key: str) -> tuple[Run, bool]:
    document = spec.model_dump(mode="json")
    request_digest = digest_of(document)
    existing = _by_key(session, idempotency_key)
    if existing is not None:
        return _replay(existing, request_digest), False

    check_refs(session, spec)
    check_image(session, spec.image)
    refs = spec.policy_refs()
    for attempt in range(CREATE_ATTEMPTS):
        run = Run(
            id=f"run_{uuid4().hex}",
            seq=session.exec(select(func.coalesce(func.max(Run.seq), 0))).one() + 1,
            spec=document,
            mount_policy_id=refs[PolicyKind.MOUNT],
            network_policy_id=refs[PolicyKind.NETWORK],
            shell_policy_id=refs[PolicyKind.SHELL],
            mcp_policy_id=refs[PolicyKind.MCP],
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )
        session.add(run)
        audit.record(session, "run_created", actor="operator", run_id=run.id)
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
    session: Session, status: RunStatus | None = None, limit: int = 50, offset: int = 0
) -> Sequence[Run]:
    statement = select(Run)
    if status is not None:
        statement = statement.where(Run.status == status)
    statement = statement.order_by(col(Run.seq).desc()).offset(offset).limit(limit)
    return session.exec(statement).all()


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

    statement = update(Run).where(col(Run.id) == run_id, col(Run.status) == expected)
    if lease_id is not None:
        live = select(Lease.id).where(col(Lease.id) == lease_id, col(Lease.expired_at).is_(None))
        statement = statement.where(col(Run.lease_id) == lease_id, live.exists())
    statement = statement.values(status=target, status_reason=reason, updated_at=now_ts())
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
