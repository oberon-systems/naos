from typing import Any

import pytest
from sqlmodel import Session, col, delete, update

from naos_api import runs
from naos_api.lifecycle import ImageStatus, RunStatus
from naos_api.models import Image, PolicySnapshot, Run
from naos_api.mounts import MountPolicyIn
from naos_api.policies import create_mount_snapshot
from naos_api.settings import Settings
from naos_api.spec import RunSpec

# Every value differs from what the started Run holds, so the guard has to refuse the write
# under both trigger forms: BEFORE UPDATE OF on sqlite/postgres, value comparison on mysql.
GUARDED: dict[str, Any] = {
    "spec": {"timeout": 60},
    "mount_policy_id": None,
    "network_policy_id": "mntpol_other",
    "shell_policy_id": "shellpol_other",
    "mcp_policy_id": "mcppol_other",
    "idempotency_key": "key-other",
    "request_digest": "sha256:" + "b" * 64,
    "seq": 999,
}


@pytest.fixture
def started(
    session: Session, settings: Settings, spec_body: dict[str, Any], mount_body: dict[str, Any]
) -> tuple[Run, PolicySnapshot]:
    policy = MountPolicyIn.model_validate(mount_body)
    snapshot, _ = create_mount_snapshot(session, settings, policy)
    spec_body["mounts"] = {"policy_snapshot": snapshot.id}
    run, _ = runs.create_run(session, RunSpec.model_validate(spec_body), "key-1")
    runs.transition_run(session, run.id, RunStatus.PENDING, RunStatus.STARTING)
    runs.transition_run(session, run.id, RunStatus.STARTING, RunStatus.STARTED)
    return runs.get_run(session, run.id), snapshot


@pytest.mark.parametrize(("column", "value"), GUARDED.items())
def test_bulk_update_of_run_boundary_fails(
    session: Session, started: tuple[Run, PolicySnapshot], column: str, value: Any
) -> None:
    run, _ = started

    with pytest.raises(Exception, match="immutable"):
        session.exec(update(Run).where(col(Run.id) == run.id).values({column: value}))
    session.rollback()


@pytest.mark.sqlite_only
def test_raw_sql_cannot_swap_policy(session: Session, started: tuple[Run, PolicySnapshot]) -> None:
    # The ? placeholder is SQLite paramstyle; the point is that raw SQL cannot get past the trigger.
    run, _ = started

    with pytest.raises(Exception, match="immutable"):
        session.connection().exec_driver_sql(
            "UPDATE run SET network_policy_id = NULL, mount_policy_id = NULL WHERE id = ?",
            (run.id,),
        )
    session.rollback()

    assert runs.get_run(session, run.id).mount_policy_id is not None


def test_orm_update_of_spec_fails(session: Session, started: tuple[Run, PolicySnapshot]) -> None:
    run, _ = started
    run.spec = run.spec | {"timeout": 60}
    session.add(run)

    with pytest.raises(Exception, match="immutable"):
        session.commit()
    session.rollback()

    assert runs.get_run(session, run.id).spec["timeout"] == 3600


def test_status_stays_mutable(session: Session, started: tuple[Run, PolicySnapshot]) -> None:
    run, _ = started

    assert runs.stop_run(session, run.id).status is RunStatus.STOPPING


def test_policy_snapshot_cannot_be_updated(
    session: Session, started: tuple[Run, PolicySnapshot]
) -> None:
    _, snapshot = started
    snapshot.document = {"workdir": "/", "mounts": []}
    session.add(snapshot)

    with pytest.raises(Exception, match="immutable"):
        session.commit()
    session.rollback()


def test_policy_snapshot_cannot_be_deleted(
    session: Session, started: tuple[Run, PolicySnapshot]
) -> None:
    _, snapshot = started

    with pytest.raises(Exception, match="immutable"):
        session.exec(delete(PolicySnapshot).where(col(PolicySnapshot.id) == snapshot.id))
    session.rollback()


@pytest.mark.parametrize(
    ("column", "value"),
    [("id", "image_other"), ("version", "9.9.9"), ("digest", "sha256:" + "c" * 64)],
)
def test_image_identity_cannot_change(
    session: Session, spec_body: dict[str, Any], column: str, value: str
) -> None:
    with pytest.raises(Exception, match="immutable"):
        session.exec(update(Image).where(col(Image.id) == "image_alpha").values({column: value}))
    session.rollback()


def test_image_cannot_be_deleted(session: Session, spec_body: dict[str, Any]) -> None:
    with pytest.raises(Exception, match="immutable"):
        session.exec(delete(Image).where(col(Image.id) == "image_alpha"))
    session.rollback()


def test_image_status_stays_mutable(session: Session, spec_body: dict[str, Any]) -> None:
    session.exec(
        update(Image).where(col(Image.id) == "image_alpha").values(status=ImageStatus.FAILED)
    )
    session.commit()

    image = session.get(Image, "image_alpha")
    assert image is not None
    session.refresh(image)
    assert image.status is ImageStatus.FAILED
