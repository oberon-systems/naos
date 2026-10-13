from typing import Any

from pydantic import BaseModel
from sqlmodel import Session, case, col, func, select
from sqlmodel.sql.expression import Select

from naos_api.models import AuditEvent, Policy, Run, RunPolicy
from naos_api.policies import held_document
from naos_api.spec import PolicyKind

REFUSALS = 5
GROUPS = 200
MINUTES = 2000


class ModelRefusal(BaseModel):
    id: str
    at: int
    provider: str
    model: str
    category: str


class ModelGroup(BaseModel):
    provider: str
    model: str
    calls: int
    denied: int
    input_tokens: int
    output_tokens: int
    slowest_ms: int
    last_at: int


class ModelMinute(BaseModel):
    at: int
    provider: str
    requests: int
    denied: int
    input_tokens: int
    output_tokens: int


class ModelGate(BaseModel):
    policy_id: str | None
    document: dict[str, Any] | None
    configured_at: int | None
    calls: int
    denied: int
    input_tokens: int
    output_tokens: int
    refusals: list[ModelRefusal]
    groups_total: int
    groups: list[ModelGroup]
    minutes: list[ModelMinute]


# Counted from the audit on every read, so the answer is never a copy that can drift from it.
def model(session: Session, run: Run) -> ModelGate:
    data = col(AuditEvent.data)
    event = col(AuditEvent.event)
    mine = col(AuditEvent.run_id) == run.id
    calls = (mine, event == "model_call")
    refused = data["decision"].as_string() == "deny"
    provider, name = data["provider"].as_string(), data["model"].as_string()
    spent_in = func.coalesce(func.sum(data["input_tokens"].as_integer()), 0)
    spent_out = func.coalesce(func.sum(data["output_tokens"].as_integer()), 0)
    denied = func.sum(case((refused, 1), else_=0))
    totals = session.exec(select(func.count(), denied, spent_in, spent_out).where(*calls)).one()
    last = session.exec(
        select(AuditEvent)
        .where(*calls, refused)
        .order_by(col(AuditEvent.seq).desc())
        .limit(REFUSALS)
    ).all()
    groups: Select[tuple[str, str, int, int, int, int, int, int]] = Select(
        provider,
        name,
        func.count(),
        denied,
        spent_in,
        spent_out,
        func.max(data["duration_ms"].as_integer()),
        func.max(AuditEvent.at),
    )
    rows = session.exec(
        groups.where(*calls)
        .group_by(provider, name)
        .order_by(denied.desc(), func.count().desc(), provider, name)
        .limit(GROUPS)
    ).all()
    every = select(provider).where(*calls).group_by(provider, name)
    minute = col(AuditEvent.at) - col(AuditEvent.at) % 60
    per_minute: Select[tuple[int, str, int, int, int, int]] = Select(
        minute, provider, func.count(), denied, spent_in, spent_out
    )
    minutes = session.exec(
        per_minute.where(*calls)
        .group_by(minute, provider)
        .order_by(minute.desc(), provider.desc())
        .limit(MINUTES)
    ).all()
    configured = session.exec(
        select(func.max(AuditEvent.at)).where(mine, event == "model_policy_configured")
    ).one()
    return ModelGate(
        policy_id=run.model_policy_id,
        document=held_document(session, run, PolicyKind.MODEL),
        configured_at=configured,
        calls=totals[0],
        denied=totals[1] or 0,
        input_tokens=totals[2],
        output_tokens=totals[3],
        refusals=[
            ModelRefusal(
                id=row.id,
                at=row.at,
                provider=row.data["provider"],
                model=row.data["model"],
                category=row.data["category"],
            )
            for row in last
        ],
        groups_total=session.exec(select(func.count()).select_from(every.subquery())).one(),
        groups=[
            ModelGroup(
                provider=row[0],
                model=row[1],
                calls=row[2],
                denied=row[3],
                input_tokens=row[4],
                output_tokens=row[5],
                slowest_ms=row[6],
                last_at=row[7],
            )
            for row in rows
        ],
        minutes=[
            ModelMinute(
                at=row[0],
                provider=row[1],
                requests=row[2],
                denied=row[3],
                input_tokens=row[4],
                output_tokens=row[5],
            )
            for row in reversed(minutes)
        ],
    )


class NetworkHost(BaseModel):
    host: str
    protocol: str
    rule: str
    allowed: int
    denied: int
    last_at: int


class NetworkGate(BaseModel):
    policy_id: str | None
    document: dict[str, Any] | None
    configured_at: int | None
    allowed: int
    denied: int
    hosts_total: int
    hosts: list[NetworkHost]


# Grouped by what the gate decided on, so a host denied under one rule and allowed under another
# shows twice; denied hosts come first.
def network(session: Session, run: Run) -> NetworkGate:
    data = col(AuditEvent.data)
    event = col(AuditEvent.event)
    mine = col(AuditEvent.run_id) == run.id
    host, protocol, rule = (data[key].as_string() for key in ("host", "protocol", "rule"))
    allowed = func.sum(case((event == "network_allowed", 1), else_=0))
    denied = func.sum(case((event == "network_denied", 1), else_=0))
    decided = (mine, event.in_(("network_allowed", "network_denied")))
    last = func.max(AuditEvent.at)
    groups: Select[tuple[str, str, str, int, int, int]] = Select(
        host, protocol, rule, allowed, denied, last
    )
    rows = session.exec(
        groups.where(*decided)
        .group_by(host, protocol, rule)
        .order_by(denied.desc(), allowed.desc(), host, protocol, rule)
        .limit(GROUPS)
    ).all()
    totals = session.exec(select(allowed, denied).where(*decided)).one()
    every = select(host).where(*decided).group_by(host, protocol, rule)
    configured = session.exec(
        select(func.max(AuditEvent.at)).where(mine, event == "network_policy_configured")
    ).one()
    return NetworkGate(
        policy_id=run.network_policy_id,
        document=held_document(session, run, PolicyKind.NETWORK),
        configured_at=configured,
        allowed=totals[0] or 0,
        denied=totals[1] or 0,
        hosts_total=session.exec(select(func.count()).select_from(every.subquery())).one(),
        hosts=[
            NetworkHost(
                host=row[0],
                protocol=row[1],
                rule=row[2],
                allowed=row[3],
                denied=row[4],
                last_at=row[5],
            )
            for row in rows
        ],
    )


class ShellGroup(BaseModel):
    capability: str
    path: str
    allowed: int
    denied: int
    last_at: int


class ShellGate(BaseModel):
    policy_id: str | None
    document: dict[str, Any] | None
    configured_at: int | None
    roots: list[str]
    allowed: int
    denied: int
    called: list[str]
    groups_total: int
    groups: list[ShellGroup]


# Only the guest side of a mount is shown: a denial never names a host path, so neither does this.
def _roots(session: Session, run: Run) -> list[str]:
    policy = session.get(Policy, run.mount_policy_id) if run.mount_policy_id else None
    return [mount["guest_path"] for mount in policy.document["mounts"]] if policy else []


def shell(session: Session, run: Run) -> ShellGate:
    data = col(AuditEvent.data)
    event = col(AuditEvent.event)
    mine = col(AuditEvent.run_id) == run.id
    capability, path = (data[key].as_string() for key in ("capability", "path"))
    allowed = func.sum(case((event == "shell_allowed", 1), else_=0))
    denied = func.sum(case((event == "shell_denied", 1), else_=0))
    decided = (mine, event.in_(("shell_allowed", "shell_denied")))
    last = func.max(AuditEvent.at)
    groups: Select[tuple[str, str, int, int, int]] = Select(capability, path, allowed, denied, last)
    rows = session.exec(
        groups.where(*decided)
        .group_by(capability, path)
        .order_by(denied.desc(), capability, allowed.desc(), path)
        .limit(GROUPS)
    ).all()
    totals = session.exec(select(allowed, denied).where(*decided)).one()
    every = select(capability).where(*decided).group_by(capability, path)
    called = select(capability).where(*decided).group_by(capability).order_by(capability)
    configured = session.exec(
        select(func.max(AuditEvent.at)).where(mine, event == "shell_policy_configured")
    ).one()
    return ShellGate(
        policy_id=run.shell_policy_id,
        document=held_document(session, run, PolicyKind.SHELL),
        configured_at=configured,
        roots=_roots(session, run),
        allowed=totals[0] or 0,
        denied=totals[1] or 0,
        called=list(session.exec(called).all()),
        groups_total=session.exec(select(func.count()).select_from(every.subquery())).one(),
        groups=[
            ShellGroup(
                capability=row[0], path=row[1], allowed=row[2], denied=row[3], last_at=row[4]
            )
            for row in rows
        ],
    )


class SecretReads(BaseModel):
    name: str
    reads: int


class SecretUse(BaseModel):
    name: str
    reads: int
    refused: int
    last_at: int


class McpGroup(BaseModel):
    server: str
    tool: str
    resource: str
    calls: int
    denied: int
    slowest_ms: int
    last_at: int


class McpMinute(BaseModel):
    at: int
    server: str
    calls: int
    denied: int


class McpBudget(BaseModel):
    rule: int
    spent: int
    peak: int
    refused: int
    since: int


class McpGate(BaseModel):
    policy_id: str | None
    document: dict[str, Any] | None
    configured_at: int | None
    calls: int
    denied: int
    secret_reads: list[SecretReads]
    secrets: list[SecretUse]
    groups_total: int
    groups: list[McpGroup]
    minutes: list[McpMinute]
    budgets: list[McpBudget]


def _documents(session: Session, run: Run) -> list[tuple[int, list[dict[str, Any]]]]:
    created = run.spec.get("mcp", {}).get("policy")
    policy = session.get(Policy, created) if created else None
    held = [(run.created_at, policy.document["rules"] if policy else [])]
    changes = select(RunPolicy).where(
        col(RunPolicy.run_id) == run.id, col(RunPolicy.kind) == PolicyKind.MCP
    )
    configured = session.exec(
        select(AuditEvent.at).where(
            col(AuditEvent.run_id) == run.id, col(AuditEvent.event) == "mcp_policy_configured"
        )
    ).all()
    for change in session.exec(changes.order_by(col(RunPolicy.seq))).all():
        # A call belongs to the document the broker held, which it takes when it says so.
        taken = [at for at in configured if at >= change.created_at] or [change.created_at]
        held.append((min(taken), (change.document or {}).get("rules", [])))
    return held


# A rule kept across a change keeps what it spent, so the count walks back while it stays.
def _budgets(session: Session, run: Run) -> list[McpBudget]:
    held = _documents(session, run)
    data = col(AuditEvent.data)
    at = col(AuditEvent.at)
    whens = [(at >= start, index) for index, (start, _) in reversed(list(enumerate(held)))]
    segment = case(*whens[:-1], else_=0) if len(held) > 1 else None
    refused = data["category"].as_string() == "denied"
    rule = data["rule"].as_string()
    minute = at - at % 60
    keys = (rule, minute) if segment is None else (segment, rule, minute)
    per_minute: Select[tuple[Any, ...]] = Select(
        *keys, func.sum(case((refused, 0), else_=1)), func.sum(case((refused, 1), else_=0))
    )
    found = session.exec(
        per_minute.where(
            col(AuditEvent.run_id) == run.id, col(AuditEvent.event) == "mcp_call", rule != "none"
        ).group_by(*keys)
    ).all()
    rows = [tuple(row) if segment is not None else (0, *row) for row in found]
    counted: dict[tuple[int, str], list[tuple[int, int, int]]] = {}
    for row in rows:
        counted.setdefault((row[0], row[1]), []).append((row[2], row[3], row[4]))
    last = len(held) - 1
    budgets = []
    for index, kept in enumerate(held[last][1]):
        limited = kept.get("max_calls") or kept.get("max_calls_per_minute")
        if kept["effect"] != "allow" or not limited:
            continue
        spent: dict[int, int] = {}
        refusals = 0
        first = last
        while first >= 0 and kept in held[first][1]:
            position = str(held[first][1].index(kept))
            for at_minute, charged, denied in counted.get((first, position), []):
                spent[at_minute] = spent.get(at_minute, 0) + charged
                refusals += denied
            first -= 1
        budgets.append(
            McpBudget(
                rule=index,
                spent=sum(spent.values()),
                peak=max(spent.values(), default=0),
                refused=refusals,
                since=held[first + 1][0],
            )
        )
    return budgets


def mcp(session: Session, run: Run) -> McpGate:
    data = col(AuditEvent.data)
    event = col(AuditEvent.event)
    mine = col(AuditEvent.run_id) == run.id
    count = select(func.count()).select_from(AuditEvent)
    calls = (mine, event == "mcp_call")
    denied = func.sum(case((data["decision"].as_string() == "deny", 1), else_=0))
    name = data["name"].as_string()
    read = (mine, event == "secret_read", data["decision"].as_string() == "allow")
    reads = session.exec(select(name, func.count()).where(*read).group_by(name).order_by(name))
    allowed_read = func.sum(case((data["decision"].as_string() == "allow", 1), else_=0))
    used: Select[tuple[str, int, int, int]] = Select(
        name, allowed_read, denied, func.max(AuditEvent.at)
    )
    secrets = session.exec(
        used.where(mine, event == "secret_read").group_by(name).order_by(name).limit(GROUPS)
    ).all()
    server, tool, resource = (data[key].as_string() for key in ("server", "tool", "resource"))
    groups: Select[tuple[str, str, str, int, int, int, int]] = Select(
        server,
        tool,
        resource,
        func.count(),
        denied,
        func.max(data["duration_ms"].as_integer()),
        func.max(AuditEvent.at),
    )
    rows = session.exec(
        groups.where(*calls)
        .group_by(server, tool, resource)
        .order_by(denied.desc(), func.count().desc(), server, tool, resource)
        .limit(GROUPS)
    ).all()
    every = select(server).where(*calls).group_by(server, tool, resource)
    minute = col(AuditEvent.at) - col(AuditEvent.at) % 60
    per_minute: Select[tuple[int, str, int, int]] = Select(minute, server, func.count(), denied)
    minutes = session.exec(
        per_minute.where(*calls)
        .group_by(minute, server)
        .order_by(minute.desc(), server.desc())
        .limit(MINUTES)
    ).all()
    configured = session.exec(
        select(func.max(AuditEvent.at)).where(mine, event == "mcp_policy_configured")
    ).one()
    return McpGate(
        policy_id=run.mcp_policy_id,
        document=run.mcp_document,
        configured_at=configured,
        calls=session.exec(count.where(*calls)).one(),
        denied=session.exec(count.where(*calls, data["decision"].as_string() == "deny")).one(),
        secret_reads=[SecretReads(name=row[0], reads=row[1]) for row in reads.all()],
        secrets=[
            SecretUse(name=row[0], reads=row[1], refused=row[2], last_at=row[3]) for row in secrets
        ],
        groups_total=session.exec(select(func.count()).select_from(every.subquery())).one(),
        groups=[
            McpGroup(
                server=row[0],
                tool=row[1],
                resource=row[2],
                calls=row[3],
                denied=row[4],
                slowest_ms=row[5],
                last_at=row[6],
            )
            for row in rows
        ],
        minutes=[
            McpMinute(at=row[0], server=row[1], calls=row[2], denied=row[3])
            for row in reversed(minutes)
        ],
        budgets=_budgets(session, run),
    )
