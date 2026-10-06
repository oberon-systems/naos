from typing import Any

from fastapi.testclient import TestClient

SHELL = {"allow": ["read_file"]}
WIDER = {"allow": ["read_file", "grep"]}


def _create(client: TestClient, document: dict[str, Any], name: str | None = None) -> Any:
    body = {"kind": "shell", "document": document} | ({"name": name} if name else {})
    return client.post("/api/v1/policies", json=body)


def test_a_policy_is_saved_under_a_name_and_found_by_it(client: TestClient) -> None:
    created = _create(client, SHELL, "alpha")
    again = _create(client, SHELL, "alpha")
    found = client.get("/api/v1/policies", params={"q": "ALPH"}).json()

    assert (created.status_code, again.status_code) == (201, 200)
    assert created.json()["name"] == "alpha"
    assert again.json()["id"] == created.json()["id"]
    assert [policy["id"] for policy in found] == [created.json()["id"]]
    [event] = client.get("/api/v1/audit", params={"event": "policy_named"}).json()
    assert event["data"] == {"policy_id": created.json()["id"], "kind": "shell", "name": "alpha"}


def test_a_name_never_moves_and_a_policy_keeps_the_one_it_has(
    client: TestClient, network_body: dict[str, Any]
) -> None:
    first = _create(client, SHELL, "alpha").json()
    network = {"kind": "network", "name": "alpha", "document": network_body}

    assert _create(client, WIDER, "alpha").status_code == 409
    assert _create(client, SHELL, "beta").status_code == 409
    assert _create(client, SHELL, "Alpha!").status_code == 422
    assert _create(client, SHELL).json() == first
    assert client.get(f"/api/v1/policies/{first['id']}").json()["name"] == "alpha"
    assert len(client.get("/api/v1/policies").json()) == 1
    assert client.post("/api/v1/policies", json=network).status_code == 201


def test_an_unnamed_policy_takes_a_name_once(client: TestClient) -> None:
    unnamed = _create(client, SHELL).json()

    named = _create(client, SHELL, "alpha")

    assert unnamed["name"] is None
    assert (named.status_code, named.json()["id"]) == (200, unnamed["id"])
    assert named.json()["name"] == "alpha"
