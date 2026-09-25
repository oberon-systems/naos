from typing import Annotated, Self

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from naos_api import profiles
from naos_api.clock import NowDep
from naos_api.lifecycle import RunStatus
from naos_api.models import Profile
from naos_api.routes.deps import IdempotencyKey, SessionDep
from naos_api.routes.runs import RunRead
from naos_api.spec import ImageRef, ProfileSpec, RunnerId, StrictModel

ProfileName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]


class ProfileCreate(StrictModel):
    name: ProfileName
    spec: ProfileSpec


class ProfileUpdate(StrictModel):
    spec: ProfileSpec


class ProfileRunCreate(StrictModel):
    image: ImageRef
    runner: RunnerId | None = None


class ActiveRunRead(BaseModel):
    id: str
    seq: int
    status: RunStatus


class ProfileRead(BaseModel):
    id: str
    name: str
    spec: ProfileSpec
    active_runs: int
    active_run: ActiveRunRead | None
    last_run_at: int | None
    runs_total: int
    runs_24h: int
    created_at: int
    updated_at: int

    @classmethod
    def viewed(cls, view: profiles.ProfileView) -> Self:
        profile, active = view.profile, view.active_run
        return cls(
            id=profile.id,
            name=profile.name,
            spec=ProfileSpec.model_validate(profile.spec),
            active_runs=view.active_runs,
            active_run=ActiveRunRead(id=active.id, seq=active.seq, status=active.status)
            if active
            else None,
            last_run_at=view.usage.last_run_at,
            runs_total=view.usage.runs_total,
            runs_24h=view.usage.runs_24h,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )


def _read(session: SessionDep, profile: Profile, now: int) -> ProfileRead:
    return ProfileRead.viewed(profiles.view_profiles(session, [profile], now)[0])


router = APIRouter()


@router.get("/profiles")
def list_profiles(
    session: SessionDep,
    now: NowDep,
    q: Annotated[str | None, Query(max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProfileRead]:
    page = profiles.list_profiles(session, q, limit, offset)
    return [ProfileRead.viewed(view) for view in profiles.view_profiles(session, page, now)]


@router.post("/profiles", status_code=201)
def create_profile(
    body: ProfileCreate, session: SessionDep, response: Response, now: NowDep
) -> ProfileRead:
    profile, created = profiles.create_profile(session, body.name, body.spec)
    if not created:
        response.status_code = 200
    return _read(session, profile, now)


@router.get("/profiles/{profile_id}")
def get_profile(profile_id: str, session: SessionDep, now: NowDep) -> ProfileRead:
    return _read(session, profiles.get_profile(session, profile_id), now)


@router.put("/profiles/{profile_id}")
def update_profile(
    profile_id: str, body: ProfileUpdate, session: SessionDep, now: NowDep
) -> ProfileRead:
    return _read(session, profiles.update_profile(session, profile_id, body.spec), now)


@router.delete("/profiles/{profile_id}", status_code=204)
def delete_profile(profile_id: str, session: SessionDep) -> None:
    profiles.delete_profile(session, profile_id)


@router.post("/profiles/{profile_id}/runs", status_code=201)
def run_from_profile(
    profile_id: str,
    body: ProfileRunCreate,
    idempotency_key: IdempotencyKey,
    session: SessionDep,
    response: Response,
    now: NowDep,
) -> RunRead:
    run, created = profiles.run_from_profile(
        session, profile_id, body.image, body.runner, idempotency_key, now
    )
    if not created:
        response.status_code = 200
    return RunRead.of(run)
