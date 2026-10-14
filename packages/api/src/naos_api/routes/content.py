import asyncio
import json
from collections.abc import Callable, Mapping
from functools import partial
from typing import Annotated, Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, WebSocketException, status
from sqlmodel import Session

from naos_api import merges, runners
from naos_api.auth import require_operator_connection, require_runner_connection
from naos_api.content_hub import Absent, ContentHub, Piece
from naos_api.db import Database
from naos_api.errors import ContentUnavailableError, DomainError
from naos_api.runners import RunnerPrincipal

SIDES = {"host": 0, "agent": 1}
MAX_LENGTH = 256 * 1024
ANSWER_SECONDS = 10.0
# The runner's answer starts with the ask it answers and the side's whole size.
ID_BYTES = 8
SIZE_BYTES = 8
REASON_CHARS = 200

router = APIRouter(dependencies=[Depends(require_operator_connection)])
runner_router = APIRouter()


async def _in_session[T](db: Database, call: Callable[[Session], T]) -> T:
    def run() -> T:
        with Session(db.engine) as session:
            return call(session)

    return await asyncio.to_thread(run)


def _range(text: str | None) -> tuple[str, int, int] | None:
    try:
        wanted = json.loads(text or "")
        side, offset, length = wanted["side"], wanted["offset"], wanted["length"]
    except (TypeError, ValueError, KeyError):
        return None
    if side not in SIDES or not isinstance(offset, int) or not isinstance(length, int):
        return None
    if offset < 0 or not 0 < length <= MAX_LENGTH:
        return None
    return side, offset, length


# One entry's content, read in ranges: a text frame asks for one, a binary frame
# answers it as side, offset and the side's size, then the bytes as the runner read them.
@router.websocket("/runs/{run_id}/merge/entries/{index}/content")
async def content(websocket: WebSocket, run_id: str, index: int) -> None:
    db: Database = websocket.app.state.db
    hub: ContentHub = websocket.app.state.content_hub
    try:
        runner_id = await _in_session(db, partial(merges.viewed, run_id=run_id, index=index))
    except DomainError as err:
        raise WebSocketException(status.WS_1008_POLICY_VIOLATION, str(err)[:120]) from err
    await websocket.accept()
    try:
        while (message := await websocket.receive())["type"] != "websocket.disconnect":
            asked = _range(message.get("text"))
            if asked is None:
                continue
            side, offset, length = asked
            ask = {"run_id": run_id, "index": index, "side": side, "offset": offset}
            try:
                await _in_session(db, partial(runners.serving, runner_id=runner_id))
                answer = await hub.ask(runner_id, {**ask, "length": length}, ANSWER_SECONDS)
            except DomainError as err:
                failed = {"side": side, "offset": offset, "error": str(err)[:REASON_CHARS]}
                await websocket.send_text(json.dumps(failed))
                continue
            if isinstance(answer, Absent):
                absent = {"side": side, "offset": offset, "absent": True}
                await websocket.send_text(json.dumps(absent))
                continue
            head = bytes([SIDES[side]]) + offset.to_bytes(8, "big")
            await websocket.send_bytes(head + answer.size.to_bytes(8, "big") + answer.data)
    except WebSocketDisconnect:
        return


def _answered(hub: ContentHub, message: Mapping[str, Any]) -> None:
    frame = message.get("bytes")
    if isinstance(frame, bytes) and len(frame) >= ID_BYTES + SIZE_BYTES:
        ask_id = int.from_bytes(frame[:ID_BYTES], "big")
        size = int.from_bytes(frame[ID_BYTES : ID_BYTES + SIZE_BYTES], "big")
        hub.answer(ask_id, Piece(size=size, data=frame[ID_BYTES + SIZE_BYTES :]))
        return
    try:
        reply = json.loads(str(message.get("text") or ""))
        ask_id = int(reply["id"])
    except (TypeError, ValueError, KeyError):
        return
    if reply.get("absent") is True:
        hub.answer(ask_id, Absent())
    else:
        hub.answer(ask_id, ContentUnavailableError(str(reply.get("error"))[:REASON_CHARS]))


async def _to_runner(websocket: WebSocket, outbox: "asyncio.Queue[str]") -> None:
    while True:
        await websocket.send_text(await outbox.get())


# The runner dials this once and keeps it: the asks of every operator viewing a
# Run it holds changes for travel down it.
@runner_router.websocket("/runners/{runner_id}/content")
async def runner_content(
    websocket: WebSocket,
    principal: Annotated[RunnerPrincipal, Depends(require_runner_connection)],
) -> None:
    db: Database = websocket.app.state.db
    hub: ContentHub = websocket.app.state.content_hub
    runner_id = principal.runner_id
    try:
        await _in_session(db, partial(runners.serving, runner_id=runner_id))
    except DomainError as err:
        raise WebSocketException(status.WS_1008_POLICY_VIOLATION, str(err)[:120]) from err
    # registered before the accept, so an ask that follows it at once finds the socket
    with hub.runner(runner_id) as outbox:
        await websocket.accept()
        sender = asyncio.create_task(_to_runner(websocket, outbox))
        try:
            while (message := await websocket.receive())["type"] != "websocket.disconnect":
                _answered(hub, message)
        except WebSocketDisconnect:
            return
        finally:
            sender.cancel()
