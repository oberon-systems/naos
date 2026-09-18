from bisect import bisect_left
from collections.abc import Mapping, Sequence
from typing import Any

from sqlmodel import Session, col, update

from naos_api import audit, runners, tasks
from naos_api.errors import InvalidTransitionError, MergeError, NotFoundError
from naos_api.lifecycle import TaskStatus
from naos_api.models import Merge, Task
from naos_api.spec import RunSpec

S = TaskStatus
REJECTED = "rejected"


def _live(entries: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [entry for entry in entries if entry["change"] != REJECTED]


def _policy_decision(policy: str, entries: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    live = _live(entries)
    if policy == "never":
        return {"paths": [], "resolutions": {}}
    if policy == "always" and not any(entry.get("sensitive") for entry in live):
        return {"paths": sorted({entry["path"] for entry in live}), "resolutions": {}}
    return None


def get_merge(session: Session, task_id: str) -> Merge:
    merge = session.get(Merge, task_id)
    if merge is None:
        raise NotFoundError(f"task {task_id} has no collected diff")
    return merge


def report_diff(
    session: Session,
    runner_id: str,
    task_id: str,
    lease_id: str,
    entries: list[dict[str, Any]],
    now: int,
) -> Task:
    task = runners.owned_task(session, runner_id, task_id, lease_id, now)
    if session.get(Merge, task_id) is None:
        policy = RunSpec.model_validate(task.spec).merge.policy
        decision = _policy_decision(policy, entries)
        session.add(
            Merge(
                task_id=task_id,
                entries=entries,
                decision=decision,
                created_at=now,
                updated_at=now,
            )
        )
        audit.record(
            session,
            "diff_reported",
            actor="runner",
            run_id=task_id,
            runner_id=runner_id,
            entries=len(entries),
            rejected=len(entries) - len(_live(entries)),
            sensitive=sum(1 for entry in entries if entry.get("sensitive")),
            policy=policy,
            decided=decision is not None,
        )
        try:
            session.commit()
        except Exception:
            # A repeated report raced this one; the diff of the same disk is the same.
            session.rollback()
            get_merge(session, task_id)
    return tasks.transition_task(
        session,
        task_id,
        S.COLLECTING,
        S.WAITING_MERGE,
        lease_id=lease_id,
        actor="runner",
        runner_id=runner_id,
    )


def report_merge(
    session: Session,
    runner_id: str,
    task_id: str,
    lease_id: str,
    report: dict[str, Any] | None,
    conflicts: list[dict[str, Any]] | None,
    now: int,
) -> Task:
    task = runners.owned_task(session, runner_id, task_id, lease_id, now)
    if task.status is S.COMPLETED and report is not None:
        return task
    if task.status is not S.WAITING_MERGE:
        raise InvalidTransitionError(f"task {task_id} is {task.status}, expected WAITING_MERGE")
    merge = get_merge(session, task_id)
    counts = {name: len(paths) for name, paths in (report or {}).items()}
    audit.record(
        session,
        "merge_reported",
        actor="runner",
        run_id=task_id,
        runner_id=runner_id,
        outcome="applied" if report is not None else "conflict",
        conflicts=len(conflicts or []),
        **counts,
    )
    if report is not None:
        merge.report, merge.conflicts, merge.updated_at = report, None, now
        session.add(merge)
        session.commit()
        return tasks.transition_task(
            session,
            task_id,
            S.WAITING_MERGE,
            S.COMPLETED,
            lease_id=lease_id,
            actor="runner",
            runner_id=runner_id,
        )
    merge.conflicts, merge.decision, merge.updated_at = conflicts or [], None, now
    session.add(merge)
    session.commit()
    return task


def _check_selection(
    entries: Sequence[Mapping[str, Any]], paths: set[str], resolutions: Mapping[str, str]
) -> None:
    live = _live(entries)
    unknown = sorted((paths | set(resolutions)) - {entry["path"] for entry in live})
    if unknown:
        raise MergeError(f"{unknown[0]} is not a mergeable path of the diff")
    unselected = sorted(set(resolutions) - paths)
    if unselected:
        raise MergeError(f"{unselected[0]} has a resolution but is not selected")
    created_dirs = {
        entry["path"] for entry in live if entry["change"] == "created" and entry["kind"] == "dir"
    }
    # Each host path a selection removes, with the entry that has to be selected for it.
    removed = sorted(
        (entry["from"] if entry["change"] == "renamed" else entry["path"], entry["path"])
        for entry in live
        if entry["change"] in ("deleted", "renamed")
    )
    for entry in live:
        path = entry["path"]
        if path not in paths:
            continue
        parent = path.rpartition("/")[0]
        if parent in created_dirs and parent not in paths:
            raise MergeError(f"{path} needs {parent} too")
        if entry["change"] != "deleted" or entry["kind"] != "dir":
            continue
        prefix = f"{path}/"
        at = bisect_left(removed, (prefix, ""))
        while at < len(removed) and removed[at][0].startswith(prefix):
            owner = removed[at][1]
            if owner not in paths:
                raise MergeError(f"{path} needs {owner} too")
            at += 1


def decide(
    session: Session,
    task_id: str,
    paths: Sequence[str],
    resolutions: Mapping[str, str],
    now: int,
) -> Merge:
    task = tasks.get_task(session, task_id)
    if task.status is not S.WAITING_MERGE:
        raise InvalidTransitionError(f"task {task_id} is {task.status}, expected WAITING_MERGE")
    merge = get_merge(session, task_id)
    _check_selection(merge.entries, set(paths), resolutions)
    decision = {"paths": sorted(set(paths)), "resolutions": dict(resolutions)}
    decided = session.exec(
        update(Merge)
        .where(col(Merge.task_id) == task_id, col(Merge.decision).is_(None))
        .values(decision=decision, updated_at=now)
    )
    if decided.rowcount == 1:
        audit.record(
            session,
            "merge_decided",
            actor="operator",
            run_id=task_id,
            paths=len(decision["paths"]),
            resolutions=len(decision["resolutions"]),
        )
    session.commit()
    if decided.rowcount != 1:
        raise InvalidTransitionError(f"task {task_id} already has a merge decision pending")
    session.refresh(merge)
    return merge
