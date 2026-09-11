from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from naos_api import runs
from naos_api.errors import (
    IdempotencyConflictError,
    InvalidTransitionError,
    NotFoundError,
    PolicyError,
)
from naos_api.lifecycle import RunStatus
from naos_api.models import PolicySnapshot, Run
from naos_api.mounts import MountPolicyIn
from naos_api.policies import create_mount_snapshot
from naos_api.settings import Settings
from naos_api.spec import PolicyKind, RunSpec

S = RunStatus
PATHS = {
    S.PENDING: [],
    S.STARTING: [S.STARTING],
    S.STARTED: [S.STARTING, S.STARTED],
    S.STOPPING: [S.STARTING, S.STARTED, S.STOPPING],
    S.COLLECTING: [S.STARTING, S.STARTED, S.STOPPING, S.COLLECTING],
    S.WAITING_MERGE: [S.STARTING, S.STARTED, S.STOPPING, S.COLLECTING, S.WAITING_MERGE],
    S.COMPLETED: [S.STARTING, S.STARTED, S.STOPPING, S.COLLECTING, S.WAITING_MERGE, S.COMPLETED],
    S.FAILED: [S.FAILED],
    S.CANCELLED: [S.CANCELLED],
}


def _spec(body: dict[str, Any], **refs: str) -> RunSpec:
    return RunSpec.model_validate(body | {k: {"policy_snapshot": v} for k, v in refs.items()})


def _advance(session: Session, run_id: str, status: RunStatus) -> None:
    current = S.PENDING
    for target in PATHS[status]:
        reason = "boom" if target is S.FAILED else None
        runs.transition_run(session, run_id, current, target, reason)
        current = target


def test_create_starts_pending(session: Session, spec_body: dict[str, Any]) -> None:
    run, created = runs.create_run(session, _spec(spec_body), "key-1")

    assert created
    assert run.status is S.PENDING
    assert run.id.startswith("run_")
    assert RunSpec.model_validate(run.spec) == _spec(spec_body)


def test_duplicate_create_returns_same_run(session: Session, spec_body: dict[str, Any]) -> None:
    first, _ = runs.create_run(session, _spec(spec_body), "key-1")
    again, created = runs.create_run(session, _spec(spec_body), "key-1")

    assert not created
    assert again.id == first.id
    assert len(runs.list_runs(session)) == 1


def test_key_reuse_with_other_spec_conflicts(session: Session, spec_body: dict[str, Any]) -> None:
    runs.create_run(session, _spec(spec_body), "key-1")
    spec_body["timeout"] = 60

    with pytest.raises(IdempotencyConflictError):
        runs.create_run(session, _spec(spec_body), "key-1")


def test_concurrent_duplicate_create_returns_winner(
    session: Session, engine: Engine, spec_body: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    winner, _ = runs.create_run(session, _spec(spec_body), "key-1")
    real_lookup = runs._by_key
    lookups: list[str] = []

    def stale_first_lookup(s: Session, key: str) -> Run | None:
        lookups.append(key)
        return None if len(lookups) == 1 else real_lookup(s, key)

    monkeypatch.setattr(runs, "_by_key", stale_first_lookup)
    with Session(engine) as other:
        loser, created = runs.create_run(other, _spec(spec_body), "key-1")

    assert not created
    assert loser.id == winner.id
    assert len(lookups) == 2


def test_happy_path_reaches_completed(session: Session, spec_body: dict[str, Any]) -> None:
    run, _ = runs.create_run(session, _spec(spec_body), "key-1")

    _advance(session, run.id, S.COMPLETED)

    assert runs.get_run(session, run.id).status is S.COMPLETED


def test_duplicate_transition_is_noop(session: Session, spec_body: dict[str, Any]) -> None:
    run, _ = runs.create_run(session, _spec(spec_body), "key-1")
    runs.transition_run(session, run.id, S.PENDING, S.STARTING)

    again = runs.transition_run(session, run.id, S.PENDING, S.STARTING)

    assert again.status is S.STARTING


def test_stale_transition_conflicts(session: Session, spec_body: dict[str, Any]) -> None:
    run, _ = runs.create_run(session, _spec(spec_body), "key-1")
    _advance(session, run.id, S.STOPPING)

    with pytest.raises(InvalidTransitionError):
        runs.transition_run(session, run.id, S.STARTING, S.STARTED)
    assert runs.get_run(session, run.id).status is S.STOPPING


def test_illegal_transition_leaves_status(session: Session, spec_body: dict[str, Any]) -> None:
    run, _ = runs.create_run(session, _spec(spec_body), "key-1")

    with pytest.raises(InvalidTransitionError):
        runs.transition_run(session, run.id, S.PENDING, S.COMPLETED)
    assert runs.get_run(session, run.id).status is S.PENDING


@pytest.mark.parametrize("reason", [None, "", "x" * 501])
def test_failure_needs_bounded_reason(
    session: Session, spec_body: dict[str, Any], reason: str | None
) -> None:
    run, _ = runs.create_run(session, _spec(spec_body), "key-1")

    with pytest.raises(ValueError, match="reason"):
        runs.transition_run(session, run.id, S.PENDING, S.FAILED, reason)


def test_failure_records_reason(session: Session, spec_body: dict[str, Any]) -> None:
    run, _ = runs.create_run(session, _spec(spec_body), "key-1")

    failed = runs.transition_run(session, run.id, S.PENDING, S.FAILED, "image missing")

    assert failed.status is S.FAILED
    assert failed.status_reason == "image missing"


def test_unknown_run_is_not_found(session: Session) -> None:
    with pytest.raises(NotFoundError):
        runs.transition_run(session, "run_" + "0" * 32, S.PENDING, S.STARTING)
    with pytest.raises(NotFoundError):
        runs.stop_run(session, "run_" + "0" * 32)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (S.PENDING, S.CANCELLED),
        (S.STARTING, S.STOPPING),
        (S.STARTED, S.STOPPING),
        (S.STOPPING, S.STOPPING),
        (S.COLLECTING, S.COLLECTING),
        (S.WAITING_MERGE, S.WAITING_MERGE),
        (S.COMPLETED, S.COMPLETED),
        (S.FAILED, S.FAILED),
        (S.CANCELLED, S.CANCELLED),
    ],
)
def test_stop_is_idempotent_from_every_status(
    session: Session, spec_body: dict[str, Any], status: RunStatus, expected: RunStatus
) -> None:
    run, _ = runs.create_run(session, _spec(spec_body), "key-1")
    _advance(session, run.id, status)

    assert runs.stop_run(session, run.id).status is expected
    assert runs.stop_run(session, run.id).status is expected


def test_stop_retries_when_runner_moves_first(
    session: Session, spec_body: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = runs.create_run(session, _spec(spec_body), "key-1")
    real_transition = runs.transition_run
    raced: list[bool] = []

    def runner_wins_first(
        s: Session, run_id: str, expected: RunStatus, target: RunStatus, reason: str | None = None
    ) -> Run:
        if not raced:
            raced.append(True)
            real_transition(s, run_id, S.PENDING, S.STARTING)
        return real_transition(s, run_id, expected, target, reason)

    monkeypatch.setattr(runs, "transition_run", runner_wins_first)

    assert runs.stop_run(session, run.id).status is S.STOPPING


def test_run_keeps_policy_references(
    session: Session, settings: Settings, spec_body: dict[str, Any], mount_body: dict[str, Any]
) -> None:
    snapshot, _ = create_mount_snapshot(session, settings, MountPolicyIn.model_validate(mount_body))

    run, _ = runs.create_run(session, _spec(spec_body, mounts=snapshot.id), "key-1")

    assert run.mount_policy_id == snapshot.id
    assert run.network_policy_id is None


def test_network_reference_is_accepted(session: Session, spec_body: dict[str, Any]) -> None:
    netpol = PolicySnapshot(
        id="netpol_" + "1" * 32, kind=PolicyKind.NETWORK, digest="d", document={}
    )
    session.add(netpol)
    session.commit()

    run, _ = runs.create_run(session, _spec(spec_body, network=netpol.id), "key-1")

    assert run.network_policy_id == netpol.id


@pytest.mark.parametrize("section", ["mounts", "network", "shell", "mcp"])
def test_unknown_policy_reference_is_rejected(
    session: Session, spec_body: dict[str, Any], section: str
) -> None:
    with pytest.raises(PolicyError):
        runs.create_run(session, _spec(spec_body, **{section: "mntpol_" + "0" * 32}), "key-1")
    assert runs.list_runs(session) == []


def test_policy_reference_of_wrong_kind_is_rejected(
    session: Session, settings: Settings, spec_body: dict[str, Any], mount_body: dict[str, Any]
) -> None:
    snapshot, _ = create_mount_snapshot(session, settings, MountPolicyIn.model_validate(mount_body))

    with pytest.raises(PolicyError):
        runs.create_run(session, _spec(spec_body, network=snapshot.id), "key-1")


def test_list_filters_and_limits(session: Session, spec_body: dict[str, Any]) -> None:
    ids = [runs.create_run(session, _spec(spec_body), f"key-{i}")[0].id for i in range(3)]
    runs.stop_run(session, ids[0])

    assert [r.id for r in runs.list_runs(session, S.CANCELLED)] == [ids[0]]
    assert len(runs.list_runs(session, limit=2)) == 2
    assert len(runs.list_runs(session, offset=2)) == 1
