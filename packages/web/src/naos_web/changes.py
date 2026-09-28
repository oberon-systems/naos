from dataclasses import dataclass
from typing import Literal

from naos_web import format
from naos_web.client import Row
from naos_web.pages import Tone

Filter = Literal["all", "created", "modified", "deleted", "renamed", "rejected", "sensitive"]

FILTERS: tuple[tuple[Filter, str], ...] = (
    ("all", "All"),
    ("created", "Created"),
    ("modified", "Modified"),
    ("deleted", "Deleted"),
    ("renamed", "Renamed"),
    ("rejected", "Rejected"),
    ("sensitive", "Sensitive"),
)
CHANGE_TONE: dict[str, Tone] = {
    "created": "green",
    "modified": "blue",
    "deleted": "red",
    "renamed": "violet",
    "rejected": "grey",
}
LIST_NOTE = (
    "Sorted by path bytes, as collected. File contents stay on the upper disk; "
    "a change is judged by size, mode and sha256."
)
ENTRY_NOTE = (
    "The base is the host file at collection time; the merge applies only while the host "
    "still matches it. Export leaves the host alone and writes the agent's version to "
    "merge/export/."
)
SENSITIVE_NOTE = (
    "These run on the host or in CI once merged. They are never selected for you, even under "
    "merge policy always \u2014 tick each one yourself."
)
REJECTED_NOTE = "A rejected object never reaches the host and cannot be selected."
KIB = 1024


@dataclass(frozen=True)
class Banner:
    tone: Tone
    title: str
    text: str


@dataclass(frozen=True)
class EntryRow:
    index: int
    path: str
    change: str
    tone: Tone
    source: str | None
    detail: str
    sensitive: bool
    rejected: bool


@dataclass(frozen=True)
class Fact:
    label: str
    value: str
    mono: bool = False


@dataclass(frozen=True)
class Picked:
    index: int
    path: str
    change: str
    tone: Tone
    facts: list[Fact]


@dataclass(frozen=True)
class Changes:
    banner: Banner
    counts: dict[Filter, int]
    shown: Filter
    rows: list[EntryRow]
    picked: Picked | None
    sensitive: list[str]
    rejected: list[EntryRow]


def size(value: int) -> str:
    return f"{value} B" if value < KIB else f"{value / KIB:.1f} KiB"


def _mode(value: int) -> str:
    return f"{value:04o}"


def _digest(value: str) -> str:
    return f"{value[:4]}\u2026{value[-4:]}"


def _detail(entry: Row) -> str:
    if entry["change"] == "rejected":
        return str(entry.get("reason") or "rejected")
    if entry["kind"] == "symlink" and entry.get("target") is not None:
        return f"\u2192 {entry['target']}"
    if entry["kind"] == "dir":
        return "dir"
    if entry.get("size") is not None:
        return size(entry["size"])
    return ""


def _row(index: int, entry: Row) -> EntryRow:
    return EntryRow(
        index=index,
        path=entry["path"],
        change=entry["change"],
        tone=CHANGE_TONE[entry["change"]],
        source=entry.get("from"),
        detail=_detail(entry),
        sensitive=bool(entry.get("sensitive")),
        rejected=entry["change"] == "rejected",
    )


def _matches(row: EntryRow, shown: Filter) -> bool:
    if shown == "all":
        return True
    if shown == "sensitive":
        return row.sensitive
    return row.change == shown


def _picked(index: int, entry: Row) -> Picked:
    facts = [Fact("Kind", entry["kind"])]
    if entry.get("size") is not None:
        facts.append(Fact("Size", size(entry["size"])))
    if entry.get("mode") is not None:
        base = entry.get("base_mode")
        facts.append(
            Fact(
                "Mode",
                _mode(entry["mode"]) + ("" if base is None else f" \u00b7 base {_mode(base)}"),
                mono=True,
            )
        )
    if entry.get("sha256") is not None:
        facts.append(Fact("sha256", _digest(entry["sha256"]), mono=True))
    if entry.get("base_sha256") is not None:
        facts.append(Fact("Base sha256", _digest(entry["base_sha256"]), mono=True))
    if entry.get("target") is not None:
        facts.append(Fact("Target", entry["target"], mono=True))
    if entry.get("base_target") is not None:
        facts.append(Fact("Base target", entry["base_target"], mono=True))
    return Picked(
        index=index,
        path=entry["path"],
        change=entry["change"],
        tone=CHANGE_TONE[entry["change"]],
        facts=facts,
    )


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def banner(merge: Row, policy: str, workspace: str, runner: str, now: int) -> Banner:
    report, conflicts, decision = merge["report"], merge["conflicts"], merge["decision"]
    if report is not None:
        return Banner(
            "green",
            f"Merged into {workspace} \u00b7 {len(report['applied'])} applied \u00b7 "
            f"{len(report['exported'])} exported \u00b7 {len(report['backed_up'])} backed up",
            "Every replaced or removed host entry was moved to the backup, never deleted. "
            "The run completed when the runner reported the merge.",
        )
    if conflicts:
        return Banner(
            "red",
            f"{_plural(len(conflicts), 'conflict')} \u00b7 nothing was written",
            "The runner compared every selected path with the host before writing and stopped: "
            "the host changed since collection. Resolve each conflict and send the decision "
            "again.",
        )
    if decision is not None:
        return Banner(
            "blue",
            f"Decision sent \u00b7 runner {runner} is applying it",
            f"You selected {_plural(len(decision['paths']), 'path')} "
            f"{format.ago(merge['updated_at'], now)}. The selection is locked until the runner "
            "reports: either the merge applies and the run completes, or conflicts come back "
            "here with nothing written.",
        )
    return Banner(
        "amber",
        f"Waiting for your decision \u00b7 merge policy {policy}",
        f"The workspace {workspace} is untouched until you merge. Nothing on the host is "
        "deleted: a replaced or removed entry moves to merge/backup/.",
    )


# A rejected entry cannot be picked, so asking for one falls back to the first live entry.
def changes(merge: Row, shown: Filter, picked: int | None, banner_row: Banner) -> Changes:
    entries: list[Row] = merge["entries"]
    rows = [_row(index, entry) for index, entry in enumerate(entries)]
    live = [row.index for row in rows if not row.rejected]
    index = picked if picked in live else (live[0] if live else None)
    return Changes(
        banner=banner_row,
        counts={key: sum(1 for row in rows if _matches(row, key)) for key, _ in FILTERS},
        shown=shown,
        rows=[row for row in rows if _matches(row, shown)],
        picked=_picked(index, entries[index]) if index is not None else None,
        sensitive=[row.path for row in rows if row.sensitive],
        rejected=[row for row in rows if row.rejected],
    )
