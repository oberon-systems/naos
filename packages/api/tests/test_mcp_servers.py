from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, update

from naos_api.models import McpServer

VALUE = "secret-alpha-value"
URL = "https://mcp.example.com/mcp"
Register = Callable[..., dict[str, str]]


def _server(client: TestClient, name: str = "alpha", **fields: Any) -> dict[str, Any]:
    response = client.post("/api/v1/mcp-servers", json={"name": name, "url": URL, **fields})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _policy(client: TestClient, *names: str) -> str:
    tools = {"shell": "read_file"}
    rules = [
        {"server": name, "tool": tools.get(name, "search"), "effect": "allow"} for name in names
    ]
    response = client.post("/api/v1/policies", json={"kind": "mcp", "document": {"rules": rules}})
    assert response.status_code in (200, 201), response.text
    policy_id: str = response.json()["id"]
    return policy_id


def _run(client: TestClient, spec_body: dict[str, Any], policy_id: str, key: str = "key-1") -> Any:
    spec_body["mcp"] = {"policy": policy_id}
    return client.post("/api/v1/runs", json=spec_body, headers={"Idempotency-Key": key})


def _desired(client: TestClient, runner: dict[str, str]) -> list[dict[str, Any]]:
    bearer = {"Authorization": f"Bearer {runner['token']}"}
    path = f"/api/v1/runners/{runner['runner_id']}"
    client.post(f"{path}/heartbeat", json={"capacity": 4}, headers=bearer)
    runs: list[dict[str, Any]] = client.get(f"{path}/runs", headers=bearer).json()["runs"]
    return runs


def _servers_of(run: dict[str, Any]) -> list[dict[str, Any]]:
    servers: list[dict[str, Any]] = run["policies"]["mcp"]["servers"]
    return servers


def test_a_registered_server_is_read_back_without_a_value(client: TestClient) -> None:
    client.post("/api/v1/secrets", json={"name": "alpha-token", "value": VALUE})
    created = _server(client, url="https://MCP.example.com:443/mcp", credential="alpha-token")
    fetched = client.get("/api/v1/mcp-servers/alpha")

    assert created["id"].startswith("mcpsrv_")
    assert created["kind"] == "external"
    assert created["url"] == URL
    assert created["credential"] == "alpha-token"
    assert (created["timeout_seconds"], created["max_calls_per_minute"]) == (30, 60)
    assert created["disabled_at"] is None
    assert fetched.json() == created
    assert VALUE not in fetched.text
    assert client.get("/api/v1/mcp-servers/beta").status_code == 404
    assert client.post("/api/v1/mcp-servers", json={"name": "alpha", "url": URL}).status_code == 409


def test_the_list_is_newest_first_with_the_built_in_servers(
    client: TestClient, session: Session
) -> None:
    _server(client, "alpha")
    _server(client, "beta")
    session.exec(update(McpServer).where(col(McpServer.name) == "alpha").values(created_at=1))
    session.commit()
    policy_id = _policy(client, "alpha", "shell")

    rows = client.get("/api/v1/mcp-servers").json()

    assert [row["name"] for row in rows] == ["beta", "alpha", "shell", "network", "secrets"]
    assert rows[1]["policies"] == [policy_id]
    assert rows[2]["policies"] == [policy_id]
    assert all(row["kind"] == "built-in" and row["url"] is None for row in rows[2:])


@pytest.mark.parametrize("name", ["shell", "network", "secrets"])
def test_a_built_in_server_cannot_be_created_changed_or_disabled(
    client: TestClient, name: str
) -> None:
    path = f"/api/v1/mcp-servers/{name}"

    assert client.post("/api/v1/mcp-servers", json={"name": name, "url": URL}).status_code == 409
    assert client.patch(path, json={"timeout_seconds": 5}).status_code == 409
    assert client.post(f"{path}/disable").status_code == 409
    assert client.post(f"{path}/enable").status_code == 409
    assert client.get(path).json()["kind"] == "built-in"


@pytest.mark.parametrize(
    "body",
    [
        {"name": "Alpha", "url": URL},
        {"name": "alpha__beta", "url": URL},
        {"name": "alpha"},
        {"name": "alpha", "url": "http://mcp.example.com/mcp"},
        {"name": "alpha", "url": "https://user:pass@mcp.example.com/mcp"},
        {"name": "alpha", "url": "https://mcp.example.com/mcp?token=alpha"},
        {"name": "alpha", "url": "https://192.0.2.10/mcp"},
        {"name": "alpha", "url": "https://localhost/mcp"},
        {"name": "alpha", "url": URL, "credential": "Bad Name"},
        {"name": "alpha", "url": URL, "timeout_seconds": 46},
        {"name": "alpha", "url": URL, "max_calls_per_minute": 601},
        {"name": "alpha", "url": URL, "value": VALUE},
    ],
)
def test_a_bad_server_is_unprocessable(client: TestClient, body: dict[str, Any]) -> None:
    assert client.post("/api/v1/mcp-servers", json=body).status_code == 422
    assert client.get("/api/v1/mcp-servers/alpha").status_code == 404


def test_patch_changes_what_it_names(client: TestClient) -> None:
    _server(client, credential="alpha-token")
    path = "/api/v1/mcp-servers/alpha"

    limits = client.patch(path, json={"timeout_seconds": 5, "max_calls_per_minute": 10}).json()
    cleared = client.patch(path, json={"credential": None, "url": "https://beta.example.com"})

    assert (limits["timeout_seconds"], limits["credential"]) == (5, "alpha-token")
    assert cleared.json()["credential"] is None
    assert cleared.json()["url"] == "https://beta.example.com/"
    assert cleared.json()["max_calls_per_minute"] == 10
    for body in (
        {},
        {"name": "beta"},
        {"url": None},
        {"timeout_seconds": None},
        {"url": "ftp://a"},
    ):
        assert client.patch(path, json=body).status_code == 422
    assert client.patch("/api/v1/mcp-servers/beta", json={"timeout_seconds": 5}).status_code == 404


def test_a_run_keeps_the_entry_it_was_created_with(
    client: TestClient, register: Register, spec_body: dict[str, Any]
) -> None:
    client.post("/api/v1/secrets", json={"name": "alpha-token", "value": VALUE})
    client.post("/api/v1/secrets", json={"name": "beta-token", "value": "secret-beta-value"})
    _server(client, credential="alpha-token")
    policy_id = _policy(client, "alpha")
    assert _run(client, spec_body, policy_id).status_code == 201

    changed = {"url": "https://beta.example.com/mcp", "credential": "beta-token"}
    client.patch("/api/v1/mcp-servers/alpha", json=changed | {"timeout_seconds": 5})
    assert _run(client, spec_body, policy_id, "key-2").status_code == 201
    first, second = _desired(client, register())
    read = client.get(f"/api/v1/runs/{first['id']}").json()

    assert _servers_of(first) == [
        {
            "name": "alpha",
            "url": URL,
            "credential": "alpha-token",
            "timeout_seconds": 30,
            "max_calls_per_minute": 60,
        }
    ]
    assert set(first["credentials"]) == {"alpha-token"}
    assert read["mcp_document"] == first["policies"]["mcp"]
    assert VALUE not in str(read)
    assert _servers_of(second)[0]["url"] == "https://beta.example.com/mcp"
    assert _servers_of(second)[0]["timeout_seconds"] == 5
    assert set(second["credentials"]) == {"beta-token"}


def test_an_unknown_or_disabled_server_never_starts_a_run(
    client: TestClient, spec_body: dict[str, Any]
) -> None:
    _server(client)
    policy_id = _policy(client, "alpha")
    rule = {"server": "beta", "tool": "search", "effect": "allow"}
    unknown = {"kind": "mcp", "document": {"rules": [rule]}}

    assert client.post("/api/v1/policies", json=unknown).status_code == 422
    assert client.post("/api/v1/mcp-servers/alpha/disable").json()["disabled_at"] is not None
    refused = _run(client, spec_body, policy_id)
    assert refused.status_code == 422
    assert "alpha is disabled" in refused.json()["detail"]
    assert client.get("/api/v1/runs").json() == []

    assert client.post("/api/v1/mcp-servers/alpha/enable").json()["disabled_at"] is None
    assert _run(client, spec_body, policy_id).status_code == 201


def test_disable_takes_the_server_out_of_runs_that_have_not_started(
    client: TestClient, register: Register, spec_body: dict[str, Any]
) -> None:
    client.post("/api/v1/secrets", json={"name": "alpha-token", "value": VALUE})
    _server(client, credential="alpha-token")
    _server(client, "beta")
    policy_id = _policy(client, "alpha", "beta")
    started = _run(client, spec_body, policy_id).json()["id"]
    runner = register()
    _desired(client, runner)
    moved = client.post(
        f"/api/v1/runners/{runner['runner_id']}/runs/{started}/transition",
        json={"lease_id": runner["lease_id"], "expected": "PENDING", "target": "STARTING"},
        headers={"Authorization": f"Bearer {runner['token']}"},
    )
    assert moved.status_code == 200, moved.text
    _run(client, spec_body, policy_id, "key-2")

    client.post("/api/v1/mcp-servers/alpha/disable")
    client.post("/api/v1/mcp-servers/alpha/disable")
    first, second = _desired(client, runner)

    assert [server["name"] for server in _servers_of(first)] == ["alpha", "beta"]
    assert set(first["credentials"]) == {"alpha-token"}
    assert [server["name"] for server in _servers_of(second)] == ["beta"]
    assert second["policies"]["mcp"]["rules"] == first["policies"]["mcp"]["rules"]
    assert second["credentials"] == {}
    [event] = client.get("/api/v1/audit", params={"event": "mcp_server_disabled"}).json()
    assert event["data"] == {"name": "alpha", "runs": 1}


def test_every_registry_write_is_audited_without_a_value(client: TestClient) -> None:
    client.post("/api/v1/secrets", json={"name": "alpha-token", "value": VALUE})
    _server(client, credential="alpha-token")
    client.patch("/api/v1/mcp-servers/alpha", json={"credential": None, "timeout_seconds": 5})
    client.post("/api/v1/mcp-servers/alpha/disable")
    client.post("/api/v1/mcp-servers/alpha/enable")

    trail = client.get("/api/v1/audit").json()
    events = {row["event"]: row for row in trail if row["event"].startswith("mcp_server_")}

    assert {name: row["data"] for name, row in events.items()} == {
        "mcp_server_registered": {"name": "alpha"},
        "mcp_server_updated": {"name": "alpha", "fields": ["credential", "timeout_seconds"]},
        "mcp_server_disabled": {"name": "alpha", "runs": 0},
        "mcp_server_enabled": {"name": "alpha"},
    }
    assert all(row["actor"] == "operator" for row in events.values())
    assert VALUE not in str(trail)


def test_the_registry_needs_an_operator(raw_client: TestClient) -> None:
    assert raw_client.get("/api/v1/mcp-servers").status_code == 401
    assert raw_client.post("/api/v1/mcp-servers", json={}).status_code == 401
    assert raw_client.patch("/api/v1/mcp-servers/alpha", json={}).status_code == 401
    assert raw_client.post("/api/v1/mcp-servers/alpha/disable").status_code == 401
