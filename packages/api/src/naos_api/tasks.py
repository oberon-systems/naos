from collections.abc import Sequence
from uuid import uuid4

from sqlmodel import Session, col, func, select, update

from naos_api import audit
from naos_api.audit import Actor
from naos_api.clock import now_ts
from naos_api.errors import IdempotencyConflictError, InvalidTransitionError, NotFoundError
from naos_api.images.service import check_image
from naos_api.lifecycle import TaskStatus, ensure_transition
from naos_api.models import Lease, Task
from naos_api.policies import check_refs
from naos_api.spec import PolicyKind, RunSpec, digest_of

MAX_REASON_LENGTH = 500
CREATE_ATTEMPTS = 3

_STOP_TARGETS = {
    TaskStatus.PENDING: TaskStatus.CANCELLED,
    TaskStatus.STARTING: TaskStatus.STOPPING,
    TaskStatus.STARTED: TaskStatus.STOPPING,
}


def _by_key(session: Session, idempotency_key: str) -> Task | None:
    return session.exec(select(Task).where(Task.idempotency_key == idempotency_key)).first()


def _replay(task: Task, request_digest: str) -> Task:
    if task.request_digest != request_digest:
        raise IdempotencyConflictError("Idempotency-Key was already used with a different spec")
    return task


def create_task(session: Session, spec: RunSpec, idempotency_key: str) -> tuple[Task, bool]:
    document = spec.model_dump(mode="json")
    request_digest = digest_of(document)
    existing = _by_key(session, idempotency_key)
    if existing is not None:
        return _replay(existing, request_digest), False

    check_refs(session, spec)
    check_image(session, spec.image)
    refs = spec.policy_refs()
    for attempt in range(CREATE_ATTEMPTS):
        task = Task(
            id=f"task_{uuid4().hex}",
            seq=session.exec(select(func.coalesce(func.max(Task.seq), 0))).one() + 1,
            spec=document,
            mount_policy_id=refs[PolicyKind.MOUNT],
            network_policy_id=refs[PolicyKind.NETWORK],
            shell_policy_id=refs[PolicyKind.SHELL],
            mcp_policy_id=refs[PolicyKind.MCP],
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )
        session.add(task)
        audit.record(session, "task_created", actor="operator", run_id=task.id)
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
        return task, True
    raise AssertionError("unreachable")


def get_task(session: Session, task_id: str) -> Task:
    task = session.get(Task, task_id)
    if task is None:
        raise NotFoundError(f"task {task_id} does not exist")
    return task


def list_tasks(
    session: Session, status: TaskStatus | None = None, limit: int = 50, offset: int = 0
) -> Sequence[Task]:
    statement = select(Task)
    if status is not None:
        statement = statement.where(Task.status == status)
    statement = statement.order_by(col(Task.seq).desc()).offset(offset).limit(limit)
    return session.exec(statement).all()


def transition_task(
    session: Session,
    task_id: str,
    expected: TaskStatus,
    target: TaskStatus,
    reason: str | None = None,
    *,
    lease_id: str | None = None,
    actor: Actor = "operator",
    runner_id: str | None = None,
) -> Task:
    ensure_transition(expected, target)
    if target is TaskStatus.FAILED and not (reason and len(reason) <= MAX_REASON_LENGTH):
        raise ValueError(f"FAILED requires a reason of 1..{MAX_REASON_LENGTH} characters")

    statement = update(Task).where(col(Task.id) == task_id, col(Task.status) == expected)
    if lease_id is not None:
        live = select(Lease.id).where(col(Lease.id) == lease_id, col(Lease.expired_at).is_(None))
        statement = statement.where(col(Task.lease_id) == lease_id, live.exists())
    statement = statement.values(status=target, status_reason=reason, updated_at=now_ts())
    result = session.exec(statement)
    if result.rowcount == 1:
        audit.transitioned(
            session, task_id, expected, target, reason, actor=actor, runner_id=runner_id
        )
    session.commit()

    task = get_task(session, task_id)
    if result.rowcount == 1 or task.status is target:
        return task
    raise InvalidTransitionError(f"task {task_id} is {task.status}, expected {expected}")


def stop_task(session: Session, task_id: str) -> Task:
    status = get_task(session, task_id).status
    audit.record(
        session, "task_stop_requested", actor="operator", run_id=task_id, status=str(status)
    )
    session.commit()
    # The lifecycle is acyclic, so racing a runner can move the task at most len(TaskStatus) times.
    for _ in TaskStatus:
        task = get_task(session, task_id)
        current = task.status
        target = _STOP_TARGETS.get(current)
        if target is None:
            return task
        try:
            return transition_task(session, task_id, current, target)
        except InvalidTransitionError:
            continue
    raise InvalidTransitionError(f"task {task_id} kept changing while being stopped")
