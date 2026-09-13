import hashlib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from naos_api.app import create_app
from naos_api.auth import require_principal
from naos_api.errors import ImageError, NotFoundError
from naos_api.images import service as images
from naos_api.images.store import FsImageStore
from naos_api.lifecycle import ImageStatus
from naos_api.models import Image
from naos_api.settings import Settings

PAYLOAD = b"naos-image-beta" * 4096
DIGEST = "sha256:" + hashlib.sha256(PAYLOAD).hexdigest()
OTHER_DIGEST = "sha256:" + "b" * 64
SEEDED_DIGEST = "sha256:" + "a" * 64
SOURCE = "https://images.example.com/releases/download/image-1.0.0/naos-agents-1.0.0.qcow2"
BODY = {"id": "image_beta", "version": "1.0.0", "digest": DIGEST}
KEY = {"Idempotency-Key": "key-1"}

Handler = Callable[[httpx.Request], httpx.Response]
Install = Callable[[Handler], list[httpx.Request]]
Register = Callable[..., dict[str, str]]
CreateTask = Callable[[str], str]
Configure = Callable[..., Settings]


@pytest.fixture
def source(client: TestClient) -> Install:
    app = client.app
    assert isinstance(app, FastAPI)

    def install(handler: Handler) -> list[httpx.Request]:
        seen: list[httpx.Request] = []

        def record(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        app.state.image_transport = httpx.MockTransport(record)
        return seen

    return install


def _serve(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=PAYLOAD)


def _import(client: TestClient, **overrides: str) -> httpx.Response:
    response: httpx.Response = client.post("/api/v1/images", json=BODY | overrides)
    return response


def _image(client: TestClient, image_id: str = "image_beta") -> dict[str, Any]:
    body: dict[str, Any] = client.get(f"/api/v1/images/{image_id}").json()
    return body


def _store_files(settings: Settings) -> list[str]:
    root = Path(settings.image_store_path)
    return sorted(path.name for path in root.iterdir()) if root.exists() else []


def _bearer(runner: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {runner['token']}"}


def _heartbeat(client: TestClient, runner: dict[str, str], capacity: int) -> None:
    response = client.post(
        f"/api/v1/runners/{runner['runner_id']}/heartbeat",
        json={"capacity": capacity},
        headers=_bearer(runner),
    )
    assert response.status_code == 200, response.text


def _image_path(runner: dict[str, str], digest: str = SEEDED_DIGEST) -> str:
    return f"/api/v1/runners/{runner['runner_id']}/images/{digest}"


def test_import_stores_a_verified_image(
    client: TestClient, source: Install, settings: Settings
) -> None:
    seen = source(_serve)

    created = _import(client)

    assert created.status_code == 202
    assert created.json()["status"] == "IMPORTING"
    assert [str(request.url) for request in seen] == [SOURCE]
    assert _image(client)["status"] == "READY"
    assert _image(client)["size_bytes"] == len(PAYLOAD)
    stored = Path(settings.image_store_path) / f"sha256-{DIGEST.removeprefix('sha256:')}.qcow2"
    assert stored.read_bytes() == PAYLOAD
    assert stored.stat().st_mode & 0o777 == 0o444
    assert [image["id"] for image in client.get("/api/v1/images").json()] == ["image_beta"]


def test_repeated_import_is_idempotent(client: TestClient, source: Install) -> None:
    seen = source(_serve)
    _import(client)

    again = _import(client)

    assert again.status_code == 200
    assert again.json()["status"] == "READY"
    assert len(seen) == 1


@pytest.mark.parametrize(
    "overrides", [{"digest": OTHER_DIGEST}, {"version": "2.0.0"}, {"id": "image_gamma"}]
)
def test_conflicting_registration_is_refused(
    client: TestClient, source: Install, overrides: dict[str, str]
) -> None:
    source(_serve)
    _import(client)

    assert _import(client, **overrides).status_code == 409


def test_redirect_to_an_allowed_host_is_followed(client: TestClient, source: Install) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "images.example.com":
            return httpx.Response(302, headers={"location": "https://objects.example.com/blob"})
        return httpx.Response(200, content=PAYLOAD)

    seen = source(handler)

    _import(client)

    assert _image(client)["status"] == "READY"
    assert [request.url.host for request in seen] == ["images.example.com", "objects.example.com"]


@pytest.mark.parametrize(
    ("handler", "reason"),
    [
        (lambda _: httpx.Response(200, content=PAYLOAD + b"x"), "digest"),
        (lambda _: httpx.Response(404), "404"),
        (lambda _: httpx.Response(200, content=b"x" * (2 * 1024 * 1024)), "exceeds"),
        (
            lambda _: httpx.Response(200, stream=httpx.ByteStream(b"x" * (2 * 1024 * 1024))),
            "exceeds",
        ),
        (
            lambda _: httpx.Response(302, headers={"location": "https://attacker.example.net/x"}),
            "not allowed",
        ),
        (
            lambda _: httpx.Response(302, headers={"location": "http://objects.example.com/x"}),
            "https",
        ),
        (lambda _: httpx.Response(302, headers={"location": SOURCE}), "too many"),
    ],
)
def test_failed_import_leaves_nothing_in_the_store(
    client: TestClient, source: Install, settings: Settings, handler: Handler, reason: str
) -> None:
    source(handler)

    _import(client)

    image = _image(client)
    assert image["status"] == "FAILED"
    assert reason in image["status_reason"]
    assert _store_files(settings) == []


def test_failed_import_can_be_retried(client: TestClient, source: Install) -> None:
    source(lambda _: httpx.Response(503))
    _import(client)
    source(_serve)

    retried = _import(client)

    assert retried.status_code == 202
    assert _image(client)["status"] == "READY"


@pytest.mark.parametrize(
    "overrides",
    [
        {"digest": "sha256:xyz"},
        {"id": "../beta"},
        {"version": "1.0/../x"},
        {"url": "https://attacker.example.net/image.qcow2"},
    ],
)
def test_bad_import_body_is_unprocessable(client: TestClient, overrides: dict[str, str]) -> None:
    assert _import(client, **overrides).status_code == 422


@pytest.mark.parametrize(
    "url",
    [
        None,
        "http://images.example.com/{version}",
        "http://127.0.0.1:8000/{version}",
        "ftp://images.example.com/{version}",
    ],
)
def test_import_needs_a_safe_source(
    settings: Settings, configure: Configure, url: str | None
) -> None:
    configure(image_source_url=url)
    app = create_app()
    app.dependency_overrides[require_principal] = lambda: None

    assert TestClient(app).post("/api/v1/images", json=BODY).status_code == 422


def test_unknown_image_is_not_found(client: TestClient) -> None:
    assert client.get("/api/v1/images/image_missing").status_code == 404


def test_restart_fails_interrupted_imports(session: Session, settings: Settings) -> None:
    session.add(Image(id="image_beta", version="1.0.0", digest=DIGEST))
    session.commit()

    create_app()

    session.expire_all()
    image = session.get(Image, "image_beta")
    assert image is not None
    assert image.status is ImageStatus.FAILED
    assert image.status_reason == images.INTERRUPTED_REASON


@pytest.mark.parametrize("status", [ImageStatus.IMPORTING, ImageStatus.FAILED])
def test_run_needs_a_ready_image(
    client: TestClient, session: Session, spec_body: dict[str, Any], status: ImageStatus
) -> None:
    session.add(Image(id="image_beta", version="1.0.0", digest=DIGEST, status=status))
    session.commit()
    spec_body["image"] = {"id": "image_beta", "digest": DIGEST}

    assert client.post("/api/v1/tasks", json=spec_body, headers=KEY).status_code == 422


@pytest.mark.parametrize(
    "image",
    [
        {"id": "image_missing", "digest": SEEDED_DIGEST},
        {"id": "image_alpha", "digest": OTHER_DIGEST},
    ],
)
def test_run_needs_a_registered_image(
    client: TestClient, spec_body: dict[str, Any], image: dict[str, str]
) -> None:
    spec_body["image"] = image

    assert client.post("/api/v1/tasks", json=spec_body, headers=KEY).status_code == 422


def test_runner_downloads_only_the_image_of_its_own_run(
    client: TestClient, register: Register, create_task: CreateTask, settings: Settings
) -> None:
    FsImageStore(Path(settings.image_store_path)).write_atomic(SEEDED_DIGEST, [PAYLOAD])
    create_task("key-1")
    alpha, beta = register("alpha"), register("beta")
    _heartbeat(client, alpha, capacity=1)
    _heartbeat(client, beta, capacity=1)

    response = client.get(_image_path(alpha), headers=_bearer(alpha))

    assert response.status_code == 200
    assert response.content == PAYLOAD
    assert response.headers["content-length"] == str(len(PAYLOAD))
    assert client.get(_image_path(beta), headers=_bearer(beta)).status_code == 404
    assert client.get(_image_path(alpha), headers=_bearer(beta)).status_code == 403
    assert client.get(_image_path(alpha)).status_code == 401
    assert client.get(_image_path(alpha, "sha256:xyz"), headers=_bearer(alpha)).status_code == 422


def test_runner_cannot_download_after_the_run_ends(
    client: TestClient, register: Register, create_task: CreateTask, settings: Settings
) -> None:
    FsImageStore(Path(settings.image_store_path)).write_atomic(SEEDED_DIGEST, [PAYLOAD])
    run_id = create_task("key-1")
    runner = register()
    _heartbeat(client, runner, capacity=1)

    client.post(f"/api/v1/tasks/{run_id}/stop")

    assert client.get(_image_path(runner), headers=_bearer(runner)).status_code == 404


def test_image_missing_from_the_store_is_not_found(
    client: TestClient, register: Register, create_task: CreateTask
) -> None:
    create_task("key-1")
    runner = register()
    _heartbeat(client, runner, capacity=1)

    assert client.get(_image_path(runner), headers=_bearer(runner)).status_code == 404


def test_store_refuses_a_symlinked_image(tmp_path: Path) -> None:
    store = FsImageStore(tmp_path / "images")
    store.write_atomic(DIGEST, [PAYLOAD])
    real = tmp_path / "images" / f"sha256-{DIGEST.removeprefix('sha256:')}.qcow2"
    (tmp_path / "images" / f"sha256-{OTHER_DIGEST.removeprefix('sha256:')}.qcow2").symlink_to(real)

    assert not store.exists(OTHER_DIGEST)
    with pytest.raises(NotFoundError):
        store.open_read(OTHER_DIGEST)


def test_store_refuses_a_shared_directory(tmp_path: Path) -> None:
    root = tmp_path / "images"
    root.mkdir()
    root.chmod(0o777)

    with pytest.raises(ImageError):
        FsImageStore(root).write_atomic(DIGEST, [PAYLOAD])


def test_store_rejects_names_that_are_not_digests(tmp_path: Path) -> None:
    with pytest.raises(ImageError):
        FsImageStore(tmp_path).open_read("sha256:../../etc/passwd")


def test_interrupted_write_leaves_no_partial_file(tmp_path: Path) -> None:
    store = FsImageStore(tmp_path / "images")

    def broken() -> Iterator[bytes]:
        yield PAYLOAD
        raise ImageError("source dropped")

    with pytest.raises(ImageError):
        store.write_atomic(DIGEST, broken())

    assert list((tmp_path / "images").iterdir()) == []
    store.delete(DIGEST)
