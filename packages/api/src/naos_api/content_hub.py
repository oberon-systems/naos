import asyncio
import itertools
import json
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass

from naos_api.errors import ContentUnavailableError

Outbox = asyncio.Queue[str]


@dataclass(frozen=True)
class Piece:
    size: int
    data: bytes


@dataclass(frozen=True)
class Absent:
    pass


Answer = Piece | Absent


# The runner holds one socket here and the operator's asks travel down it, so the
# runner listens on no port of its own. Like the console hub it lives in one process.
class ContentHub:
    def __init__(self) -> None:
        self._runners: dict[str, tuple[asyncio.AbstractEventLoop, Outbox]] = {}
        self._waiting: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Future[Answer]]] = {}
        self._ids = itertools.count(1)

    # A second socket of the same runner replaces the first.
    @contextmanager
    def runner(self, runner_id: str) -> Iterator[Outbox]:
        entry = (asyncio.get_running_loop(), Outbox())
        self._runners[runner_id] = entry
        try:
            yield entry[1]
        finally:
            if self._runners.get(runner_id) is entry:
                self._runners.pop(runner_id, None)

    async def ask(self, runner_id: str, frame: dict[str, object], timeout: float) -> Answer:
        entry = self._runners.get(runner_id)
        if entry is None:
            raise ContentUnavailableError(f"runner {runner_id} is not connected")
        loop, outbox = entry
        ask_id = next(self._ids)
        waiting: asyncio.Future[Answer] = asyncio.get_running_loop().create_future()
        self._waiting[ask_id] = (asyncio.get_running_loop(), waiting)
        try:
            loop.call_soon_threadsafe(outbox.put_nowait, _encode(ask_id, frame))
            return await asyncio.wait_for(waiting, timeout)
        except TimeoutError as err:
            raise ContentUnavailableError(f"runner {runner_id} did not answer") from err
        finally:
            self._waiting.pop(ask_id, None)

    def answer(self, ask_id: int, result: Answer | ContentUnavailableError) -> None:
        entry = self._waiting.get(ask_id)
        if entry is None:
            return
        loop, waiting = entry

        def settle() -> None:
            if waiting.done():
                return
            if isinstance(result, ContentUnavailableError):
                waiting.set_exception(result)
            else:
                waiting.set_result(result)

        with suppress(RuntimeError):
            loop.call_soon_threadsafe(settle)


def _encode(ask_id: int, frame: dict[str, object]) -> str:
    return json.dumps({"id": ask_id, **frame})
