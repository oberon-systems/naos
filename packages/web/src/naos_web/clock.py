import time
from typing import Annotated

from fastapi import Depends


def now_ts() -> int:
    """Seconds since the Unix epoch, UTC. Every age and duration on a page reads this."""
    return int(time.time())


def get_now() -> int:
    return now_ts()


NowDep = Annotated[int, Depends(get_now)]
