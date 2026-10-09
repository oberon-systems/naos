from dataclasses import dataclass
from typing import Literal

from naos_web.client import Row
from naos_web.events import clock, refusal
from naos_web.format import DASH

Decision = Literal["all", "allowed", "denied"]
EVENTS: dict[Decision, tuple[str, ...]] = {
    "all": ("network_allowed", "network_denied"),
    "allowed": ("network_allowed",),
    "denied": ("network_denied",),
}
GATES = (("network", "Network"), ("shell", "Shell"), ("mcp", "MCP"), ("model", "Model"))
LIVE = frozenset({"network"})
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


def named(run: Row) -> list[Gate]:
    spec = run["spec"]
    return [
        Gate(key, label, key in LIVE)
        for key, label in GATES
        if key == "network" or spec[key]["policy"]
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


def _changed(run: Row, started: bool) -> str:
    edits = [row for row in run.get("policy_history", []) if row["kind"] == "network"]
    if not edits:
        return "never" if started else DASH
    word = "edit" if len(edits) == 1 else "edits"
    return f"{len(edits)} {word} · last {clock(edits[-1]['created_at'])}"


def network(
    run: Row, gate: Row, rows: list[Row], decision: Decision, limit: int, host: str | None
) -> Network:
    document = gate["document"] or {}
    allow, deny = len(document.get("allow", [])), len(document.get("deny", []))
    started = run["status"] not in WAITING
    runner = run["runner"]["name"] if run["runner"] else None
    if gate["configured_at"] is not None:
        configured = clock(gate["configured_at"]) + (f" · by runner {runner}" if runner else "")
    else:
        configured = "not yet" if started else "not yet · when the runner starts the VM"
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
        changed=_changed(run, started),
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
