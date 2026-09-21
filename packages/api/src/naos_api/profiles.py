from collections.abc import Sequence
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import ColumnElement
from sqlmodel import Session, col, func, or_, select, update

from naos_api import audit
from naos_api.clock import now_ts
from naos_api.errors import NotFoundError, ProfileBusyError, ProfileConflictError
from naos_api.lifecycle import TERMINAL, RunStatus
from naos_api.models import Profile, Run
from naos_api.policies import check_refs
from naos_api.runs import create_run
from naos_api.spec import ImageRef, ProfileSpec, RunSpec, digest_of


@dataclass(frozen=True)
class ActiveRun:
    id: str
    seq: int
    status: RunStatus


@dataclass(frozen=True)
class ProfileView:
    profile: Profile
    active_runs: int
    active_run: ActiveRun | None
    last_run_at: int | None


def _open() -> ColumnElement[bool]:
    return col(Run.status).not_in(TERMINAL)


def _by_name(session: Session, name: str) -> Profile | None:
    return session.exec(select(Profile).where(col(Profile.name) == name)).first()


def _replay(profile: Profile, digest: str) -> Profile:
    if profile.digest != digest:
        raise ProfileConflictError(f"profile {profile.name} already exists with another spec")
    return profile


def get_profile(session: Session, profile_id: str) -> Profile:
    profile = session.get(Profile, profile_id)
    if profile is None:
        raise NotFoundError(f"profile {profile_id} does not exist")
    return profile


def list_profiles(
    session: Session, query: str | None = None, limit: int = 100, offset: int = 0
) -> Sequence[Profile]:
    statement = select(Profile)
    if query:
        needle = query.lower()
        statement = statement.where(
            or_(
                func.lower(col(Profile.name)).contains(needle),
                func.lower(col(Profile.id)).contains(needle),
            )
        )
    statement = statement.order_by(col(Profile.name)).offset(offset).limit(limit)
    return session.exec(statement).all()


def view_profiles(session: Session, profiles: Sequence[Profile]) -> list[ProfileView]:
    ids = [profile.id for profile in profiles]
    if not ids:
        return []
    of_page = col(Run.profile_id).in_(ids)
    active = session.exec(
        select(Run.profile_id, Run.id, Run.seq, Run.status)
        .where(of_page, _open())
        .order_by(col(Run.seq))
    ).all()
    last = dict(
        session.exec(
            select(Run.profile_id, func.max(Run.created_at))
            .where(of_page)
            .group_by(col(Run.profile_id))
        ).all()
    )
    counts: dict[str, int] = {}
    first: dict[str, ActiveRun] = {}
    for profile_id, run_id, seq, status in active:
        if profile_id is None:
            continue
        counts[profile_id] = counts.get(profile_id, 0) + 1
        first.setdefault(profile_id, ActiveRun(id=run_id, seq=seq, status=RunStatus(status)))
    return [
        ProfileView(
            profile=profile,
            active_runs=counts.get(profile.id, 0),
            active_run=first.get(profile.id),
            last_run_at=last.get(profile.id),
        )
        for profile in profiles
    ]


def create_profile(session: Session, name: str, spec: ProfileSpec) -> tuple[Profile, bool]:
    document = spec.model_dump(mode="json")
    digest = digest_of(document)
    existing = _by_name(session, name)
    if existing is not None:
        return _replay(existing, digest), False

    check_refs(session, spec)
    profile = Profile(id=f"prof_{uuid4().hex}", name=name, spec=document, digest=digest)
    session.add(profile)
    audit.record(session, "profile_created", actor="operator", profile_id=profile.id, name=name)
    try:
        session.commit()
    except Exception:
        session.rollback()
        existing = _by_name(session, name)
        if existing is None:
            raise
        return _replay(existing, digest), False
    return profile, True


def update_profile(session: Session, profile_id: str, spec: ProfileSpec) -> Profile:
    profile = get_profile(session, profile_id)
    document = spec.model_dump(mode="json")
    digest = digest_of(document)
    # An unchanged spec is a replay, so a double submit is not refused by its own Run.
    if profile.digest == digest:
        return profile

    check_refs(session, spec)
    busy = select(Run.id).where(col(Run.profile_id) == profile_id, _open())
    result = session.exec(
        update(Profile)
        .where(col(Profile.id) == profile_id, ~busy.exists())
        .values(spec=document, digest=digest, updated_at=now_ts())
    )
    if result.rowcount == 1:
        audit.record(
            session, "profile_updated", actor="operator", profile_id=profile_id, name=profile.name
        )
    session.commit()
    if result.rowcount == 1:
        session.refresh(profile)
        return profile

    view = view_profiles(session, [profile])[0]
    blocking = view.active_run
    detail = f"#{blocking.seq} is {blocking.status}" if blocking else "a run is active"
    raise ProfileBusyError(f"profile {profile.name} cannot change: {detail} on it")


def run_from_profile(
    session: Session,
    profile_id: str,
    image: ImageRef,
    runner: str | None,
    idempotency_key: str,
) -> tuple[Run, bool]:
    profile = get_profile(session, profile_id)
    spec = RunSpec.model_validate({**profile.spec, "image": image, "runner": runner})
    return create_run(session, spec, idempotency_key, profile_id=profile.id)
