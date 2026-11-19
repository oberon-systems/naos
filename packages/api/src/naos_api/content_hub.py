import asyncio
import json
import secrets
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


@dataclass(frozen=True)
class _Waiting:
    loop: asyncio.AbstractEventLoop
    future: "asyncio.Future[Answer]"
    runner_id: str
    length: int


# The runner holds one socket here and the operator's asks travel down it, so the
# runner listens on no port of its own. Like the console hub it lives in one process.
class ContentHub:
    def __init__(self) -> None:
        self._runners: dict[str, tuple[asyncio.AbstractEventLoop, Outbox]] = {}
        self._waiting: dict[int, _Waiting] = {}

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
        ask_id = secrets.randbits(63)
        while ask_id in self._waiting:
            ask_id = secrets.randbits(63)
        waiting: asyncio.Future[Answer] = asyncio.get_running_loop().create_future()
        length = frame.get("length")
        self._waiting[ask_id] = _Waiting(
            asyncio.get_running_loop(),
            waiting,
            runner_id,
            length if isinstance(length, int) else 0,
        )
        try:
            loop.call_soon_threadsafe(outbox.put_nowait, _encode(ask_id, frame))
            return await asyncio.wait_for(waiting, timeout)
        except TimeoutError as err:
            raise ContentUnavailableError(f"runner {runner_id} did not answer") from err
        finally:
            self._waiting.pop(ask_id, None)

    # Only the runner an ask went to settles it, and with no more than it was asked for.
    def answer(self, runner_id: str, ask_id: int, result: Answer | ContentUnavailableError) -> None:
        entry = self._waiting.get(ask_id)
        if entry is None or entry.runner_id != runner_id:
            return
        if isinstance(result, Piece) and len(result.data) > entry.length:
            result = ContentUnavailableError(f"runner {runner_id} answered more than was asked")
        loop, waiting = entry.loop, entry.future

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
