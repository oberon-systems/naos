from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from naos_api.app import create_app
from naos_api.settings import Settings

KEY = {"Idempotency-Key": "key-1"}


def _create(client: TestClient, body: dict[str, Any], key: str = "key-1") -> Response:
    response: Response = client.post("/api/v1/tasks", json=body, headers={"Idempotency-Key": key})
    return response


def test_runs_api_denies_without_principal(settings: Settings, spec_body: dict[str, Any]) -> None:
    client = TestClient(create_app())

    assert client.post("/api/v1/tasks", json=spec_body, headers=KEY).status_code == 401
    assert client.get("/api/v1/tasks").status_code == 401
    assert client.post("/api/v1/policies", json={}).status_code == 401
    assert client.post("/api/v1/images", json={}).status_code == 401
    assert client.get("/api/v1/images").status_code == 401


def test_create_and_replay(client: TestClient, spec_body: dict[str, Any]) -> None:
    created = _create(client, spec_body)
    replayed = _create(client, spec_body)

    assert created.status_code == 201
    assert replayed.status_code == 200
    assert replayed.json()["id"] == created.json()["id"]
    assert created.json()["status"] == "PENDING"
    assert "idempotency_key" not in created.json()
    assert "request_digest" not in created.json()


def test_key_reuse_with_other_spec_is_conflict(
    client: TestClient, spec_body: dict[str, Any]
) -> None:
    _create(client, spec_body)
    spec_body["timeout"] = 60

    assert _create(client, spec_body).status_code == 409


@pytest.mark.parametrize("headers", [{}, {"Idempotency-Key": "a b"}, {"Idempotency-Key": ""}])
def test_create_requires_valid_idempotency_key(
    client: TestClient, spec_body: dict[str, Any], headers: dict[str, str]
) -> None:
    assert client.post("/api/v1/tasks", json=spec_body, headers=headers).status_code == 422


def test_client_cannot_set_status(client: TestClient, spec_body: dict[str, Any]) -> None:
    assert _create(client, spec_body | {"status": "STARTED"}).status_code == 422


def test_unknown_policy_reference_is_unprocessable(
    client: TestClient, spec_body: dict[str, Any]
) -> None:
    spec_body["shell"] = {"policy": "shellpol_" + "0" * 32}

    assert _create(client, spec_body).status_code == 422


def test_get_list_and_stop(client: TestClient, spec_body: dict[str, Any]) -> None:
    run_id = _create(client, spec_body).json()["id"]
    _create(client, spec_body, key="key-2")

    assert client.get(f"/api/v1/tasks/{run_id}").json()["id"] == run_id
    assert client.get("/api/v1/tasks/run_missing").status_code == 404
    assert client.post("/api/v1/tasks/run_missing/stop").status_code == 404

    stopped = client.post(f"/api/v1/tasks/{run_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "CANCELLED"
    assert client.post(f"/api/v1/tasks/{run_id}/stop").json()["status"] == "CANCELLED"

    listed = client.get("/api/v1/tasks", params={"status": "CANCELLED"}).json()
    assert [run["id"] for run in listed] == [run_id]
    assert len(client.get("/api/v1/tasks").json()) == 2


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"status": "X"}])
def test_list_rejects_bad_query(client: TestClient, params: dict[str, Any]) -> None:
    assert client.get("/api/v1/tasks", params=params).status_code == 422


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_run_has_no_mutation_endpoints(
    client: TestClient, spec_body: dict[str, Any], method: str
) -> None:
    run_id = _create(client, spec_body).json()["id"]

    response = client.request(method, f"/api/v1/tasks/{run_id}", json={"status": "COMPLETED"})

    assert response.status_code == 405
    assert client.get(f"/api/v1/tasks/{run_id}").json()["status"] == "PENDING"


def test_mount_policy_flow(
    client: TestClient, spec_body: dict[str, Any], mount_body: dict[str, Any]
) -> None:
    body = {"kind": "mount", "document": mount_body}
    created = client.post("/api/v1/policies", json=body)
    replayed = client.post("/api/v1/policies", json=body)
    policy_id = created.json()["id"]

    assert created.status_code == 201
    assert replayed.status_code == 200
    assert replayed.json()["id"] == policy_id
    assert created.json()["document"]["workdir"] == "/naos/alpha"
    assert client.get(f"/api/v1/policies/{policy_id}").json() == created.json()

    spec_body["mounts"] = {"policy": policy_id}
    run = _create(client, spec_body).json()
    assert run["spec"]["mounts"]["policy"] == policy_id


def test_network_policy_flow(
    client: TestClient, spec_body: dict[str, Any], network_body: dict[str, Any]
) -> None:
    body = {"kind": "network", "document": network_body}
    created = client.post("/api/v1/policies", json=body)
    replayed = client.post("/api/v1/policies", json=body)
    policy_id = created.json()["id"]

    assert created.status_code == 201
    assert replayed.status_code == 200
    assert replayed.json()["id"] == policy_id
    assert policy_id.startswith("netpol_")
    assert created.json()["document"]["allow"][0]["host"] == "example.com"
    assert client.get(f"/api/v1/policies/{policy_id}").json() == created.json()

    spec_body["network"] = {"policy": policy_id}
    run = _create(client, spec_body).json()
    assert run["spec"]["network"]["policy"] == policy_id


def test_shell_policy_flow(
    client: TestClient, spec_body: dict[str, Any], shell_body: dict[str, Any]
) -> None:
    body = {"kind": "shell", "document": shell_body}
    created = client.post("/api/v1/policies", json=body)
    replayed = client.post("/api/v1/policies", json=body)
    policy_id = created.json()["id"]

    assert created.status_code == 201
    assert replayed.status_code == 200
    assert replayed.json()["id"] == policy_id
    assert policy_id.startswith("shellpol_")
    assert created.json()["document"]["allow"] == ["read_file", "list_dir", "grep"]
    assert client.get(f"/api/v1/policies/{policy_id}").json() == created.json()

    spec_body["shell"] = {"policy": policy_id}
    run = _create(client, spec_body).json()
    assert run["spec"]["shell"]["policy"] == policy_id


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "network", "document": {}},
        {"kind": "network", "document": {"allow": [{}]}},
        {"kind": "network", "document": {"allow": [{"host": "bad_host"}]}},
        {"kind": "network", "document": {"allow": [{"protocol": "ftp"}]}},
        {"kind": "beta", "document": {"allow": [{"host": "example.com"}]}},
        {"kind": "shell", "document": {}},
        {"kind": "shell", "document": {"allow": []}},
        {"kind": "shell", "document": {"allow": ["write_file"]}},
        {"kind": "shell", "document": {"allow": ["read_file", "read_file"]}},
        {"kind": "mount", "document": {"workspace": {"host_path": "/etc/beta"}}},
        {"kind": "mount", "document": {"workspace": {"host_path": "/srv/projects/../../etc"}}},
        {"kind": "mount", "document": {"workspace": {"host_path": "/srv/projects/alpha"}, "x": 1}},
    ],
)
def test_bad_policy_is_unprocessable(client: TestClient, body: dict[str, Any]) -> None:
    assert client.post("/api/v1/policies", json=body).status_code == 422


def test_unknown_policy_is_not_found(client: TestClient) -> None:
    assert client.get("/api/v1/policies/mntpol_missing").status_code == 404
