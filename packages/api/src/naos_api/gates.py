from pydantic import BaseModel
from sqlmodel import Session, col, func, select

from naos_api.models import AuditEvent

REFUSALS = 5


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
