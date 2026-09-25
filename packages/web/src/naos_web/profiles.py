from dataclasses import dataclass, field
from typing import Literal

from naos_web import format
from naos_web.client import Row
from naos_web.new_run import (
    NEW_PROFILE,
    POLICY_KINDS,
    Option,
    policy_label,
    short,
    spec_of,
    values_of,
)
from naos_web.pages import FINISHED, Summary, TileValue, Tone
from naos_web.rows import Fact, RunRow, run_rows

Mode = Literal["new", "edit", "clone"]
TAGS = {"mounts": "mnt", "network": "net", "shell": "sh", "mcp": "mcp"}
GATES = {"mounts": "Mount", "network": "Network", "shell": "Shell", "mcp": "MCP"}
MISSING = {"mounts": "no mounts", "network": "no network", "shell": "no shell", "mcp": "no mcp"}
CLOSED = {
    "mounts": "none \u2014 nothing mounted",
    "network": "none \u2014 no network",
    "shell": "none \u2014 no shell",
    "mcp": "none \u2014 no servers, no secrets",
}
KIND_NAMES = {"mount": "mount", "network": "net", "shell": "shell", "mcp": "mcp"}
MERGE: dict[str, tuple[str, Tone, str]] = {
    "ask": ("ASK", "amber", "operator decides"),
    "never": ("NEVER", "grey", "workspace untouched"),
    "always": ("ALWAYS", "blue", "merges unless sensitive"),
}


def _plural(count: int, word: str, words: str = "") -> str:
    return f"{count} {word}" if count == 1 else f"{count} {words or word + 's'}"


@dataclass(frozen=True)
class Tag:
    label: str
    set: bool


@dataclass(frozen=True)
class ProfileRow:
    id: str
    short_id: str
    name: str
    tone: Tone
    runtime: str
    disk: str
    tags: list[Tag]
    policies: str
    merge: str
    merge_tone: Tone
    merge_note: str
    timeout: str
    timeout_note: str
    runs: str
    runs_note: str


def _tags(spec: Row) -> list[Tag]:
    return [Tag(TAGS[name], bool(spec[name]["policy"])) for name, _ in POLICY_KINDS]


def _policies_note(spec: Row) -> str:
    missing = [MISSING[name] for name, _ in POLICY_KINDS if not spec[name]["policy"]]
    return ", ".join(missing) or f"{len(POLICY_KINDS)} of {len(POLICY_KINDS)} set"


# Without a workspace mount a Run has nothing to merge back, whatever the policy says.
def _merge(spec: Row) -> tuple[str, Tone, str]:
    label, tone, note = MERGE[spec["merge"]["policy"]]
    return label, tone, note if spec["mounts"]["policy"] else "nothing to merge"


def profile_rows(profiles: list[Row], now: int) -> list[ProfileRow]:
    rows = []
    for profile in profiles:
        spec, runtime = profile["spec"], profile["spec"]["runtime"]
        merge, merge_tone, merge_note = _merge(spec)
        total = profile["runs_total"]
        rows.append(
            ProfileRow(
                id=profile["id"],
                short_id=short(profile["id"]),
                name=profile["name"],
                tone="green" if total else "faint",
                runtime=f"{runtime['cpu']} cpu \u00b7 {runtime['memory_mib']} MiB",
                disk=f"{runtime['disk_gib']} GiB disk",
                tags=_tags(spec),
                policies=_policies_note(spec),
                merge=merge,
                merge_tone=merge_tone,
                merge_note=merge_note,
                timeout=f"{spec['timeout']}s",
                timeout_note=format.fine(spec["timeout"]),
                runs=_plural(total, "run") if total else "no runs",
                runs_note=(
                    f"last {format.ago(profile['last_run_at'], now)}"
                    if profile["last_run_at"] is not None
                    else "never used"
                ),
            )
        )
    return rows


def pick_profiles(profiles: list[Row], state: str, query: str) -> list[Row]:
    needle = query.strip().lower()
    return [
        profile
        for profile in profiles
        if (state == "all" or (profile["runs_total"] > 0) == (state == "used"))
        and (
            not needle
            or needle in profile["name"].lower()
            or needle in profile["id"].lower()
            or any(
                needle in (profile["spec"][name]["policy"] or "").lower()
                for name, _ in POLICY_KINDS
            )
        )
    ]


# Secrets are counted by the names MCP policies bind; their values never reach the web.
def _secret_names(policies: list[Row]) -> set[str]:
    return {
        server["credential"]
        for policy in policies
        if policy["kind"] == "mcp"
        for server in policy["document"].get("servers", [])
        if server.get("credential")
    }


def shelf(profiles: list[Row], policies: list[Row]) -> Summary:
    kinds = [KIND_NAMES[kind] for kind in KIND_NAMES if any(p["kind"] == kind for p in policies)]
    secrets = len(_secret_names(policies))
    return Summary(
        subtitle=(
            f"{_plural(len(profiles), 'profile')} \u00b7 "
            f"{_plural(len(policies), 'policy', 'policies')} \u00b7 {_plural(secrets, 'secret')}"
        ),
        values={
            "profiles": TileValue(str(len(profiles)), "runtime presets"),
            "policies": TileValue(str(len(policies)), " \u00b7 ".join(kinds) or "none yet"),
            "secrets": TileValue(str(secrets), "named by mcp policies"),
            "runs_24h": TileValue(
                str(sum(profile["runs_24h"] for profile in profiles)), "launched from a profile"
            ),
        },
    )


def _mounts(document: Row) -> str:
    mounts = ", ".join(
        f"{mount['host_path']} \u2192 {mount['guest_path']} \u00b7 {mount['mode']}"
        for mount in document["mounts"]
    )
    return f"{mounts} \u00b7 workdir {document['workdir']}"


def _rule(rule: Row) -> str:
    parts = (rule.get("protocol"), rule.get("host") or rule.get("ip"))
    return " ".join(part for part in parts if part) or "any"


def _network(document: Row) -> str:
    allowed = [_rule(rule) for rule in document["allow"]]
    denied = [f"deny {_rule(rule)}" for rule in document["deny"]]
    return " \u00b7 ".join(allowed + denied)


def _mcp(document: Row) -> str:
    servers = document["servers"]
    names = sorted({server["credential"] for server in servers if server.get("credential")})
    bound = f"secrets {', '.join(names)} \u2014 values never shown" if names else "no secrets"
    return f"{_plural(len(servers), 'server')} \u00b7 {bound}"


def summary_of(policy: Row) -> str:
    document, kind = policy["document"], policy["kind"]
    if kind == "mount":
        return _mounts(document)
    if kind == "network":
        return _network(document)
    if kind == "shell":
        return " \u00b7 ".join(document["allow"])
    return _mcp(document)


@dataclass(frozen=True)
class PolicyLine:
    tag: str
    id: str
    summary: str


@dataclass(frozen=True)
class ProfileDetailRow:
    id: str
    name: str
    tone: Tone
    pill: str
    pill_tone: Tone
    meta: list[str]
    facts: list[Fact]
    policies: list[PolicyLine]
    open: list[RunRow]
    runs: list[RunRow]
    finished: int


def _pill(profile: Row) -> tuple[str, Tone]:
    if profile["active_runs"]:
        return "IN USE", "green"
    return ("IDLE", "grey") if profile["runs_total"] else ("UNUSED", "grey")


def profile_detail(
    profile: Row, runs: list[Row], policies: list[Row], now: int
) -> ProfileDetailRow:
    spec, runtime = profile["spec"], profile["spec"]["runtime"]
    known = {policy["id"]: policy for policy in policies}
    ordered = run_rows(
        sorted(runs, key=lambda run: (run["status"] in FINISHED, -run["seq"])), [], now
    )
    opened = [row for row in ordered if row.status not in FINISHED]
    changed = profile["updated_at"] != profile["created_at"]
    stamp = "updated" if changed else "created"
    meta = [
        f"{stamp} {format.ago(profile['updated_at'], now)}",
        f"{runtime['cpu']} cpu \u00b7 {runtime['memory_mib']} MiB \u00b7 {runtime['disk_gib']} GiB",
        _plural(profile["runs_total"], "run") if profile["runs_total"] else "no runs",
        f"{profile['active_runs']} open" if profile["active_runs"] else None,
    ]
    merge = spec["merge"]["policy"]
    lines = []
    for name, _ in POLICY_KINDS:
        policy_id = spec[name]["policy"]
        found = known.get(policy_id) if policy_id else None
        summary = summary_of(found) if found else CLOSED[name]
        lines.append(PolicyLine(TAGS[name], policy_id or "", summary))
    pill, pill_tone = _pill(profile)
    return ProfileDetailRow(
        id=profile["id"],
        name=profile["name"],
        tone="green" if profile["runs_total"] else "faint",
        pill=pill,
        pill_tone=pill_tone,
        meta=[part for part in meta if part],
        facts=[
            Fact("CPU", f"{runtime['cpu']} cpu"),
            Fact("Memory", f"{runtime['memory_mib']} MiB"),
            Fact("Disk", f"{runtime['disk_gib']} GiB"),
            Fact("Timeout", f"{spec['timeout']}s \u00b7 {format.fine(spec['timeout'])}"),
            Fact("Merge policy", f"{merge} \u00b7 {MERGE[merge][2]}"),
        ],
        policies=lines,
        open=opened,
        runs=ordered,
        finished=profile["runs_total"] - profile["active_runs"],
    )


@dataclass(frozen=True)
class GateField:
    name: str
    label: str
    options: list[Option]


@dataclass(frozen=True)
class Form:
    mode: Mode
    name: str = ""
    profile_id: str = ""
    source: str = ""
    values: dict[str, str] = field(default_factory=lambda: dict(NEW_PROFILE))

    @property
    def title(self) -> str:
        if self.mode == "edit":
            return f"Edit {self.name}"
        return f"Clone {self.source}" if self.mode == "clone" else "New profile"

    @property
    def action(self) -> str:
        return f"/profiles/{self.profile_id}/edit" if self.mode == "edit" else "/profiles/new"

    @property
    def submit(self) -> str:
        return "Save profile" if self.mode == "edit" else "Create profile"

    def spec(self) -> Row:
        return spec_of(self.values)


def new_form() -> Form:
    return Form("new")


def edit_form(profile: Row) -> Form:
    return Form("edit", profile["name"], profile["id"], values=values_of(profile["spec"]))


def clone_form(profile: Row) -> Form:
    values = values_of(profile["spec"])
    return Form("clone", f"{profile['name']}-copy", source=profile["name"], values=values)


def read_form(mode: Mode, fields: dict[str, str], profile: Row | None = None) -> Form:
    values = {name: fields.get(name, NEW_PROFILE[name]).strip() for name in NEW_PROFILE}
    if mode == "edit" and profile is not None:
        return Form("edit", profile["name"], profile["id"], values=values)
    name, source = fields.get("name", "").strip(), fields.get("source", "")
    return Form(mode, name, source=source, values=values)


def gate_fields(form: Form, policies: list[Row]) -> list[GateField]:
    gates = []
    for name, kind in POLICY_KINDS:
        chosen = form.values[name]
        options = [Option("", CLOSED[name], chosen == "")] + [
            Option(
                policy["id"],
                f"{short(policy['id'])} \u00b7 {policy_label(policy)}",
                policy["id"] == chosen,
            )
            for policy in policies
            if policy["kind"] == kind
        ]
        gates.append(GateField(name, GATES[name], options))
    return gates


def merge_options(form: Form) -> list[Option]:
    return [
        Option(mode, f"{mode} \u00b7 {note}", mode == form.values["merge"])
        for mode, (_, _, note) in MERGE.items()
    ]


# The same sentence the api answers with, so the popup reads alike before and after a race.
def busy_note(profile: Row, runs: list[Row]) -> str:
    named = " and ".join(
        f"#{run['seq']} {run['status']}" for run in runs if run["status"] not in FINISHED
    )
    used = named or "an active run"
    return f"profile {profile['name']} is used by {used}. Delete it once they finish."
