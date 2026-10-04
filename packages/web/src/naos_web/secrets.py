from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from naos_web import format
from naos_web.client import Row
from naos_web.new_run import FormError, short
from naos_web.pages import STATUS_TONE, Summary, TileValue, Tone
from naos_web.profiles import Tag
from naos_web.rows import Fact

State = Literal["all", "valid", "expiring", "expired", "used", "unused"]
STATE_TONE: dict[str, Tone] = {"valid": "green", "expiring": "amber", "expired": "red"}
# The board draws four ways to name a secret; the api reports these two so far.
TAGS = ("reg", "model", "cred", "grant")
ROLE = {"reg": "server", "model": "provider"}
TERMS: dict[str, int | None] = {
    "never": None,
    "1h": format.HOUR,
    "3h": 3 * format.HOUR,
    "12h": 12 * format.HOUR,
    "1d": format.DAY,
    "7d": 7 * format.DAY,
    "30d": 30 * format.DAY,
}
DEFAULT_TERM = "7d"
CONFIRMED = "confirmed"
REFUSED_NOTE = (
    "the api refused the name or the value: a name is lowercase letters, digits, . _ and -; "
    "a value is 1 to 8192 visible ASCII characters, spaces and line breaks"
)


def _plural(count: int, word: str, words: str = "") -> str:
    return f"{count} {word}" if count == 1 else f"{count} {words or word + 's'}"


def _stamp(at: int, pattern: str = "%Y-%m-%d %H:%M UTC") -> str:
    return datetime.fromtimestamp(at, UTC).strftime(pattern)


def api_filter(state: State) -> tuple[str | None, bool | None]:
    if state in ("used", "unused"):
        return None, state == "used"
    return (None if state == "all" else state), None


def shelf(secrets: list[Row]) -> Summary:
    named = sum(len(secret["named_by"]) for secret in secrets)
    holders = {holder["run_id"] for secret in secrets for holder in secret["held_by"]}
    expired = sum(secret["state"] == "expired" for secret in secrets)
    ending = expired + sum(secret["state"] == "expiring" for secret in secrets)
    return Summary(
        subtitle=(
            f"{_plural(len(secrets), 'secret')} \u00b7 named by {named} "
            f"\u00b7 held by {_plural(len(holders), 'run')}"
        ),
        values={
            "secrets": TileValue(str(len(secrets)), "names only, never values"),
            "in_use": TileValue(
                str(sum(bool(secret["named_by"]) for secret in secrets)),
                "named by a server or policy",
            ),
            "expiring": TileValue(
                str(ending), "within 7 days" + (f" \u00b7 {expired} expired" if expired else "")
            ),
            "held": TileValue(str(len(holders)), "issued, run still open"),
        },
    )


def _left(secret: Row, now: int) -> str:
    expires = secret["expires_at"]
    if expires is None:
        return "no expiry"
    if expires <= now:
        return f"since {format.coarse(now - expires)}"
    return f"{format.coarse(expires - now)} left"


def _expiry(secret: Row, now: int) -> str:
    expires = secret["expires_at"]
    if expires is None:
        return "no expiry"
    if expires <= now:
        return f"expired {format.ago(expires, now)}"
    return f"expires in {format.coarse(expires - now)}"


def _policies(secret: Row, kind: str) -> int:
    return len({usage["id"] for usage in secret["named_by"] if usage["kind"] == kind})


def _used(secret: Row) -> str:
    parts = []
    if _policies(secret, "reg"):
        parts.append(_plural(_policies(secret, "reg"), "server"))
    if _policies(secret, "model"):
        parts.append(_plural(_policies(secret, "model"), "model policy", "model policies"))
    if not parts:
        return "held by an open run" if secret["held_by"] else "unused \u00b7 can be deleted"
    if secret["state"] == "expired":
        parts.append("issue refused")
    return " \u00b7 ".join(parts)


def _tone(secret: Row) -> Tone:
    used = secret["named_by"] or secret["held_by"]
    return STATE_TONE[secret["state"]] if used or secret["state"] != "valid" else "faint"


def _counts(secret: Row) -> str:
    held = len(secret["held_by"])
    return f"named by {len(secret['named_by'])} \u00b7 held by {_plural(held, 'run')}"


def _subject(secret: Row) -> str:
    named, held = len(secret["named_by"]), len(secret["held_by"])
    holders = _plural(held, "run") if held else "no Run"
    return (
        f"Secret {format.short_id(secret['id'])} \u00b7 named by {named or 'nothing'} "
        f"\u00b7 held by {holders}"
    )


@dataclass(frozen=True)
class SecretRow:
    name: str
    short_id: str
    tone: Tone
    muted: bool
    pill: str
    pill_tone: Tone
    left: str
    expires: str
    expires_note: str
    tags: list[Tag]
    used: str
    rotated: str
    rotated_note: str
    held: str
    held_note: str


def secret_rows(secrets: list[Row], now: int) -> list[SecretRow]:
    rows = []
    for secret in secrets:
        expires, rotated = secret["expires_at"], secret["rotated_at"]
        kinds = {usage["kind"] for usage in secret["named_by"]}
        holders = secret["held_by"]
        rows.append(
            SecretRow(
                name=secret["name"],
                short_id=format.short_id(secret["id"]),
                tone=_tone(secret),
                muted=secret["state"] == "expired" or not (kinds or holders),
                pill=secret["state"].upper(),
                pill_tone=STATE_TONE[secret["state"]],
                left=_left(secret, now),
                expires="never" if expires is None else _stamp(expires, "%Y-%m-%d"),
                expires_note=format.DASH if expires is None else _stamp(expires, "%H:%M UTC"),
                tags=[Tag(tag, tag in kinds) for tag in TAGS],
                used=_used(secret),
                rotated=format.ago(rotated or secret["created_at"], now),
                rotated_note="by operator" if rotated else "never rotated",
                held=_plural(len(holders), "run") if holders else "no runs",
                held_note=" \u00b7 ".join(f"#{holder['seq']}" for holder in holders) or "none open",
            )
        )
    return rows


@dataclass(frozen=True)
class NamedBy:
    tag: str
    id: str
    short_id: str
    kind: str
    role: str
    line: str
    scope: str
    scope_note: str
    href: str


@dataclass(frozen=True)
class RunUse:
    id: str
    seq: int
    status: str
    tone: Tone
    profile: str
    access: str = ""
    note: str = ""
    last: str = ""


@dataclass(frozen=True)
class SecretDetail:
    name: str
    id: str
    tone: Tone
    pill: str
    pill_tone: Tone
    meta: list[str]
    subject: str
    expires: str
    facts: list[Fact]
    named: list[NamedBy]
    held: list[RunUse]
    runs: list[RunUse]


def _named(usage: Row, policies: dict[str, Row]) -> NamedBy:
    role = f"{ROLE[usage['kind']]} {usage['server']}"
    policy = policies.get(usage["id"])
    # A registry server has no screen of its own yet, so it is named without a link.
    kind, href = "mcp registry", ""
    if usage["kind"] == "model":
        kind, href = "model policy", f"/policies/{usage['id']}"
    return NamedBy(
        tag=usage["kind"],
        id=usage["id"],
        short_id=short(usage["id"]),
        kind=kind,
        role=role,
        line=f"{kind} \u00b7 credential of {role}",
        scope=_plural(len(policy["profiles"]), "profile") if policy else format.DASH,
        scope_note=_plural(policy["runs_total"], "run") if policy else "",
        href=href,
    )


def _run(holder: Row, profiles: dict[str, str], **shown: str) -> RunUse:
    profile = holder["profile_id"]
    return RunUse(
        id=holder["run_id"],
        seq=holder["seq"],
        status=holder["status"],
        tone=STATUS_TONE[holder["status"]],
        profile=profiles.get(profile or "", profile or format.DASH),
        **shown,
    )


def _rotated(secret: Row, events: list[Row], now: int) -> str:
    rotated = secret["rotated_at"]
    if rotated is None:
        return "never"
    issues = sum(row["event"] == "credentials_issued" and row["at"] >= rotated for row in events)
    return f"{format.ago(rotated, now)} \u00b7 by operator \u00b7 {_plural(issues, 'issue')} since"


def secret_detail(
    secret: Row, events: list[Row], policies: list[Row], profiles: list[Row], now: int
) -> SecretDetail:
    known = {policy["id"]: policy for policy in policies}
    names = {profile["id"]: profile["name"] for profile in profiles}
    holding = {holder["run_id"] for holder in secret["held_by"]}
    rotated = secret["rotated_at"]
    expires = "never" if secret["expires_at"] is None else _stamp(secret["expires_at"])
    return SecretDetail(
        name=secret["name"],
        id=secret["id"],
        tone=_tone(secret),
        pill=secret["state"].upper(),
        pill_tone=STATE_TONE[secret["state"]],
        meta=[
            f"created {format.ago(secret['created_at'], now)}",
            f"rotated {format.ago(rotated, now)}" if rotated else "never rotated",
            _expiry(secret, now),
            _counts(secret),
        ],
        subject=_subject(secret),
        expires=expires,
        facts=[
            Fact("Name", secret["name"]),
            Fact("Id", secret["id"]),
            Fact("State", f"{secret['state']} \u00b7 {_expiry(secret, now)}"),
            Fact("Expires", expires),
            Fact("Rotated", _rotated(secret, events, now)),
        ],
        named=[_named(usage, known) for usage in secret["named_by"]],
        held=[_run(holder, names) for holder in secret["held_by"]],
        runs=[
            _run(
                run,
                names,
                access="issued" if run["issued"] == 1 else f"issued {run['issued']}\u00d7",
                note="holds it now" if run["run_id"] in holding else "",
                last=format.ago(run["last_at"], now),
            )
            for run in secret["runs"]
        ],
    )


@dataclass(frozen=True)
class Term:
    key: str
    note: str
    selected: bool


def _span(key: str) -> str:
    return _plural(int(key[:-1]), "hour" if key.endswith("h") else "day")


def terms(now: int, chosen: str) -> list[Term]:
    options = []
    for key, seconds in TERMS.items():
        note = "never expires"
        if seconds is not None:
            note = f"expires {_stamp(now + seconds)} \u00b7 in {_span(key)}"
        options.append(Term(key, note, key == chosen))
    return options


def chosen_term(fields: dict[str, str]) -> str:
    term = fields.get("term", DEFAULT_TERM)
    return term if term in TERMS else DEFAULT_TERM


def confirmed(fields: dict[str, str]) -> bool:
    return chosen_term(fields) == "never" and fields.get("never") == CONFIRMED


# An unconfirmed never falls back, so picking it again asks the question again.
def shown_term(fields: dict[str, str]) -> str:
    term = chosen_term(fields)
    return term if term != "never" or confirmed(fields) else DEFAULT_TERM


# The browser asks before it posts never; a post that skipped the question is refused.
def expires_at(fields: dict[str, str], now: int) -> int | None:
    seconds = TERMS[chosen_term(fields)]
    if seconds is None:
        if not confirmed(fields):
            raise FormError("never needs its confirmation: pick it again and answer Yes")
        return None
    return now + seconds


# A browser posts line breaks as CRLF; the secret keeps the ones that were typed.
def value_of(fields: dict[str, str]) -> str:
    value = fields.get("value", "").replace("\r\n", "\n")
    if not value.strip():
        raise FormError("a secret needs a value")
    return value
