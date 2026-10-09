from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from naos_web import format
from naos_web.client import Row
from naos_web.new_run import FormError, short
from naos_web.pages import STATUS_TONE, Summary, TileValue, Tone
from naos_web.policies import granted
from naos_web.rows import Fact

State = Literal["all", "external", "built-in", "disabled", "unused"]
BUILT_IN_LIMITS = {
    "shell": "set by the shell policy",
    "network": "set by the network policy",
    "secrets": "per rule of a policy",
}
DEFAULT_TIMEOUT = "30"
DEFAULT_CALLS = "60"
ENTRY = ("url", "credential", "timeout_seconds", "max_calls_per_minute")


def _plural(count: int, word: str, words: str = "") -> str:
    return f"{count} {word}" if count == 1 else f"{count} {words or word + 's'}"


def _stamp(at: int, pattern: str = "%Y-%m-%d %H:%M") -> str:
    return datetime.fromtimestamp(at, UTC).strftime(pattern)


def external(server: Row) -> bool:
    return bool(server["kind"] == "external")


def _unused(server: Row) -> bool:
    return not server["policies"] and not server["runs"]


def _matches(server: Row, needle: str, names: dict[str, str]) -> bool:
    words = [server["name"], server.get("url") or "", server.get("credential") or ""]
    words += server["policies"] + [names.get(policy_id, "") for policy_id in server["policies"]]
    return any(needle in word.lower() for word in words)


def pick(servers: list[Row], state: State, query: str, names: dict[str, str]) -> list[Row]:
    shown = {
        "all": servers,
        "external": [server for server in servers if external(server)],
        "built-in": [server for server in servers if not external(server)],
        "disabled": [server for server in servers if server.get("disabled_at")],
        "unused": [server for server in servers if _unused(server)],
    }[state]
    needle = query.strip().lower()
    return [server for server in shown if _matches(server, needle, names)] if needle else shown


def shelf(servers: list[Row]) -> Summary:
    externals = sum(external(server) for server in servers)
    disabled = sum(bool(server.get("disabled_at")) for server in servers)
    policies = {policy_id for server in servers for policy_id in server["policies"]}
    runs = {run["run_id"] for server in servers for run in server["runs"]}
    return Summary(
        subtitle=(
            f"{_plural(len(servers), 'server')} \u00b7 {externals} external \u00b7 "
            f"named by {_plural(len(policies), 'policy', 'policies')} \u00b7 "
            f"held by {_plural(len(runs), 'run')}"
        ),
        values={
            "servers": TileValue(
                str(len(servers)),
                f"{externals} external \u00b7 {len(servers) - externals} built-in",
            ),
            "enabled": TileValue(
                str(len(servers) - disabled),
                f"{disabled} disabled" if disabled else "none disabled",
            ),
            "named": TileValue(str(len(policies)), "mcp policies with a rule on one"),
            "held": TileValue(str(len(runs)), "open Runs with a copy"),
        },
    )


def secret_state(secret: Row | None, now: int) -> str:
    if secret is None:
        return "not stored"
    expires = secret["expires_at"]
    if expires is None:
        return "valid \u00b7 no expiry"
    if expires <= now:
        return "expired"
    return f"{secret['state']} \u00b7 expires in {format.coarse(expires - now)}"


def policy_label(policy_id: str, names: dict[str, str]) -> str:
    return names.get(policy_id) or short(policy_id)


def _pill(server: Row) -> tuple[str, Tone]:
    if not external(server):
        return "BUILT-IN", "grey"
    return ("DISABLED", "red") if server.get("disabled_at") else ("EXTERNAL", "blue")


def _statuses(runs: list[Row]) -> str:
    counts: dict[str, int] = {}
    for run in runs:
        counts[run["status"].lower()] = counts.get(run["status"].lower(), 0) + 1
    return " \u00b7 ".join(f"{count} {status}" for status, count in counts.items())


@dataclass(frozen=True)
class RunRef:
    id: str
    seq: int
    status: str
    tone: Tone
    line: str = ""
    note: str = ""


@dataclass(frozen=True)
class PolicyRef:
    id: str
    label: str
    note: str = ""
    line: str = ""


@dataclass(frozen=True)
class ServerRow:
    name: str
    id: str
    external: bool
    tone: Tone
    pill: str
    pill_tone: Tone
    kind_note: str
    url: str
    url_note: str
    credential: str
    credential_note: str
    limits: str
    limits_note: str
    policies: list[PolicyRef]
    more: int
    policies_note: str
    runs: list[RunRef]
    runs_note: str


def _run(run: Row, line: str = "", note: str = "") -> RunRef:
    return RunRef(run["run_id"], run["seq"], run["status"], STATUS_TONE[run["status"]], line, note)


def _granted(policies: dict[str, Row]) -> int:
    return len({name for policy in policies.values() for name in granted(policy["document"])})


def _policies_note(server: Row, policies: dict[str, Row]) -> str:
    count = _plural(len(server["policies"]), "policy", "policies")
    if not server["policies"]:
        return "no policy names it"
    if server.get("disabled_at"):
        return f"{count} \u00b7 cannot start a Run"
    if server["name"] == "secrets":
        named = {policy_id: policies[policy_id] for policy_id in server["policies"]}
        return f"{count} \u00b7 {_plural(_granted(named), 'secret')} granted"
    return count


def server_rows(
    servers: list[Row], policies: dict[str, Row], secrets: dict[str, Row], now: int
) -> list[ServerRow]:
    names = {policy_id: policy.get("name") or "" for policy_id, policy in policies.items()}
    rows = []
    for server in servers:
        pill, pill_tone = _pill(server)
        named = server["policies"]
        refs = [PolicyRef(named[0], policy_label(named[0], names))] if named else []
        if external(server):
            credential = server.get("credential") or ""
            disabled = server.get("disabled_at")
            kind_note = (
                f"external \u00b7 since {format.coarse(max(now - disabled, 0))}"
                if disabled
                else f"enabled \u00b7 {format.ago(server['created_at'], now)}"
            )
            url_note = "https \u00b7 Streamable HTTP"
            if disabled:
                url_note = "new Runs naming it are refused"
            limits = f"{server['timeout_seconds']}s \u00b7 {server['max_calls_per_minute']}/min"
            credential_note = (
                secret_state(secrets.get(credential), now)
                if credential
                else "calls go without a header"
            )
            limits_note = "timeout \u00b7 calls"
        else:
            credential, kind_note, url_note = "", "always on", "served by the runner"
            limits, limits_note = format.DASH, BUILT_IN_LIMITS.get(server["name"], "")
            secrets_server = server["name"] == "secrets"
            credential_note = "grants are per policy" if secrets_server else "needs none"
        rows.append(
            ServerRow(
                name=server["name"],
                id=short(server["id"]) if server.get("id") else "built-in",
                external=external(server),
                tone="faint" if server.get("disabled_at") or _unused(server) else "green",
                pill=pill,
                pill_tone=pill_tone,
                kind_note=kind_note,
                url=server.get("url") or format.DASH,
                url_note=url_note,
                credential=credential,
                credential_note=credential_note,
                limits=limits,
                limits_note=limits_note,
                policies=refs,
                more=max(len(named) - 1, 0),
                policies_note=_policies_note(server, policies),
                runs=[_run(run) for run in server["runs"]],
                runs_note=_statuses(server["runs"]) if server["runs"] else "none open",
            )
        )
    return rows


@dataclass(frozen=True)
class LastFailure:
    id: str
    category: str


@dataclass(frozen=True)
class ServerDetail:
    name: str
    id: str
    external: bool
    disabled: bool
    tone: Tone
    pill: str
    pill_tone: Tone
    meta: list[str]
    entry: list[Fact]
    credential: str
    credential_note: str
    policies: list[PolicyRef]
    runs: list[RunRef]
    calls: list[Fact]
    failure: LastFailure | None


def _rule_line(rule: Row) -> str:
    target = rule.get("tool") or rule.get("resource") or format.DASH
    return target if rule["effect"] == "allow" else f"deny {target}"


def _named(server: Row, policies: dict[str, Row], names: dict[str, str]) -> list[PolicyRef]:
    refs = []
    for policy_id in server["policies"]:
        policy = policies.get(policy_id)
        if policy is None:
            refs.append(PolicyRef(policy_id, short(policy_id)))
            continue
        rules = [r for r in policy["document"]["rules"] if r["server"] == server["name"]]
        refs.append(
            PolicyRef(
                id=policy_id,
                label=policy_label(policy_id, names),
                note=policy_id if names.get(policy_id) else "unnamed",
                line=f"{_plural(len(rules), 'rule')} on {server['name']} \u00b7 "
                + " \u00b7 ".join(_rule_line(rule) for rule in rules),
            )
        )
    return refs


def _held(server: Row) -> list[RunRef]:
    if not external(server):
        return [_run(run, "held through a rule") for run in server["runs"]]
    return [
        _run(run, "current entry", "as the registry holds it")
        if run["current"]
        else _run(run, "an older entry", "copied before an edit")
        for run in server["runs"]
    ]


def _last_call(calls: Row) -> str:
    last = calls["last"]
    if last is None:
        return "none yet"
    target = last["tool"] or last["resource"]
    return f"{_stamp(last['at'], '%H:%M:%S')} \u00b7 {target} \u00b7 {last['decision']}"


def _updated(server: Row, changed: Row | None) -> str:
    if server["updated_at"] == server["created_at"]:
        return "never"
    fields = changed["data"]["fields"] if changed else []
    note = f" \u00b7 {', '.join(field.removesuffix('_seconds') for field in fields)} changed"
    return _stamp(server["updated_at"]) + (note if fields else "")


def server_detail(
    server: Row,
    policies: dict[str, Row],
    secrets: dict[str, Row],
    changed: Row | None,
    now: int,
) -> ServerDetail:
    names = {policy_id: policy.get("name") or "" for policy_id, policy in policies.items()}
    pill, pill_tone = _pill(server)
    calls = server["calls"]
    failure = calls["last_failure"]
    runs = _plural(len(server["runs"]), "run")
    meta = [
        f"{_plural(len(server['policies']), 'policy', 'policies')} \u00b7 {runs} open "
        f"\u00b7 tools appear as {server['name']}__<tool>"
    ]
    credential = server.get("credential") or ""
    if external(server):
        meta = [f"registered {format.ago(server['created_at'], now)}", server["id"], *meta]
        entry = [
            Fact("Url", server["url"], mono=True),
            Fact("Timeout", f"{server['timeout_seconds']} s per call"),
            Fact(
                "Calls per minute",
                f"{server['max_calls_per_minute']} \u00b7 tools and resource reads",
            ),
            Fact("Registered", f"{_stamp(server['created_at'])} \u00b7 by operator"),
            Fact("Updated", _updated(server, changed)),
        ]
        if server.get("disabled_at"):
            entry.append(Fact("Disabled", _stamp(server["disabled_at"])))
    else:
        entry = [
            Fact("Served by", "the runner, for the Run alone"),
            Fact("Limits", BUILT_IN_LIMITS.get(server["name"], format.DASH)),
        ]
    return ServerDetail(
        name=server["name"],
        id=server.get("id") or "",
        external=external(server),
        disabled=bool(server.get("disabled_at")),
        tone="faint" if server.get("disabled_at") or _unused(server) else "green",
        pill=pill,
        pill_tone=pill_tone,
        meta=meta,
        entry=entry,
        credential=credential,
        credential_note=secret_state(secrets.get(credential), now) if credential else "",
        policies=_named(server, policies, names),
        runs=_held(server),
        calls=[
            Fact("Calls today", f"{calls['today']} \u00b7 {calls['denied_today']} denied"),
            Fact("Last call", _last_call(calls)),
        ],
        failure=LastFailure(failure["id"], failure["category"]) if failure else None,
    )


@dataclass(frozen=True)
class ServerForm:
    name: str = ""
    url: str = ""
    credential: str = ""
    timeout: str = DEFAULT_TIMEOUT
    calls: str = DEFAULT_CALLS

    def body(self) -> Row:
        return {
            "name": self.name,
            "url": self.url,
            "credential": self.credential or None,
            "timeout_seconds": _whole(self.timeout, "timeout"),
            "max_calls_per_minute": _whole(self.calls, "calls per minute"),
        }

    # Only what changed goes up, so the audit names the fields the operator touched.
    def patch(self, server: Row) -> Row:
        body = self.body()
        return {key: body[key] for key in ENTRY if body[key] != server.get(key)}


def _whole(value: str, label: str) -> int:
    if not value.strip().isdigit():
        raise FormError(f"{label} must be a whole number")
    return int(value)


def read_form(fields: dict[str, str]) -> ServerForm:
    return ServerForm(
        name=fields.get("name", "").strip(),
        url=fields.get("url", "").strip(),
        credential=fields.get("credential", "").strip(),
        timeout=fields.get("timeout", DEFAULT_TIMEOUT).strip(),
        calls=fields.get("calls", DEFAULT_CALLS).strip(),
    )


def from_server(server: Row) -> ServerForm:
    return ServerForm(
        name=server["name"],
        url=server["url"],
        credential=server.get("credential") or "",
        timeout=str(server["timeout_seconds"]),
        calls=str(server["max_calls_per_minute"]),
    )


@dataclass(frozen=True)
class Credential:
    name: str
    note: str
    selected: bool


def credentials(secrets: list[Row], chosen: str, now: int) -> list[Credential]:
    known = {secret["name"]: secret for secret in secrets}
    options = [Credential("", "No credential", not chosen)]
    options += [
        Credential(name, secret_state(secret, now), name == chosen)
        for name, secret in known.items()
    ]
    if chosen and chosen not in known:
        options.append(Credential(chosen, "not stored", True))
    return options


@dataclass(frozen=True)
class Disable:
    pending: list[RunRef]
    started: list[RunRef]
    policies: list[PolicyRef]


def disable(server: Row, policies: dict[str, Row]) -> Disable:
    names = {policy_id: policy.get("name") or "" for policy_id, policy in policies.items()}
    return Disable(
        pending=[
            _run(run, "loses " + server["name"] + ", keeps its rules")
            for run in server["runs"]
            if run["status"] == "PENDING"
        ],
        started=[
            _run(run, "keeps its entry") for run in server["runs"] if run["status"] != "PENDING"
        ],
        policies=[
            PolicyRef(policy_id, policy_label(policy_id, names), short(policy_id))
            for policy_id in server["policies"]
        ],
    )
