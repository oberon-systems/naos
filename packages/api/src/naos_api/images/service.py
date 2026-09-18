from collections.abc import Sequence

from sqlmodel import Session, col, select

from naos_api import audit
from naos_api.errors import ImageConflictError, NotFoundError, PolicyError
from naos_api.models import Image
from naos_api.spec import ImageRef


def _find(session: Session, image_id: str, digest: str) -> Image | None:
    by_digest = session.exec(select(Image).where(col(Image.digest) == digest)).first()
    return by_digest or session.get(Image, image_id)


def _replay(image: Image, image_id: str, version: str, digest: str, url: str) -> Image:
    if (image.id, image.version, image.digest, image.url) != (image_id, version, digest, url):
        raise ImageConflictError(
            f"image {image.id} is already registered with another version, digest or url"
        )
    return image


def register_image(
    session: Session, image_id: str, version: str, digest: str, url: str
) -> tuple[Image, bool]:
    existing = _find(session, image_id, digest)
    if existing is not None:
        return _replay(existing, image_id, version, digest, url), False

    image = Image(id=image_id, version=version, digest=digest, url=url)
    session.add(image)
    audit.record(
        session,
        "image_registered",
        actor="operator",
        image_id=image_id,
        version=version,
        digest=digest,
    )
    try:
        session.commit()
    except Exception:
        session.rollback()
        existing = _find(session, image_id, digest)
        if existing is None:
            raise
        return _replay(existing, image_id, version, digest, url), False
    return image, True


def list_images(session: Session) -> Sequence[Image]:
    statement = select(Image).order_by(col(Image.created_at).desc(), col(Image.id))
    return session.exec(statement).all()


def get_image(session: Session, image_id: str) -> Image:
    image = session.get(Image, image_id)
    if image is None:
        raise NotFoundError(f"image {image_id} does not exist")
    return image


def check_image(session: Session, ref: ImageRef) -> Image:
    image = session.get(Image, ref.id)
    if image is None or image.digest != ref.digest:
        raise PolicyError(f"image {ref.id} with digest {ref.digest} is not a registered image")
    return image
