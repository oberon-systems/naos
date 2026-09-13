import hashlib
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

import httpx
from sqlmodel import Session, col, select, update

from naos_api.clock import now_ts
from naos_api.db import Database
from naos_api.errors import ImageConflictError, ImageError, NotFoundError, PolicyError
from naos_api.images.store import ImageStore
from naos_api.lifecycle import ImageStatus
from naos_api.models import Image
from naos_api.spec import ImageRef

MAX_REASON_LENGTH = 500
MAX_REDIRECTS = 5
INTERRUPTED_REASON = "import interrupted by an api restart"


@dataclass(frozen=True)
class ImportRequest:
    image: Image
    created: bool
    scheduled: bool


@dataclass(frozen=True)
class ImageSource:
    url: str | None
    allowed_hosts: frozenset[str]
    max_bytes: int
    timeout_seconds: int


class _Verified:
    def __init__(self, chunks: Iterable[bytes], digest: str, limit: int) -> None:
        self._chunks = chunks
        self._digest = digest
        self._limit = limit
        self.size = 0

    def __iter__(self) -> Iterator[bytes]:
        hasher = hashlib.sha256()
        for chunk in self._chunks:
            self.size += len(chunk)
            if self.size > self._limit:
                raise ImageError(f"image exceeds {self._limit} bytes")
            hasher.update(chunk)
            yield chunk
        if f"sha256:{hasher.hexdigest()}" != self._digest:
            raise ImageError("image digest does not match")


def _checked(url: httpx.URL, hosts: frozenset[str]) -> httpx.URL:
    if url.userinfo or url.host not in hosts:
        raise ImageError(f"image source host {url.host!r} is not allowed")
    if url.scheme != "https":
        raise ImageError("image source must use https")
    return url


def source_url(source: ImageSource, version: str) -> tuple[httpx.URL, frozenset[str]]:
    if source.url is None:
        raise ImageError("image import is not configured: NAOS_IMAGE_SOURCE_URL is not set")
    try:
        url = httpx.URL(source.url.replace("{version}", version))
    except httpx.InvalidURL as err:
        raise ImageError("NAOS_IMAGE_SOURCE_URL is not a valid URL") from err
    hosts = frozenset({url.host, *source.allowed_hosts})
    return _checked(url, hosts), hosts


def _find(session: Session, image_id: str, digest: str) -> Image | None:
    by_digest = session.exec(select(Image).where(col(Image.digest) == digest)).first()
    return by_digest or session.get(Image, image_id)


def _replay(
    session: Session, image: Image, image_id: str, version: str, digest: str
) -> ImportRequest:
    if (image.id, image.version, image.digest) != (image_id, version, digest):
        raise ImageConflictError(
            f"image {image.id} is already registered with another version or digest"
        )
    if image.status is not ImageStatus.FAILED:
        return ImportRequest(image, created=False, scheduled=False)
    retried = session.exec(
        update(Image)
        .where(col(Image.id) == image.id, col(Image.status) == ImageStatus.FAILED)
        .values(status=ImageStatus.IMPORTING, status_reason=None, updated_at=now_ts())
    )
    session.commit()
    session.refresh(image)
    return ImportRequest(image, created=False, scheduled=retried.rowcount == 1)


def request_import(
    session: Session, source: ImageSource, image_id: str, version: str, digest: str
) -> ImportRequest:
    source_url(source, version)
    existing = _find(session, image_id, digest)
    if existing is not None:
        return _replay(session, existing, image_id, version, digest)

    image = Image(id=image_id, version=version, digest=digest)
    session.add(image)
    try:
        session.commit()
    except Exception:
        session.rollback()
        existing = _find(session, image_id, digest)
        if existing is None:
            raise
        return _replay(session, existing, image_id, version, digest)
    return ImportRequest(image, created=True, scheduled=True)


def _download(
    source: ImageSource,
    store: ImageStore,
    version: str,
    digest: str,
    transport: httpx.BaseTransport | None,
) -> int:
    url, hosts = source_url(source, version)
    timeout = httpx.Timeout(source.timeout_seconds)
    with httpx.Client(timeout=timeout, follow_redirects=False, transport=transport) as client:
        for _ in range(MAX_REDIRECTS + 1):
            with client.stream("GET", url) as response:
                if response.is_redirect:
                    url = _checked(url.join(response.headers["location"]), hosts)
                    continue
                if response.status_code != 200:
                    raise ImageError(f"image source returned {response.status_code}")
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > source.max_bytes:
                    raise ImageError(f"image exceeds {source.max_bytes} bytes")
                verified = _Verified(response.iter_bytes(), digest, source.max_bytes)
                store.write_atomic(digest, verified)
                return verified.size
    raise ImageError("image source redirected too many times")


def _finish(
    session: Session, image_id: str, status: ImageStatus, reason: str | None, size: int | None
) -> None:
    session.exec(
        update(Image)
        .where(col(Image.id) == image_id, col(Image.status) == ImageStatus.IMPORTING)
        .values(status=status, status_reason=reason, size_bytes=size, updated_at=now_ts())
    )
    session.commit()


def import_image(
    session: Session,
    source: ImageSource,
    store: ImageStore,
    image_id: str,
    transport: httpx.BaseTransport | None = None,
) -> ImageStatus | None:
    image = session.get(Image, image_id)
    if image is None or image.status is not ImageStatus.IMPORTING:
        return None
    try:
        size = _download(source, store, image.version, image.digest, transport)
    except (ImageError, httpx.HTTPError, OSError) as err:
        reason = (str(err) or type(err).__name__)[:MAX_REASON_LENGTH]
        _finish(session, image_id, ImageStatus.FAILED, reason, None)
        return ImageStatus.FAILED
    _finish(session, image_id, ImageStatus.READY, None, size)
    return ImageStatus.READY


def run_import(
    db: Database,
    source: ImageSource,
    store: ImageStore,
    image_id: str,
    transport: httpx.BaseTransport | None = None,
) -> None:
    with Session(db.engine) as session:
        import_image(session, source, store, image_id, transport)


def fail_interrupted(session: Session, now: int) -> None:
    session.exec(
        update(Image)
        .where(col(Image.status) == ImageStatus.IMPORTING)
        .values(status=ImageStatus.FAILED, status_reason=INTERRUPTED_REASON, updated_at=now)
    )
    session.commit()


def list_images(session: Session) -> Sequence[Image]:
    statement = select(Image).order_by(col(Image.created_at).desc(), col(Image.id))
    return session.exec(statement).all()


def get_image(session: Session, image_id: str) -> Image:
    image = session.get(Image, image_id)
    if image is None:
        raise NotFoundError(f"image {image_id} does not exist")
    return image


def check_image(session: Session, ref: ImageRef) -> None:
    image = session.get(Image, ref.id)
    if image is None or image.digest != ref.digest or image.status is not ImageStatus.READY:
        raise PolicyError(f"image {ref.id} with digest {ref.digest} is not an approved image")
