from datetime import datetime
from typing import Annotated

from fastapi import Depends

from naos_api.models import utcnow


def get_now() -> datetime:
    return utcnow()


NowDep = Annotated[datetime, Depends(get_now)]
