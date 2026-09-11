from collections.abc import Sequence
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select, update

from naos_api.errors import IdempotencyConflictError, InvalidTransitionError, NotFoundError
from naos_api.lifecycle import RunStatus, ensure_transition
from naos_api.models import Run, utcnow
from naos_api.policies import check_refs
from naos_api.spec import PolicyKind, RunSpec, digest_of

MAX_REASON_LENGTH = 500

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
    refs = spec.policy_refs()
    run = Run(
        id=f"run_{uuid4().hex}",
        spec=document,
        mount_policy_id=refs[PolicyKind.MOUNT],
        network_policy_id=refs[PolicyKind.NETWORK],
        shell_policy_id=refs[PolicyKind.SHELL],
        mcp_policy_id=refs[PolicyKind.MCP],
        idempotency_key=idempotency_key,
        request_digest=request_digest,
    )
    session.add(run)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = _by_key(session, idempotency_key)
        if existing is None:
            raise
        return _replay(existing, request_digest), False
    return run, True


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
    statement = (
        statement.order_by(col(Run.created_at).desc(), col(Run.id).desc())
        .offset(offset)
        .limit(limit)
    )
    return session.exec(statement).all()


def transition_run(
    session: Session,
    run_id: str,
    expected: RunStatus,
    target: RunStatus,
    reason: str | None = None,
) -> Run:
    ensure_transition(expected, target)
    if target is RunStatus.FAILED and not (reason and len(reason) <= MAX_REASON_LENGTH):
        raise ValueError(f"FAILED requires a reason of 1..{MAX_REASON_LENGTH} characters")

    statement = (
        update(Run)
        .where(col(Run.id) == run_id, col(Run.status) == expected)
        .values(status=target, status_reason=reason, updated_at=utcnow())
    )
    result = session.exec(statement)
    session.commit()

    run = get_run(session, run_id)
    if result.rowcount == 1 or run.status is target:
        return run
    raise InvalidTransitionError(f"run {run_id} is {run.status}, expected {expected}")


def stop_run(session: Session, run_id: str) -> Run:
    # The lifecycle is acyclic, so racing a runner can move the Run at most len(RunStatus) times.
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
