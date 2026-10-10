from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from sqlalchemy import ColumnElement
from sqlmodel import Session, case, col, delete, func, select

from naos_api import audit
from naos_api.errors import ImageBusyError, ImageConflictError, NotFoundError, PolicyError
from naos_api.lifecycle import TERMINAL, RunStatus
from naos_api.models import Image, Run
from naos_api.spec import ImageRef

FIELDS = ("id", "version", "digest", "url", "name", "size_bytes", "built_at")


@dataclass(frozen=True)
class Usage:
    open: int = 0
    total: int = 0


def _find(session: Session, image_id: str, digest: str) -> Image | None:
    by_digest = session.exec(select(Image).where(col(Image.digest) == digest)).first()
    return by_digest or session.get(Image, image_id)


def _replay(image: Image, entry: Image) -> Image:
    if any(getattr(image, name) != getattr(entry, name) for name in FIELDS):
        raise ImageConflictError(
            f"image {image.id} is already registered with another version, digest, url or facts"
        )
    return image


def register_image(session: Session, entry: Image) -> tuple[Image, bool]:
    existing = _find(session, entry.id, entry.digest)
    if existing is not None:
        return _replay(existing, entry), False

    session.add(entry)
    audit.record(
        session,
        "image_registered",
        actor="operator",
        image_id=entry.id,
        version=entry.version,
        digest=entry.digest,
    )
    try:
        session.commit()
    except Exception:
        session.rollback()
        existing = _find(session, entry.id, entry.digest)
        if existing is None:
            raise
        return _replay(existing, entry), False
    return entry, True


def list_images(session: Session) -> Sequence[Image]:
    statement = select(Image).order_by(col(Image.created_at).desc(), col(Image.id))
    return session.exec(statement).all()


def get_image(session: Session, image_id: str) -> Image:
    image = session.get(Image, image_id)
    if image is None:
        raise NotFoundError(f"image {image_id} does not exist")
    return image


# The image a Run boots lives in its spec, so SQLite and PostgreSQL read it by JSON path.
def booted_image() -> ColumnElement[str]:
    return cast(ColumnElement[str], col(Run.spec)[("image", "id")].as_string())


# One grouped read for the whole catalog, whatever its length.
def usage(session: Session) -> dict[str, Usage]:
    image_id = booted_image()
    is_open = case((col(Run.status).not_in(TERMINAL), 1), else_=0)
    statement = select(image_id, func.count(), func.sum(is_open)).group_by(image_id)
    return {
        str(found): Usage(open=int(opened or 0), total=int(total))
        for found, total, opened in session.exec(statement).all()
    }


def _busy_detail(session: Session, image: Image) -> str:
    active = session.exec(
        select(Run.seq, Run.status)
        .where(booted_image() == image.id, col(Run.status).not_in(TERMINAL))
        .order_by(col(Run.seq).desc())
    ).all()
    named = " and ".join(f"#{seq} {RunStatus(status)}" for seq, status in active)
    return f"image {image.id} is used by {named or 'an active run'}. Delete it once they end."


def delete_image(session: Session, image_id: str) -> None:
    image = get_image(session, image_id)
    busy = select(Run.id).where(booted_image() == image_id, col(Run.status).not_in(TERMINAL))
    result = session.exec(delete(Image).where(col(Image.id) == image_id, ~busy.exists()))
    if result.rowcount == 1:
        audit.record(
            session,
            "image_deleted",
            actor="operator",
            image_id=image.id,
            version=image.version,
            digest=image.digest,
        )
    session.commit()
    if result.rowcount != 1:
        raise ImageBusyError(_busy_detail(session, image))


def check_image(session: Session, ref: ImageRef) -> Image:
    image = session.get(Image, ref.id)
    if image is None or image.digest != ref.digest:
        raise PolicyError(f"image {ref.id} with digest {ref.digest} is not a registered image")
    return image
