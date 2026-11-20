import re
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from naos_api import policies
from naos_api.shell import ShellPolicyIn
from naos_api.spec import PolicyKind

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


def test_a_policy_nothing_names_is_deleted_and_audited(client: TestClient) -> None:
    created = _create(client, SHELL, "alpha").json()
    path = f"/api/v1/policies/{created['id']}"

    assert client.delete(path).status_code == 204

    assert client.get(path).status_code == 404
    assert client.delete(path).status_code == 404
    [event] = client.get("/api/v1/audit", params={"event": "policy_deleted"}).json()
    assert event["data"] == {"policy_id": created["id"], "kind": "shell", "name": "alpha"}


def test_a_policy_a_profile_names_stays(client: TestClient) -> None:
    created = _create(client, SHELL, "shell_alpha").json()
    spec = {
        "runtime": {"cpu": 2, "memory_mib": 4096, "disk_gib": 20},
        "merge": {"policy": "ask"},
        "timeout": 3600,
        "shell": {"policy": created["id"]},
    }
    assert client.post("/api/v1/profiles", json={"name": "alpha", "spec": spec}).status_code == 201

    refused = client.delete(f"/api/v1/policies/{created['id']}")

    assert refused.status_code == 409
    assert "policy shell_alpha is named by 1 profile: alpha" in refused.json()["detail"]
    assert client.get("/api/v1/audit", params={"event": "policy_deleted"}).json() == []


def test_an_unfinished_run_holds_its_policy(
    client: TestClient, spec_body: dict[str, Any], create_run: Callable[[str], str]
) -> None:
    create_run("key-1")
    path = f"/api/v1/policies/{spec_body['model']['policy']}"

    refused = client.delete(path)

    assert refused.status_code == 409
    assert re.search(r"used by 1 unfinished run: #\d+ \(PENDING\)", refused.json()["detail"])
    assert client.get(path).status_code == 200


def test_a_policy_goes_once_its_runs_have_ended(
    client: TestClient, spec_body: dict[str, Any], create_run: Callable[[str], str]
) -> None:
    run_id = create_run("key-1")
    client.post(f"/api/v1/runs/{run_id}/stop")
    path = f"/api/v1/policies/{spec_body['model']['policy']}"

    assert client.delete(path).status_code == 204
    assert client.get(path).status_code == 404
    assert client.get(f"/api/v1/runs/{run_id}").status_code == 200


def test_a_delete_and_a_reference_check_lock_the_policy_row(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    created, _ = policies.create_shell_policy(session, ShellPolicyIn(allow=["read_file"]))
    seen: list[Any] = []
    original = Session.exec

    def recording(self: Session, statement: Any, *args: Any, **kwargs: Any) -> Any:
        seen.append(getattr(statement, "_for_update_arg", None))
        return original(self, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "exec", recording)
    spec = SimpleNamespace(policy_refs=lambda: {PolicyKind.SHELL: created.id})
    policies.check_refs(session, cast(Any, spec))
    referenced = [lock for lock in seen if lock is not None]
    seen.clear()
    policies.delete_policy(session, created.id)
    deleted = [lock for lock in seen if lock is not None]

    assert [lock.key_share for lock in referenced] == [True]
    assert [lock.key_share for lock in deleted] == [False]
