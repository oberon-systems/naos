from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

KEY = {"Idempotency-Key": "key-1"}
CreateRun = Callable[[str], str]


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_policy_has_no_mutation_endpoints(
    client: TestClient, mount_body: dict[str, Any], method: str
) -> None:
    created = client.post("/api/v1/policies", json={"kind": "mount", "document": mount_body}).json()
    path = f"/api/v1/policies/{created['id']}"

    response = client.request(method, path, json={"kind": "mount", "document": {}})

    assert response.status_code == 405
    assert client.get(path).json() == created


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_image_has_no_mutation_endpoints(
    client: TestClient, spec_body: dict[str, Any], method: str
) -> None:
    path = "/api/v1/images/image_alpha"
    before = client.get(path).json()

    response = client.request(method, path, json={"digest": "sha256:" + "c" * 64})

    assert response.status_code == 405
    assert client.get(path).json() == before


def test_stop_keeps_the_run_spec(client: TestClient, create_run: CreateRun) -> None:
    run_id = create_run("key-1")
    before = client.get(f"/api/v1/runs/{run_id}").json()

    stopped = client.post(f"/api/v1/runs/{run_id}/stop").json()

    assert stopped["spec"] == before["spec"]


def test_replay_cannot_swap_the_spec(
    client: TestClient, create_run: CreateRun, spec_body: dict[str, Any]
) -> None:
    run_id = create_run("key-1")
    spec_body["timeout"] = 60

    assert client.post("/api/v1/runs", json=spec_body, headers=KEY).status_code == 409
    assert client.get(f"/api/v1/runs/{run_id}").json()["spec"]["timeout"] == 3600
