import asyncio
import json
import time
from collections.abc import Callable
from contextlib import suppress
from functools import partial
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, WebSocketException, status
from sqlmodel import Session

from naos_api import consoles
from naos_api.auth import require_operator_connection, require_runner_connection
from naos_api.console_hub import ConsoleHub
from naos_api.db import Database
from naos_api.errors import DomainError, NotFoundError
from naos_api.models import ConsoleSize
from naos_api.routes.deps import ConsoleLimitDep
from naos_api.runners import RunnerPrincipal

POLL_SECONDS = 0.5
FRAME_BYTES = 32 * 1024
# Every frame the runner sends begins with the offset its bytes start at.
OFFSET_BYTES = 8
# How long a viewer waits for the store to catch up with a frame ahead of it.
GAP_TRIES = 10
GAP_WAIT = 0.05

router = APIRouter(dependencies=[Depends(require_operator_connection)])
# The runner carries its own credentials, so its socket has its own router.
runner_router = APIRouter()


async def _in_session[T](db: Database, call: Callable[[Session], T]) -> T:
    def run() -> T:
        with Session(db.engine) as session:
            return call(session)

    return await asyncio.to_thread(run)


async def _send(websocket: WebSocket, data: bytes) -> None:
    for start in range(0, len(data), FRAME_BYTES):
        await websocket.send_bytes(data[start : start + FRAME_BYTES])


def _size_frame(size: ConsoleSize) -> str:
    return json.dumps({"cols": size.cols, "rows": size.rows})


# The keys of whoever drives, handed to the runner in memory. They are never
# stored and never audited: an operator types secrets, and the log already holds
# whatever the guest echoed back.
async def _typed(
    db: Database, hub: ConsoleHub, run_id: str, owner: str, keys: bytes, driving: bool
) -> bool:
    size = await _in_session(db, partial(consoles.size, run_id=run_id))
    if size is None or size.owner != owner or not hub.attached(run_id):
        return driving
    # the taking is recorded before the first key travels, so a socket that closes
    # straight after still leaves the audit entry behind
    if not driving:
        await _in_session(db, partial(consoles.took_keyboard, run_id=run_id, view=size.view))
    hub.to_runner(run_id, keys)
    return True


async def _until_disconnect(
    websocket: WebSocket, db: Database, hub: ConsoleHub, run_id: str, owner: str
) -> None:
    driving = False
    while (message := await websocket.receive())["type"] != "websocket.disconnect":
        if (keys := message.get("bytes")) is not None:
            driving = await _typed(db, hub, run_id, owner, keys, driving)
            continue
        # the only thing a viewer says is how big its grid is
        try:
            wanted = json.loads(message.get("text") or "")
            cols, rows = int(wanted["cols"]), int(wanted["rows"])
            view = str(wanted["view"])
        except (TypeError, ValueError, KeyError):
            continue
        try:
            size = await _in_session(
                db,
                partial(
                    consoles.resize,
                    run_id=run_id,
                    cols=cols,
                    rows=rows,
                    owner=owner,
                    view=view,
                    now=int(time.time()),
                ),
            )
        except DomainError:
            continue
        # only the guest's own grid travels; a viewer that lost the claim resizes nothing
        if size.owner == owner:
            hub.to_runner(run_id, _size_frame(size))
        await websocket.send_text(
            json.dumps({"driving": size.owner == owner, "cols": size.cols, "rows": size.rows})
        )


@router.websocket("/runs/{run_id}/attach")
async def attach(websocket: WebSocket, run_id: str) -> None:
    db: Database = websocket.app.state.db
    hub: ConsoleHub = websocket.app.state.console_hub
    try:
        await _in_session(db, partial(consoles.attached, run_id=run_id))
    except NotFoundError as err:
        raise WebSocketException(status.WS_1008_POLICY_VIOLATION, str(err)) from err
    await websocket.accept()
    owner = uuid4().hex
    listener = asyncio.create_task(_until_disconnect(websocket, db, hub, run_id, owner))
    offset = 0
    try:
        # registered before the history is read, so nothing that arrives meanwhile is lost
        with hub.viewer(run_id) as live:
            while not listener.done():
                tail = await _in_session(db, partial(consoles.tail, run_id=run_id, after=offset))
                if tail.end > offset:
                    await _send(websocket, tail.data)
                    offset = tail.end
                    continue
                if tail.done:
                    await websocket.close()
                    return
                break
            # From here the terminal gets the raw bytes as the runner read them; the
            # store only covers what reached another worker, or the run's end.
            while not listener.done():
                try:
                    start, data = await asyncio.wait_for(live.get(), timeout=POLL_SECONDS)
                except TimeoutError:
                    tail = await _in_session(
                        db, partial(consoles.tail, run_id=run_id, after=offset)
                    )
                    if tail.end > offset:
                        await _send(websocket, tail.data)
                        offset = tail.end
                    elif tail.done:
                        await websocket.close()
                        return
                    continue
                # the frames before this one may still be on their way into the store
                for _ in range(GAP_TRIES):
                    if start <= offset:
                        break
                    tail = await _in_session(
                        db, partial(consoles.tail, run_id=run_id, after=offset)
                    )
                    if tail.end > offset:
                        await _send(websocket, tail.data)
                        offset = tail.end
                    else:
                        await asyncio.sleep(GAP_WAIT)
                if start + len(data) > offset:
                    await _send(websocket, data[max(offset - start, 0) :])
                    offset = start + len(data)
    except WebSocketDisconnect:
        return
    finally:
        listener.cancel()
        # the next viewer takes the size over at once, not after the claim ages
        await _in_session(db, partial(consoles.release, run_id=run_id, owner=owner))


# The store is written in order and apart from the socket, so a commit never
# stands between a key and its echo.
async def _keep(
    db: Database,
    runner_id: str,
    run_id: str,
    limit: int,
    store: "asyncio.Queue[tuple[int, bytes] | None]",
) -> None:
    while (item := await store.get()) is not None:
        offset, data = item
        await _in_session(
            db,
            partial(
                consoles.append,
                runner_id=runner_id,
                run_id=run_id,
                offset=offset,
                data=data,
                limit=limit,
                now=int(time.time()),
            ),
        )


async def _to_runner(websocket: WebSocket, outbox: "asyncio.Queue[bytes | str]") -> None:
    while True:
        frame = await outbox.get()
        if isinstance(frame, str):
            await websocket.send_text(frame)
        else:
            await websocket.send_bytes(frame)


# The live console: output arrives as the runner reads it and the keys of whoever
# drives go back the same way. The console report stays the fallback for a runner
# whose socket is down, and both carry offsets, so neither loses a byte.
@runner_router.websocket("/runners/{runner_id}/runs/{run_id}/console")
async def runner_console(
    websocket: WebSocket,
    run_id: str,
    principal: Annotated[RunnerPrincipal, Depends(require_runner_connection)],
    limit: ConsoleLimitDep,
) -> None:
    db: Database = websocket.app.state.db
    hub: ConsoleHub = websocket.app.state.console_hub
    # checked before a byte is let through: from here on the output reaches the
    # terminals ahead of the store, and so ahead of its own lease check
    try:
        await _in_session(db, partial(consoles.hold, runner_id=principal.runner_id, run_id=run_id))
    except DomainError as err:
        raise WebSocketException(status.WS_1008_POLICY_VIOLATION, str(err)[:120]) from err
    await websocket.accept()
    store: asyncio.Queue[tuple[int, bytes] | None] = asyncio.Queue()
    with hub.runner(run_id) as outbox:
        sender = asyncio.create_task(_to_runner(websocket, outbox))
        keeper = asyncio.create_task(_keep(db, principal.runner_id, run_id, limit, store))
        try:
            if (size := await _in_session(db, partial(consoles.size, run_id=run_id))) is not None:
                await websocket.send_text(_size_frame(size))
            while True:
                # a store that refuses ends the socket at once, not on the next frame
                receiving = asyncio.create_task(websocket.receive())
                await asyncio.wait({receiving, keeper, sender}, return_when=asyncio.FIRST_COMPLETED)
                if not receiving.done():
                    receiving.cancel()
                    break
                message = receiving.result()
                if message["type"] == "websocket.disconnect":
                    return
                frame = message.get("bytes")
                if frame is None or len(frame) < OFFSET_BYTES:
                    continue
                offset, data = int.from_bytes(frame[:OFFSET_BYTES], "big"), frame[OFFSET_BYTES:]
                # an echo reaches the terminal and the runner moves on before any commit;
                # viewers cut what they already have by offset
                hub.publish(run_id, offset, data)
                store.put_nowait((offset, data))
                await websocket.send_text(json.dumps({"offset": offset + len(data)}))
            if keeper.done() and isinstance(refused := keeper.exception(), DomainError):
                await websocket.close(status.WS_1008_POLICY_VIOLATION, str(refused)[:120])
        except WebSocketDisconnect:
            return
        finally:
            sender.cancel()
            # what already arrived still reaches the store
            store.put_nowait(None)
            with suppress(Exception):
                await keeper
