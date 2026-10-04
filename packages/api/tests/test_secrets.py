from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from naos_api.models import AuditEvent

VALUE = "secret-alpha-value"
ROTATED = "secret-alpha-rotated"
DAY = 86_400
Register = Callable[..., dict[str, str]]


def _secret(client: TestClient, name: str, value: str = VALUE, **fields: Any) -> dict[str, Any]:
    response = client.post("/api/v1/secrets", json={"name": name, "value": value, **fields})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _names(client: TestClient, **params: Any) -> list[str]:
    return [row["name"] for row in client.get("/api/v1/secrets", params=params).json()]


def _mcp_policy(client: TestClient, mcp_body: dict[str, Any]) -> str:
    policy_id: str = client.post(
        "/api/v1/policies", json={"kind": "mcp", "document": mcp_body}
    ).json()["id"]
    return policy_id


def _issue(
    client: TestClient, register: Register, spec_body: dict[str, Any], policy_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    spec_body["mcp"] = {"policy": policy_id}
    run: dict[str, Any] = client.post(
        "/api/v1/runs", json=spec_body, headers={"Idempotency-Key": "key-1"}
    ).json()
    runner = register()
    bearer = {"Authorization": f"Bearer {runner['token']}"}
    client.post(
        f"/api/v1/runners/{runner['runner_id']}/heartbeat", json={"capacity": 1}, headers=bearer
    )
    desired = client.get(f"/api/v1/runners/{runner['runner_id']}/runs", headers=bearer).json()
    credentials: dict[str, Any] = desired["runs"][0]["credentials"]
    return run, credentials


def test_list_filters_by_state_and_query(client: TestClient, clock: Callable[[], int]) -> None:
    now = clock()
    _secret(client, "alpha-token")
    _secret(client, "beta-token", expires_at=now + 30 * DAY)
    _secret(client, "gamma-token", expires_at=now + DAY)
    _secret(client, "delta-token", expires_at=now)

    rows = {row["name"]: row["state"] for row in client.get("/api/v1/secrets").json()}

    assert rows == {
        "alpha-token": "valid",
        "beta-token": "valid",
        "delta-token": "expired",
        "gamma-token": "expiring",
    }
    assert _names(client, state="expiring") == ["gamma-token"]
    assert _names(client, q="BETA") == ["beta-token"]
    assert client.get("/api/v1/secrets", params={"state": "stale"}).status_code == 422


def test_usage_names_the_registry_server(client: TestClient, mcp_body: dict[str, Any]) -> None:
    _secret(client, "alpha-token")
    _secret(client, "beta-token")
    server_id = client.get("/api/v1/mcp-servers/alpha").json()["id"]

    [row] = client.get("/api/v1/secrets", params={"used": True}).json()

    assert row["name"] == "alpha-token"
    assert row["named_by"] == [{"kind": "reg", "id": server_id, "server": "alpha"}]
    assert row["held_by"] == []
    assert _names(client, used=False) == ["beta-token"]
    assert _names(client, q=server_id) == ["alpha-token"]


def test_a_run_holds_what_was_issued_until_it_stops(
    client: TestClient,
    register: Register,
    spec_body: dict[str, Any],
    mcp_body: dict[str, Any],
) -> None:
    _secret(client, "alpha-token")
    run, _ = _issue(client, register, spec_body, _mcp_policy(client, mcp_body))

    detail = client.get("/api/v1/secrets/alpha-token").json()

    assert detail["held_by"] == [
        {"run_id": run["id"], "seq": run["seq"], "status": "PENDING", "profile_id": None}
    ]
    assert [use["run_id"] for use in detail["runs"]] == [run["id"]]
    assert detail["runs"][0]["issued"] == 1

    client.post(f"/api/v1/runs/{run['id']}/stop")
    detail = client.get("/api/v1/secrets/alpha-token").json()

    assert detail["held_by"] == []
    assert detail["runs"][0]["status"] == "CANCELLED"


def test_rotate_keeps_the_name_and_the_next_issue_carries_it(
    client: TestClient,
    register: Register,
    spec_body: dict[str, Any],
    mcp_body: dict[str, Any],
    clock: Callable[[], int],
) -> None:
    created = _secret(client, "alpha-token")

    rotated = client.post("/api/v1/secrets/alpha-token/rotate", json={"value": ROTATED})
    _, credentials = _issue(client, register, spec_body, _mcp_policy(client, mcp_body))

    assert rotated.status_code == 200
    assert rotated.json()["id"] == created["id"]
    assert rotated.json()["rotated_at"] == clock()
    assert ROTATED not in rotated.text
    assert credentials["alpha-token"]["value"] == ROTATED
    missing = client.post("/api/v1/secrets/beta-token/rotate", json={"value": ROTATED})
    assert missing.status_code == 404


def test_patch_changes_the_expiry_only(client: TestClient, clock: Callable[[], int]) -> None:
    _secret(client, "alpha-token")
    until = clock() + DAY

    dated = client.patch("/api/v1/secrets/alpha-token", json={"expires_at": until})
    cleared = client.patch("/api/v1/secrets/alpha-token", json={"expires_at": None})

    assert dated.json()["expires_at"] == until
    assert dated.json()["state"] == "expiring"
    assert cleared.json()["expires_at"] is None
    assert client.patch("/api/v1/secrets/alpha-token", json={}).status_code == 422
    assert client.patch("/api/v1/secrets/alpha-token", json={"value": ROTATED}).status_code == 422
    trail = client.get("/api/v1/audit", params={"event": "secret_expiry_changed"}).json()
    assert [row["data"] for row in trail] == [
        {"name": "alpha-token", "from": None, "to": until},
        {"name": "alpha-token", "from": until, "to": None},
    ]


def test_delete_is_refused_while_named(client: TestClient, mcp_body: dict[str, Any]) -> None:
    _secret(client, "alpha-token")
    server_id = client.get("/api/v1/mcp-servers/alpha").json()["id"]

    refused = client.delete("/api/v1/secrets/alpha-token")

    assert refused.status_code == 409
    assert f"{server_id} (server alpha)" in refused.json()["detail"]
    assert client.get("/api/v1/secrets/alpha-token").status_code == 200
    [event] = client.get("/api/v1/audit", params={"event": "secret_delete_refused"}).json()
    assert event["data"] == {"name": "alpha-token", "named_by": 1, "held_by": []}


def test_a_model_provider_names_its_credential(
    client: TestClient, model_body: dict[str, Any]
) -> None:
    _secret(client, "beta-key")
    created = client.post("/api/v1/policies", json={"kind": "model", "document": model_body})
    policy_id = created.json()["id"]

    row = client.get("/api/v1/secrets/beta-key").json()
    refused = client.delete("/api/v1/secrets/beta-key")

    assert row["named_by"] == [{"kind": "model", "id": policy_id, "server": "beta"}]
    assert refused.status_code == 409
    assert f"{policy_id} (provider beta)" in refused.json()["detail"]


def test_delete_of_an_unused_secret(client: TestClient) -> None:
    _secret(client, "alpha-token")

    assert client.delete("/api/v1/secrets/alpha-token").status_code == 204
    assert client.get("/api/v1/secrets/alpha-token").status_code == 404
    assert client.delete("/api/v1/secrets/alpha-token").status_code == 404
    assert _names(client) == []


def test_multi_line_value_is_accepted(client: TestClient) -> None:
    value = '{\n  "type": "service_account",\n  "project_id": "alpha"\n}'

    assert client.post("/api/v1/secrets", json={"name": "alpha", "value": value}).status_code == 201


@pytest.mark.parametrize("value", [" \n\t", "a\x00b", "x" * 8193])
def test_bad_value_is_unprocessable(client: TestClient, value: str) -> None:
    _secret(client, "alpha-token")

    assert client.post("/api/v1/secrets", json={"name": "beta", "value": value}).status_code == 422
    rotate = client.post("/api/v1/secrets/alpha-token/rotate", json={"value": value})
    assert rotate.status_code == 422


def test_audit_by_secret_holds_its_events_and_issues(
    client: TestClient,
    register: Register,
    spec_body: dict[str, Any],
    mcp_body: dict[str, Any],
) -> None:
    _secret(client, "alpha-token")
    _secret(client, "alpha-token2")
    run, _ = _issue(client, register, spec_body, _mcp_policy(client, mcp_body))
    client.post("/api/v1/secrets/alpha-token/rotate", json={"value": ROTATED})

    trail = client.get("/api/v1/audit", params={"secret": "alpha-token"}).json()

    assert [row["event"] for row in trail] == [
        "secret_created",
        "credentials_issued",
        "secret_rotated",
    ]
    assert trail[1]["run_id"] == run["id"]
    assert client.get("/api/v1/audit", params={"secret": "alpha_token"}).json() == []


def test_no_response_or_audit_row_carries_a_value(
    client: TestClient, session: Session, mcp_body: dict[str, Any]
) -> None:
    texts = [
        client.post("/api/v1/secrets", json={"name": "alpha-token", "value": VALUE}).text,
        client.post("/api/v1/secrets/alpha-token/rotate", json={"value": ROTATED}).text,
        client.patch("/api/v1/secrets/alpha-token", json={"expires_at": None}).text,
        client.get("/api/v1/secrets").text,
        client.get("/api/v1/secrets/alpha-token").text,
    ]
    _mcp_policy(client, mcp_body)
    texts.append(client.delete("/api/v1/secrets/alpha-token").text)
    texts.append(client.get("/api/v1/audit", params={"secret": "alpha-token"}).text)
    rows = session.exec(select(AuditEvent)).all()

    for text in texts + [str(row.data) for row in rows]:
        assert VALUE not in text
        assert ROTATED not in text
