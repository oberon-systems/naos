from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, update

from naos_api import runs
from naos_api.lifecycle import RunStatus
from naos_api.models import Run, Runner

Register = Callable[..., dict[str, str]]
PROFILE_SPEC: dict[str, Any] = {
    "runtime": {"cpu": 2, "memory_mib": 4096, "disk_gib": 20},
    "merge": {"policy": "ask"},
    "timeout": 3600,
}
BIGGER = {**PROFILE_SPEC, "runtime": {"cpu": 4, "memory_mib": 8192, "disk_gib": 20}}


@pytest.fixture
def image(spec_body: dict[str, Any]) -> dict[str, Any]:
    ref: dict[str, Any] = spec_body["image"]
    return ref


def _create(
    client: TestClient, name: str = "alpha", spec: dict[str, Any] = PROFILE_SPEC, status: int = 201
) -> dict[str, Any]:
    response = client.post("/api/v1/profiles", json={"name": name, "spec": spec})
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    return body


def _run(
    client: TestClient,
    profile_id: str,
    image: dict[str, Any],
    key: str = "alpha",
    runner: str | None = None,
    status: int = 201,
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/profiles/{profile_id}/runs",
        json={"image": image, "runner": runner},
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    return body


def _update(client: TestClient, profile_id: str, spec: dict[str, Any]) -> Any:
    return client.put(f"/api/v1/profiles/{profile_id}", json={"spec": spec})


def test_a_profile_is_created_and_replayed_by_name(client: TestClient) -> None:
    created = _create(client)
    replayed = _create(client, status=200)

    assert created["id"].startswith("prof_")
    assert replayed["id"] == created["id"]
    assert created["active_runs"] == 0
    assert created["active_run"] is None
    assert created["last_run_at"] is None


def test_a_taken_name_with_another_spec_is_refused(client: TestClient) -> None:
    _create(client)

    response = client.post("/api/v1/profiles", json={"name": "alpha", "spec": BIGGER})

    assert response.status_code == 409


def test_a_profile_refuses_image_and_runner(client: TestClient, image: dict[str, Any]) -> None:
    for extra in ({"image": image}, {"runner": "rnr_alpha"}):
        response = client.post(
            "/api/v1/profiles", json={"name": "alpha", "spec": {**PROFILE_SPEC, **extra}}
        )
        assert response.status_code == 422


def test_a_profile_refuses_an_unknown_policy(client: TestClient) -> None:
    spec = {**PROFILE_SPEC, "network": {"policy": "netpol_" + "0" * 32}}

    assert client.post("/api/v1/profiles", json={"name": "alpha", "spec": spec}).status_code == 422


def test_the_list_searches_name_and_id(client: TestClient) -> None:
    alpha = _create(client, "alpha-build")
    _create(client, "beta-docs")

    by_name = client.get("/api/v1/profiles", params={"q": "ALPHA"}).json()
    by_id = client.get("/api/v1/profiles", params={"q": alpha["id"][5:15]}).json()
    everything = client.get("/api/v1/profiles").json()

    assert [row["name"] for row in by_name] == ["alpha-build"]
    assert [row["id"] for row in by_id] == [alpha["id"]]
    assert [row["name"] for row in everything] == ["alpha-build", "beta-docs"]


def test_a_run_copies_the_profile_and_records_it(
    client: TestClient, session: Session, image: dict[str, Any]
) -> None:
    profile = _create(client)

    run = _run(client, profile["id"], image)

    assert run["status"] == "PENDING"
    assert run["spec"]["runtime"] == PROFILE_SPEC["runtime"]
    assert run["spec"]["image"] == image
    assert run["spec"]["runner"] is None
    stored = session.get(Run, run["id"])
    assert stored is not None and stored.profile_id == profile["id"]
    assert client.get(f"/api/v1/runs/{run['id']}").json()["profile_id"] == profile["id"]
    listed = client.get(f"/api/v1/profiles/{profile['id']}").json()
    assert listed["active_runs"] == 1
    assert listed["active_run"] == {"id": run["id"], "seq": run["seq"], "status": "PENDING"}
    assert listed["last_run_at"] == run["created_at"]


def test_one_key_creates_one_run(
    client: TestClient, session: Session, image: dict[str, Any]
) -> None:
    profile = _create(client)

    first = _run(client, profile["id"], image)
    second = _run(client, profile["id"], image, status=200)

    assert first["id"] == second["id"]
    assert len(runs.list_runs(session)) == 1


def test_a_key_reused_for_another_profile_conflicts(
    client: TestClient, image: dict[str, Any]
) -> None:
    alpha = _create(client, "alpha")
    beta = _create(client, "beta")
    _run(client, alpha["id"], image)

    _run(client, beta["id"], image, status=409)


def test_an_update_is_refused_while_a_run_is_active(
    client: TestClient, session: Session, image: dict[str, Any]
) -> None:
    profile = _create(client)
    run = _run(client, profile["id"], image)

    refused = _update(client, profile["id"], BIGGER)

    assert refused.status_code == 409
    assert f"#{run['seq']} is PENDING" in refused.json()["detail"]
    assert client.get(f"/api/v1/profiles/{profile['id']}").json()["spec"] == PROFILE_SPEC | {
        "mounts": {"policy": None},
        "network": {"policy": None},
        "shell": {"policy": None},
        "mcp": {"policy": None},
    }


def test_an_unchanged_update_replays_while_a_run_is_active(
    client: TestClient, image: dict[str, Any]
) -> None:
    profile = _create(client)
    _run(client, profile["id"], image)

    assert _update(client, profile["id"], PROFILE_SPEC).status_code == 200


def test_an_update_frees_once_the_run_is_terminal_and_never_reaches_it(
    client: TestClient, session: Session, image: dict[str, Any]
) -> None:
    profile = _create(client)
    run = _run(client, profile["id"], image)
    runs.stop_run(session, run["id"])

    updated = _update(client, profile["id"], BIGGER)

    assert updated.status_code == 200, updated.text
    assert updated.json()["spec"]["runtime"] == BIGGER["runtime"]
    assert updated.json()["active_runs"] == 0
    started = client.get(f"/api/v1/runs/{run['id']}").json()
    assert started["spec"]["runtime"] == PROFILE_SPEC["runtime"]


def test_a_run_pins_a_known_runner(
    client: TestClient, register: Register, image: dict[str, Any]
) -> None:
    runner = register("alpha")
    profile = _create(client)

    run = _run(client, profile["id"], image, runner=runner["runner_id"])

    assert run["spec"]["runner"] == runner["runner_id"]


def test_a_run_refuses_an_unknown_or_revoked_runner(
    client: TestClient, session: Session, register: Register, image: dict[str, Any]
) -> None:
    profile = _create(client)
    _run(client, profile["id"], image, key="unknown", runner="rnr_missing", status=422)

    runner = register("alpha")
    session.exec(update(Runner).where(col(Runner.id) == runner["runner_id"]).values(revoked_at=1))
    session.commit()

    _run(client, profile["id"], image, key="revoked", runner=runner["runner_id"], status=422)


def test_a_pinned_run_is_claimed_only_by_its_runner(
    client: TestClient, session: Session, register: Register, image: dict[str, Any]
) -> None:
    alpha, beta = register("alpha"), register("beta")
    profile = _create(client)
    run = _run(client, profile["id"], image, runner=beta["runner_id"])

    def beat(runner: dict[str, str]) -> None:
        response = client.post(
            f"/api/v1/runners/{runner['runner_id']}/heartbeat",
            json={"capacity": 4},
            headers={"Authorization": f"Bearer {runner['token']}"},
        )
        assert response.status_code == 200, response.text

    beat(alpha)
    session.expire_all()
    assert runs.get_run(session, run["id"]).lease_id is None

    beat(beta)
    session.expire_all()
    claimed = runs.get_run(session, run["id"])
    assert claimed.status is RunStatus.PENDING
    assert claimed.lease_id is not None


def test_policies_are_listed_by_kind(
    client: TestClient, network_body: dict[str, Any], shell_body: dict[str, Any]
) -> None:
    network = client.post("/api/v1/policies", json={"kind": "network", "document": network_body})
    shell = client.post("/api/v1/policies", json={"kind": "shell", "document": shell_body})

    everything = client.get("/api/v1/policies").json()
    only_shell = client.get("/api/v1/policies", params={"kind": "shell"}).json()

    assert {row["id"] for row in everything} == {network.json()["id"], shell.json()["id"]}
    assert [row["id"] for row in only_shell] == [shell.json()["id"]]
    assert client.get("/api/v1/policies", params={"kind": "disk"}).status_code == 422


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/profiles"),
        ("POST", "/api/v1/profiles"),
        ("GET", "/api/v1/profiles/prof_alpha"),
        ("PUT", "/api/v1/profiles/prof_alpha"),
        ("POST", "/api/v1/profiles/prof_alpha/runs"),
        ("GET", "/api/v1/policies"),
    ],
)
def test_new_routes_need_the_operator_token(raw_client: TestClient, method: str, path: str) -> None:
    assert raw_client.request(method, path).status_code == 401
