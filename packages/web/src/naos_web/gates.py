from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from naos_web import run_mcp
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
LIVE = frozenset({"network", "shell", "mcp", "model"})
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


McpDecision = Literal["all", "allowed", "denied", "secrets"]
MCP_DECISIONS: dict[McpDecision, str | None] = {
    "all": None,
    "allowed": "allow",
    "denied": "deny",
    "secrets": None,
}
MCP_EVENTS = (
    "mcp_policy_configured",
    "mcp_attached",
    "mcp_rejected",
    "mcp_credentials_updated",
    "policy_changed",
)
MCP_REFUSALS = {
    "invalid": ("grey", "malformed call"),
    "timeout": ("amber", "the server did not answer in time"),
    "credential": ("red", "credential missing or expired"),
    "provider": ("amber", "the server failed"),
}
EVENT_TONES = {
    "mcp_policy_configured": "grey",
    "mcp_attached": "green",
    "mcp_rejected": "red",
    "mcp_credentials_updated": "grey",
    "policy_changed": "amber",
}
LANE = 18
CLUSTER = 1.5


@dataclass(frozen=True)
class Held:
    start: int
    policy_id: str | None
    label: str
    rules: list[Row]


@dataclass(frozen=True)
class Place:
    left: float
    width: float


@dataclass(frozen=True)
class Segment:
    place: Place
    label: str
    tone: str


@dataclass(frozen=True)
class LaneBar:
    place: Place
    height: int
    denied: bool


@dataclass(frozen=True)
class Lane:
    server: str
    link: bool
    known: bool
    spans: list[Place]
    bars: list[LaneBar]


@dataclass(frozen=True)
class Marker:
    left: float
    label: str
    tone: str
    id: str


@dataclass(frozen=True)
class Timeline:
    span: str
    segments: list[Segment]
    lanes: list[Lane]
    markers: list[Marker]
    axis: list[str]


@dataclass(frozen=True)
class GateEvent:
    number: int
    id: str
    at: int
    time: str
    event: str
    tone: str
    detail: str
    before: str
    after: str
    names: list[str]
    actor: str
    refused: bool


@dataclass(frozen=True)
class Budget:
    rule: int
    server: str
    link: bool
    tool: str
    constraint: str
    limit: str
    meter: int
    spent: str
    full: bool
    refused: int
    since: str


@dataclass(frozen=True)
class McpGroup:
    server: str
    link: bool
    known: bool
    tool: str
    resource: str
    calls: int
    denied: int
    slowest: str
    last: str


@dataclass(frozen=True)
class McpCall:
    id: str
    time: str
    decision: str
    server: str
    link: bool
    known: bool
    tool: str
    resource: str
    category: str
    why: str
    tone: str
    rule: str
    rule_policy: str | None
    duration: str
    secret: str
    refused: bool


@dataclass(frozen=True)
class SecretRow:
    name: str
    granted: bool
    rule: str
    budget: str
    reads: int
    refused: int
    last: str


@dataclass(frozen=True)
class Mcp:
    gates: list[Gate]
    policy_id: str | None
    policy_name: str
    policy_tag: tuple[str, str]
    edited_from: str
    edited_from_id: str | None
    configured: str
    rules_line: str
    servers: list[tuple[str, bool]]
    changed: str
    tiles: list[Tile]
    started: bool
    timeline: Timeline | None
    events: list[GateEvent]
    budgets: list[Budget]
    groups: list[McpGroup]
    groups_total: int
    decision: McpDecision
    filters: list[tuple[McpDecision, str]]
    calls: list[McpCall]
    total: int
    more: int | None
    secrets: list[SecretRow]
    granted_by: str


def _held(run: Row, policies: dict[str, Row]) -> list[Held]:
    names = {key: policy.get("name") or "" for key, policy in policies.items()}
    created: str | None = run["spec"]["mcp"]["policy"]
    first = (policies.get(created or "", {}).get("document") or {}).get("rules", [])
    held = [Held(run["created_at"], created, run_mcp.label(created, names), first)]
    for change in run.get("policy_history", []):
        if change["kind"] == "mcp":
            rules = (change["document"] or {}).get("rules", [])
            name = run_mcp.label(change["policy_id"], names)
            held.append(Held(change["created_at"], change["policy_id"], name, rules))
    return held


def _at(held: list[Held], at: int) -> Held:
    return [row for row in held if row.start <= at][-1] if at >= held[0].start else held[0]


def _servers_of(rules: list[Row]) -> set[str]:
    return {rule["server"] for rule in rules if rule["effect"] == "allow"}


def _external(server: str) -> bool:
    return server not in ("network", "secrets", "shell", "unknown")


def _place(start: int, width: int, total: int) -> Place:
    return Place(round(start * 100 / total, 2), round(width * 100 / total, 2))


def _timeline(run: Row, gate: Row, held: list[Held], events: list[GateEvent], end: int) -> Timeline:
    start = run.get("started_at") or run["created_at"]
    total = max(end - start, 60)
    bounds = [max(row.start, start) for row in held] + [start + total]
    segments = [
        Segment(
            _place(bounds[index] - start, bounds[index + 1] - bounds[index], total),
            row.label + ("" if row.policy_id is None else " \u00b7 stored"),
            "amber" if row.policy_id is None else "grey",
        )
        for index, row in enumerate(held)
    ]
    minutes = gate["minutes"]
    width = -(-(total // 60 + 1) // BARS)
    counts: dict[str, dict[int, list[int]]] = {}
    for row in minutes:
        bucket = max(row["at"] - start, 0) // (60 * width)
        slot = counts.setdefault(row["server"], {}).setdefault(bucket, [0, 0])
        slot[0] += row["calls"]
        slot[1] += row["denied"]
    named = set(counts) | set().union(*(_servers_of(row.rules) for row in held))
    lanes = []
    for server in sorted(named, key=lambda name: (name == "unknown", name)):
        buckets = counts.get(server, {})
        peak = max((slot[0] for slot in buckets.values()), default=1)
        spans = [
            _place(bounds[index] - start, bounds[index + 1] - bounds[index], total)
            for index, row in enumerate(held)
            if server in _servers_of(row.rules)
        ]
        bars = [
            LaneBar(
                _place(bucket * 60 * width, 60 * width, total),
                max(3, round(slot[0] * LANE / peak)),
                slot[1] > 0,
            )
            for bucket, slot in sorted(buckets.items())
        ]
        lanes.append(Lane(server, _external(server), server != "unknown", spans, bars))
    markers: list[Marker] = []
    group: list[tuple[float, GateEvent]] = []
    for event in events:
        left = round(min(max((event.at - start) * 100 / total, 0), 100), 2)
        if group and left - group[0][0] > CLUSTER:
            markers.append(_marker(group))
            group = []
        group.append((left, event))
    if group:
        markers.append(_marker(group))
    ticks = [start + total * part // 3 for part in range(4)]
    axis = [_minute(at) for at in ticks]
    return Timeline(f"{clock(start)} \u2013 {clock(start + total)}", segments, lanes, markers, axis)


def _marker(group: list[tuple[float, GateEvent]]) -> Marker:
    first, last = group[0][1], group[-1][1]
    label = str(first.number) if first is last else f"{first.number}\u2013{last.number}"
    tones = {event.tone for _, event in group}
    tone = next((tone for tone in ("red", "amber") if tone in tones), "grey")
    return Marker(group[0][0], label, tone, first.id)


def _events(rows: list[Row], held: list[Held], names: dict[str, str]) -> list[GateEvent]:
    found: list[GateEvent] = []
    changes = iter(held[1:])
    rules = held[0].rules
    for row in sorted(rows, key=lambda row: row["seq"]):
        if row["event"] == "policy_changed" and row["data"].get("kind") != "mcp":
            continue
        why = refusal(row)
        data = row["data"] if why is None else {}
        detail, before, after, listed = DASH, "", "", []
        if why is not None:
            detail = f"refused: {why}"
        elif row["event"] == "mcp_policy_configured":
            detail = f"the runner holds {_at(held, row['at']).label}"
        elif row["event"] == "mcp_attached":
            detail = "the broker serves the VM"
        elif row["event"] == "mcp_rejected":
            detail = data.get("reason", DASH)
        elif row["event"] == "mcp_credentials_updated":
            listed = [name for name in data.get("names", "").split(",") if name]
            detail = "names" if listed else "the gate let go of the Run"
        else:
            change = next(changes, None)
            before = run_mcp.label(data.get("from"), names)
            after = run_mcp.label(data.get("to"), names)
            detail = run_mcp.summary(rules, change.rules) if change else DASH
            rules = change.rules if change else rules
        found.append(
            GateEvent(
                number=len(found) + 1,
                id=row["id"],
                at=row["at"],
                time=clock(row["at"]),
                event=row["event"],
                tone="grey" if why else EVENT_TONES.get(row["event"], "grey"),
                detail=detail,
                before=before,
                after=after,
                names=listed,
                actor=row["actor"],
                refused=why is not None,
            )
        )
    return found


def _budgets(gate: Row, rules: list[Row], start: int) -> list[Budget]:
    budgets = []
    for row in gate["budgets"]:
        rule = rules[row["rule"]] if row["rule"] < len(rules) else None
        if rule is None:
            continue
        per_run, per_minute = rule.get("max_calls"), rule.get("max_calls_per_minute")
        limits = [f"{grouped(per_run)} for the Run"] if per_run else []
        limits += [f"{per_minute} a minute"] if per_minute else []
        if per_run:
            meter, spent = row["spent"] * 100 // per_run, f"{row['spent']} / {per_run}"
        else:
            meter, spent = row["peak"] * 100 // per_minute, f"peak {row['peak']} / {per_minute}"
        name = rule.get("arguments", {}).get("name", {}).get("equals")
        budgets.append(
            Budget(
                rule=row["rule"],
                server=rule["server"],
                link=_external(rule["server"]),
                tool=rule.get("tool", DASH),
                constraint=f"name = {name}" if name else "",
                limit=" \u00b7 ".join(limits),
                meter=min(meter, 100),
                spent=spent,
                full=meter >= 100,
                refused=row["refused"],
                since="" if row["since"] <= start else clock(row["since"]),
            )
        )
    return budgets


def _mcp_call(row: Row, held: list[Held], secret: str = "") -> McpCall:
    why = refusal(row)
    data = row["data"] if why is None else {}
    category = data.get("category", "none")
    rule = data.get("rule", "none")
    policy = _at(held, row["at"])
    if why:
        tone, reason = "red", f"refused: {why}"
    elif category == "denied":
        index = int(rule) if rule.isdigit() else None
        if index is None:
            tone, reason = "red", "no rule allows this call"
        elif index < len(policy.rules) and policy.rules[index]["effect"] == "allow":
            tone, reason = "red", f"budget of rule {index} is spent"
        else:
            tone, reason = "red", f"denied by rule {index}"
    else:
        tone, reason = MCP_REFUSALS.get(category, ("grey", ""))
    server = data.get("server", DASH)
    return McpCall(
        id=row["id"],
        time=clock(row["at"]),
        decision="refused" if why else "denied" if data.get("decision") == "deny" else "allowed",
        server=server,
        link=_external(server),
        known=server not in ("unknown", DASH),
        tool=data.get("tool", DASH),
        resource="" if data.get("resource") in (None, "none") else data["resource"],
        category=category,
        why=reason,
        tone=tone,
        rule="none" if rule == "none" else f"rule {rule}",
        rule_policy=policy.policy_id if rule != "none" else None,
        duration=duration(data["duration_ms"]) if data else DASH,
        secret=secret,
        refused=why is not None,
    )


# The broker writes secret_read before an allowed get and after a get the rules denied.
def paired(calls: list[Row], reads: list[Row]) -> dict[str, str]:
    stream = sorted(calls + reads, key=lambda row: row["seq"])
    used: set[str] = set()
    names: dict[str, str] = {}
    for index, row in enumerate(stream):
        if row["event"] != "mcp_call" or row["data"].get("tool") != "get":
            continue
        after = row["data"].get("category") == "denied"
        near = index + 1 if after else index - 1
        if 0 <= near < len(stream):
            read = stream[near]
            if read["event"] == "secret_read" and read["id"] not in used and not refusal(read):
                used.add(read["id"])
                names[row["id"]] = read["data"]["name"]
    return names


def _secrets(gate: Row, rules: list[Row]) -> list[SecretRow]:
    grants = {
        rule["arguments"]["name"]["equals"]: (index, rule.get("max_calls"))
        for index, rule in enumerate(rules)
        if _grants(rule)
    }
    used = {row["name"]: row for row in gate["secrets"]}
    rows = []
    for name in sorted(set(grants) | set(used)):
        index, budget = grants.get(name, (None, None))
        use = used.get(name, {"reads": 0, "refused": 0, "last_at": None})
        if index is None:
            limit = DASH
        else:
            limit = f"{use['reads']} of {budget} for the Run" if budget else "no budget"
        rows.append(
            SecretRow(
                name=name,
                granted=index is not None,
                rule="none" if index is None else f"rule {index}",
                budget=limit,
                reads=use["reads"],
                refused=use["refused"],
                last=clock(use["last_at"]) if use["last_at"] else DASH,
            )
        )
    return rows


MCP_TILES = ("Allowed", "Denied", "Secrets read")


def _mcp_tiles(gate: Row, started: bool, rules: list[Row]) -> list[Tile]:
    if not started:
        return [Tile(label, DASH, "the Run has not started", None, False) for label in MCP_TILES]
    refused = [row for row in gate["groups"] if row["denied"]]
    if not gate["calls"]:
        denied = "no call was made"
    elif refused:
        tools = len(refused)
        when = clock(max(row["last_at"] for row in refused))
        denied = f"{tools} {'tool' if tools == 1 else 'tools'} \u00b7 last {when}"
    else:
        denied = "none denied"
    secrets = gate["secrets"]
    reads = sum(row["reads"] for row in secrets)
    parts = [f"{row['name']} {row['reads']}" for row in secrets if row["reads"]]
    lost = sum(row["refused"] for row in secrets)
    parts += [f"{lost} refused"] if lost else []
    return [
        Tile(
            "Allowed",
            str(gate["calls"] - gate["denied"]),
            "calls the rules let through" if rules else "no rule allows a call",
            "green",
        ),
        Tile("Denied", str(gate["denied"]), denied, "red"),
        Tile("Secrets read", str(reads), " \u00b7 ".join(parts) or "no secret was read", None),
    ]


def mcp(
    run: Row,
    gate: Row,
    rows: list[Row],
    trail: list[Row],
    policies: dict[str, Row],
    decision: McpDecision,
    limit: int,
    now: int,
    reads: list[Row] | None = None,
) -> Mcp:
    names = {key: policy.get("name") or "" for key, policy in policies.items()}
    document: Row = gate["document"] or {"rules": [], "servers": []}
    rules: list[Row] = document["rules"]
    started = run["status"] not in WAITING
    held = _held(run, policies)
    if not rules:
        tag = ("allows nothing", "amber")
    elif gate["policy_id"] is None:
        tag = ("temporary", "blue")
    else:
        tag = ("stored", "grey")
    previous = held[-2].policy_id if len(held) > 1 and gate["policy_id"] is None else None
    budgeted = sum(1 for rule in rules if rule.get("max_calls") or rule.get("max_calls_per_minute"))
    denies = sum(rule["effect"] == "deny" for rule in rules)
    events = _events(trail, held, names)
    end = now if run.get("finished_at") is None else run["finished_at"]
    start = run.get("started_at") or run["created_at"]
    groups = [
        McpGroup(
            server=row["server"],
            link=_external(row["server"]),
            known=row["server"] != "unknown",
            tool=row["tool"],
            resource="" if row["resource"] == "none" else row["resource"],
            calls=row["calls"],
            denied=row["denied"],
            slowest=duration(row["slowest_ms"]),
            last=clock(row["last_at"]),
        )
        for row in gate["groups"]
    ]
    to_secrets = sum(row["calls"] for row in gate["groups"] if row["server"] == "secrets")
    counts = {"all": gate["calls"], "allowed": gate["calls"] - gate["denied"]}
    counts |= {"denied": gate["denied"], "secrets": to_secrets}
    secret_names = paired(rows, reads or []) if decision == "secrets" else {}
    grants = [str(index) for index, rule in enumerate(rules) if _grants(rule)]
    return Mcp(
        gates=named(run),
        policy_id=gate["policy_id"],
        policy_name=run_mcp.label(gate["policy_id"], names),
        policy_tag=tag,
        edited_from=run_mcp.label(previous, names) if previous else "",
        edited_from_id=previous,
        configured=_configured(run, gate, started),
        rules_line=f"{len(rules)} \u00b7 {denies} deny \u00b7 {budgeted} with a budget",
        servers=[(name, _external(name)) for name in sorted(_servers_of(rules))],
        changed=_changed(run, started, "mcp"),
        tiles=_mcp_tiles(gate, started, rules),
        started=started,
        timeline=_timeline(run, gate, held, events, max(end, start + 60)) if started else None,
        events=events,
        budgets=_budgets(gate, rules, start),
        groups=groups,
        groups_total=gate["groups_total"],
        decision=decision,
        filters=[(key, f"{key.capitalize()} \u00b7 {counts[key]}") for key in MCP_DECISIONS],
        calls=[_mcp_call(row, held, secret_names.get(row["id"], "")) for row in rows],
        total=counts[decision],
        more=min(limit + PAGE, MOST) if len(rows) == limit and limit < MOST else None,
        secrets=_secrets(gate, rules),
        granted_by=" and ".join(grants),
    )


def _grants(rule: Row) -> bool:
    return rule["server"] == "secrets" and rule["effect"] == "allow" and rule.get("tool") == "get"
