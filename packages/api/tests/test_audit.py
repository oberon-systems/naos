import json
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session, col, select

from naos_api.clock import now_ts
from naos_api.lifecycle import TaskStatus
from naos_api.models import AuditEvent

S = TaskStatus
LEASE_TTL = 60
TOKEN_TTL = 3600
ENROLLMENT_TOKEN = "enroll-alpha-" + "0" * 32
Register = Callable[..., dict[str, str]]
CreateTask = Callable[[str], str]
Advance = Callable[[int], None]
ALPHA_VALUE = "secret-alpha-value"


def _bearer(runner: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {runner['token']}"}


def _heartbeat(client: TestClient, runner: dict[str, str]) -> None:
    response = client.post(
        f"/api/v1/runners/{runner['runner_id']}/heartbeat",
        json={"capacity": 1},
        headers=_bearer(runner),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    runner["lease_id"] = body["lease"]["id"]
    if body["token"]:
        runner["token"] = body["token"]["value"]


def _runner_post(
    client: TestClient, runner: dict[str, str], task_id: str, action: str, body: dict[str, Any]
) -> None:
    response = client.post(
        f"/api/v1/runners/{runner['runner_id']}/tasks/{task_id}/{action}",
        json={"lease_id": runner["lease_id"], **body},
        headers=_bearer(runner),
    )
    assert response.status_code == 200, response.text


def _walk(client: TestClient, runner: dict[str, str], task_id: str, walk: list[S]) -> None:
    for expected, target in zip(walk, walk[1:], strict=False):
        _runner_post(
            client, runner, task_id, "transition", {"expected": expected, "target": target}
        )


def _event(name: str, **fields: Any) -> dict[str, Any]:
    return {"id": f"evt_{uuid4().hex}", "at": now_ts(), "event": name, **fields}


def _post_events(
    client: TestClient, runner: dict[str, str], events: list[dict[str, Any]]
) -> Response:
    response: Response = client.post(
        f"/api/v1/runners/{runner['runner_id']}/events",
        json={"events": events},
        headers=_bearer(runner),
    )
    return response


def _rows(session: Session) -> list[AuditEvent]:
    session.expire_all()
    return list(session.exec(select(AuditEvent).order_by(col(AuditEvent.seq))).all())


def test_operator_changes_are_recorded(
    client: TestClient, session: Session, shell_body: dict[str, Any]
) -> None:
    image = {
        "id": "image_beta",
        "version": "1.0.0",
        "digest": "sha256:" + "b" * 64,
        "url": "https://images.example.com/beta.qcow2",
    }
    assert client.post("/api/v1/images", json=image).status_code == 201
    assert client.post("/api/v1/images", json=image).status_code == 200
    policy = client.post("/api/v1/policies", json={"kind": "shell", "document": shell_body})
    secret = {"name": "alpha-token", "value": ALPHA_VALUE}
    assert client.post("/api/v1/secrets", json=secret).status_code == 201

    rows = _rows(session)
    assert [row.event for row in rows] == ["image_registered", "policy_created", "secret_created"]
    assert rows[0].data == {"image_id": "image_beta", "version": "1.0.0", "digest": image["digest"]}
    assert rows[1].data == {"policy_id": policy.json()["id"], "kind": "shell"}
    assert rows[2].data == {"name": "alpha-token"}
    assert {row.actor for row in rows} == {"operator"}


def test_task_creation_and_stop_are_recorded(
    client: TestClient, create_task: CreateTask, session: Session
) -> None:
    task_id = create_task("key-1")
    client.post(f"/api/v1/tasks/{task_id}/stop")

    rows = _rows(session)
    assert [row.event for row in rows] == ["task_created", "task_stop_requested", "task_transition"]
    assert {row.run_id for row in rows} == {task_id}
    assert rows[1].data == {"status": "PENDING"}
    assert rows[2].data == {"from": "PENDING", "to": "CANCELLED", "reason": None}


def test_runner_lifecycle_is_recorded(
    client: TestClient,
    register: Register,
    create_task: CreateTask,
    session: Session,
    advance: Advance,
) -> None:
    task_id = create_task("key-1")
    runner = register()
    _heartbeat(client, runner)
    _walk(client, runner, task_id, [S.PENDING, S.STARTING, S.STARTED])
    first_lease = runner["lease_id"]

    advance(max(LEASE_TTL, TOKEN_TTL // 2))
    _heartbeat(client, runner)

    rows = [row for row in _rows(session) if row.event != "task_created"]
    assert [(row.event, row.actor) for row in rows] == [
        ("runner_registered", "runner"),
        ("lease_acquired", "runner"),
        ("task_transition", "runner"),
        ("task_transition", "runner"),
        ("lease_expired", "system"),
        ("task_transition", "system"),
        ("lease_acquired", "runner"),
        ("token_rotated", "runner"),
    ]
    assert {row.runner_id for row in rows} == {runner["runner_id"]}
    assert rows[4].data == {"lease_id": first_lease}
    assert rows[5].data["to"] == "FAILED"
    assert rows[6].data == {"lease_id": runner["lease_id"]}


def test_issued_credentials_are_recorded_by_name(
    client: TestClient,
    register: Register,
    spec_body: dict[str, Any],
    mcp_body: dict[str, Any],
    session: Session,
) -> None:
    client.post("/api/v1/secrets", json={"name": "alpha-token", "value": ALPHA_VALUE})
    policy = client.post("/api/v1/policies", json={"kind": "mcp", "document": mcp_body}).json()
    spec_body["mcp"] = {"policy": policy["id"]}
    task = client.post("/api/v1/tasks", json=spec_body, headers={"Idempotency-Key": "k"}).json()
    runner = register()
    _heartbeat(client, runner)
    client.get(f"/api/v1/runners/{runner['runner_id']}/tasks", headers=_bearer(runner))

    issued = [row for row in _rows(session) if row.event == "credentials_issued"]
    assert [(row.run_id, row.data) for row in issued] == [(task["id"], {"names": ["alpha-token"]})]


def test_runner_events_are_validated(
    client: TestClient, register: Register, create_task: CreateTask, session: Session
) -> None:
    task_id = create_task("key-1")
    runner = register()
    _heartbeat(client, runner)
    other = register("beta")
    stranger = create_task("key-2")

    unknown = _post_events(client, runner, [_event("vm_exploded", run_id=task_id)])
    extra = _post_events(client, runner, [_event("run_claimed", run_id=task_id, note="x")])
    typed = _post_events(client, runner, [_event("lease_fenced", vms="2")])
    assert (unknown.status_code, extra.status_code, typed.status_code) == (422, 422, 422)

    mine = _event("run_claimed", run_id=task_id)
    foreign = _event("run_claimed", run_id=stranger)
    accepted = _post_events(client, runner, [mine, foreign, mine])
    assert accepted.json() == {"accepted": 1, "refused": [foreign["id"]]}
    again = _post_events(client, runner, [mine])
    assert again.json() == {"accepted": 0, "refused": []}
    assert _post_events(client, other, [mine]).json()["refused"] == [mine["id"]]

    stored = [row for row in _rows(session) if row.source == "runner"]
    assert [(row.id, row.run_id, row.runner_id) for row in stored] == [
        (mine["id"], task_id, runner["runner_id"])
    ]


def test_runner_events_need_runner_credentials(client: TestClient, register: Register) -> None:
    runner = register()
    response = client.post(
        f"/api/v1/runners/{runner['runner_id']}/events",
        json={"events": [_event("lease_fenced", vms=1)]},
        headers={"Authorization": f"Bearer {ENROLLMENT_TOKEN}"},
    )
    assert response.status_code == 401


def test_a_run_is_reconstructed_from_its_timeline(
    client: TestClient, register: Register, spec_body: dict[str, Any], session: Session
) -> None:
    created = client.post("/api/v1/tasks", json=spec_body, headers={"Idempotency-Key": "key-1"})
    task_id = created.json()["id"]
    runner = register()
    _heartbeat(client, runner)
    _walk(client, runner, task_id, [S.PENDING, S.STARTING])
    vm = {"run_id": task_id, "vm_id": "vm_alpha"}
    _post_events(client, runner, [_event("vm_created", **vm)])
    _walk(client, runner, task_id, [S.STARTING, S.STARTED, S.STOPPING, S.COLLECTING])
    entries = [{"path": "notes.txt", "change": "created", "kind": "file", "size": 1}]
    _post_events(
        client, runner, [_event("workspace_collected", run_id=task_id, entries=1, rejected=0)]
    )
    _runner_post(client, runner, task_id, "diff", {"entries": entries})
    client.post(f"/api/v1/tasks/{task_id}/merge", json={"paths": ["notes.txt"]})
    _runner_post(client, runner, task_id, "merge", {"outcome": "applied", "applied": ["notes.txt"]})
    _post_events(client, runner, [_event("changes_archived", **vm)])

    timeline = client.get(f"/api/v1/tasks/{task_id}/events").json()
    steps = [
        (row["event"], row["data"].get("to")) if row["event"] == "task_transition" else row["event"]
        for row in timeline
    ]
    assert steps == [
        "task_created",
        ("task_transition", "STARTING"),
        "vm_created",
        ("task_transition", "STARTED"),
        ("task_transition", "STOPPING"),
        ("task_transition", "COLLECTING"),
        "workspace_collected",
        "diff_reported",
        ("task_transition", "WAITING_MERGE"),
        "merge_decided",
        "merge_reported",
        ("task_transition", "COMPLETED"),
        "changes_archived",
    ]
    assert timeline[2]["vm_id"] == "vm_alpha" and timeline[2]["source"] == "runner"
    assert client.get("/api/v1/tasks/task_missing/events").status_code == 404

    audit = client.get("/api/v1/audit", params={"runner_id": runner["runner_id"], "limit": 3})
    page = audit.json()
    assert len(page) == 3
    rest = client.get("/api/v1/audit", params={"after": page[-1]["seq"], "event": "vm_created"})
    assert [row["event"] for row in rest.json()] == ["vm_created"]


def test_no_credential_reaches_the_audit_table(
    client: TestClient,
    register: Register,
    spec_body: dict[str, Any],
    mcp_body: dict[str, Any],
    session: Session,
    advance: Advance,
) -> None:
    client.post("/api/v1/secrets", json={"name": "alpha-token", "value": ALPHA_VALUE})
    policy = client.post("/api/v1/policies", json={"kind": "mcp", "document": mcp_body}).json()
    spec_body["mcp"] = {"policy": policy["id"]}
    client.post("/api/v1/tasks", json=spec_body, headers={"Idempotency-Key": "key-1"})
    runner = register()
    tokens = {runner["token"]}
    _heartbeat(client, runner)
    advance(TOKEN_TTL // 2)
    _heartbeat(client, runner)
    tokens.add(runner["token"])
    client.get(f"/api/v1/runners/{runner['runner_id']}/tasks", headers=_bearer(runner))

    dump = json.dumps([row.model_dump() for row in _rows(session)])
    assert "credentials_issued" in dump and "token_rotated" in dump
    for value in [*tokens, ENROLLMENT_TOKEN, ALPHA_VALUE]:
        assert value not in dump
