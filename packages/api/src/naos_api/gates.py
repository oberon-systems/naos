from typing import Any

from pydantic import BaseModel
from sqlmodel import Session, case, col, func, select
from sqlmodel.sql.expression import Select

from naos_api.models import AuditEvent, Policy, Run
from naos_api.policies import held_document
from naos_api.spec import PolicyKind

REFUSALS = 5
GROUPS = 200


class ModelRefusal(BaseModel):
    id: str
    at: int
    provider: str
    model: str
    category: str


class ModelGate(BaseModel):
    calls: int
    denied: int
    input_tokens: int
    output_tokens: int
    refusals: list[ModelRefusal]


# Counted from the audit on every read, so the answer is never a copy that can drift from it.
def model(session: Session, run_id: str) -> ModelGate:
    data = col(AuditEvent.data)
    calls = (col(AuditEvent.run_id) == run_id, col(AuditEvent.event) == "model_call")
    denied = (*calls, data["decision"].as_string() == "deny")
    count, spent_in, spent_out = session.exec(
        select(
            func.count(),
            func.sum(data["input_tokens"].as_integer()),
            func.sum(data["output_tokens"].as_integer()),
        )
        .select_from(AuditEvent)
        .where(*calls)
    ).one()
    refused = session.exec(select(func.count()).select_from(AuditEvent).where(*denied)).one()
    last = session.exec(
        select(AuditEvent).where(*denied).order_by(col(AuditEvent.seq).desc()).limit(REFUSALS)
    ).all()
    return ModelGate(
        calls=count,
        denied=refused,
        input_tokens=spent_in or 0,
        output_tokens=spent_out or 0,
        refusals=[
            ModelRefusal(
                id=event.id,
                at=event.at,
                provider=event.data["provider"],
                model=event.data["model"],
                category=event.data["category"],
            )
            for event in last
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


class McpGate(BaseModel):
    policy_id: str | None
    document: dict[str, Any] | None
    configured_at: int | None
    calls: int
    denied: int
    secret_reads: list[SecretReads]


def mcp(session: Session, run: Run) -> McpGate:
    data = col(AuditEvent.data)
    event = col(AuditEvent.event)
    mine = col(AuditEvent.run_id) == run.id
    count = select(func.count()).select_from(AuditEvent)
    calls = (mine, event == "mcp_call")
    name = data["name"].as_string()
    read = (mine, event == "secret_read", data["decision"].as_string() == "allow")
    reads = session.exec(select(name, func.count()).where(*read).group_by(name).order_by(name))
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
    )
