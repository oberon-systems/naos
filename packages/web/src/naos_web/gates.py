from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from naos_web.client import Row
from naos_web.events import clock, refusal
from naos_web.format import DASH, grouped
from naos_web.rows import MODEL_REFUSALS

Decision = Literal["all", "allowed", "denied"]
EVENTS: dict[Decision, tuple[str, ...]] = {
    "all": ("network_allowed", "network_denied"),
    "allowed": ("network_allowed",),
    "denied": ("network_denied",),
}
SHELL_EVENTS: dict[Decision, tuple[str, ...]] = {
    "all": ("shell_allowed", "shell_denied"),
    "allowed": ("shell_allowed",),
    "denied": ("shell_denied",),
}
GATES = (("network", "Network"), ("shell", "Shell"), ("mcp", "MCP"), ("model", "Model"))
MODEL_DECISIONS: dict[Decision, str | None] = {"all": None, "allowed": "allow", "denied": "deny"}
LIVE = frozenset({"network", "shell", "model"})
PAGE = 50
MOST = 1000
WAITING = frozenset({"PENDING", "STARTING"})


@dataclass(frozen=True)
class Gate:
    key: str
    label: str
    live: bool


@dataclass(frozen=True)
class Tile:
    label: str
    value: str
    note: str
    tone: str | None
    counted: bool = True


@dataclass(frozen=True)
class Host:
    host: str
    protocol: str
    rule: str
    allowed: int
    denied: int
    last: str


@dataclass(frozen=True)
class Request:
    id: str
    time: str
    decision: str
    protocol: str
    host: str
    rule: str
    reason: str
    refused: bool


@dataclass(frozen=True)
class Network:
    gates: list[Gate]
    policy_id: str | None
    policy_tag: tuple[str, str]
    configured: str
    rules: str
    changed: str
    tiles: list[Tile]
    started: bool
    allows: bool
    hosts: list[Host]
    hosts_total: int
    decision: Decision
    filters: list[tuple[Decision, str]]
    host: str | None
    requests: list[Request]
    total: int
    more: int | None
    policy_name: str = ""


@dataclass(frozen=True)
class Group:
    capability: str
    path: str
    allowed: int
    denied: int
    last: str


@dataclass(frozen=True)
class Call:
    id: str
    time: str
    decision: str
    capability: str
    path: str
    reason: str
    refused: bool


@dataclass(frozen=True)
class Shell:
    gates: list[Gate]
    policy_id: str | None
    policy_tag: tuple[str, str]
    configured: str
    capabilities: list[str]
    roots: list[str]
    changed: str
    tiles: list[Tile]
    started: bool
    grants: bool
    groups: list[Group]
    groups_total: int
    decision: Decision
    filters: list[tuple[Decision, str]]
    calls: list[Call]
    total: int
    more: int | None
    policy_name: str = ""


# A live gate shows even without a policy, since a Run without one is exactly what it reports.
def named(run: Row) -> list[Gate]:
    spec = run["spec"]
    return [
        Gate(key, label, key in LIVE) for key, label in GATES if key in LIVE or spec[key]["policy"]
    ]


def _request(row: Row) -> Request:
    why = refusal(row)
    data = row["data"] if why is None else {}
    return Request(
        id=row["id"],
        time=clock(row["at"]),
        decision="refused" if why else row["event"].removeprefix("network_"),
        protocol=data.get("protocol", DASH),
        host=data.get("host", DASH),
        rule=data.get("rule", DASH),
        reason=f"refused: {why}" if why else data.get("reason", DASH),
        refused=why is not None,
    )


TILES = ("Allowed", "Denied", "Hosts")


def _tiles(gate: Row, started: bool, allows: bool) -> list[Tile]:
    if not started:
        return [Tile(label, DASH, "the Run has not started", None, False) for label in TILES]
    total = gate["allowed"] + gate["denied"]
    refused = [row for row in gate["hosts"] if row["denied"]]
    if not total:
        denied = "no request was made"
    elif refused:
        denied = f"{len(refused)} hosts · last {clock(max(row['last_at'] for row in refused))}"
    else:
        denied = "none denied"
    reached = len(gate["hosts"]) - len(refused)
    return [
        Tile(
            "Allowed",
            str(gate["allowed"]),
            "requests the policy let out" if allows else "nothing is allowed",
            "green",
        ),
        Tile("Denied", str(gate["denied"]), denied, "red"),
        Tile(
            "Hosts",
            str(gate["hosts_total"]),
            f"{reached} allowed · {len(refused)} denied" if total else "none reached",
            None,
        ),
    ]


def _changed(run: Row, started: bool, kind: str) -> str:
    edits = [row for row in run.get("policy_history", []) if row["kind"] == kind]
    if not edits:
        return "never" if started else DASH
    word = "edit" if len(edits) == 1 else "edits"
    return f"{len(edits)} {word} · last {clock(edits[-1]['created_at'])}"


def _configured(run: Row, gate: Row, started: bool) -> str:
    runner = run["runner"]["name"] if run["runner"] else None
    if gate["configured_at"] is not None:
        return clock(gate["configured_at"]) + (f" · by runner {runner}" if runner else "")
    return "not yet" if started else "not yet · when the runner starts the VM"


def network(
    run: Row, gate: Row, rows: list[Row], decision: Decision, limit: int, host: str | None
) -> Network:
    document = gate["document"] or {}
    allow, deny = len(document.get("allow", [])), len(document.get("deny", []))
    started = run["status"] not in WAITING
    configured = _configured(run, gate, started)
    if not allow:
        tag = ("allows nothing", "amber")
    elif gate["policy_id"] is None:
        tag = ("temporary", "blue")
    else:
        tag = ("stored", "grey")
    hosts = [
        Host(
            row["host"],
            row["protocol"],
            row["rule"],
            row["allowed"],
            row["denied"],
            clock(row["last_at"]),
        )
        for row in gate["hosts"]
    ]
    requests = [_request(row) for row in rows]
    if host is not None:
        requests = [row for row in requests if row.host == host]
    counts = {"all": gate["allowed"] + gate["denied"], "allowed": gate["allowed"]}
    counts["denied"] = gate["denied"]
    return Network(
        gates=named(run),
        policy_id=gate["policy_id"],
        policy_tag=tag,
        configured=configured,
        rules=f"allow {allow} · deny {deny}",
        changed=_changed(run, started, "network"),
        tiles=_tiles(gate, started, bool(allow)),
        started=started,
        allows=bool(allow),
        hosts=hosts,
        hosts_total=gate["hosts_total"],
        decision=decision,
        filters=[(key, f"{key.capitalize()} · {counts[key]}") for key in EVENTS],
        host=host,
        requests=requests,
        total=counts[decision],
        more=min(limit + PAGE, MOST) if len(rows) == limit and limit < MOST else None,
    )


def _call(row: Row) -> Call:
    why = refusal(row)
    data = row["data"] if why is None else {}
    return Call(
        id=row["id"],
        time=clock(row["at"]),
        decision="refused" if why else row["event"].removeprefix("shell_"),
        capability=data.get("capability", DASH),
        path=data.get("path", DASH),
        reason=f"refused: {why}" if why else data.get("reason", DASH),
        refused=why is not None,
    )


SHELL_TILES = ("Allowed", "Denied", "Capabilities")


def _shell_tiles(gate: Row, started: bool, granted: list[str]) -> list[Tile]:
    if not started:
        return [Tile(label, DASH, "the Run has not started", None, False) for label in SHELL_TILES]
    total = gate["allowed"] + gate["denied"]
    refused = [row for row in gate["groups"] if row["denied"]]
    if not total:
        denied = "no call was made"
    elif refused:
        named = len({row["capability"] for row in refused})
        when = clock(max(row["last_at"] for row in refused))
        denied = f"{named} {'capability' if named == 1 else 'capabilities'} · last {when}"
    else:
        denied = "none denied"
    called = gate["called"]
    outside = len([name for name in called if name not in granted])
    if called:
        used = f"{len(called) - outside} granted · {outside} not granted"
    else:
        used = "none granted" if not granted else "none called yet"
    return [
        Tile(
            "Allowed",
            str(gate["allowed"]),
            "calls the policy let through" if granted else "no capability is granted",
            "green",
        ),
        Tile("Denied", str(gate["denied"]), denied, "red"),
        Tile("Capabilities", str(len(called)), used, None),
    ]


def shell(run: Row, gate: Row, rows: list[Row], decision: Decision, limit: int) -> Shell:
    granted: list[str] = (gate["document"] or {}).get("allow", [])
    started = run["status"] not in WAITING
    configured = _configured(run, gate, started)
    if not granted:
        tag = ("grants nothing", "amber")
    elif gate["policy_id"] is None:
        tag = ("temporary", "blue")
    else:
        tag = ("stored", "grey")
    groups = [
        Group(row["capability"], row["path"], row["allowed"], row["denied"], clock(row["last_at"]))
        for row in gate["groups"]
    ]
    counts = {"all": gate["allowed"] + gate["denied"], "allowed": gate["allowed"]}
    counts["denied"] = gate["denied"]
    return Shell(
        gates=named(run),
        policy_id=gate["policy_id"],
        policy_tag=tag,
        configured="never" if not granted and gate["configured_at"] is None else configured,
        capabilities=granted,
        roots=gate["roots"],
        changed=_changed(run, started, "shell"),
        tiles=_shell_tiles(gate, started, granted),
        started=started,
        grants=bool(granted),
        groups=groups,
        groups_total=gate["groups_total"],
        decision=decision,
        filters=[(key, f"{key.capitalize()} · {counts[key]}") for key in SHELL_EVENTS],
        calls=[_call(row) for row in rows],
        total=counts[decision],
        more=min(limit + PAGE, MOST) if len(rows) == limit and limit < MOST else None,
    )


@dataclass(frozen=True)
class Provider:
    name: str
    api: str
    models: str
    rate: str
    credential: str


@dataclass(frozen=True)
class ModelGroup:
    provider: str
    model: str
    calls: int
    denied: int
    input: str
    output: str
    slowest: str
    last: str
    known: bool


@dataclass(frozen=True)
class ModelCall:
    id: str
    time: str
    decision: str
    provider: str
    model: str
    input: str
    output: str
    duration: str
    category: str
    why: str
    tone: str
    refused: bool
    known: bool


@dataclass(frozen=True)
class Bar:
    x: float
    y: float
    height: float
    tone: str


@dataclass(frozen=True)
class Strip:
    provider: str
    note: str
    refused: str
    bars: list[Bar]


@dataclass(frozen=True)
class Spend:
    input: str
    output: str
    input_line: str
    output_line: str
    strips: list[Strip]
    axis: list[str]
    bar_width: float


@dataclass(frozen=True)
class Model:
    gates: list[Gate]
    policy_id: str | None
    policy_tag: tuple[str, str]
    configured: str
    providers: list[Provider]
    changed: str
    tiles: list[Tile]
    started: bool
    spend: Spend | None
    groups: list[ModelGroup]
    groups_total: int
    decision: Decision
    filters: list[tuple[Decision, str]]
    calls: list[ModelCall]
    total: int
    more: int | None
    policy_name: str = ""


WIDTH = 504
HEIGHT = 104
STRIP = 38
LANE = 44
BARS = 120
RATE = 60
TIMEOUT = 600


def duration(ms: int) -> str:
    return f"{ms} ms" if ms < 1000 else f"{ms / 1000:.1f} s"


def _minute(at: int) -> str:
    return datetime.fromtimestamp(at, UTC).strftime("%H:%M")


def _line(points: list[float]) -> str:
    step = WIDTH / max(len(points) - 1, 1)
    return " ".join(
        f"{index * step:.1f},{HEIGHT * (1 - min(point, 1)):.1f}"
        for index, point in enumerate(points)
    )


def _spent(spent: int, budget: int) -> str:
    return f"{grouped(spent)} / {grouped(budget)} \u00b7 {spent * 100 // budget}%"


# Buckets are whole minutes, widened only so a long Run still fits in BARS of them.
def _spend(gate: Row, document: Row) -> Spend | None:
    minutes = gate["minutes"]
    if not minutes:
        return None
    first, last = minutes[0]["at"], minutes[-1]["at"]
    width = -(-((last - first) // 60 + 1) // BARS)
    count = (last - first) // (60 * width) + 1
    spent_in, spent_out = [0] * count, [0] * count
    peaks: dict[str, list[int]] = {}
    denied: dict[str, list[int]] = {}
    for row in minutes:
        bucket = (row["at"] - first) // (60 * width)
        spent_in[bucket] += row["input_tokens"]
        spent_out[bucket] += row["output_tokens"]
        peak = peaks.setdefault(row["provider"], [0] * count)
        peak[bucket] = max(peak[bucket], row["requests"])
        denied.setdefault(row["provider"], [0] * count)[bucket] += row["denied"]
    budget_in, budget_out = document["max_input_tokens"], document["max_output_tokens"]
    total_in = total_out = 0
    line_in, line_out = [0.0], [0.0]
    for spent in zip(spent_in, spent_out, strict=True):
        total_in, total_out = total_in + spent[0], total_out + spent[1]
        line_in.append(total_in / budget_in)
        line_out.append(total_out / budget_out)
    step = WIDTH / count
    strips = []
    for provider in document["providers"]:
        rate = provider.get("max_requests_per_minute", RATE)
        peak, refused = peaks.get(provider["name"], [0] * count), denied.get(provider["name"])
        top = max(peak)
        tones = ["amber" if value >= rate * 0.85 else "blue" for value in peak]
        heights = [round(min(value / rate, 1) * STRIP, 1) for value in peak]
        bars = [
            Bar(
                round(index * step, 1),
                round(LANE - heights[index], 1),
                heights[index],
                "red" if refused and refused[index] else tones[index],
            )
            for index, value in enumerate(peak)
            if value
        ]
        when = f" at {_minute(first + peak.index(top) * 60 * width)}" if top else ""
        lost = sum(refused or [])
        strips.append(
            Strip(
                provider=provider["name"],
                note=f"{rate}/min \u00b7 peak {top}/min{when}",
                refused=f"{lost} refused" if lost else "",
                bars=bars,
            )
        )
    ticks = [first + (last - first) * part // 3 for part in range(4)]
    return Spend(
        input=_spent(total_in, budget_in),
        output=_spent(total_out, budget_out),
        input_line=_line(line_in),
        output_line=_line(line_out),
        strips=strips,
        axis=[_minute(at) for at in ticks],
        bar_width=round(max(step - 4, 1), 1),
    )


def _model_call(row: Row) -> ModelCall:
    why = refusal(row)
    data = row["data"] if why is None else {}
    category = data.get("category", "none")
    denied = data.get("decision") == "deny"
    return ModelCall(
        id=row["id"],
        time=clock(row["at"]),
        decision="refused" if why else "denied" if denied else "allowed",
        provider=data.get("provider", DASH),
        model=data.get("model", DASH),
        input=grouped(data["input_tokens"]) if data else DASH,
        output=grouped(data["output_tokens"]) if data else DASH,
        duration=duration(data["duration_ms"]) if data else DASH,
        category=category,
        why=f"refused: {why}" if why else MODEL_REFUSALS.get(category, ""),
        tone="amber" if category in ("budget", "rate") else "red",
        refused=why is not None,
        known=data.get("provider") not in ("unknown", None),
    )


MODEL_TILES = ("Allowed", "Denied", "Slowest")


def _model_tiles(gate: Row, started: bool, document: Row) -> list[Tile]:
    if not started:
        return [Tile(label, DASH, "the Run has not started", None, False) for label in MODEL_TILES]
    refusals = gate["refusals"]
    if not gate["calls"]:
        denied = "no call was made"
    elif refusals:
        denied = f"last {clock(refusals[0]['at'])} \u00b7 {refusals[0]['category']}"
    else:
        denied = "none denied"
    groups = [row for row in gate["groups"] if row["calls"] > row["denied"]]
    slowest = max(groups, key=lambda row: row["slowest_ms"], default=None)
    if slowest is None:
        tile = Tile("Slowest", DASH, "no call was answered", None, False)
    else:
        limits = {row["name"]: row.get("timeout_seconds", TIMEOUT) for row in document["providers"]}
        limit = limits.get(slowest["provider"])
        where = f"{slowest['provider']} / {slowest['model']}"
        tile = Tile(
            "Slowest",
            duration(slowest["slowest_ms"]),
            f"{where} \u00b7 limit {limit} s" if limit else where,
            None,
        )
    return [
        Tile(
            "Allowed",
            str(gate["calls"] - gate["denied"]),
            "calls the policy let through" if document["providers"] else "no model is allowed",
            "green",
        ),
        Tile("Denied", str(gate["denied"]), denied, "red"),
        tile,
    ]


def model(run: Row, gate: Row, rows: list[Row], decision: Decision, limit: int) -> Model:
    document: Row = gate["document"] or {"providers": []}
    started = run["status"] not in WAITING
    if not document["providers"]:
        tag = ("allows nothing", "amber")
    elif gate["policy_id"] is None:
        tag = ("temporary", "blue")
    else:
        tag = ("stored", "grey")
    providers = [
        Provider(
            name=row["name"],
            api=row["api"],
            models=" \u00b7 ".join(row["models"]),
            rate=f"{row.get('max_requests_per_minute', RATE)}/min",
            credential=row["credential"],
        )
        for row in document["providers"]
    ]
    groups = [
        ModelGroup(
            provider=row["provider"],
            model=row["model"],
            calls=row["calls"],
            denied=row["denied"],
            input=grouped(row["input_tokens"]),
            output=grouped(row["output_tokens"]),
            slowest=duration(row["slowest_ms"]),
            last=clock(row["last_at"]),
            known=row["provider"] != "unknown",
        )
        for row in gate["groups"]
    ]
    counts = {"all": gate["calls"], "allowed": gate["calls"] - gate["denied"]}
    counts["denied"] = gate["denied"]
    return Model(
        gates=named(run),
        policy_id=gate["policy_id"],
        policy_tag=tag,
        configured=_configured(run, gate, started),
        providers=providers,
        changed=_changed(run, started, "model"),
        tiles=_model_tiles(gate, started, document),
        started=started,
        spend=_spend(gate, document) if document["providers"] else None,
        groups=groups,
        groups_total=gate["groups_total"],
        decision=decision,
        filters=[(key, f"{key.capitalize()} \u00b7 {counts[key]}") for key in MODEL_DECISIONS],
        calls=[_model_call(row) for row in rows],
        total=counts[decision],
        more=min(limit + PAGE, MOST) if len(rows) == limit and limit < MOST else None,
    )
