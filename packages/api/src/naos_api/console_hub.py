import asyncio
from collections.abc import Iterator
from contextlib import contextmanager, suppress

Outbox = asyncio.Queue[bytes | str]
# Raw console bytes and the offset they start at, as the runner read them.
Output = asyncio.Queue[tuple[int, bytes]]


# The live terminal is fed from here and not from the store: the store keeps the
# text and drops what only redrew the screen, while a terminal needs every byte of
# a redraw, a lone echoed space included. What a viewer types never reaches the
# database either - it is handed to the runner's socket in memory and forgotten.
#
# Every socket registers the loop it runs in, because two of them need not share
# one: a queue belongs to its own loop and is fed through it.
class ConsoleHub:
    def __init__(self) -> None:
        self._viewers: dict[str, set[tuple[asyncio.AbstractEventLoop, Output]]] = {}
        self._runners: dict[str, tuple[asyncio.AbstractEventLoop, Outbox]] = {}

    @contextmanager
    def viewer(self, run_id: str) -> Iterator[Output]:
        entry = (asyncio.get_running_loop(), Output())
        self._viewers.setdefault(run_id, set()).add(entry)
        try:
            yield entry[1]
        finally:
            watching = self._viewers.get(run_id, set())
            watching.discard(entry)
            if not watching:
                self._viewers.pop(run_id, None)

    # One runner holds a run at a time, so a second socket for it replaces the first.
    @contextmanager
    def runner(self, run_id: str) -> Iterator[Outbox]:
        entry = (asyncio.get_running_loop(), Outbox())
        self._runners[run_id] = entry
        try:
            yield entry[1]
        finally:
            if self._runners.get(run_id) is entry:
                self._runners.pop(run_id, None)

    def attached(self, run_id: str) -> bool:
        return run_id in self._runners

    def publish(self, run_id: str, offset: int, data: bytes) -> None:
        for loop, output in list(self._viewers.get(run_id, set())):
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(output.put_nowait, (offset, data))

    def to_runner(self, run_id: str, frame: bytes | str) -> bool:
        entry = self._runners.get(run_id)
        if entry is None:
            return False
        loop, outbox = entry
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(outbox.put_nowait, frame)
        return True
