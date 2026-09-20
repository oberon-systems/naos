from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session

from naos_api import runners
from naos_api.lifecycle import RunStatus

S = RunStatus
LEASE_TTL = 60
Register = Callable[..., dict[str, str]]
Advance = Callable[[float], None]

ENTRIES: list[dict[str, Any]] = [
    {"path": "docs", "change": "deleted", "kind": "dir"},
    {
        "path": "docs/x.md",
        "change": "deleted",
        "kind": "file",
        "size": 2,
        "sha256": "a" * 64,
        "mode": 0o644,
    },
    {"path": "newdir", "change": "created", "kind": "dir"},
    {
        "path": "newdir/f",
        "change": "created",
        "kind": "file",
        "size": 2,
        "sha256": "b" * 64,
        "mode": 0o644,
    },
    {
        "path": "notes.txt",
        "change": "modified",
        "kind": "file",
        "size": 5,
        "sha256": "c" * 64,
        "mode": 0o644,
        "base_sha256": "d" * 64,
        "base_mode": 0o644,
    },
    {"path": "pipe", "change": "rejected", "kind": "other", "reason": "special file"},
]
SENSITIVE: dict[str, Any] = {
    "path": "Makefile",
    "change": "created",
    "kind": "file",
    "size": 1,
    "sha256": "e" * 64,
    "mode": 0o644,
    "sensitive": True,
}
MERGEABLE = ["docs", "docs/x.md", "newdir", "newdir/f", "notes.txt"]


def _bearer(runner: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {runner['token']}"}


def _heartbeat(client: TestClient, runner: dict[str, str]) -> None:
    response = client.post(
        f"/api/v1/runners/{runner['runner_id']}/heartbeat",
        json={"capacity": 1},
        headers=_bearer(runner),
    )
    assert response.status_code == 200, response.text
    runner["lease_id"] = response.json()["lease"]["id"]


def _runner_post(
    client: TestClient, runner: dict[str, str], run_id: str, action: str, body: dict[str, Any]
) -> Response:
    response: Response = client.post(
        f"/api/v1/runners/{runner['runner_id']}/runs/{run_id}/{action}",
        json={"lease_id": runner["lease_id"], **body},
        headers=_bearer(runner),
    )
    return response


def _desired(client: TestClient, runner: dict[str, str]) -> dict[str, Any]:
    response = client.get(f"/api/v1/runners/{runner['runner_id']}/runs", headers=_bearer(runner))
    assert response.status_code == 200, response.text
    return {run["id"]: run for run in response.json()["runs"]}


def _collecting(
    client: TestClient,
    register: Register,
    spec_body: dict[str, Any],
    policy: str | None = None,
) -> tuple[dict[str, str], str]:
    body = {**spec_body, "merge": {"policy": policy}} if policy else spec_body
    created = client.post("/api/v1/runs", json=body, headers={"Idempotency-Key": "key-1"})
    assert created.status_code == 201, created.text
    run_id: str = created.json()["id"]
    runner = register()
    _heartbeat(client, runner)
    walk = [S.PENDING, S.STARTING, S.STARTED, S.STOPPING, S.COLLECTING]
    for expected, target in zip(walk, walk[1:], strict=False):
        moved = _runner_post(
            client, runner, run_id, "transition", {"expected": expected, "target": target}
        )
        assert moved.status_code == 200, moved.text
    return runner, run_id


def _waiting(
    client: TestClient,
    register: Register,
    spec_body: dict[str, Any],
    policy: str | None = None,
    entries: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, str], str]:
    runner, run_id = _collecting(client, register, spec_body, policy)
    reported = _runner_post(client, runner, run_id, "diff", {"entries": entries or ENTRIES})
    assert reported.status_code == 200, reported.text
    assert reported.json()["status"] == S.WAITING_MERGE
    return runner, run_id


def _decide(client: TestClient, run_id: str, body: dict[str, Any]) -> Response:
    response: Response = client.post(f"/api/v1/runs/{run_id}/merge", json=body)
    return response


def test_ask_waits_for_an_operator_decision(
    client: TestClient, register: Register, spec_body: dict[str, Any]
) -> None:
    runner, run_id = _waiting(client, register, spec_body)

    assert _desired(client, runner)[run_id]["merge"] is None
    shown = client.get(f"/api/v1/runs/{run_id}/merge").json()
    assert [entry["path"] for entry in shown["entries"]] == [*MERGEABLE, "pipe"]
    assert _decide(client, run_id, {"paths": ["notes.txt"]}).status_code == 200
    assert _desired(client, runner)[run_id]["merge"] == {
        "paths": ["notes.txt"],
        "resolutions": {},
    }

    done = _runner_post(
        client, runner, run_id, "merge", {"outcome": "applied", "applied": ["notes.txt"]}
    )

    assert done.status_code == 200, done.text
    assert done.json()["status"] == S.COMPLETED
    report = client.get(f"/api/v1/runs/{run_id}/merge").json()["report"]
    assert report == {"applied": ["notes.txt"], "skipped": [], "exported": [], "backed_up": []}


def test_always_decides_every_mergeable_path(
    client: TestClient, register: Register, spec_body: dict[str, Any]
) -> None:
    runner, run_id = _waiting(client, register, spec_body, "always")

    assert _desired(client, runner)[run_id]["merge"] == {"paths": MERGEABLE, "resolutions": {}}


def test_always_waits_when_a_sensitive_path_changed(
    client: TestClient, register: Register, spec_body: dict[str, Any]
) -> None:
    runner, run_id = _waiting(client, register, spec_body, "always", [*ENTRIES, SENSITIVE])

    assert _desired(client, runner)[run_id]["merge"] is None


def test_never_merges_nothing_and_completes(
    client: TestClient, register: Register, spec_body: dict[str, Any]
) -> None:
    runner, run_id = _waiting(client, register, spec_body, "never")

    assert _desired(client, runner)[run_id]["merge"] == {"paths": [], "resolutions": {}}
    done = _runner_post(client, runner, run_id, "merge", {"outcome": "applied"})
    assert done.json()["status"] == S.COMPLETED


def test_reject_merges_nothing(
    client: TestClient, register: Register, spec_body: dict[str, Any]
) -> None:
    runner, run_id = _waiting(client, register, spec_body)

    assert client.post(f"/api/v1/runs/{run_id}/merge/reject").status_code == 200
    assert _desired(client, runner)[run_id]["merge"] == {"paths": [], "resolutions": {}}


def test_a_conflict_keeps_the_run_waiting_for_a_new_decision(
    client: TestClient, register: Register, spec_body: dict[str, Any]
) -> None:
    runner, run_id = _waiting(client, register, spec_body)
    assert _decide(client, run_id, {"paths": ["notes.txt"]}).status_code == 200
    assert _decide(client, run_id, {"paths": ["notes.txt"]}).status_code == 409
    conflict = {"path": "notes.txt", "reason": "the host changed since collection"}

    reported = _runner_post(
        client, runner, run_id, "merge", {"outcome": "conflict", "conflicts": [conflict]}
    )

    assert reported.json()["status"] == S.WAITING_MERGE
    shown = client.get(f"/api/v1/runs/{run_id}/merge").json()
    assert shown["decision"] is None
    assert shown["conflicts"] == [conflict]
    assert _desired(client, runner)[run_id]["merge"] is None
    retry = {"paths": ["notes.txt"], "resolutions": {"notes.txt": "take"}}
    assert _decide(client, run_id, retry).status_code == 200
    assert _desired(client, runner)[run_id]["merge"] == retry


@pytest.mark.parametrize(
    "body",
    [
        {"paths": ["pipe"]},
        {"paths": ["../notes.txt"]},
        {"paths": ["docs"]},
        {"paths": ["newdir/f"]},
        {"paths": ["notes.txt"], "resolutions": {"docs/x.md": "skip"}},
        {"paths": ["notes.txt"], "resolutions": {"notes.txt": "overwrite"}},
    ],
)
def test_a_selection_must_fit_the_diff(
    client: TestClient, register: Register, spec_body: dict[str, Any], body: dict[str, Any]
) -> None:
    _, run_id = _waiting(client, register, spec_body)

    assert _decide(client, run_id, body).status_code == 422
    assert client.get(f"/api/v1/runs/{run_id}/merge").json()["decision"] is None


def test_a_deleted_directory_needs_the_rename_out_of_it(
    client: TestClient, register: Register, spec_body: dict[str, Any]
) -> None:
    renamed = {
        "path": "moved.md",
        "change": "renamed",
        "kind": "file",
        "from": "docs/x.md",
        "size": 2,
        "sha256": "a" * 64,
        "mode": 0o644,
        "base_mode": 0o644,
    }
    entries = [ENTRIES[0], renamed]
    _, run_id = _waiting(client, register, spec_body, entries=entries)

    refused = _decide(client, run_id, {"paths": ["docs"]})

    assert refused.status_code == 422
    assert "moved.md" in refused.json()["detail"]
    assert _decide(client, run_id, {"paths": ["docs", "moved.md"]}).status_code == 200


def test_a_decision_needs_a_waiting_run(
    client: TestClient, register: Register, spec_body: dict[str, Any]
) -> None:
    _, run_id = _collecting(client, register, spec_body)

    assert _decide(client, run_id, {"paths": []}).status_code == 409
    assert client.get(f"/api/v1/runs/{run_id}/merge").status_code == 404


def test_the_diff_report_is_idempotent_and_owned(
    client: TestClient, register: Register, spec_body: dict[str, Any]
) -> None:
    runner, run_id = _collecting(client, register, spec_body)
    other = register("beta")
    _heartbeat(client, other)

    assert _runner_post(client, other, run_id, "diff", {"entries": ENTRIES}).status_code == 404
    stale = {**runner, "lease_id": "lease_" + "0" * 32}
    assert _runner_post(client, stale, run_id, "diff", {"entries": ENTRIES}).status_code == 409
    for _ in range(2):
        reported = _runner_post(client, runner, run_id, "diff", {"entries": ENTRIES})
        assert reported.status_code == 200, reported.text
    assert reported.json()["status"] == S.WAITING_MERGE


def test_a_merge_report_needs_a_waiting_run_and_names_its_conflicts(
    client: TestClient, register: Register, spec_body: dict[str, Any]
) -> None:
    runner, run_id = _collecting(client, register, spec_body)

    early = _runner_post(client, runner, run_id, "merge", {"outcome": "applied"})
    empty = _runner_post(client, runner, run_id, "merge", {"outcome": "conflict"})

    assert early.status_code == 409
    assert empty.status_code == 422


def test_a_waiting_run_follows_its_runner_to_the_next_lease(
    client: TestClient,
    register: Register,
    spec_body: dict[str, Any],
    session: Session,
    clock: Callable[[], int],
    advance: Advance,
) -> None:
    runner, run_id = _waiting(client, register, spec_body)
    old_lease = runner["lease_id"]

    advance(LEASE_TTL)
    runners.expire_leases(session, clock())
    _heartbeat(client, runner)

    assert runner["lease_id"] != old_lease
    assert run_id in _desired(client, runner)
    assert _decide(client, run_id, {"paths": []}).status_code == 200
    done = _runner_post(client, runner, run_id, "merge", {"outcome": "applied"})
    assert done.json()["status"] == S.COMPLETED


def test_a_runner_can_fail_a_waiting_run(
    client: TestClient, register: Register, spec_body: dict[str, Any]
) -> None:
    runner, run_id = _waiting(client, register, spec_body)

    failed = _runner_post(
        client,
        runner,
        run_id,
        "transition",
        {"expected": S.WAITING_MERGE, "target": S.FAILED, "reason": "vm lost"},
    )

    assert failed.json()["status"] == S.FAILED
