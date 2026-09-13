from typing import Annotated, Self

from fastapi import APIRouter, BackgroundTasks, Request, Response
from pydantic import BaseModel, Field

from naos_api.images import service as images
from naos_api.lifecycle import ImageStatus
from naos_api.models import Image
from naos_api.routes.deps import ImageSourceDep, SessionDep
from naos_api.spec import Digest, ImageId, StrictModel

Version = Annotated[str, Field(pattern=r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")]


class ImageCreate(StrictModel):
    id: ImageId
    version: Version
    digest: Digest


class ImageRead(BaseModel):
    id: str
    version: str
    digest: str
    status: ImageStatus
    status_reason: str | None
    size_bytes: int | None
    created_at: int
    updated_at: int

    @classmethod
    def of(cls, image: Image) -> Self:
        return cls(
            id=image.id,
            version=image.version,
            digest=image.digest,
            status=image.status,
            status_reason=image.status_reason,
            size_bytes=image.size_bytes,
            created_at=image.created_at,
            updated_at=image.updated_at,
        )


router = APIRouter()


@router.post("/images", status_code=202)
def create_image(
    body: ImageCreate,
    session: SessionDep,
    source: ImageSourceDep,
    request: Request,
    response: Response,
    background: BackgroundTasks,
) -> ImageRead:
    result = images.request_import(session, source, body.id, body.version, body.digest)
    if result.scheduled:
        background.add_task(
            images.run_import,
            request.app.state.db,
            source,
            request.app.state.image_store,
            result.image.id,
            request.app.state.image_transport,
        )
    else:
        response.status_code = 200
    return ImageRead.of(result.image)


@router.get("/images")
def list_images(session: SessionDep) -> list[ImageRead]:
    return [ImageRead.of(image) for image in images.list_images(session)]


@router.get("/images/{image_id}")
def get_image(image_id: str, session: SessionDep) -> ImageRead:
    return ImageRead.of(images.get_image(session, image_id))
