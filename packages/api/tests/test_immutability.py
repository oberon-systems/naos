from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, delete, update

from naos_api import runs
from naos_api.lifecycle import RunStatus
from naos_api.models import PolicySnapshot, Run
from naos_api.mounts import MountPolicyIn
from naos_api.policies import create_mount_snapshot
from naos_api.settings import Settings
from naos_api.spec import RunSpec

GUARDED = [
    "spec",
    "mount_policy_id",
    "network_policy_id",
    "shell_policy_id",
    "mcp_policy_id",
    "idempotency_key",
    "request_digest",
]


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


@pytest.mark.parametrize("column", GUARDED)
def test_bulk_update_of_run_boundary_fails(
    session: Session, started: tuple[Run, PolicySnapshot], column: str
) -> None:
    run, _ = started

    with pytest.raises(IntegrityError, match="immutable"):
        session.exec(update(Run).where(col(Run.id) == run.id).values({column: None}))
    session.rollback()


def test_raw_sql_cannot_swap_policy(session: Session, started: tuple[Run, PolicySnapshot]) -> None:
    run, _ = started

    with pytest.raises(IntegrityError, match="immutable"):
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

    with pytest.raises(IntegrityError, match="immutable"):
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

    with pytest.raises(IntegrityError, match="immutable"):
        session.commit()
    session.rollback()


def test_policy_snapshot_cannot_be_deleted(
    session: Session, started: tuple[Run, PolicySnapshot]
) -> None:
    _, snapshot = started

    with pytest.raises(IntegrityError, match="immutable"):
        session.exec(delete(PolicySnapshot).where(col(PolicySnapshot.id) == snapshot.id))
    session.rollback()
