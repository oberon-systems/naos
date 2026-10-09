from typing import Any

from pydantic import BaseModel
from sqlmodel import Session, case, col, func, select
from sqlmodel.sql.expression import Select

from naos_api.models import AuditEvent, Run
from naos_api.policies import held_document
from naos_api.spec import PolicyKind

REFUSALS = 5
HOSTS = 200


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
        .limit(HOSTS)
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
