from dataclasses import dataclass, field
from typing import Literal

Tone = Literal["blue", "green", "amber", "red", "grey", "faint"]


@dataclass(frozen=True)
class Tile:
    key: str
    label: str
    tone: Tone
    qualifier: str = ""


@dataclass(frozen=True)
class Action:
    label: str
    href: str


@dataclass(frozen=True)
class Chip:
    label: str
    tone: Tone = "grey"


@dataclass(frozen=True)
class ListPage:
    key: str
    title: str
    href: str
    tiles: tuple[Tile, ...] = ()
    action: Action | None = None
    chip: Chip | None = None
    header: bool = True


NAV: tuple[ListPage, ...] = (
    ListPage(
        key="runs",
        title="Runs",
        href="/runs",
        tiles=(
            Tile("active", "Active runs", "blue"),
            Tile("queued", "Queued", "grey"),
            Tile("waiting_merge", "Waiting merge", "amber"),
            Tile("failed", "Failed", "red", qualifier="24h"),
        ),
        header=False,
    ),
    ListPage(
        key="runners",
        title="Runners",
        href="/runners",
        tiles=(
            Tile("live", "Live", "green"),
            Tile("stale", "Stale", "amber"),
            Tile("revoked", "Revoked", "red"),
            Tile("slots_busy", "Slots busy", "blue"),
        ),
        action=Action("Enroll runner", "/runners/enroll"),
        chip=Chip("enrollment open", "green"),
    ),
    ListPage(
        key="images",
        title="Images",
        href="/images",
        tiles=(
            Tile("registered", "Registered", "blue"),
            Tile("in_use", "In use", "green"),
            Tile("unused", "Unused", "faint"),
            Tile("catalog_size", "Catalog size", "grey"),
        ),
        action=Action("Register image", "/images/register"),
        chip=Chip("catalog is append-only", "grey"),
    ),
    ListPage(
        key="profiles",
        title="Profiles",
        href="/profiles",
        tiles=(
            Tile("profiles", "Profiles", "blue"),
            Tile("policies", "Policies", "green"),
            Tile("secrets", "Secrets", "amber"),
            Tile("runs_24h", "Runs 24h", "grey"),
        ),
        action=Action("New profile", "/profiles/new"),
        chip=Chip("a Run copies, never references", "grey"),
    ),
    ListPage(
        key="audit",
        title="Audit",
        href="/audit",
        tiles=(
            Tile("events_24h", "Events 24h", "blue"),
            Tile("gate_denials", "Gate denials", "red"),
            Tile("refused", "Refused", "green"),
            Tile("spool_lag", "Spool lag", "amber"),
        ),
        action=Action("Export", "/audit/export"),
        chip=Chip("live tail", "green"),
    ),
)

PAGES: dict[str, ListPage] = {page.key: page for page in NAV}


@dataclass
class TileValue:
    number: str = ""
    note: str = ""


@dataclass
class Summary:
    subtitle: str = ""
    values: dict[str, TileValue] = field(default_factory=dict)
