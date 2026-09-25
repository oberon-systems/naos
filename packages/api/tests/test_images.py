from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

DIGEST = "sha256:" + "b" * 64
OTHER_DIGEST = "sha256:" + "c" * 64
URL = "https://images.example.com/releases/download/image-2.0.0/naos-agents-2.0.0.qcow2"
BODY = {"id": "image_beta", "version": "2.0.0", "digest": DIGEST, "url": URL}
FACTS = {"name": "beta", "size_bytes": 4_080_218_931, "built_at": 1767139200}
KEY = {"Idempotency-Key": "key-1"}


def _register(client: TestClient, **overrides: Any) -> httpx.Response:
    response: httpx.Response = client.post("/api/v1/images", json=BODY | overrides)
    return response


def test_registration_stores_the_catalog_entry(client: TestClient) -> None:
    created = _register(client)

    assert created.status_code == 201
    assert {key: created.json()[key] for key in BODY} == BODY
    assert client.get("/api/v1/images/image_beta").json() == created.json()
    assert [image["id"] for image in client.get("/api/v1/images").json()] == ["image_beta"]


def test_repeated_registration_is_idempotent(client: TestClient) -> None:
    created = _register(client)

    again = _register(client)

    assert again.status_code == 200
    assert again.json() == created.json()


@pytest.mark.parametrize(
    "overrides",
    [
        {"digest": OTHER_DIGEST},
        {"version": "3.0.0"},
        {"id": "image_gamma"},
        {"url": "https://mirror.example.com/naos-agents-2.0.0.qcow2"},
    ],
)
def test_conflicting_registration_is_refused(client: TestClient, overrides: dict[str, str]) -> None:
    _register(client)

    assert _register(client, **overrides).status_code == 409


@pytest.mark.parametrize(
    "overrides",
    [
        {"digest": "sha256:xyz"},
        {"id": "../beta"},
        {"version": "1.0/../x"},
        {"url": "http://images.example.com/naos-agents.qcow2"},
        {"url": "ftp://images.example.com/naos-agents.qcow2"},
        {"url": "https://user:secret@images.example.com/naos-agents.qcow2"},
        {"url": "not a url"},
        {"name": "beta/../x"},
        {"size_bytes": 0},
        {"built_at": -1},
    ],
)
def test_bad_registration_is_unprocessable(client: TestClient, overrides: dict[str, Any]) -> None:
    assert _register(client, **overrides).status_code == 422


def test_stated_facts_are_kept(client: TestClient) -> None:
    created = _register(client, **FACTS)

    assert created.status_code == 201
    assert {key: created.json()[key] for key in FACTS} == FACTS
    assert client.get("/api/v1/images/image_beta").json() == created.json()


def test_facts_are_optional(client: TestClient) -> None:
    created = _register(client).json()

    assert (created["name"], created["size_bytes"], created["built_at"]) == (None, None, None)


@pytest.mark.parametrize("overrides", [{"name": "gamma"}, {"size_bytes": 1}, {"built_at": 0}])
def test_other_facts_are_a_conflict(client: TestClient, overrides: dict[str, Any]) -> None:
    _register(client, **FACTS)

    assert _register(client, **(FACTS | overrides)).status_code == 409


def test_an_image_counts_the_runs_booting_it(
    client: TestClient, create_run: Callable[[str], str]
) -> None:
    _register(client)
    stopped = create_run("key-1")
    create_run("key-2")
    client.post(f"/api/v1/runs/{stopped}/stop")

    counts = {
        image["id"]: (image["runs_open"], image["runs_total"])
        for image in client.get("/api/v1/images").json()
    }

    assert counts == {"image_alpha": (1, 2), "image_beta": (0, 0)}
    assert client.get("/api/v1/images/image_alpha").json()["runs_open"] == 1


def test_runs_are_filtered_by_image(client: TestClient, create_run: Callable[[str], str]) -> None:
    run_id = create_run("key-1")

    booting = client.get("/api/v1/runs", params={"image": "image_alpha"}).json()

    assert [run["id"] for run in booting] == [run_id]
    assert client.get("/api/v1/runs", params={"image": "image_beta"}).json() == []


def test_audit_is_filtered_by_image(client: TestClient, create_run: Callable[[str], str]) -> None:
    run_id = create_run("key-1")
    _register(client)

    alpha = client.get("/api/v1/audit", params={"image_id": "image_alpha"}).json()
    beta = client.get("/api/v1/audit", params={"image_id": "image_beta"}).json()

    assert {row["run_id"] for row in alpha} == {run_id}
    assert [(row["event"], row["data"]["image_id"]) for row in beta] == [
        ("image_registered", "image_beta")
    ]


def test_registration_needs_a_url(client: TestClient) -> None:
    body = {key: value for key, value in BODY.items() if key != "url"}

    assert client.post("/api/v1/images", json=body).status_code == 422


def test_unknown_image_is_not_found(client: TestClient) -> None:
    assert client.get("/api/v1/images/image_missing").status_code == 404


@pytest.mark.parametrize(
    "image",
    [
        {"id": "image_missing", "digest": DIGEST},
        {"id": "image_alpha", "digest": OTHER_DIGEST},
    ],
)
def test_run_needs_a_registered_image(
    client: TestClient, spec_body: dict[str, Any], image: dict[str, str]
) -> None:
    spec_body["image"] = image

    assert client.post("/api/v1/runs", json=spec_body, headers=KEY).status_code == 422
