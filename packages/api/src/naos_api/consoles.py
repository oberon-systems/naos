import re
from dataclasses import dataclass

from sqlmodel import Session, col, func, select

from naos_api import audit
from naos_api.errors import ConsoleFullError, LeaseError, SizeError
from naos_api.lifecycle import RunStatus
from naos_api.models import ConsoleChunk, ConsoleSize, Lease, Run
from naos_api.runs import get_run

LIVE = frozenset({RunStatus.PENDING, RunStatus.STARTING, RunStatus.STARTED, RunStatus.STOPPING})
READ_CHUNKS = 256


@dataclass(frozen=True)
class Tail:
    data: bytes
    end: int
    done: bool


# An OSC string, a CSI sequence, whatever other escape is left, then the control
# bytes a terminal reads and a reader cannot.
ESCAPES = re.compile(
    rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    rb"|\x1b\[[0-?]*[ -/]*[@-~]"
    rb"|\x1b."
    rb"|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
)


# What a capture says, without the screen it drew: a tmux pane redraws itself
# once a second while nothing happens, and none of that belongs in a log.
def clean(data: bytes) -> bytes:
    text = ESCAPES.sub(b"", data.replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
    lines = [line.rstrip() for line in text.split(b"\n")]
    return b"\n".join(line for line in lines if line)


def held(session: Session, run_id: str) -> int:
    end = session.exec(
        select(func.max(ConsoleChunk.end)).where(col(ConsoleChunk.run_id) == run_id)
    ).one()
    return end or 0


def hold(session: Session, runner_id: str, run_id: str) -> None:
    run = session.get(Run, run_id)
    lease = session.get(Lease, run.lease_id) if run is not None and run.lease_id else None
    if lease is None or lease.runner_id != runner_id:
        raise LeaseError(f"run {run_id} is not held by runner {runner_id}")


# Answers where the new bytes start and the bytes themselves, raw: the store may
# drop them, but a live terminal still needs every one.
def append(
    session: Session, runner_id: str, run_id: str, offset: int, data: bytes, limit: int, now: int
) -> tuple[int, bytes]:
    hold(session, runner_id, run_id)
    start = held(session, run_id)
    fresh = data[start - offset :] if offset <= start else b""
    if not fresh:
        return start, b""
    if start >= limit:
        raise ConsoleFullError(f"the console log of run {run_id} is full")
    fresh = fresh[: limit - start]
    # A chunk that drew the screen and said nothing is recorded as read and dropped,
    # so the runner moves on and the log does not carry it.
    kept = fresh if clean(fresh) else b""
    session.add(
        ConsoleChunk(run_id=run_id, offset=start, end=start + len(fresh), data=kept, at=now)
    )
    session.commit()
    return start, fresh


def _chunks(session: Session, run_id: str, after: int, chunks: int | None) -> list[ConsoleChunk]:
    return list(
        session.exec(
            select(ConsoleChunk)
            .where(col(ConsoleChunk.run_id) == run_id, col(ConsoleChunk.end) > after)
            .order_by(col(ConsoleChunk.offset))
            .limit(chunks)
        ).all()
    )


def _joined(rows: list[ConsoleChunk], after: int) -> bytes:
    return b"".join(row.data[max(after - row.offset, 0) :] for row in rows)


def read(session: Session, run_id: str, after: int = 0, chunks: int | None = READ_CHUNKS) -> bytes:
    get_run(session, run_id)
    return _joined(_chunks(session, run_id, after, chunks), after)


# The end travels with the tail: a dropped chunk carries no bytes, so a viewer
# counting what it received would read it again forever.
def tail(session: Session, run_id: str, after: int) -> Tail:
    rows = _chunks(session, run_id, after, READ_CHUNKS)
    status = get_run(session, run_id).status
    return Tail(
        data=_joined(rows, after),
        end=rows[-1].end if rows else after,
        done=not rows and status not in LIVE,
    )


# The serial console carries no resize signal, so the viewer's grid travels
# to the runner in the reply to the next console report.
COLS = range(20, 501)
ROWS = range(5, 201)
# The guest has one size, so one viewer holds it. A detached window outranks a
# panel, and the claim ages out in case a socket dies without saying goodbye.
CLAIM_SECONDS = 15
RANK = {"panel": 1, "window": 2}


def resize(
    session: Session, run_id: str, cols: int, rows: int, owner: str, view: str, now: int
) -> ConsoleSize:
    get_run(session, run_id)
    if cols not in COLS or rows not in ROWS:
        raise SizeError(f"a console of {cols}x{rows} is out of range")
    if view not in RANK:
        raise SizeError(f"{view} is not a kind of viewer")
    size = session.get(ConsoleSize, run_id)
    if size is None:
        size = ConsoleSize(run_id=run_id, cols=cols, rows=rows, owner=owner, view=view, at=now)
    elif (
        size.owner == owner
        or not size.owner
        or now - size.at > CLAIM_SECONDS
        or RANK[view] > RANK.get(size.view, 0)
    ):
        size.cols, size.rows, size.owner, size.view, size.at = cols, rows, owner, view, now
    else:
        return size
    session.add(size)
    session.commit()
    session.refresh(size)
    return size


def release(session: Session, run_id: str, owner: str) -> None:
    size = session.get(ConsoleSize, run_id)
    if size is None or size.owner != owner:
        return
    size.owner, size.at = "", 0
    session.add(size)
    session.commit()


def size(session: Session, run_id: str) -> ConsoleSize | None:
    return session.get(ConsoleSize, run_id)


def attached(session: Session, run_id: str) -> None:
    get_run(session, run_id)
    audit.record(session, "console_attached", actor="operator", run_id=run_id)
    session.commit()


# That the keyboard was taken, and from which viewer. What was typed is not
# recorded anywhere: it is an operator's own, and often a secret.
def took_keyboard(session: Session, run_id: str, view: str) -> None:
    get_run(session, run_id)
    audit.record(session, "console_typing", actor="operator", run_id=run_id, view=view)
    session.commit()
