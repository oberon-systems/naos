import time
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, FastAPI


def now_ts() -> int:
    """Seconds since the Unix epoch, UTC. The only time source in the API."""
    return int(time.time())


def get_now() -> int:
    return now_ts()


NowDep = Annotated[int, Depends(get_now)]


# A socket outlives the request that opened it, so it reads the time the way a route would.
def clock_of(app: FastAPI) -> Callable[[], int]:
    clock: Callable[[], int] = app.dependency_overrides.get(get_now, get_now)
    return clock
