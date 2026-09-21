import asyncio
from collections.abc import Callable
from functools import partial

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, WebSocketException, status
from sqlmodel import Session

from naos_api import consoles
from naos_api.auth import require_operator_connection
from naos_api.db import Database
from naos_api.errors import NotFoundError

POLL_SECONDS = 0.5
FRAME_BYTES = 32 * 1024

router = APIRouter(dependencies=[Depends(require_operator_connection)])


async def _in_session[T](db: Database, call: Callable[[Session], T]) -> T:
    def run() -> T:
        with Session(db.engine) as session:
            return call(session)

    return await asyncio.to_thread(run)


async def _until_disconnect(websocket: WebSocket) -> None:
    while (await websocket.receive())["type"] != "websocket.disconnect":
        pass


@router.websocket("/runs/{run_id}/attach")
async def attach(websocket: WebSocket, run_id: str) -> None:
    db: Database = websocket.app.state.db
    try:
        await _in_session(db, partial(consoles.attached, run_id=run_id))
    except NotFoundError as err:
        raise WebSocketException(status.WS_1008_POLICY_VIOLATION, str(err)) from err
    await websocket.accept()
    listener = asyncio.create_task(_until_disconnect(websocket))
    offset = 0
    try:
        while not listener.done():
            tail = await _in_session(db, partial(consoles.tail, run_id=run_id, after=offset))
            if tail.data:
                for start in range(0, len(tail.data), FRAME_BYTES):
                    await websocket.send_bytes(tail.data[start : start + FRAME_BYTES])
                offset += len(tail.data)
                continue
            if tail.done:
                await websocket.close()
                return
            await asyncio.wait({listener}, timeout=POLL_SECONDS)
    except WebSocketDisconnect:
        return
    finally:
        listener.cancel()
