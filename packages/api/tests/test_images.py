from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

DIGEST = "sha256:" + "b" * 64
OTHER_DIGEST = "sha256:" + "c" * 64
URL = "https://images.example.com/releases/download/image-2.0.0/naos-agents-2.0.0.qcow2"
BODY = {"id": "image_beta", "version": "2.0.0", "digest": DIGEST, "url": URL}
KEY = {"Idempotency-Key": "key-1"}


def _register(client: TestClient, **overrides: str) -> httpx.Response:
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
    ],
)
def test_bad_registration_is_unprocessable(client: TestClient, overrides: dict[str, str]) -> None:
    assert _register(client, **overrides).status_code == 422


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
