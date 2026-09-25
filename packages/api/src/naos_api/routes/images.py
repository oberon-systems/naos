from typing import Annotated, Self

from fastapi import APIRouter, Response
from pydantic import AnyUrl, BaseModel, Field, UrlConstraints, field_validator

from naos_api.images import service as images
from naos_api.models import Image
from naos_api.routes.deps import SessionDep
from naos_api.spec import Digest, ImageId, StrictModel

Version = Annotated[str, Field(pattern=r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")]
Name = Annotated[str, Field(pattern=r"^[0-9A-Za-z][0-9A-Za-z ._+-]{0,63}$")]
SourceUrl = Annotated[AnyUrl, UrlConstraints(max_length=2048, allowed_schemes=["https"])]


class ImageCreate(StrictModel):
    id: ImageId
    version: Version
    digest: Digest
    url: SourceUrl
    name: Name | None = None
    size_bytes: Annotated[int, Field(ge=1)] | None = None
    built_at: Annotated[int, Field(ge=0)] | None = None

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
    name: str | None
    size_bytes: int | None
    built_at: int | None
    created_at: int
    runs_open: int
    runs_total: int

    @classmethod
    def of(cls, image: Image, usage: images.Usage) -> Self:
        return cls(
            id=image.id,
            version=image.version,
            digest=image.digest,
            url=image.url,
            name=image.name,
            size_bytes=image.size_bytes,
            built_at=image.built_at,
            created_at=image.created_at,
            runs_open=usage.open,
            runs_total=usage.total,
        )


router = APIRouter()


@router.post("/images", status_code=201)
def create_image(body: ImageCreate, session: SessionDep, response: Response) -> ImageRead:
    entry = Image(**body.model_dump(exclude={"url"}), url=str(body.url))
    image, created = images.register_image(session, entry)
    if not created:
        response.status_code = 200
    return ImageRead.of(image, images.usage(session).get(image.id, images.Usage()))


@router.get("/images")
def list_images(session: SessionDep) -> list[ImageRead]:
    usage = images.usage(session)
    return [
        ImageRead.of(image, usage.get(image.id, images.Usage()))
        for image in images.list_images(session)
    ]


@router.get("/images/{image_id}")
def get_image(image_id: str, session: SessionDep) -> ImageRead:
    image = images.get_image(session, image_id)
    return ImageRead.of(image, images.usage(session).get(image.id, images.Usage()))
