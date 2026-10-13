from collections.abc import Callable
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session

from naos_api.models import AuditEvent, RunPolicy
from naos_api.spec import PolicyKind

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
    gate = _gate(client, create_run("key-1"))

    assert gate["policy_id"] == "modelpol_" + "c" * 32
    assert gate["document"]["providers"][0]["credential"] == "gamma-key"
    assert gate["configured_at"] is None
    assert {key: gate[key] for key in ("calls", "denied", "input_tokens", "output_tokens")} == {
        "calls": 0,
        "denied": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    assert (gate["refusals"], gate["groups"], gate["groups_total"], gate["minutes"]) == (
        [],
        [],
        0,
        [],
    )


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


def test_calls_are_grouped_by_provider_and_model_denied_first(
    client: TestClient, session: Session, create_run: CreateRun
) -> None:
    run_id = create_run("key-1")
    _call(session, run_id, 60, input_tokens=100, output_tokens=10, duration_ms=900)
    _call(session, run_id, 70, input_tokens=50, output_tokens=5, duration_ms=4000)
    _call(session, run_id, 130, provider="beta", model="beta-pro", decision="deny", category="rate")
    _call(session, run_id, 140, provider="unknown", model="beta-max", decision="deny")

    gate = _gate(client, run_id)

    assert [(row["provider"], row["model"]) for row in gate["groups"]] == [
        ("beta", "beta-pro"),
        ("unknown", "beta-max"),
        ("alpha", "alpha-mini"),
    ]
    assert gate["groups"][2] == {
        "provider": "alpha",
        "model": "alpha-mini",
        "calls": 2,
        "denied": 0,
        "input_tokens": 150,
        "output_tokens": 15,
        "slowest_ms": 4000,
        "last_at": 70,
    }
    assert gate["groups_total"] == 3


def test_calls_are_counted_per_minute_and_provider(
    client: TestClient, session: Session, create_run: CreateRun
) -> None:
    run_id = create_run("key-1")
    _call(session, run_id, 61, input_tokens=100, output_tokens=10)
    _call(session, run_id, 119, input_tokens=20, output_tokens=2)
    _call(session, run_id, 120, decision="deny", category="budget")
    _call(session, run_id, 125, provider="beta", model="beta-pro")

    minutes = _gate(client, run_id)["minutes"]

    assert minutes == [
        {
            "at": 60,
            "provider": "alpha",
            "requests": 2,
            "denied": 0,
            "input_tokens": 120,
            "output_tokens": 12,
        },
        {
            "at": 120,
            "provider": "alpha",
            "requests": 1,
            "denied": 1,
            "input_tokens": 0,
            "output_tokens": 0,
        },
        {
            "at": 120,
            "provider": "beta",
            "requests": 1,
            "denied": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        },
    ]


def test_the_audit_is_filtered_by_decision(
    client: TestClient, session: Session, create_run: CreateRun
) -> None:
    run_id = create_run("key-1")
    _call(session, run_id, 10)
    refused = _call(session, run_id, 11, decision="deny", category="budget")
    params = {"run_id": run_id, "event": "model_call", "decision": "deny"}

    rows = client.get("/api/v1/audit", params=params).json()

    assert [row["id"] for row in rows] == [refused]
    assert client.get("/api/v1/audit", params={"decision": "maybe"}).status_code == 422


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


def _event(session: Session, run_id: str, at: int, event: str, **data: Any) -> None:
    session.add(
        AuditEvent(
            id=f"evt_{uuid4().hex}",
            at=at,
            received_at=at,
            source="runner",
            event=event,
            actor="runner",
            run_id=run_id,
            data=data,
        )
    )
    session.commit()


def _mcp_call(
    session: Session, run_id: str, at: int, tool: str, decision: str = "allow", **fields: Any
) -> None:
    data = {
        "server": "alpha",
        "tool": tool,
        "resource": "none",
        "decision": decision,
        "duration_ms": 5,
        "category": "none" if decision == "allow" else "denied",
        "rule": "none",
    }
    _event(session, run_id, at, "mcp_call", **(data | fields))


def _mcp_run(client: TestClient, spec_body: dict[str, Any], rules: list[Any]) -> dict[str, Any]:
    policy = client.post("/api/v1/policies", json={"kind": "mcp", "document": {"rules": rules}})
    assert policy.status_code in (200, 201), policy.text
    spec_body["mcp"] = {"policy": policy.json()["id"]}
    run: dict[str, Any] = client.post(
        "/api/v1/runs", json=spec_body, headers={"Idempotency-Key": f"key-{uuid4().hex}"}
    ).json()
    return run


def _mcp(client: TestClient, run_id: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/runs/{run_id}/gates/mcp")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_the_mcp_gate_counts_calls_and_secret_reads(
    client: TestClient, session: Session, spec_body: dict[str, Any], mcp_body: dict[str, Any]
) -> None:
    run = _mcp_run(client, spec_body, mcp_body["rules"])
    other = _mcp_run(client, spec_body, mcp_body["rules"])
    for at, decision in ((10, "allow"), (11, "deny"), (12, "allow")):
        _mcp_call(session, run["id"], at, "search", decision)
    _mcp_call(session, other["id"], 13, "search", "deny")
    for at, name in ((14, "beta-key"), (15, "alpha-key"), (16, "alpha-key")):
        _event(session, run["id"], at, "secret_read", name=name, decision="allow")
    _event(session, run["id"], 17, "secret_read", name="beta-key", decision="deny")
    _event(session, run["id"], 18, "mcp_policy_configured")

    gate = _mcp(client, run["id"])

    assert {key: gate[key] for key in ("policy_id", "document", "configured_at")} == {
        "policy_id": spec_body["mcp"]["policy"],
        "document": run["mcp_document"],
        "configured_at": 18,
    }
    assert (gate["calls"], gate["denied"]) == (3, 1)
    assert gate["secret_reads"] == [
        {"name": "alpha-key", "reads": 2},
        {"name": "beta-key", "reads": 1},
    ]
    assert gate["secrets"] == [
        {"name": "alpha-key", "reads": 2, "refused": 0, "last_at": 16},
        {"name": "beta-key", "reads": 1, "refused": 1, "last_at": 17},
    ]
    assert gate["budgets"] == []


def test_mcp_calls_are_grouped_by_server_and_tool_denied_first(
    client: TestClient, session: Session, spec_body: dict[str, Any], mcp_body: dict[str, Any]
) -> None:
    run_id = _mcp_run(client, spec_body, mcp_body["rules"])["id"]
    _mcp_call(session, run_id, 60, "search", duration_ms=40)
    _mcp_call(session, run_id, 61, "search", duration_ms=900)
    _mcp_call(session, run_id, 125, "delete", "deny")
    _mcp_call(session, run_id, 130, "resources/read", resource="docs://alpha/")
    _mcp_call(session, run_id, 131, "read_file", server="shell", duration_ms=3)

    gate = _mcp(client, run_id)

    assert gate["groups_total"] == 4
    assert [(g["server"], g["tool"], g["resource"]) for g in gate["groups"]] == [
        ("alpha", "delete", "none"),
        ("alpha", "search", "none"),
        ("alpha", "resources/read", "docs://alpha/"),
        ("shell", "read_file", "none"),
    ]
    assert gate["groups"][1] == {
        "server": "alpha",
        "tool": "search",
        "resource": "none",
        "calls": 2,
        "denied": 0,
        "slowest_ms": 900,
        "last_at": 61,
    }
    assert gate["groups"][0]["denied"] == 1
    assert gate["minutes"] == [
        {"at": 60, "server": "alpha", "calls": 2, "denied": 0},
        {"at": 120, "server": "alpha", "calls": 2, "denied": 1},
        {"at": 120, "server": "shell", "calls": 1, "denied": 0},
    ]


BUDGETED = [
    {"server": "alpha", "tool": "search", "effect": "allow", "max_calls": 5},
    {"server": "alpha", "tool": "fetch", "effect": "allow", "max_calls_per_minute": 2},
]


def test_a_rule_budget_counts_what_its_rule_charged(
    client: TestClient, session: Session, spec_body: dict[str, Any], mcp_body: dict[str, Any]
) -> None:
    run = _mcp_run(client, spec_body, BUDGETED)
    fetch, search = ("0", "1")
    for at in (60, 61, 62):
        _mcp_call(session, run["id"], at, "search", rule=search)
    _mcp_call(session, run["id"], 63, "search", "deny", rule=search)
    for at in (60, 70, 130):
        _mcp_call(session, run["id"], at, "fetch", rule=fetch, category="timeout")
    _mcp_call(session, run["id"], 64, "delete", "deny")

    gate = _mcp(client, run["id"])

    assert gate["budgets"] == [
        {"rule": 0, "spent": 3, "peak": 2, "refused": 0, "since": run["created_at"]},
        {"rule": 1, "spent": 3, "peak": 3, "refused": 1, "since": run["created_at"]},
    ]


def test_a_kept_rule_keeps_its_budget_across_a_change(
    client: TestClient, session: Session, spec_body: dict[str, Any], mcp_body: dict[str, Any]
) -> None:
    run = _mcp_run(client, spec_body, BUDGETED)
    rules = [
        {"server": "alpha", "tool": "delete", "effect": "deny"},
        BUDGETED[0],
        {**BUDGETED[1], "max_calls_per_minute": 3},
    ]
    changed = client.post("/api/v1/policies", json={"kind": "mcp", "document": {"rules": rules}})
    document = client.get(f"/api/v1/policies/{changed.json()['id']}").json()["document"]
    session.add(
        RunPolicy(
            run_id=run["id"],
            seq=1,
            kind=PolicyKind.MCP,
            policy_id=changed.json()["id"],
            document={"servers": [], "secrets": [], "rules": document["rules"]},
            created_at=100,
        )
    )
    session.commit()
    _event(session, run["id"], 103, "mcp_policy_configured")
    for at in (90, 101):
        _mcp_call(session, run["id"], at, "search", rule="1")
        _mcp_call(session, run["id"], at, "fetch", rule="0")
    _mcp_call(session, run["id"], 104, "search", rule="2")
    _mcp_call(session, run["id"], 105, "fetch", rule="1")

    gate = _mcp(client, run["id"])

    assert gate["budgets"] == [
        {"rule": 1, "spent": 1, "peak": 1, "refused": 0, "since": 103},
        {"rule": 2, "spent": 3, "peak": 3, "refused": 0, "since": run["created_at"]},
    ]


def test_the_audit_is_filtered_by_server(
    client: TestClient, session: Session, create_run: CreateRun
) -> None:
    run_id = create_run("key-1")
    _mcp_call(session, run_id, 10, "get", server="secrets")
    _mcp_call(session, run_id, 11, "search")
    _event(session, run_id, 12, "secret_read", name="alpha-key", decision="allow")

    params = {"run_id": run_id, "event": ["mcp_call", "secret_read"], "server": "secrets"}
    found = client.get("/api/v1/audit", params=params).json()

    assert [(row["event"], row["data"]["tool"]) for row in found] == [("mcp_call", "get")]


def test_a_run_without_a_shell_policy_grants_nothing(
    client: TestClient, create_run: CreateRun
) -> None:
    response = client.get(f"/api/v1/runs/{create_run('key-1')}/gates/shell")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "policy_id": None,
        "document": None,
        "configured_at": None,
        "roots": [],
        "allowed": 0,
        "denied": 0,
        "called": [],
        "groups_total": 0,
        "groups": [],
    }


def test_shell_calls_are_grouped_by_capability_and_path(
    client: TestClient,
    session: Session,
    spec_body: dict[str, Any],
    mount_body: dict[str, Any],
    shell_body: dict[str, Any],
) -> None:
    shell = client.post("/api/v1/policies", json={"kind": "shell", "document": shell_body})
    mount = client.post("/api/v1/policies", json={"kind": "mount", "document": mount_body})
    spec_body["shell"] = {"policy": shell.json()["id"]}
    spec_body["mounts"] = {"policy": mount.json()["id"]}
    run = client.post("/api/v1/runs", json=spec_body, headers={"Idempotency-Key": "key-1"}).json()
    other = client.post("/api/v1/runs", json=spec_body, headers={"Idempotency-Key": "key-2"})
    outside = {"reason": "path is outside every mount"}
    read = {"capability": "read_file", "path": "/naos/alpha/a"}
    for at in (10, 11):
        _event(session, run["id"], at, "shell_allowed", **read)
    _event(session, run["id"], 12, "shell_allowed", capability="grep", path="/naos/alpha")
    _event(session, run["id"], 13, "shell_denied", capability="read_file", path="/etc", **outside)
    _event(session, run["id"], 14, "shell_denied", capability="read_file", path="/etc", **outside)
    _event(
        session,
        run["id"],
        15,
        "shell_denied",
        capability="git_diff",
        path="/naos/alpha",
        reason="capability not granted",
    )
    _event(session, other.json()["id"], 16, "shell_denied", capability="grep", path="/", **outside)
    _event(session, run["id"], 17, "shell_policy_configured")

    gate = client.get(f"/api/v1/runs/{run['id']}/gates/shell").json()

    assert (gate["policy_id"], gate["document"]) == (shell.json()["id"], shell.json()["document"])
    assert (gate["allowed"], gate["denied"], gate["configured_at"]) == (3, 3, 17)
    assert gate["roots"] == ["/naos/alpha", "/home/naos/.claude"]
    assert gate["called"] == ["git_diff", "grep", "read_file"]
    assert gate["groups_total"] == 4
    keys = ("capability", "path", "allowed", "denied", "last_at")
    assert [tuple(group[key] for key in keys) for group in gate["groups"]] == [
        ("read_file", "/etc", 0, 2, 14),
        ("git_diff", "/naos/alpha", 0, 1, 15),
        ("grep", "/naos/alpha", 1, 0, 12),
        ("read_file", "/naos/alpha/a", 2, 0, 11),
    ]
