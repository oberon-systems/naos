import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

UNITS = {"": 1 << 30, "gib": 1 << 30, "gb": 10**9, "mib": 1 << 20, "mb": 10**6}
SIZE = re.compile(r"^(\d+(?:\.\d+)?)\s*([a-z]*)$")


@dataclass(frozen=True)
class Draft:
    id: str = ""
    version: str = ""
    digest: str = ""
    url: str = ""
    name: str = ""
    size: str = ""
    built: str = ""


def draft(form: dict[str, str]) -> Draft:
    return Draft(**{key: form.get(key, "").strip() for key in Draft.__dataclass_fields__})


# A bare number is GiB, the unit the list and the popup show.
def _bytes(size: str) -> int:
    found = SIZE.match(size.lower())
    if found is None or found[2] not in UNITS:
        raise ValueError(f"size {size} is not a number of GiB, GB, MiB or MB")
    return round(float(found[1]) * UNITS[found[2]])


def _day(built: str) -> int:
    try:
        return int(datetime.strptime(built, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())
    except ValueError:
        raise ValueError(f"build date {built} is not a YYYY-MM-DD date") from None


# The api validates every field again; this only turns what a person types into its body.
def body(entry: Draft) -> dict[str, Any]:
    found: dict[str, Any] = {
        "id": entry.id,
        "version": entry.version,
        "digest": entry.digest,
        "url": entry.url,
    }
    if entry.name:
        found["name"] = entry.name
    if entry.size:
        found["size_bytes"] = _bytes(entry.size)
    if entry.built:
        found["built_at"] = _day(entry.built)
    return found
