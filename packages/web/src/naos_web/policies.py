import json
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Literal

from naos_web import format
from naos_web.client import Row
from naos_web.new_run import FormError, short
from naos_web.pages import Summary, TileValue, Tone
from naos_web.profiles import ProfileRow, holders, profile_rows, summary_of
from naos_web.rows import Fact, RunRow, run_rows

Kind = Literal["mount", "network", "shell", "mcp", "model"]
KINDS: tuple[Kind, ...] = ("mount", "network", "shell", "mcp", "model")
KIND_OF: dict[str, Kind] = {kind: kind for kind in KINDS}
KIND_LABELS: dict[Kind, str] = {
    "mount": "Mount",
    "network": "Network",
    "shell": "Shell",
    "mcp": "MCP",
    "model": "Model",
}
TAGS: dict[Kind, str] = {
    "mount": "mnt",
    "network": "net",
    "shell": "sh",
    "mcp": "mcp",
    "model": "mdl",
}
GUEST_HOME = "/home/naos/"
MAX_RULES = 64
MAX_HOME = 32
MAX_MCP_RULES = 256
MAX_GRANTS = 64
RESOURCE = re.compile(r"^[a-z][a-z0-9+.-]*:")
EFFECTS = ("allow", "deny")
MAX_PROVIDERS = 16
CAPABILITIES: dict[str, str] = {
    "read_file": "read a file under a mount",
    "list_dir": "list a directory",
    "grep": "search file contents",
    "git_status": "status of a repository",
    "git_diff": "diff of a repository",
}
PROTOCOLS = ("any", "http", "https")
MODES = ("ro", "rw")
DIALECTS = ("openai", "anthropic")


def _plural(count: int, word: str, words: str = "") -> str:
    return f"{count} {word}" if count == 1 else f"{count} {words or word + 's'}"


def _digest(digest: str, head: int = 8, tail: int = 6) -> str:
    return f"sha256 {digest[:head]}\u2026{digest[-tail:]}"


def _stamp(at: int) -> str:
    return datetime.fromtimestamp(at, UTC).strftime("%Y-%m-%d %H:%M")


def _used(policy: Row) -> bool:
    return bool(policy["profiles"]) or policy["runs_total"] > 0


def _secret_names(policies: list[Row]) -> list[str]:
    return sorted(
        {
            holder["credential"]
            for policy in policies
            for holder in holders(policy)
            if holder.get("credential")
        }
    )


def secret_names(policy: Row) -> list[str]:
    named = _secret_names([policy])
    return sorted({*named, *granted(policy["document"])}) if policy["kind"] == "mcp" else named


def shelf(policies: list[Row]) -> Summary:
    count = {kind: sum(policy["kind"] == kind for policy in policies) for kind in KINDS}
    secrets = len(_secret_names([policy for policy in policies if policy["kind"] == "mcp"]))
    providers = sum(len(p["document"]["providers"]) for p in policies if p["kind"] == "model")
    kinds = sum(1 for kind in KINDS if count[kind])
    return Summary(
        subtitle=(
            f"{_plural(len(policies), 'policy', 'policies')} \u00b7 {_plural(kinds, 'kind')} "
            f"\u00b7 {sum(_used(policy) for policy in policies)} in use"
        ),
        values={
            "mount": TileValue(str(count["mount"]), "workspace and home mounts"),
            "network": TileValue(str(count["network"]), "egress allowlists"),
            "shell": TileValue(str(count["shell"]), "capability sets"),
            "mcp": TileValue(str(count["mcp"]), f"{_plural(secrets, 'secret')} named, never shown"),
            "model": TileValue(
                str(count["model"]), f"{_plural(providers, 'provider')}, keys never shown"
            ),
        },
    )


def _document_note(policy: Row) -> str:
    document, kind = policy["document"], policy["kind"]
    if kind == "mount":
        home = document["mounts"][1:]
        modes = sorted({mount["mode"] for mount in home})
        if not home:
            return "no home mount"
        return f"+ {_plural(len(home), 'home mount')} \u00b7 {'/'.join(modes)}"
    if kind == "network":
        return f"{len(document['allow'])} allow \u00b7 {len(document['deny'])} deny"
    if kind == "shell":
        return f"{len(document['allow'])} of {len(CAPABILITIES)} capabilities"
    if kind == "model":
        return (
            f"{format.grouped(document['max_input_tokens'])} input \u00b7 "
            f"{format.grouped(document['max_output_tokens'])} output tokens per run"
        )
    counts: dict[str, int] = {}
    for rule in document["rules"]:
        counts[rule["server"]] = counts.get(rule["server"], 0) + 1
    return " \u00b7 ".join(f"{name} {_plural(count, 'rule')}" for name, count in counts.items())


def _usage(policy: Row) -> tuple[str, str]:
    profiles, total, opened = policy["profiles"], policy["runs_total"], policy["runs_open"]
    used = _plural(len(profiles), "profile") if profiles else "no profile"
    if not total:
        return used, "never used"
    runs = _plural(total, "run")
    return used, f"{runs} \u00b7 {opened} open" if opened else runs


@dataclass(frozen=True)
class PolicyRow:
    id: str
    digest: str
    kind: Kind
    tag: str
    tone: Tone
    document: str
    document_note: str
    used: str
    used_note: str
    created: str
    created_note: str


def policy_rows(policies: list[Row], now: int) -> list[PolicyRow]:
    rows = []
    for policy in policies:
        used, used_note = _usage(policy)
        rows.append(
            PolicyRow(
                id=policy["id"],
                digest=_digest(policy["digest"]),
                kind=policy["kind"],
                tag=TAGS[policy["kind"]],
                tone="green" if _used(policy) else "faint",
                document=summary_of(policy),
                document_note=_document_note(policy),
                used=used,
                used_note=used_note,
                created=format.ago(policy["created_at"], now),
                created_note=_stamp(policy["created_at"]),
            )
        )
    return rows


@dataclass(frozen=True)
class Cell:
    text: str
    mono: bool = False
    faint: bool = False


@dataclass(frozen=True)
class Table:
    title: str
    columns: tuple[str, ...]
    rows: list[list[Cell]]
    empty: str
    note: str


@dataclass(frozen=True)
class Capability:
    name: str
    note: str
    granted: bool


@dataclass(frozen=True)
class McpRule:
    index: int
    effect: str
    server: str
    target: str
    budget: str
    note: str


@dataclass(frozen=True)
class Server:
    name: str
    external: bool
    rules: str
    note: str


@dataclass(frozen=True)
class Grant:
    name: str
    state: str
    rule: str


@dataclass(frozen=True)
class Provider:
    name: str
    url: str
    api: str
    models: str
    credential: str
    limits: str


@dataclass(frozen=True)
class PolicyDetail:
    id: str
    kind: Kind
    tag: str
    tone: Tone
    pill: str
    pill_tone: Tone
    meta: list[str]
    tables: list[Table]
    workdir: str
    capabilities: list[Capability]
    rules: list[McpRule]
    servers: list[Server]
    grants: list[Grant]
    listing: bool
    providers: list[Provider]
    budget: list[Fact]
    canonical: str
    canonical_note: str
    identity: list[Fact]
    enforcement: list[Fact]
    enforcement_note: str
    secrets: list[Fact]


def _rule_rows(rules: list[Row]) -> list[list[Cell]]:
    return [
        [
            Cell(rule["protocol"] or "any", mono=True),
            Cell(rule["host"] or format.DASH, faint=not rule["host"]),
            Cell(rule["ip"] or format.DASH, mono=bool(rule["ip"]), faint=not rule["ip"]),
        ]
        for rule in rules
    ]


def _network_tables(document: Row) -> list[Table]:
    allow, deny = document["allow"], document["deny"]
    columns = ("PROTOCOL", "HOST", "IP")
    return [
        Table(
            f"ALLOW \u00b7 {_plural(len(allow), 'RULE', 'RULES')}",
            columns,
            _rule_rows(allow),
            "no allow rule",
            "A rule matches when every field it sets matches. An address belongs in ip and is "
            "compared with what the host resolves to.",
        ),
        Table(
            f"DENY \u00b7 {_plural(len(deny), 'RULE', 'RULES')}",
            columns,
            _rule_rows(deny),
            "no deny rule",
            "Everything the allow list does not match is denied; "
            "a deny rule narrows an allow rule.",
        ),
    ]


def _mount_tables(document: Row) -> list[Table]:
    mounts = document["mounts"]
    return [
        Table(
            f"MOUNTS \u00b7 {len(mounts)}",
            ("HOST", "GUEST", "MODE"),
            [
                [
                    Cell(mount["host_path"], mono=True),
                    Cell(mount["guest_path"], mono=True),
                    Cell(mount["mode"], mono=True, faint=True),
                ]
                for mount in mounts
            ],
            "no mount",
            "The workspace reaches the guest read-only over virtiofs; in rw mode its writes land "
            "on a disk of their own and come back only through a merge.",
        )
    ]


def _budget_of(rule: Row) -> str:
    limits = []
    if rule.get("max_calls_per_minute"):
        limits.append(f"{rule['max_calls_per_minute']} calls/min")
    if rule.get("max_calls"):
        limits.append(f"{rule['max_calls']} calls per Run")
    return " \u00b7 ".join(limits)


def _constraint(name: str, constraint: Row) -> str:
    if "equals" in constraint:
        value = constraint["equals"]
        return f"{name} equals {value if isinstance(value, str) else json.dumps(value)}"
    if "prefix" in constraint:
        return f"{name} starts with {constraint['prefix']}"
    if "regex" in constraint:
        return f"{name} matches {constraint['regex']}"
    enum = constraint["schema"].get("enum")
    if isinstance(enum, list):
        return f"{name} in {', '.join(str(item) for item in enum)}"
    return f"{name} matches a schema"


def _overlaps(rule: Row, other: Row) -> bool:
    tools = (rule.get("tool"), other.get("tool"))
    return None not in tools and (tools[0] == tools[1] or "*" in tools)


def _rule_note(index: int, rule: Row, rules: list[Row]) -> str:
    words = [_constraint(name, item) for name, item in (rule.get("arguments") or {}).items()]
    if rule["effect"] == "deny":
        won = [
            str(at)
            for at, other in enumerate(rules)
            if at != index
            and other["effect"] == "allow"
            and other["server"] == rule["server"]
            and _overlaps(rule, other)
        ]
        if won:
            words.append(f"deny wins over rule {', '.join(won)}")
    return " \u00b7 ".join(words)


def mcp_rules(document: Row) -> list[McpRule]:
    rules = document["rules"]
    return [
        McpRule(
            index=index,
            effect=rule["effect"],
            server=rule["server"],
            target=rule["tool"] if "tool" in rule else f"resource {rule['resource']}",
            budget=_budget_of(rule),
            note=_rule_note(index, rule, rules),
        )
        for index, rule in enumerate(rules)
    ]


def granted(document: Row) -> list[str]:
    return sorted({rule["arguments"]["name"]["equals"] for rule in _grants(document)})


def _grants(document: Row) -> list[Row]:
    return [
        rule
        for rule in document["rules"]
        if rule["server"] == "secrets" and rule["effect"] == "allow" and rule.get("tool") == "get"
    ]


def _lists(rule: Row) -> bool:
    return rule["server"] == "secrets" and rule["effect"] == "allow" and rule.get("tool") == "list"


def listing(document: Row) -> bool:
    return any(_lists(rule) for rule in document["rules"])


# The limits are the catalog's, as it holds them now; a built-in server lists the tools it serves.
def _servers(policy: Row) -> list[Server]:
    servers = []
    for server in holders(policy):
        external = server.get("kind") != "built-in"
        tools = dict.fromkeys(rule.get("tool") or rule["resource"] for rule in server["rules"])
        note = " \u00b7 ".join(tools)
        if external:
            note = "not in the catalog"
            if server.get("timeout_seconds") is not None:
                note = f"{server['timeout_seconds']}s \u00b7 {server['max_calls_per_minute']}/min"
        rules = _plural(len(server["rules"]), "rule")
        servers.append(Server(server["name"], external, rules, note))
    return servers


def grants(document: Row, secrets: dict[str, Row | None], now: int) -> list[Grant]:
    rules = document["rules"]
    found = []
    for rule in _grants(document):
        reads = f"{rule['max_calls']} reads per Run" if rule.get("max_calls") else "no budget"
        name = rule["arguments"]["name"]["equals"]
        found.append(
            Grant(name, _state(secrets.get(name), now), f"rule {rules.index(rule)} \u00b7 {reads}")
        )
    return found


def _providers(document: Row) -> list[Provider]:
    return [
        Provider(
            name=provider["name"],
            url=provider["url"],
            api=provider["api"],
            models=" \u00b7 ".join(provider["models"]),
            credential=provider["credential"],
            limits=(
                f"{provider['timeout_seconds']}s timeout \u00b7 "
                f"{provider['max_requests_per_minute']} requests/min"
            ),
        )
        for provider in document["providers"]
    ]


def _budget(document: Row) -> list[Fact]:
    return [
        Fact("Input tokens", format.grouped(document["max_input_tokens"])),
        Fact("Output tokens", format.grouped(document["max_output_tokens"])),
    ]


def _egress(document: Row) -> str:
    allow = document["allow"]
    if not allow:
        return "none"
    protocols = sorted({rule["protocol"] or "any" for rule in allow})
    hosts = {rule["host"] for rule in allow if rule["host"]}
    scheme = f"{protocols[0]} only" if len(protocols) == 1 else "/".join(protocols)
    return f"{scheme} \u00b7 {_plural(len(hosts), 'host')}"


def _enforcement(policy: Row) -> tuple[list[Fact], str]:
    document, kind = policy["document"], policy["kind"]
    if kind == "network":
        return [
            Fact("Gate", "network gate"),
            Fact("Reaches the runner", "as a snapshot"),
            Fact("Egress", _egress(document)),
        ], (
            "A Run carries the resolved document from the moment it starts; nothing created "
            "later reaches it."
        )
    if kind == "mount":
        workspace, home = document["mounts"][0], document["mounts"][1:]
        return [
            Fact("Workspace", "virtiofs \u00b7 read-only share"),
            Fact("Writes", "upper disk \u00b7 merge" if workspace["mode"] == "rw" else "none"),
            Fact("Home entries", "shell gate \u00b7 read-only" if home else "none"),
        ], "The host never receives a write from the guest directly."
    if kind == "shell":
        return [
            Fact("Gate", "shell gate"),
            Fact("Serves", "the mount policy roots"),
            Fact("Reaches the runner", "as a snapshot"),
        ], "A capability the policy does not grant is refused and audited."
    return [], ""


def _expiry(secret: Row | None, now: int) -> str:
    if secret is None:
        return "not stored"
    if secret["expires_at"] is None:
        return "no expiry"
    if secret["expires_at"] <= now:
        return "expired"
    return f"expires in {format.coarse(secret['expires_at'] - now)}"


def _state(secret: Row | None, now: int) -> str:
    expiry = _expiry(secret, now)
    return f"valid \u00b7 {expiry}" if secret and expiry != "expired" else expiry


def _pill(policy: Row) -> tuple[str, Tone]:
    if policy["profiles"] or policy["runs_open"]:
        return "IN USE", "green"
    return ("IDLE", "grey") if policy["runs_total"] else ("UNUSED", "grey")


def policy_detail(policy: Row, secrets: dict[str, Row | None], now: int) -> PolicyDetail:
    document, kind = policy["document"], policy["kind"]
    tables: list[Table] = []
    if kind == "network":
        tables = _network_tables(document)
    elif kind == "mount":
        tables = _mount_tables(document)
    enforcement, enforcement_note = _enforcement(policy)
    pill, pill_tone = _pill(policy)
    used, runs = _usage(policy)
    created = policy["created_at"]
    identity = [
        Fact("Id", short(policy["id"])),
        Fact("Kind", kind),
        Fact("Digest", _digest(policy["digest"], 8, 4)),
        Fact("Created", f"{_stamp(created)} \u00b7 {format.ago(created, now)}"),
        Fact("Status", "immutable"),
    ]
    notes = {
        "network": "The document as the API resolved and stored it. Its sha256 is the digest, "
        "and equivalent documents share one id.",
        "mount": "The document as the API resolved and stored it.",
        "shell": "Stored in a fixed order, so the same set always has one id.",
        "mcp": "",
        "model": "",
    }
    return PolicyDetail(
        id=policy["id"],
        kind=kind,
        tag=TAGS[kind],
        tone="green" if _used(policy) else "faint",
        pill=pill,
        pill_tone=pill_tone,
        meta=[
            f"created {format.ago(policy['created_at'], now)}",
            _digest(policy["digest"], 20, 4),
            f"{used} \u00b7 {runs}" if policy["runs_total"] else used,
        ],
        tables=tables,
        workdir=document.get("workdir", ""),
        capabilities=[
            Capability(name, note, name in document["allow"]) for name, note in CAPABILITIES.items()
        ]
        if kind == "shell"
        else [],
        rules=mcp_rules(document) if kind == "mcp" else [],
        servers=_servers(policy) if kind == "mcp" else [],
        grants=grants(document, secrets, now) if kind == "mcp" else [],
        listing=kind == "mcp" and listing(document),
        providers=_providers(document) if kind == "model" else [],
        budget=_budget(document) if kind == "model" else [],
        canonical=json.dumps(document, indent=2),
        canonical_note=notes[kind],
        identity=identity,
        enforcement=enforcement,
        enforcement_note=enforcement_note,
        secrets=[Fact(name, _expiry(secrets.get(name), now)) for name in secret_names(policy)],
    )


@dataclass(frozen=True)
class UsedRun:
    row: RunRow
    profile: str


@dataclass(frozen=True)
class UsedBy:
    profiles: list[ProfileRow]
    runs: list[UsedRun]
    opened: int
    finished: int


def used_by(policy: Row, profiles: list[Row], runs: list[Row], now: int) -> UsedBy:
    names: dict[str, str] = {profile["id"]: profile["name"] for profile in profiles}
    rows = run_rows(runs, [], now)
    return UsedBy(
        profiles=profile_rows(profiles, now),
        runs=[
            UsedRun(row, names.get(run["profile_id"] or "", str(run["profile_id"] or format.DASH)))
            for row, run in zip(rows, runs, strict=True)
        ],
        opened=policy["runs_open"],
        finished=policy["runs_total"] - policy["runs_open"],
    )


LISTS: dict[Kind, dict[str, tuple[str, ...]]] = {
    "mount": {"home": ("host_path", "guest_path", "mode")},
    "network": {
        "allow": ("protocol", "host", "ip"),
        "deny": ("protocol", "host", "ip"),
    },
    "shell": {},
    "mcp": {
        "rules": ("effect", "server", "target", "arguments", "per_minute", "per_run"),
        "grants": ("name", "reads"),
    },
    "model": {
        "providers": ("name", "api", "url", "models", "credential", "timeout", "requests"),
    },
}
LIMITS = {
    "home": MAX_HOME,
    "allow": MAX_RULES,
    "deny": MAX_RULES,
    "rules": MAX_MCP_RULES,
    "grants": MAX_GRANTS,
    "providers": MAX_PROVIDERS,
}
BLANK: dict[str, dict[str, str]] = {
    "home": {"host_path": "", "guest_path": "", "mode": "ro"},
    "allow": {"protocol": "any", "host": "", "ip": ""},
    "deny": {"protocol": "any", "host": "", "ip": ""},
    "rules": {
        "effect": "allow",
        "server": "",
        "target": "",
        "arguments": "",
        "per_minute": "",
        "per_run": "",
    },
    "grants": {"name": "", "reads": ""},
    "providers": {
        "name": "",
        "api": "openai",
        "url": "",
        "models": "",
        "credential": "",
        "timeout": "600",
        "requests": "60",
    },
}
_ITEM = re.compile(r"^(\w+)\.(\d+)\.(\w+)$")


@dataclass(frozen=True)
class PolicyForm:
    kind: Kind = "mount"
    lists: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    workspace: str = ""
    workspace_mode: str = "rw"
    allow: tuple[str, ...] = ()
    max_input: str = ""
    max_output: str = ""
    name: str = ""
    listing: bool = False

    def items(self, name: str) -> list[dict[str, str]]:
        return self.lists.get(name, [])

    def full(self, name: str) -> bool:
        return len(self.items(name)) >= LIMITS[name]

    def fields(self) -> list[tuple[str, str]]:
        pairs = [("kind", self.kind), ("workspace", self.workspace)]
        pairs += [("workspace_mode", self.workspace_mode)]
        pairs += [(f"cap.{name}", "on") for name in self.allow]
        pairs += [("max_input", self.max_input), ("max_output", self.max_output)]
        pairs += [("name", self.name)] + ([("list_names", "on")] if self.listing else [])
        for name, items in self.lists.items():
            for index, item in enumerate(items):
                pairs += [(f"{name}.{index}.{key}", value) for key, value in item.items()]
        return pairs

    def stepped(self, step: str) -> "PolicyForm":
        verb, _, rest = step.partition(":")
        name, _, index = rest.partition(":")
        if name not in LISTS[self.kind]:
            return self
        items = list(self.items(name))
        if verb == "add" and not self.full(name):
            items.append(dict(BLANK[name]))
        elif verb == "drop" and index.isdigit() and int(index) < len(items):
            del items[int(index)]
        return replace(self, lists={**self.lists, name: items})

    def document(self) -> Row:
        if self.kind == "mount":
            home = [item for item in self.items("home") if item["host_path"] or item["guest_path"]]
            return {
                "workspace": {"host_path": self.workspace, "mode": self.workspace_mode},
                "home": home,
            }
        if self.kind == "network":
            return {name: _rules(self.items(name)) for name in ("allow", "deny")}
        if self.kind == "shell":
            return {"allow": [name for name in CAPABILITIES if name in self.allow]}
        if self.kind == "model":
            providers = [item for item in self.items("providers") if item["name"] or item["url"]]
            return {
                "providers": [_provider(item) for item in providers],
                "max_input_tokens": _whole(self.max_input, "max input tokens"),
                "max_output_tokens": _whole(self.max_output, "max output tokens"),
            }
        items = [item for item in self.items("rules") if item["server"] or item["target"]]
        rules = [_mcp_rule(item) for item in items]
        rules += [_grant(item) for item in self.items("grants") if item["name"]]
        if self.listing:
            rules.append({"server": "secrets", "tool": "list", "effect": "allow"})
        return {"rules": rules}


# A row left at any protocol with no host and no ip is an empty row, not a rule.
def _rules(items: list[dict[str, str]]) -> list[Row]:
    rules = []
    for item in items:
        rule = {key: value for key, value in item.items() if value not in ("", "any")}
        if rule:
            rules.append(rule)
    return rules


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _whole(value: str, label: str) -> int:
    if not value.strip().isdigit():
        raise FormError(f"{label} must be a whole number")
    return int(value)


# A tool name never holds a colon, so a target with a scheme is a resource prefix.
def _mcp_rule(item: dict[str, str]) -> Row:
    rule: Row = {"server": item["server"], "effect": item["effect"]}
    rule["resource" if RESOURCE.match(item["target"]) else "tool"] = item["target"]
    if item["arguments"]:
        try:
            rule["arguments"] = json.loads(item["arguments"])
        except ValueError:
            raise FormError("argument constraints must be a JSON object") from None
    for key, name in (("per_minute", "max_calls_per_minute"), ("per_run", "max_calls")):
        if item[key]:
            rule[name] = _whole(item[key], "a call budget")
    return rule


def _grant(item: dict[str, str]) -> Row:
    rule: Row = {
        "server": "secrets",
        "tool": "get",
        "effect": "allow",
        "arguments": {"name": {"equals": item["name"]}},
    }
    if item["reads"]:
        rule["max_calls"] = _whole(item["reads"], "a read budget")
    return rule


def _provider(item: dict[str, str]) -> Row:
    return {
        "name": item["name"],
        "api": item["api"],
        "url": item["url"],
        "credential": item["credential"],
        "models": _split(item["models"]),
        "timeout_seconds": _whole(item["timeout"], "timeout"),
        "max_requests_per_minute": _whole(item["requests"], "requests per minute"),
    }


def new_form(kind: str) -> PolicyForm:
    chosen = KIND_OF.get(kind, "mount")
    starts = {"network": ["allow"], "mcp": ["rules"], "model": ["providers"]}.get(chosen, [])
    return PolicyForm(chosen, {name: [dict(BLANK[name])] for name in starts})


def read_form(fields: dict[str, str]) -> PolicyForm:
    kind = KIND_OF.get(fields.get("kind", ""), "mount")
    found: dict[str, dict[int, dict[str, str]]] = {}
    for key, value in fields.items():
        match = _ITEM.match(key)
        if match and match[1] in LISTS[kind] and match[3] in LISTS[kind][match[1]]:
            found.setdefault(match[1], {}).setdefault(int(match[2]), {})[match[3]] = value.strip()
    lists = {
        name: [
            {key: found.get(name, {})[index].get(key, BLANK[name][key]) for key in keys}
            for index in sorted(found.get(name, {}))
        ]
        for name, keys in LISTS[kind].items()
    }
    return PolicyForm(
        kind,
        lists,
        fields.get("workspace", "").strip(),
        fields.get("workspace_mode", "rw") if fields.get("workspace_mode") in MODES else "rw",
        tuple(name for name in CAPABILITIES if fields.get(f"cap.{name}")),
        fields.get("max_input", "").strip(),
        fields.get("max_output", "").strip(),
        fields.get("name", "").strip(),
        bool(fields.get("list_names")),
    )


# A mount document maps back as its first mount to the workspace and the rest to home.
def from_document(policy: Row) -> PolicyForm:
    document, kind = policy["document"], policy["kind"]
    if kind == "mount":
        workspace, *home = document["mounts"]
        items = [
            {
                "host_path": mount["host_path"],
                "guest_path": mount["guest_path"].removeprefix(GUEST_HOME),
                "mode": mount["mode"],
            }
            for mount in home
        ]
        return PolicyForm("mount", {"home": items}, workspace["host_path"], workspace["mode"])
    if kind == "network":
        return PolicyForm(
            "network",
            {
                name: [
                    {
                        "protocol": rule["protocol"] or "any",
                        "host": rule["host"] or "",
                        "ip": rule["ip"] or "",
                    }
                    for rule in document[name]
                ]
                for name in ("allow", "deny")
            },
        )
    if kind == "shell":
        return PolicyForm("shell", allow=tuple(document["allow"]))
    if kind == "model":
        return PolicyForm(
            "model",
            {
                "providers": [
                    {
                        "name": provider["name"],
                        "api": provider["api"],
                        "url": provider["url"],
                        "models": ", ".join(provider["models"]),
                        "credential": provider["credential"],
                        "timeout": str(provider["timeout_seconds"]),
                        "requests": str(provider["max_requests_per_minute"]),
                    }
                    for provider in document["providers"]
                ]
            },
            max_input=str(document["max_input_tokens"]),
            max_output=str(document["max_output_tokens"]),
        )
    grants = _grants(document)
    told = [rule for rule in document["rules"] if rule not in grants and not _lists(rule)]
    return PolicyForm(
        "mcp",
        {
            "rules": [
                {
                    "effect": rule["effect"],
                    "server": rule["server"],
                    "target": rule.get("tool") or rule.get("resource", ""),
                    "arguments": json.dumps(rule["arguments"]) if rule.get("arguments") else "",
                    "per_minute": str(rule.get("max_calls_per_minute") or ""),
                    "per_run": str(rule.get("max_calls") or ""),
                }
                for rule in told
            ],
            "grants": [
                {
                    "name": rule["arguments"]["name"]["equals"],
                    "reads": str(rule.get("max_calls") or ""),
                }
                for rule in grants
            ],
        },
        name=policy.get("name") or "",
        listing=listing(document),
    )
