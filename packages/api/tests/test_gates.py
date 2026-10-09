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
    assert client.get("/api/v1/runs/run_missing/gates/network").status_code == 404


def _decided(session: Session, run_id: str, at: int, host: str, rule: str, **reason: str) -> None:
    data = {"protocol": "https", "host": host, "rule": rule} | reason
    event = AuditEvent(
        id=f"evt_{uuid4().hex}",
        at=at,
        received_at=at,
        source="runner",
        event="network_denied" if reason else "network_allowed",
        actor="runner",
        run_id=run_id,
        data=data,
    )
    session.add(event)
    session.commit()


def _network(client: TestClient, run_id: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/runs/{run_id}/gates/network")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_a_run_without_requests_reached_no_host(
    client: TestClient, spec_body: dict[str, Any], network_body: dict[str, Any]
) -> None:
    policy = client.post("/api/v1/policies", json={"kind": "network", "document": network_body})
    spec_body["network"] = {"policy": policy.json()["id"]}
    run = client.post("/api/v1/runs", json=spec_body, headers={"Idempotency-Key": "key-1"})

    gate = _network(client, run.json()["id"])

    assert gate == {
        "policy_id": policy.json()["id"],
        "document": policy.json()["document"],
        "configured_at": None,
        "allowed": 0,
        "denied": 0,
        "hosts_total": 0,
        "hosts": [],
    }


def test_hosts_are_grouped_with_the_denied_ones_first(
    client: TestClient, session: Session, create_run: CreateRun
) -> None:
    run_id, other = create_run("key-1"), create_run("key-2")
    for at in (10, 11, 12):
        _decided(session, run_id, at, "example.com", "allow[0]")
    _decided(session, run_id, 13, "registry.example.com", "allow[1]")
    _decided(session, run_id, 14, "private.example.com", "none", reason="no matching allow rule")
    _decided(session, run_id, 15, "internal.example.com", "deny[0]", reason="explicit deny rule")
    _decided(session, run_id, 16, "private.example.com", "none", reason="no matching allow rule")
    _decided(session, other, 17, "other.example.com", "allow[0]")
    for at in (5, 20):
        event = AuditEvent(
            id=f"evt_{uuid4().hex}",
            at=at,
            received_at=at,
            source="runner",
            event="network_policy_configured",
            actor="runner",
            run_id=run_id,
            data={},
        )
        session.add(event)
    session.commit()

    gate = _network(client, run_id)

    assert (gate["allowed"], gate["denied"], gate["configured_at"]) == (4, 3, 20)
    assert gate["hosts_total"] == 4
    held = [(h["host"], h["rule"], h["allowed"], h["denied"], h["last_at"]) for h in gate["hosts"]]
    assert held == [
        ("private.example.com", "none", 0, 2, 16),
        ("internal.example.com", "deny[0]", 0, 1, 15),
        ("example.com", "allow[0]", 3, 0, 12),
        ("registry.example.com", "allow[1]", 1, 0, 13),
    ]
    assert {h["protocol"] for h in gate["hosts"]} == {"https"}
