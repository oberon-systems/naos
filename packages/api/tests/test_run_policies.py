from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

VALUE = "secret-alpha-value"
Register = Callable[..., dict[str, str]]


def _server(client: TestClient, name: str) -> None:
    client.post("/api/v1/secrets", json={"name": f"{name}-token", "value": f"secret-{name}-value"})
    body = {"name": name, "url": f"https://{name}.example.com/mcp", "credential": f"{name}-token"}
    assert client.post("/api/v1/mcp-servers", json=body).status_code == 201


def _policy(client: TestClient, *names: str) -> str:
    rules = [{"server": name, "tool": "search", "effect": "allow"} for name in names]
    response = client.post("/api/v1/policies", json={"kind": "mcp", "document": {"rules": rules}})
    assert response.status_code in (200, 201), response.text
    policy_id: str = response.json()["id"]
    return policy_id


def _desired(client: TestClient, runner: dict[str, str]) -> dict[str, Any]:
    bearer = {"Authorization": f"Bearer {runner['token']}"}
    path = f"/api/v1/runners/{runner['runner_id']}"
    client.post(f"{path}/heartbeat", json={"capacity": 4}, headers=bearer)
    [run] = client.get(f"{path}/runs", headers=bearer).json()["runs"]
    desired: dict[str, Any] = run
    return desired


def _started(
    client: TestClient, runner: dict[str, str], spec_body: dict[str, Any], policy_id: str | None
) -> str:
    if policy_id:
        spec_body["mcp"] = {"policy": policy_id}
    created = client.post("/api/v1/runs", json=spec_body, headers={"Idempotency-Key": "key-1"})
    assert created.status_code == 201, created.text
    run_id: str = created.json()["id"]
    _desired(client, runner)
    for expected, target in (("PENDING", "STARTING"), ("STARTING", "STARTED")):
        moved = client.post(
            f"/api/v1/runners/{runner['runner_id']}/runs/{run_id}/transition",
            json={"lease_id": runner["lease_id"], "expected": expected, "target": target},
            headers={"Authorization": f"Bearer {runner['token']}"},
        )
        assert moved.status_code == 200, moved.text
    return run_id


def _change(client: TestClient, run_id: str, policy_id: str, kind: str = "mcp") -> Any:
    body = {"kind": kind, "policy_id": policy_id}
    return client.post(f"/api/v1/runs/{run_id}/policies", json=body)


def test_a_started_run_takes_another_mcp_policy(
    client: TestClient, register: Register, spec_body: dict[str, Any]
) -> None:
    _server(client, "alpha")
    _server(client, "beta")
    first, second = _policy(client, "alpha"), _policy(client, "beta")
    runner = register()
    run_id = _started(client, runner, spec_body, first)
    assert set(_desired(client, runner)["credentials"]) == {"alpha-token"}

    changed = _change(client, run_id, second)
    desired = _desired(client, runner)
    read = client.get(f"/api/v1/runs/{run_id}").json()

    assert changed.status_code == 200, changed.text
    assert [server["name"] for server in desired["policies"]["mcp"]["servers"]] == ["beta"]
    assert set(desired["credentials"]) == {"beta-token"}
    assert read["mcp_document"] == desired["policies"]["mcp"]
    assert read["spec"]["mcp"] == {"policy": first}
    [held] = read["policy_history"]
    assert (held["seq"], held["kind"]) == (1, "mcp")
    assert (held["previous_id"], held["policy_id"]) == (first, second)
    assert changed.json()["policy_history"] == read["policy_history"]
    [event] = client.get("/api/v1/audit", params={"event": "policy_changed"}).json()
    assert (event["actor"], event["run_id"]) == ("operator", run_id)
    digest = client.get(f"/api/v1/policies/{second}").json()["digest"]
    assert event["data"] == {"kind": "mcp", "from": first, "to": second, "digest": digest}
    assert VALUE not in str(read) + str(event)


def test_a_run_without_an_mcp_policy_takes_one_and_keeps_every_change(
    client: TestClient, register: Register, spec_body: dict[str, Any]
) -> None:
    _server(client, "alpha")
    _server(client, "beta")
    first, second = _policy(client, "alpha"), _policy(client, "alpha", "beta")
    runner = register()
    run_id = _started(client, runner, spec_body, None)

    assert _change(client, run_id, first).status_code == 200
    assert _change(client, run_id, second).status_code == 200
    assert _change(client, run_id, second).status_code == 200
    history = client.get(f"/api/v1/runs/{run_id}").json()["policy_history"]

    assert [(held["seq"], held["previous_id"], held["policy_id"]) for held in history] == [
        (1, None, first),
        (2, first, second),
    ]
    assert len(client.get("/api/v1/audit", params={"event": "policy_changed"}).json()) == 2


def test_an_edited_document_belongs_to_the_run_alone(
    client: TestClient, register: Register, spec_body: dict[str, Any]
) -> None:
    _server(client, "alpha")
    _server(client, "beta")
    first = _policy(client, "alpha")
    runner = register()
    run_id = _started(client, runner, spec_body, first)
    stored = [policy["id"] for policy in client.get("/api/v1/policies").json()]
    rules = [{"server": "beta", "tool": "search", "effect": "allow"}]
    body = {"kind": "mcp", "document": {"rules": rules}}
    path = f"/api/v1/runs/{run_id}/policies"

    changed = client.post(path, json=body)
    assert client.post(path, json=body).status_code == 200
    desired = _desired(client, runner)

    assert changed.status_code == 200, changed.text
    assert [server["name"] for server in desired["policies"]["mcp"]["servers"]] == ["beta"]
    assert set(desired["credentials"]) == {"beta-token"}
    [held] = changed.json()["policy_history"]
    assert (held["previous_id"], held["policy_id"]) == (first, None)
    assert [policy["id"] for policy in client.get("/api/v1/policies").json()] == stored
    [event] = client.get("/api/v1/audit", params={"event": "policy_changed"}).json()
    assert (event["data"]["from"], event["data"]["to"]) == (first, None)
    assert len(event["data"]["digest"]) == 64
    unknown = {"kind": "mcp", "document": {"rules": [rules[0] | {"server": "gamma"}]}}
    assert client.post(path, json=unknown).status_code == 422
    assert client.post(path, json={"kind": "mcp"}).status_code == 422
    assert client.post(path, json=body | {"policy_id": first}).status_code == 422


def test_an_edited_document_is_saved_under_a_new_name(
    client: TestClient, register: Register, spec_body: dict[str, Any]
) -> None:
    _server(client, "alpha")
    _server(client, "beta")
    first = _policy(client, "alpha")
    run_id = _started(client, register(), spec_body, first)
    rules = [{"server": "beta", "tool": "search", "effect": "allow"}]
    body = {"kind": "mcp", "document": {"rules": rules}, "save": True, "name": "beta-only"}
    path = f"/api/v1/runs/{run_id}/policies"

    changed = client.post(path, json=body)
    other = {"rules": [rules[0] | {"tool": "fetch"}]}
    taken = client.post(path, json=body | {"document": other})

    assert changed.status_code == 200, changed.text
    [held] = changed.json()["policy_history"]
    saved = client.get(f"/api/v1/policies/{held['policy_id']}").json()
    assert (saved["name"], saved["runs_open"]) == ("beta-only", 1)
    assert (
        client.get(f"/api/v1/policies/{first}").json()["document"]["rules"][0]["server"] == "alpha"
    )
    assert taken.status_code == 409
    assert len(client.get(f"/api/v1/runs/{run_id}").json()["policy_history"]) == 1
    assert (
        client.post(path, json={"kind": "mcp", "policy_id": first, "save": True}).status_code == 422
    )
    unsaved = {"kind": "mcp", "document": {"rules": rules}, "name": "gamma"}
    assert client.post(path, json=unsaved).status_code == 422


def test_only_a_started_run_changes_its_policy(
    client: TestClient, register: Register, spec_body: dict[str, Any]
) -> None:
    _server(client, "alpha")
    _server(client, "beta")
    first, second = _policy(client, "alpha"), _policy(client, "beta")
    spec_body["mcp"] = {"policy": first}
    pending = client.post("/api/v1/runs", json=spec_body, headers={"Idempotency-Key": "key-0"})
    pending_id = pending.json()["id"]

    assert _change(client, pending_id, second).status_code == 409
    client.post(f"/api/v1/runs/{pending_id}/stop")
    run_id = _started(client, register(), spec_body, first)
    client.post(f"/api/v1/runs/{run_id}/stop")

    assert _change(client, run_id, second).status_code == 409
    assert _change(client, pending_id, second).status_code == 409
    read = client.get(f"/api/v1/runs/{run_id}").json()
    assert [server["name"] for server in read["mcp_document"]["servers"]] == ["alpha"]
    assert read["policy_history"] == []
    assert client.get("/api/v1/audit", params={"event": "policy_changed"}).json() == []


def test_a_change_the_run_cannot_take_is_refused(
    client: TestClient,
    register: Register,
    spec_body: dict[str, Any],
    mount_body: dict[str, Any],
    network_body: dict[str, Any],
) -> None:
    _server(client, "alpha")
    _server(client, "beta")
    first, second = _policy(client, "alpha"), _policy(client, "beta")
    mount = client.post("/api/v1/policies", json={"kind": "mount", "document": mount_body})
    network = client.post("/api/v1/policies", json={"kind": "network", "document": network_body})
    run_id = _started(client, register(), spec_body, first)
    client.post("/api/v1/mcp-servers/beta/disable")

    assert _change(client, run_id, mount.json()["id"], "mount").status_code == 422
    assert _change(client, run_id, network.json()["id"], "network").status_code == 409
    assert _change(client, run_id, network.json()["id"]).status_code == 422
    assert _change(client, run_id, "mcppol_" + "0" * 32).status_code == 422
    assert _change(client, run_id, second).status_code == 422
    assert _change(client, "run_" + "0" * 32, first).status_code == 404
    read = client.get(f"/api/v1/runs/{run_id}").json()
    assert [server["name"] for server in read["mcp_document"]["servers"]] == ["alpha"]
    assert read["policy_history"] == []


def test_changing_a_policy_needs_an_operator(raw_client: TestClient) -> None:
    body = {"kind": "mcp", "policy_id": "mcppol_" + "0" * 32}

    assert raw_client.post("/api/v1/runs/run_alpha/policies", json=body).status_code == 401
