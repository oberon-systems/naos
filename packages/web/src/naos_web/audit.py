from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from naos_web import events, format
from naos_web.client import Row
from naos_web.pages import SearchFilter, Summary, TileValue, Tone

Category = Literal["all", "lifecycle", "gates", "merge", "runner"]
Kind = tuple[str, Category]

AUDIT_FILTERS: tuple[SearchFilter, ...] = (
    SearchFilter("all", "All", "/audit"),
    SearchFilter("lifecycle", "Lifecycle", "/audit"),
    SearchFilter("gates", "Gates", "/audit"),
    SearchFilter("merge", "Merge", "/audit"),
    SearchFilter("runner", "Runner", "/audit"),
)
AUDIT_COLUMNS = ("TIME", "EVENT", "ACTOR", "SOURCE", "RUN", "RUNNER", "DATA")
AUDIT_NOTE = (
    "No field takes free text: tokens, secret values and gate payloads never reach the trail, "
    "and a runner event with an unknown name or an extra field is refused whole."
)
DENIAL_NOTE = (
    "Every denial is the gate refusing a call the policy never allowed \u2014 the Run keeps going."
)
PAGE_SIZE = 100
EXPORT_SIZE = 1000

LIFECYCLE: Kind = ("lifecycle", "lifecycle")
VM: Kind = ("vm lifecycle", "lifecycle")
DECISION: Kind = ("gate decision", "gates")
GATE: Kind = ("gate setup", "gates")
MERGE: Kind = ("merge", "merge")
HEALTH: Kind = ("runner health", "runner")
CREDENTIALS: Kind = ("credentials", "runner")
CATALOG: Kind = ("catalog", "all")
CONSOLE: Kind = ("console", "all")

KINDS: dict[str, Kind] = {
    "run_created": LIFECYCLE,
    "run_queued": LIFECYCLE,
    "run_assigned": LIFECYCLE,
    "run_transition": LIFECYCLE,
    "run_stop_requested": LIFECYCLE,
    "run_claimed": LIFECYCLE,
    "run_failed": LIFECYCLE,
    "waiting_rebound": LIFECYCLE,
    "vm_created": VM,
    "vm_stopped": VM,
    "vm_destroyed": VM,
    "orphan_destroyed": VM,
    "changes_archived": VM,
    "workspace_shared": VM,
    "network_allowed": DECISION,
    "network_denied": DECISION,
    "shell_allowed": DECISION,
    "shell_denied": DECISION,
    "mcp_call": DECISION,
    "network_policy_configured": GATE,
    "shell_policy_configured": GATE,
    "mcp_policy_configured": GATE,
    "mcp_attached": GATE,
    "mcp_rejected": GATE,
    "workspace_collected": MERGE,
    "diff_reported": MERGE,
    "merge_decided": MERGE,
    "merge_reported": MERGE,
    "merge_conflict": MERGE,
    "merge_applied": MERGE,
    "runner_registered": HEALTH,
    "runner_revoked": HEALTH,
    "runner_drained": HEALTH,
    "runner_credentials_dropped": HEALTH,
    "lease_acquired": HEALTH,
    "lease_expired": HEALTH,
    "lease_fenced": HEALTH,
    "token_rotated": HEALTH,
    "audit_dropped": HEALTH,
    "runner_events_refused": HEALTH,
    "image_cached": HEALTH,
    "image_rejected": HEALTH,
    "credentials_issued": CREDENTIALS,
    "mcp_credentials_updated": CREDENTIALS,
    "image_registered": CATALOG,
    "policy_created": CATALOG,
    "secret_created": CATALOG,
    "profile_created": CATALOG,
    "profile_updated": CATALOG,
    "profile_deleted": CATALOG,
    "console_attached": CONSOLE,
    "console_typing": CONSOLE,
}
GATES = {"network_": "network", "shell_": "shell", "mcp_call": "mcp"}


def params(category: Category, query: str) -> Row | None:
    """The API filter for a filter and a search, or None when nothing can match."""
    found: Row = {}
    names = [name for name, (_, kind) in KINDS.items() if kind == category != "all"]
    query = query.strip()
    if query.startswith("run_"):
        found["run_id"] = query
    elif query.startswith("rnr_"):
        found["runner_id"] = query
    elif query:
        if names and query not in names:
            return None
        names = [query]
    if names:
        found["event"] = names
    return found


def _number(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _sources(summary: Row) -> str:
    return " and ".join(summary["sources"])


def tiles(summary: Row) -> Summary:
    denials: dict[str, int] = summary["denials"]
    lag = summary["spool_lag"]
    last = summary["last_seq"]
    parts = [_number(summary["total"]) + (" event" if summary["total"] == 1 else " events")]
    parts += [_sources(summary)] if summary["sources"] else []
    parts += [f"up to seq {_number(last)}"] if last is not None else []
    return Summary(
        subtitle=" \u00b7 ".join(parts),
        values={
            "events_24h": TileValue(_number(summary["events_24h"]), _sources(summary)),
            "gate_denials": TileValue(
                _number(sum(denials.values())),
                " \u00b7 ".join(f"{gate} {count}" for gate, count in denials.items() if count),
            ),
            "refused": TileValue(_number(summary["refused_24h"]), "runner events rejected"),
            "spool_lag": TileValue(
                format.DASH if lag is None else format.coarse(lag),
                "no runner event yet" if lag is None else "behind the runner log",
            ),
        },
    )


@dataclass(frozen=True)
class AuditRow:
    id: str
    time: str
    age: str
    event: str
    kind: str
    actor: str
    source: str
    run_id: str | None
    run: str
    run_note: str
    runner: str
    runner_note: str
    data: str
    error: bool
    refused: bool


def _run(run_id: str | None, seqs: dict[str, int]) -> tuple[str, str]:
    if not run_id:
        return format.DASH, "no run"
    return (f"#{seqs[run_id]}" if run_id in seqs else run_id), run_id


def _runner(runner_id: str | None, names: dict[str, str]) -> tuple[str, str]:
    if not runner_id:
        return format.DASH, "no runner"
    return names.get(runner_id) or runner_id, runner_id


def audit_row(row: Row, seqs: dict[str, int], names: dict[str, str], now: int) -> AuditRow:
    logged = events.log_row(row, {})
    run, run_note = _run(row.get("run_id"), seqs)
    runner, runner_note = _runner(row.get("runner_id"), names)
    kind = KINDS.get(row["event"], ("", "all"))[0] if not logged.refused else "refused"
    return AuditRow(
        id=str(row["id"]),
        time=events.clock(row["at"]),
        age=format.ago(row["at"], now),
        event=logged.event,
        kind=kind,
        actor=str(row["actor"]) if row["actor"] in events.ACTORS else "unknown",
        source=logged.kind,
        run_id=row.get("run_id"),
        run=run,
        run_note=run_note,
        runner=runner,
        runner_note=runner_note,
        data=logged.detail,
        error=logged.error,
        refused=logged.refused,
    )


def audit_rows(
    rows: list[Row], seqs: dict[str, int], names: dict[str, str], now: int
) -> list[AuditRow]:
    return [audit_row(row, seqs, names, now) for row in rows]


@dataclass(frozen=True)
class Fact:
    label: str
    value: str
    mono: bool = False
    href: str | None = None


@dataclass(frozen=True)
class Detail:
    id: str
    event: str
    source: str
    tone: Tone
    refusal: str | None
    meta: list[str]
    when: list[Fact]
    correlation: list[Fact]
    data: list[Fact]
    schema: str
    policy: list[Fact]
    logs: str | None
    denial: bool


def _value(value: Any) -> str:
    if value is None:
        return format.DASH
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or format.DASH
    return str(value)


def _listed(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _schema(fields: list[str]) -> str:
    allows = f"allows {_listed(fields)}" if fields else "carries no data"
    return (
        f"The schema for this event {allows}. An extra field, a wrongly typed one "
        "or an unknown event name is refused whole."
    )


def _gate(event: str) -> str | None:
    return next((gate for prefix, gate in GATES.items() if event.startswith(prefix)), None)


def _reached(row: Row) -> str:
    if row["source"] != "runner":
        return "written by the API"
    return f"{format.coarse(max(row['received_at'] - row['at'], 0))} after the runner wrote it"


def _policy(gate: str | None, run: Row | None) -> list[Fact]:
    if gate is None or run is None:
        return []
    policy = run["spec"].get(gate, {}).get("policy")
    return [
        Fact("Gate", gate),
        Fact("Policy", policy or "none \u00b7 default deny", bool(policy)),
        Fact("Run status", str(run["status"])),
    ]


def _logs(run: Row | None, runner_id: str | None, names: dict[str, str]) -> str | None:
    if run is not None:
        return f"timeline of run #{run['seq']}"
    if runner_id:
        return f"latest events of runner {names.get(runner_id) or runner_id}"
    return None


def _where(run: Row | None, run_id: str | None, runner_id: str | None, runner: str | None) -> str:
    place = f"run #{run['seq']}" if run else (run_id or "no run")
    return f"{place} on {runner or runner_id}" if runner_id else place


def detail(row: Row, run: Row | None, names: dict[str, str]) -> Detail:
    logged = events.log_row(row, {})
    schema = events.SCHEMAS.get(str(row["source"]), {}).get(row["event"])
    fields = list(schema.fields) if schema is not None else []
    data = row["data"] if isinstance(row["data"], dict) else {}
    run_id, runner_id = row.get("run_id"), row.get("runner_id")
    runner_name = names.get(runner_id or "")
    gate = _gate(row["event"]) if not logged.refused else None
    at = datetime.fromtimestamp(row["at"], UTC).strftime("%H:%M:%S \u00b7 %Y-%m-%d")
    kind = "refused" if logged.refused else KINDS.get(row["event"], ("", "all"))[0]
    where = _where(run, run_id, runner_id, runner_name)
    return Detail(
        id=str(row["id"]),
        event=logged.event,
        source=logged.kind.upper(),
        tone="red" if logged.error else "blue",
        refusal=logged.detail if logged.refused else None,
        meta=[part for part in (at, f"seq {_number(row['seq'])}", kind, where) if part],
        when=[
            Fact("At", at),
            Fact("Seq", _number(row["seq"])),
            Fact("Actor", str(row["actor"]) if row["actor"] in events.ACTORS else "unknown"),
            Fact("Reached the API", _reached(row)),
        ],
        correlation=[
            Fact(
                "Run",
                f"{run_id} \u00b7 #{run['seq']}" if run else (run_id or format.DASH),
                True,
                f"/runs/{run_id}" if run else None,
            ),
            Fact("VM", row.get("vm_id") or format.DASH, True),
            Fact(
                "Runner",
                " \u00b7 ".join(part for part in (runner_id, runner_name) if part) or format.DASH,
                True,
            ),
        ],
        data=[]
        if logged.refused
        else [Fact(name, _value(data[name]), True) for name in fields if name in data],
        schema=_schema(fields),
        policy=_policy(gate, run),
        logs=_logs(run, runner_id, names),
        denial=bool(gate) and logged.error,
    )


@dataclass(frozen=True)
class LogLine:
    row: events.LogRow
    current: bool


def timeline(rows: list[Row], names: dict[str, str], event_id: str) -> list[LogLine]:
    return [LogLine(events.log_row(row, names), row["id"] == event_id) for row in rows]
