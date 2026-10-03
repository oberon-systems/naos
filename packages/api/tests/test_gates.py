from collections.abc import Callable
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session

from naos_api.models import AuditEvent

CreateRun = Callable[[str], str]


def _call(session: Session, run_id: str, at: int, **fields: Any) -> str:
    data = {
        "provider": "alpha",
        "model": "alpha-mini",
        "input_tokens": 0,
        "output_tokens": 0,
        "decision": "allow",
        "duration_ms": 5,
        "category": "none",
    }
    event = AuditEvent(
        id=f"evt_{uuid4().hex}",
        at=at,
        received_at=at,
        source="runner",
        event="model_call",
        actor="runner",
        run_id=run_id,
        data=data | fields,
    )
    session.add(event)
    session.commit()
    return event.id


def _gate(client: TestClient, run_id: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/runs/{run_id}/gates/model")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_a_run_without_calls_has_spent_nothing(client: TestClient, create_run: CreateRun) -> None:
    assert _gate(client, create_run("key-1")) == {
        "calls": 0,
        "denied": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "refusals": [],
    }


def test_calls_are_summed_for_their_run_only(
    client: TestClient, session: Session, create_run: CreateRun
) -> None:
    run_id, other = create_run("key-1"), create_run("key-2")
    _call(session, run_id, 10, input_tokens=120, output_tokens=30)
    _call(session, run_id, 11, input_tokens=80, output_tokens=5)
    refused = _call(session, run_id, 12, decision="deny", category="budget")
    _call(session, other, 13, input_tokens=999, output_tokens=999)

    gate = _gate(client, run_id)

    assert (gate["calls"], gate["denied"]) == (3, 1)
    assert (gate["input_tokens"], gate["output_tokens"]) == (200, 35)
    assert gate["refusals"] == [
        {"id": refused, "at": 12, "provider": "alpha", "model": "alpha-mini", "category": "budget"}
    ]


def test_only_the_last_refusals_are_listed_newest_first(
    client: TestClient, session: Session, create_run: CreateRun
) -> None:
    run_id = create_run("key-1")
    refused = [
        _call(session, run_id, at, decision="deny", category="rate", provider="beta")
        for at in range(7)
    ]

    gate = _gate(client, run_id)

    assert gate["denied"] == 7
    assert [row["id"] for row in gate["refusals"]] == refused[:1:-1]


def test_an_unknown_run_or_gate_is_refused(client: TestClient, create_run: CreateRun) -> None:
    run_id = create_run("key-1")

    assert client.get("/api/v1/runs/run_missing/gates/model").status_code == 404
    assert client.get(f"/api/v1/runs/{run_id}/gates/unknown").status_code == 422
