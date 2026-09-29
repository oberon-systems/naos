from dataclasses import dataclass, replace

from naos_web.changes import Selection, resolved
from naos_web.client import Row
from naos_web.rows import Fact, RunDetailRow, RunnerDetailRow

LEAD = "A new run will be created with these options:"
DECIDED = "the audit keeps who decided"
WORDS = ("No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten")


@dataclass(frozen=True)
class Confirm:
    title: str
    subject: str
    question: str
    action: str
    back: str
    footer: str
    danger: bool = False
    note: str | None = None
    lead: str | None = None
    facts: tuple[Fact, ...] = ()
    key: str | None = None
    button: str = "Yes"
    fields: tuple[tuple[str, str], ...] = ()


def stop(run: RunDetailRow) -> Confirm:
    return Confirm(
        title="Stop run?",
        subject=run.id,
        question=f"Are you sure you want to stop {run.id}?",
        note="The runner shuts the VM down and the run ends as STOPPED. Nothing is retried.",
        action=f"/runs/{run.id}/stop",
        back=f"/runs/{run.id}",
        footer="the audit keeps who stopped it",
        danger=True,
    )


# The rerun carries the spec and nothing else, so the new run waits for whichever
# runner is free rather than the one that held this one.
def rerun(run: RunDetailRow, key: str) -> Confirm:
    facts = {fact.label: fact for fact in run.run} | {fact.label: fact for fact in run.policy}
    options = [facts[label] for label in ("Spec", "Image", "Profile") if label in facts]
    options.append(Fact("Runner", "any live runner with a free slot"))
    if "Timeouts" in facts:
        options.append(facts["Timeouts"])
    return Confirm(
        title="Rerun?",
        subject=run.id,
        question=f"Are you sure you want to rerun {run.id}?",
        lead=LEAD,
        facts=tuple(options),
        action=f"/runs/{run.id}/rerun",
        back=f"/runs/{run.id}",
        footer="a new run id is issued \u00b7 this run is kept",
        key=key,
    )


def revoke(runner: RunnerDetailRow) -> Confirm:
    return Confirm(
        title="Revoke runner?",
        subject=runner.id,
        question=f"Are you sure you want to revoke {runner.name}?",
        note=(
            "Its tokens are refused from now on and its lease ends at once: the runs it holds "
            "fail as runner revoked and its pending runs go back to the queue."
        ),
        action=f"/runners/{runner.id}/revoke",
        back=f"/runners/{runner.id}",
        footer="the audit keeps who revoked it",
        danger=True,
    )


def drain(runner: RunnerDetailRow) -> Confirm:
    return Confirm(
        title="Drain runner?",
        subject=runner.id,
        question=f"Are you sure you want to drain {runner.name}?",
        note="It takes no new run; the runs it holds finish as usual.",
        action=f"/runners/{runner.id}/drain",
        back=f"/runners/{runner.id}",
        footer="the audit keeps who drained it",
    )


def _plural(count: int, word: str, many: str | None = None) -> str:
    return f"{count} {word if count == 1 else many or word + 's'}"


def _listed(parts: list[str]) -> str:
    if len(parts) < 2:
        return "".join(parts) or "Nothing"
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def _kinds(chosen: list[Row]) -> str:
    dirs = sum(1 for entry in chosen if entry["kind"] == "dir")
    parts = [
        f"{count} {change}"
        for change in ("created", "modified", "deleted", "renamed")
        if (count := sum(1 for e in chosen if e["change"] == change and e["kind"] != "dir"))
    ]
    if dirs:
        parts.append(_plural(dirs, "directory", "directories"))
    return _listed(parts)


# A selection holding a sensitive path asks the louder question and names every one of them.
def merge(run_id: str, workspace: str, diff: Row, chosen: Selection, back: str) -> Confirm:
    entries = [entry for entry in diff["entries"] if entry["change"] != "rejected"]
    picked = [entry for entry in entries if entry["path"] in chosen.paths]
    sensitive = [entry["path"] for entry in picked if entry.get("sensitive")]
    paths = _plural(len(picked), "path")
    common = Confirm(
        title=f"Merge {paths}?",
        subject=run_id,
        question=f"Apply {paths} to {workspace}?",
        action=f"/runs/{run_id}/merge",
        back=back,
        footer=DECIDED,
        button="Merge",
        fields=tuple(chosen.fields()),
    )
    if sensitive:
        one = len(sensitive) == 1
        count = WORDS[len(sensitive)] if len(sensitive) < len(WORDS) else str(len(sensitive))
        return replace(
            common,
            question=f"{count} of them {'runs' if one else 'run'} on the host or in CI once "
            "merged.",
            note=(
                f"{_listed(sensitive)} {'is' if one else 'are'} sensitive. Read "
                f"{'it' if one else 'them'} in the runner's archive before you merge: naos shows "
                f"{'its hash, not its contents' if one else 'their hashes, not their contents'}."
            ),
            button=f"Merge {paths}",
            danger=True,
        )
    left = [entry["path"] for entry in entries if entry["path"] not in chosen.paths]
    summary = resolved(chosen.resolutions)
    note = (
        f"{_kinds(picked)}. Replaced and removed host entries move to merge/backup/; nothing on "
        "the host is deleted."
    )
    if summary:
        note += f" Resolutions: {summary}."
    if left:
        note += f" Left out: {', '.join(left)}."
    return replace(common, note=note)


def merge_nothing(run_id: str, workspace: str, diff: Row, back: str) -> Confirm:
    live = sum(1 for entry in diff["entries"] if entry["change"] != "rejected")
    return Confirm(
        title="Merge nothing?",
        subject=run_id,
        question=f"Complete {run_id} without touching {workspace}?",
        note=(
            f"None of the {_plural(live, 'change')} reach the host and the run completes. The "
            "agent's changes stay in the runner's archive with the upper disk."
        ),
        action=f"/runs/{run_id}/merge/reject",
        back=back,
        footer=DECIDED,
        button="Merge nothing",
        danger=True,
    )
