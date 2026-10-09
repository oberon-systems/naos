from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from naos_web.client import Row
from naos_web.new_run import FormError, short
from naos_web.pages import STATUS_TONE, Tone
from naos_web.policies import McpRule, PolicyForm, from_document, granted, mcp_rules, read_form

Source = Literal["edit", "stored"]
Keep = Literal["temporary", "save"]
BUILT_IN = ("shell", "network", "secrets")


def _plural(count: int, word: str, words: str = "") -> str:
    return f"{count} {word}" if count == 1 else f"{count} {words or word + 's'}"


def _clock(at: int) -> str:
    return datetime.fromtimestamp(at, UTC).strftime("%H:%M:%S")


def label(policy_id: str | None, names: dict[str, str]) -> str:
    if policy_id is None:
        return "temporary"
    return names.get(policy_id) or short(policy_id)


def allowed(rules: list[Row]) -> list[str]:
    return sorted({rule["server"] for rule in rules if rule["effect"] == "allow"})


def changes(history: list[Row]) -> list[Row]:
    return [change for change in history if change["kind"] == "mcp"]


# The spec keeps the policy a Run was created with; the last change names what it holds now.
def held_id(run: Row) -> str | None:
    made = changes(run["policy_history"])
    created: str | None = run["spec"]["mcp"]["policy"]
    return made[-1]["policy_id"] if made else created


@dataclass(frozen=True)
class HeldServer:
    name: str
    external: bool
    url: str
    credential: str
    limits: str
    added: str


@dataclass(frozen=True)
class Change:
    seq: str
    time: str
    before: str
    after: str
    after_id: str | None
    note: str
    now: bool


@dataclass(frozen=True)
class McpCard:
    policy_id: str | None
    policy: str
    edited_from: str
    created_id: str | None
    created: str
    rules: list[McpRule]
    rules_line: str
    calls: str
    grants: list[str]
    reads: str
    applied: str
    servers: list[HeldServer]
    history: list[Change]
    start: str
    changeable: bool


def _summary(before: list[Row], after: list[Row]) -> str:
    gone, came = set(allowed(before)), set(allowed(after))
    parts = [f"added server {name}" for name in sorted(came - gone)]
    parts += [f"took away {name}" for name in sorted(gone - came)]
    delta = len(after) - len(before)
    if delta:
        parts.append(f"{'+' if delta > 0 else ''}{_plural(delta, 'rule')}")
    return " \u00b7 ".join(parts) or "same servers"


def _servers(document: Row, history: list[Row]) -> list[HeldServer]:
    added: dict[str, str] = {}
    for before, after in zip(history, history[1:], strict=False):
        held = {server["name"] for server in (before["document"] or {}).get("servers", [])}
        names = {server["name"] for server in (after["document"] or {}).get("servers", [])}
        for name in names - held:
            added[name] = _clock(after["created_at"])[:5]
    servers = [
        HeldServer(
            name=server["name"],
            external=True,
            url=server["url"],
            credential=server["credential"] or "",
            limits=f"{server['timeout_seconds']}s \u00b7 {server['max_calls_per_minute']}/min",
            added=added.get(server["name"], ""),
        )
        for server in document["servers"]
    ]
    servers += [
        HeldServer(name, False, "served by the runner", "", "", "")
        for name in allowed(document["rules"])
        if name in BUILT_IN
    ]
    return servers


def _reads(gate: Row, rules: list[Row]) -> str:
    budgets = {
        rule["arguments"]["name"]["equals"]: rule.get("max_calls")
        for rule in rules
        if rule["server"] == "secrets" and rule["effect"] == "allow" and rule.get("tool") == "get"
    }
    reads = gate["secret_reads"]
    parts = [
        f"{row['name']} {row['reads']} of {budgets[row['name']]}"
        if budgets.get(row["name"])
        else f"{row['name']} {row['reads']}"
        for row in reads
    ]
    return " \u00b7 ".join([str(sum(row["reads"] for row in reads)), *parts])


def _applied(gate: Row, history: list[Row]) -> str:
    configured = gate["configured_at"]
    if configured is None:
        return "not yet"
    if not history or configured < history[-1]["created_at"]:
        return _clock(configured)
    late = configured - history[-1]["created_at"]
    return f"{_clock(configured)} \u00b7 {late} s after the change"


def card(run: Row, gate: Row, policies: dict[str, Row]) -> McpCard:
    names = {policy_id: policy.get("name") or "" for policy_id, policy in policies.items()}
    document = run["mcp_document"]
    rules = document["rules"]
    history = changes(run["policy_history"])
    held = held_id(run)
    created = run["spec"]["mcp"]["policy"]
    previous = history[-1]["previous_id"] if history and held is None else None
    documents = [
        (policies.get(created or "", {}).get("document") or {}).get("rules", []),
        *[(change["document"] or {}).get("rules", []) for change in history],
    ]
    denies = sum(rule["effect"] == "deny" for rule in rules)
    return McpCard(
        policy_id=held,
        policy=label(held, names),
        edited_from=label(previous, names) if previous else "",
        created_id=created,
        created=created or "none",
        rules=mcp_rules(document),
        rules_line=f"{len(rules)} \u00b7 {denies} deny",
        calls=f"{gate['calls']} \u00b7 {gate['denied']} denied",
        grants=granted(document),
        reads=_reads(gate, rules) if gate["secret_reads"] else "none",
        applied=_applied(gate, history),
        servers=_servers(document, history),
        history=[
            Change(
                seq=f"seq {change['seq']}",
                time=_clock(change["created_at"]),
                before=label(change["previous_id"], names),
                after=label(change["policy_id"], names),
                after_id=change["policy_id"],
                note=_summary(documents[index], documents[index + 1]),
                now=index == len(history) - 1,
            )
            for index, change in reversed(list(enumerate(history)))
        ],
        start=_clock(run["created_at"]),
        changeable=run["status"] == "STARTED",
    )


@dataclass(frozen=True)
class Choice:
    id: str
    label: str
    note: str
    held: bool
    picked: bool


@dataclass(frozen=True)
class Gets:
    servers: list[str]
    taken: list[str]
    grants: list[str]
    dropped: list[str]


def gets(held: list[Row], rules: list[Row]) -> Gets:
    before, after = set(allowed(held)), allowed(rules)
    was, now = set(granted({"rules": held})), granted({"rules": rules})
    return Gets(
        servers=after,
        taken=sorted(before - set(after)),
        grants=now,
        dropped=sorted(was - set(now)),
    )


def _choice(policy: Row, held: str | None, picked: str, names: dict[str, str]) -> Choice:
    rules = policy["document"]["rules"]
    note = _plural(len(rules), "rule")
    secrets = granted(policy["document"])
    note += f" \u00b7 {_plural(len(secrets), 'secret')}" if secrets else " \u00b7 no secrets"
    return Choice(
        id=policy["id"],
        label=names.get(policy["id"]) or "unnamed",
        note=f"holds now \u00b7 {note}" if policy["id"] == held else note,
        held=policy["id"] == held,
        picked=policy["id"] == picked,
    )


def stored_choices(policies: list[Row], held: str | None, picked: str, query: str) -> list[Choice]:
    names = {policy["id"]: policy.get("name") or "" for policy in policies}
    needle = query.strip().lower()
    shown = [
        policy
        for policy in policies
        if any(
            needle in word.lower() for word in (policy["id"], policy["digest"], names[policy["id"]])
        )
    ]
    return [_choice(policy, held, picked, names) for policy in shown]


def edit_form(document: Row) -> PolicyForm:
    return from_document({"kind": "mcp", "document": {"rules": document["rules"]}})


def read_dialog(fields: dict[str, str]) -> tuple[PolicyForm, int, Keep, str]:
    form = read_form(fields | {"kind": "mcp"})
    held = int(fields.get("held", "0") or 0)
    folded = 0 if fields.get("unfolded") or fields.get("step") == "unfold" else held
    keep: Keep = "save" if fields.get("keep") == "save" else "temporary"
    return form, folded, keep, fields.get("save_name", "").strip()


@dataclass(frozen=True)
class Added:
    name: str
    note: str


@dataclass(frozen=True)
class Confirm:
    run_id: str
    seq: int
    status: str
    tone: Tone
    before: str
    after: str
    after_note: str
    added: list[Added]
    removed: list[str]
    grants: list[Added]
    fields: list[tuple[str, str]]
    warning: str


def _limits(rules: list[Row], server: str) -> str:
    mine = [rule for rule in rules if rule["server"] == server]
    budgets = sorted(
        {
            f"{rule['max_calls_per_minute']} calls/min"
            for rule in mine
            if rule.get("max_calls_per_minute")
        }
    )
    return " \u00b7 ".join([_plural(len(mine), "rule"), *budgets])


def confirm(
    run: Row,
    held_rules: list[Row],
    rules: list[Row],
    before: str,
    after: str,
    after_note: str,
    fields: list[tuple[str, str]],
) -> Confirm:
    diff = gets(held_rules, rules)
    added = sorted(set(diff.servers) - set(allowed(held_rules)))
    was = set(granted({"rules": held_rules}))
    reads = {
        rule["arguments"]["name"]["equals"]: rule.get("max_calls")
        for rule in rules
        if rule["server"] == "secrets" and rule["effect"] == "allow" and rule.get("tool") == "get"
    }
    grants = []
    for name in diff.grants:
        budget = reads[name]
        if name not in was:
            grants.append(Added(name, _plural(budget, "read") if budget else "no read budget"))
    reached = [*(f"call {name}" for name in added), *(f"read {grant.name}" for grant in grants)]
    return Confirm(
        run_id=run["id"],
        seq=run["seq"],
        status=run["status"],
        tone=STATUS_TONE[run["status"]],
        before=before,
        after=after,
        after_note=after_note,
        added=[Added(name, _limits(rules, name)) for name in added],
        removed=diff.taken,
        grants=grants,
        fields=fields,
        warning=(
            f"The agent can {' and '.join(reached)} from its next call on. A value it reads is "
            "the agent's from then on and cannot be taken back."
            if reached
            else ""
        ),
    )


def change_body(fields: dict[str, str]) -> Row:
    if fields.get("source") == "stored":
        picked = fields.get("policy_id", "")
        if not picked:
            raise FormError("pick a stored mcp policy")
        return {"kind": "mcp", "policy_id": picked}
    form, _, keep, name = read_dialog(fields)
    body: Row = {"kind": "mcp", "document": form.document()}
    if keep == "save":
        body["save"] = True
        if name:
            body["name"] = name
    return body
