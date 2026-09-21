from dataclasses import dataclass

from sqlmodel import Session, col, func, select

from naos_api import audit
from naos_api.errors import ConsoleFullError, LeaseError
from naos_api.lifecycle import RunStatus
from naos_api.models import ConsoleChunk, Lease, Run
from naos_api.runs import get_run

LIVE = frozenset({RunStatus.PENDING, RunStatus.STARTING, RunStatus.STARTED, RunStatus.STOPPING})
READ_CHUNKS = 256


@dataclass(frozen=True)
class Tail:
    data: bytes
    done: bool


def held(session: Session, run_id: str) -> int:
    end = session.exec(
        select(func.max(ConsoleChunk.end)).where(col(ConsoleChunk.run_id) == run_id)
    ).one()
    return end or 0


def append(
    session: Session, runner_id: str, run_id: str, offset: int, data: bytes, limit: int, now: int
) -> int:
    run = session.get(Run, run_id)
    lease = session.get(Lease, run.lease_id) if run is not None and run.lease_id else None
    if lease is None or lease.runner_id != runner_id:
        raise LeaseError(f"run {run_id} is not held by runner {runner_id}")
    start = held(session, run_id)
    fresh = data[start - offset :] if offset <= start else b""
    if not fresh:
        return start
    if start >= limit:
        raise ConsoleFullError(f"the console log of run {run_id} is full")
    fresh = fresh[: limit - start]
    session.add(
        ConsoleChunk(run_id=run_id, offset=start, end=start + len(fresh), data=fresh, at=now)
    )
    session.commit()
    return start + len(fresh)


def read(session: Session, run_id: str, after: int = 0, chunks: int | None = READ_CHUNKS) -> bytes:
    get_run(session, run_id)
    rows = session.exec(
        select(ConsoleChunk)
        .where(col(ConsoleChunk.run_id) == run_id, col(ConsoleChunk.end) > after)
        .order_by(col(ConsoleChunk.offset))
        .limit(chunks)
    ).all()
    return b"".join(row.data[max(after - row.offset, 0) :] for row in rows)


def tail(session: Session, run_id: str, after: int) -> Tail:
    data = read(session, run_id, after)
    status = get_run(session, run_id).status
    return Tail(data=data, done=not data and status not in LIVE)


def attached(session: Session, run_id: str) -> None:
    get_run(session, run_id)
    audit.record(session, "console_attached", actor="operator", run_id=run_id)
    session.commit()
