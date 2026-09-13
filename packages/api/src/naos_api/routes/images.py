from typing import Annotated, Self

from fastapi import APIRouter, Response
from pydantic import AnyUrl, BaseModel, Field, UrlConstraints, field_validator

from naos_api.images import service as images
from naos_api.models import Image
from naos_api.routes.deps import SessionDep
from naos_api.spec import Digest, ImageId, StrictModel

Version = Annotated[str, Field(pattern=r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")]
SourceUrl = Annotated[AnyUrl, UrlConstraints(max_length=2048, allowed_schemes=["https"])]


class ImageCreate(StrictModel):
    id: ImageId
    version: Version
    digest: Digest
    url: SourceUrl

    @field_validator("url")
    @classmethod
    def _no_credentials(cls, url: AnyUrl) -> AnyUrl:
        if url.username or url.password:
            raise ValueError("image url must not carry credentials")
        return url


class ImageRead(BaseModel):
    id: str
    version: str
    digest: str
    url: str
    created_at: int

    @classmethod
    def of(cls, image: Image) -> Self:
        return cls(
            id=image.id,
            version=image.version,
            digest=image.digest,
            url=image.url,
            created_at=image.created_at,
        )


router = APIRouter()


@router.post("/images", status_code=201)
def create_image(body: ImageCreate, session: SessionDep, response: Response) -> ImageRead:
    image, created = images.register_image(
        session, body.id, body.version, body.digest, str(body.url)
    )
    if not created:
        response.status_code = 200
    return ImageRead.of(image)


@router.get("/images")
def list_images(session: SessionDep) -> list[ImageRead]:
    return [ImageRead.of(image) for image in images.list_images(session)]


@router.get("/images/{image_id}")
def get_image(image_id: str, session: SessionDep) -> ImageRead:
    return ImageRead.of(images.get_image(session, image_id))
