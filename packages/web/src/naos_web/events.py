from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from naos_web import format
from naos_web.client import Row

Kind = Literal["all", "api", "runner", "errors"]
Check = Callable[[Any], bool]
Detail = Callable[[Row], str]


def _text(value: Any) -> bool:
    return isinstance(value, str)


def _maybe_text(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _flag(value: Any) -> bool:
    return isinstance(value, bool)


def _names(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(name, str) for name in value)


def _one_of(*choices: str) -> Check:
    return lambda value: value in choices


@dataclass(frozen=True)
class Schema:
    fields: dict[str, Check]
    detail: Detail
    error: Callable[[Row], bool] = lambda data: False
    optional: frozenset[str] = frozenset()


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _entries(count: int) -> str:
    return "1 entry" if count == 1 else f"{count} entries"


def _joined(*parts: str | None) -> str:
    return " · ".join(part for part in parts if part)


def _vm(row: Row) -> str:
    return str(row.get("vm_id") or "")


def _none(row: Row) -> str:
    return ""


def _data(detail: Callable[[Row], str]) -> Detail:
    return lambda row: detail(row["data"])


def _reason(data: Row) -> str:
    return str(data["reason"])


def _created(data: Row) -> str:
    profile = f"profile {data['profile']}" if data.get("profile") else None
    return _joined(data.get("workspace") or format.DASH, profile)


def _transition(data: Row) -> str:
    return _joined(f"{data['from']} \u2192 {data['to']}", data["reason"])


def _credentials(data: Row) -> str:
    ttl = f"ttl {format.coarse(data['ttl'])}" if "ttl" in data else None
    return _joined(_plural(len(data["names"]), "name"), ttl)


def _diff(data: Row) -> str:
    return _joined(
        _entries(data["entries"]),
        f"{data['rejected']} rejected",
        f"{data['sensitive']} sensitive",
        f"merge {data['policy']}",
        "decided" if data["decided"] else None,
    )


def _merged(data: Row) -> str:
    counts = [f"{data[name]} {name.replace('_', ' ')}" for name in MERGE_COUNTS if name in data]
    return _joined(data["outcome"], *counts, _plural(data["conflicts"], "conflict"))


def _decided(data: Row) -> str:
    return _joined(_plural(data["paths"], "path"), _plural(data["resolutions"], "resolution"))


def _applied(data: Row) -> str:
    return _joined(
        f"{data['applied']} applied",
        f"{data['backed_up']} backed up",
        f"{data['exported']} exported",
    )


def _typing(data: Row) -> str:
    return f"keyboard taken by the {data['view']}"


def _network(data: Row) -> str:
    return _joined(f"{data['protocol']} {data['host']}", f"rule {data['rule']}", data.get("reason"))


def _shell(data: Row) -> str:
    return _joined(f"{data['capability']} {data['path']}", data.get("reason"))


def _mcp_call(data: Row) -> str:
    return _joined(
        f"{data['server']}/{data['tool']} {data['resource']}".rstrip(),
        data["decision"],
        f"{data['duration_ms']}ms",
    )


MERGE_COUNTS = ("applied", "skipped", "exported", "backed_up")
NETWORK: dict[str, Check] = {"protocol": _text, "host": _text, "rule": _text}
SHELL: dict[str, Check] = {"capability": _text, "path": _text}

# The run-scoped events of both writers, keyed as the API stores them: a runner's
# run_id and vm_id are lifted into columns. Optional fields are the ones a row may lack.
API: dict[str, Schema] = {
    "run_created": Schema(
        {"workspace": _maybe_text, "profile": _maybe_text},
        _data(_created),
        optional=frozenset({"workspace", "profile"}),
    ),
    "run_queued": Schema({"position": _count}, _data(lambda d: f"position {d['position']}")),
    "run_assigned": Schema(
        {"lease_id": _text, "slot": _count, "slots": _count},
        _data(lambda d: f"{d['lease_id']} · slot {d['slot']} of {d['slots']}"),
    ),
    "run_transition": Schema(
        {"from": _text, "to": _text, "reason": _maybe_text},
        _data(_transition),
        lambda d: d["to"] == "FAILED",
    ),
    "run_stop_requested": Schema({"status": _text}, _data(lambda d: f"while {d['status']}")),
    "waiting_rebound": Schema({"lease_id": _text}, _data(lambda d: str(d["lease_id"]))),
    "credentials_issued": Schema(
        {"names": _names, "ttl": _count}, _data(_credentials), optional=frozenset({"ttl"})
    ),
    "diff_reported": Schema(
        {
            "entries": _count,
            "rejected": _count,
            "sensitive": _count,
            "policy": _text,
            "decided": _flag,
        },
        _data(_diff),
    ),
    "merge_decided": Schema(
        {"paths": _count, "resolutions": _count},
        _data(_decided),
    ),
    "merge_reported": Schema(
        {
            "outcome": _one_of("applied", "conflict"),
            "applied": _count,
            "skipped": _count,
            "exported": _count,
            "backed_up": _count,
            "conflicts": _count,
        },
        _data(_merged),
        lambda d: d["outcome"] == "conflict",
        frozenset(MERGE_COUNTS),
    ),
    "console_attached": Schema({}, _none),
    "console_typing": Schema({"view": _text}, _data(_typing)),
}

RUNNER: dict[str, Schema] = {
    "run_claimed": Schema({}, _none),
    "run_failed": Schema({"reason": _text}, _data(_reason), lambda d: True),
    "orphan_destroyed": Schema({}, _vm),
    "vm_created": Schema({}, _vm),
    "vm_stopped": Schema({}, _vm),
    "vm_destroyed": Schema({}, _vm),
    "changes_archived": Schema({}, _vm),
    "console_attached": Schema({}, _vm),
    "workspace_shared": Schema({"mode": _one_of("ro", "rw")}, _data(lambda d: f"mode {d['mode']}")),
    "workspace_collected": Schema(
        {"entries": _count, "rejected": _count},
        _data(lambda d: _joined(_entries(d["entries"]), f"{d['rejected']} rejected")),
    ),
    "merge_conflict": Schema(
        {"conflicts": _count}, _data(lambda d: _plural(d["conflicts"], "conflict")), lambda d: True
    ),
    "merge_applied": Schema(
        {"applied": _count, "backed_up": _count, "exported": _count},
        _data(_applied),
    ),
    "network_policy_configured": Schema({}, _none),
    "network_allowed": Schema(NETWORK, _data(_network)),
    "network_denied": Schema(NETWORK | {"reason": _text}, _data(_network), lambda d: True),
    "shell_policy_configured": Schema({}, _none),
    "shell_allowed": Schema(SHELL, _data(_shell)),
    "shell_denied": Schema(SHELL | {"reason": _text}, _data(_shell), lambda d: True),
    "mcp_attached": Schema({}, _none),
    "mcp_rejected": Schema({"reason": _text}, _data(_reason), lambda d: True),
    "mcp_policy_configured": Schema({}, _none),
    "mcp_credentials_updated": Schema({"names": _text}, _data(lambda d: str(d["names"]))),
    "mcp_call": Schema(
        {
            "server": _text,
            "tool": _text,
            "resource": _text,
            "decision": _one_of("allow", "deny"),
            "duration_ms": _count,
            "category": _text,
        },
        _data(_mcp_call),
        lambda d: d["decision"] == "deny",
    ),
}

SCHEMAS = {"api": API, "runner": RUNNER}
ACTORS = frozenset({"operator", "runner", "system"})


@dataclass(frozen=True)
class LogRow:
    time: str
    kind: str
    event: str
    actor: str
    detail: str
    error: bool
    refused: bool


def clock(at: int) -> str:
    return datetime.fromtimestamp(at, UTC).strftime("%H:%M:%S")


def _actor(row: Row, runners: dict[str, str]) -> str:
    if row["actor"] != "runner":
        return str(row["actor"])
    runner_id = row["runner_id"]
    return f"runner:{runners.get(runner_id) or runner_id or 'unknown'}"


def _refusal(row: Row, schema: Schema | None) -> str | None:
    if schema is None:
        return "unknown event"
    if row["actor"] not in ACTORS:
        return "unknown actor"
    data = row["data"] if isinstance(row["data"], dict) else {}
    extra = sorted(set(data) - set(schema.fields))
    if extra:
        return f"unexpected field {extra[0]}"
    for name, check in schema.fields.items():
        if name not in data:
            if name in schema.optional:
                continue
            return f"missing field {name}"
        if not check(data[name]):
            return f"field {name} has the wrong type"
    return None


# Whatever the renderer cannot vouch for is shown as refused, never as the text it carried.
def log_row(row: Row, runners: dict[str, str]) -> LogRow:
    source = str(row["source"])
    schema = SCHEMAS.get(source, {}).get(row["event"])
    refusal = _refusal(row, schema)
    if schema is None or refusal is not None:
        return LogRow(
            time=clock(row["at"]),
            kind=source if source in SCHEMAS else "unknown",
            event=row["event"] if schema is not None else "unknown event",
            actor=_actor(row, runners) if row["actor"] in ACTORS else "unknown",
            detail=f"refused: {refusal}",
            error=True,
            refused=True,
        )
    return LogRow(
        time=clock(row["at"]),
        kind=source,
        event=row["event"],
        actor=_actor(row, runners),
        detail=schema.detail(row),
        error=schema.error(row["data"]),
        refused=False,
    )


def log_rows(events: list[Row], runners: dict[str, str], kind: Kind) -> list[LogRow]:
    rows = [log_row(event, runners) for event in events]
    if kind == "errors":
        return [row for row in rows if row.error]
    if kind in SCHEMAS:
        return [row for row in rows if row.kind == kind]
    return rows
