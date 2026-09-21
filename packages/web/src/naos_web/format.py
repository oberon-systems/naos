DASH = "\u2014"
MINUTE = 60
HOUR = 3600
DAY = 86400


def _coarse(seconds: int) -> str:
    if seconds < MINUTE:
        return f"{seconds}s"
    if seconds < HOUR:
        return f"{seconds // MINUTE}m"
    if seconds < DAY:
        return f"{seconds // HOUR}h"
    return f"{seconds // DAY}d"


def ago(then: int | None, now: int) -> str:
    if then is None:
        return DASH
    return f"{_coarse(max(now - then, 0))} ago"


# A heartbeat is read in seconds while it is still recent; past that the age is the point.
def heartbeat_ago(then: int | None, now: int) -> str:
    if then is None:
        return "no heartbeat"
    seconds = max(now - then, 0)
    return f"hb {seconds}s ago" if seconds < 10 * MINUTE else f"hb {_coarse(seconds)} ago"


def duration(started_at: int | None, finished_at: int | None, now: int) -> str:
    if started_at is None:
        return DASH
    seconds = max((finished_at if finished_at is not None else now) - started_at, 0)
    return f"{seconds // HOUR:02d}:{seconds % HOUR // MINUTE:02d}:{seconds % MINUTE:02d}"


def lease_left(acquired_at: int | None, expires_at: int | None, now: int) -> str:
    if acquired_at is None or expires_at is None:
        return "lease expired \u00b7 fenced"
    return f"lease {max(expires_at - now, 0)}s of {max(expires_at - acquired_at, 0)}s"


def lease_percent(acquired_at: int | None, expires_at: int | None, now: int) -> int:
    if acquired_at is None or expires_at is None or expires_at <= acquired_at:
        return 0
    left = max(min(expires_at - now, expires_at - acquired_at), 0)
    return round(100 * left / (expires_at - acquired_at))


def slots(capacity: int | None, held: int) -> str:
    return f"{DASH} slots" if capacity is None else f"{held} / {capacity} slots"
