from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import Any, Literal
from uuid import uuid4

from naos_web.client import Choices

Row = dict[str, Any]
Mode = Literal["update", "new"]
AUTO = "auto"
POLICY_KINDS = (("mounts", "mount"), ("network", "network"), ("shell", "shell"), ("mcp", "mcp"))
MERGE_NOTES = {
    "never": "never: changes are kept out of the workspace",
    "ask": "ask: changes wait in WAITING_MERGE for approval",
    "always": "always: changes merge into the workspace once collected",
}
NEW_PROFILE: dict[str, str] = {
    "cpu": "2",
    "memory_mib": "4096",
    "disk_gib": "20",
    "timeout": "3600",
    "merge": "ask",
    "mounts": "",
    "network": "",
    "shell": "",
    "mcp": "",
}
FIELDS = tuple(NEW_PROFILE)
NUMBERS = {"cpu": "vCPU", "memory_mib": "MiB", "disk_gib": "GiB", "timeout": "s"}
LABELS = {
    "cpu": "CPU",
    "memory_mib": "Memory",
    "disk_gib": "Disk",
    "timeout": "Timeout",
    "merge": "Merge",
    "mounts": "Mounts",
    "network": "Network",
    "shell": "Shell",
    "mcp": "MCP",
}


class FormError(ValueError):
    """The form carries a value the dialog cannot turn into a spec."""


def values_of(spec: Row) -> dict[str, str]:
    runtime = spec["runtime"]
    values = {
        "cpu": str(runtime["cpu"]),
        "memory_mib": str(runtime["memory_mib"]),
        "disk_gib": str(runtime["disk_gib"]),
        "timeout": str(spec["timeout"]),
        "merge": str(spec["merge"]["policy"]),
    }
    for name, _ in POLICY_KINDS:
        values[name] = spec[name]["policy"] or ""
    return values


def _number(values: Mapping[str, str], name: str) -> int:
    try:
        return int(values[name])
    except ValueError as err:
        raise FormError(f"{LABELS[name]} must be a whole number") from err


def spec_of(values: Mapping[str, str]) -> Row:
    return {
        "runtime": {
            "cpu": _number(values, "cpu"),
            "memory_mib": _number(values, "memory_mib"),
            "disk_gib": _number(values, "disk_gib"),
        },
        **{name: {"policy": values[name] or None} for name, _ in POLICY_KINDS},
        "merge": {"policy": values["merge"]},
        "timeout": _number(values, "timeout"),
    }


@dataclass(frozen=True)
class Draft:
    """What the dialog has gathered so far; it travels between steps in hidden fields."""

    key: str
    profile: str = ""
    values: dict[str, str] = field(default_factory=lambda: dict(NEW_PROFILE))
    image: str = AUTO
    runner: str = AUTO
    mode: Mode = "update"
    name: str = ""

    @classmethod
    def fresh(cls) -> "Draft":
        return cls(key=uuid4().hex)

    @classmethod
    def of(cls, form: Mapping[str, str]) -> "Draft":
        key = form.get("key") or uuid4().hex
        values = {name: form.get(name, NEW_PROFILE[name]).strip() for name in FIELDS}
        mode: Mode = "new" if form.get("mode") == "new" else "update"
        return cls(
            key=key,
            profile=form.get("profile", ""),
            values=values,
            image=form.get("image") or AUTO,
            runner=form.get("runner") or AUTO,
            mode=mode,
            name=form.get("name", "").strip(),
        )

    def based_on(self, profile: Row | None) -> "Draft":
        return replace(self, values=values_of(profile["spec"]) if profile else dict(NEW_PROFILE))

    def edited(self, profile: Row | None) -> list[str]:
        if profile is None:
            return []
        base = values_of(profile["spec"])
        return [name for name in FIELDS if self.values[name] != base[name]]

    @property
    def hidden(self) -> dict[str, str]:
        return {
            "key": self.key,
            "profile": self.profile,
            **self.values,
            "image": self.image,
            "runner": self.runner,
        }


def usage(profile: Row) -> str:
    if profile["active_runs"]:
        return f"{profile['active_runs']} active"
    return "never used" if profile["last_run_at"] is None else "idle"


def runtime_line(spec: Row) -> str:
    runtime = spec["runtime"]
    return (
        f"{runtime['cpu']} cpu · {runtime['memory_mib']} MiB · "
        f"{runtime['disk_gib']} GiB · {spec['timeout']}s · {spec['merge']['policy']}"
    )


def short(identifier: str) -> str:
    return f"{identifier[:12]}\u2026" if len(identifier) > 13 else identifier


# Labels read the resolved document; a credential is named by the policy, its value never is.
def policy_label(policy: Row) -> str:
    document = policy["document"]
    kind = policy["kind"]
    if kind == "mount":
        workdir = document["workdir"]
        mount = next((m for m in document["mounts"] if m["guest_path"] == workdir), None)
        if mount is None:
            return PurePosixPath(workdir).name
        path = PurePosixPath(mount["host_path"])
        return f"{path.parent.name}/{path.name} · {mount['mode']} workspace"
    if kind == "network":
        return f"allowlist · {len(document['allow'])} hosts"
    if kind == "shell":
        return f"{len(document['allow'])} capabilities"
    names = ", ".join(server["name"] for server in document["servers"])
    return f"{len(document['servers'])} servers · {names}"


def image_label(image: Row) -> str:
    return f"{image['id']} · v{image['version']}"


def digest_note(image: Row) -> str:
    digest = image["digest"].removeprefix("sha256:")
    return f"sha256:{digest[:4]}\u2026{digest[-4:]} · pinned by digest"


def resolve_image(images: list[Row], choice: str) -> Row | None:
    if choice == AUTO:
        return images[0] if images else None
    return next((image for image in images if image["id"] == choice), None)


@dataclass(frozen=True)
class Option:
    value: str
    label: str
    selected: bool


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    value: str
    unit: str = ""
    edited: bool = False
    hint: str = ""
    options: list[Option] = field(default_factory=list)


def _profile_hint(name: str, base: Mapping[str, str] | None, labels: Mapping[str, str]) -> str:
    if base is None:
        return ""
    value = base[name]
    unit = NUMBERS.get(name, "")
    shown = labels.get(value, value) or "none"
    return f"profile: {shown} {unit}".rstrip()


def configure_fields(draft: Draft, profile: Row | None, choices: Choices) -> dict[str, Field]:
    base = values_of(profile["spec"]) if profile else None
    edited = set(draft.edited(profile))
    labels = {policy["id"]: policy_label(policy) for policy in choices.policies}
    fields: dict[str, Field] = {}
    for name, unit in NUMBERS.items():
        fields[name] = Field(
            name=name,
            label=LABELS[name],
            value=draft.values[name],
            unit=unit,
            edited=name in edited,
            hint=_profile_hint(name, base, labels) if name in edited else "",
        )
    fields["timeout"] = replace(fields["timeout"], hint=fields["timeout"].hint or "60 \u2013 86400")
    for name, kind in POLICY_KINDS:
        chosen = draft.values[name]
        options = [Option("", "none", chosen == "")] + [
            Option(policy["id"], labels[policy["id"]], policy["id"] == chosen)
            for policy in choices.policies
            if policy["kind"] == kind
        ]
        fields[name] = Field(
            name=name,
            label=LABELS[name],
            value=chosen,
            edited=name in edited,
            hint=_profile_hint(name, base, labels) if name in edited else short(chosen),
            options=options,
        )
    fields["merge"] = Field(
        name="merge",
        label=LABELS["merge"],
        value=draft.values["merge"],
        edited="merge" in edited,
        hint=MERGE_NOTES.get(draft.values["merge"], ""),
        options=[Option(mode, mode, mode == draft.values["merge"]) for mode in MERGE_NOTES],
    )
    return fields


def image_field(draft: Draft, images: list[Row]) -> Field:
    resolved = resolve_image(images, draft.image)
    newest = f"auto · newest: {image_label(images[0])}" if images else "auto"
    return Field(
        name="image",
        label="Image",
        value=draft.image,
        hint=digest_note(resolved) if resolved else "no registered image",
        options=[Option(AUTO, newest, draft.image == AUTO)]
        + [Option(image["id"], image_label(image), image["id"] == draft.image) for image in images],
    )


def runner_field(draft: Draft, runners: list[Row]) -> Field:
    live = [runner for runner in runners if runner["status"] == "live"]
    names = ", ".join(runner["name"] for runner in live)
    return Field(
        name="runner",
        label="Runner",
        value=draft.runner,
        hint=f"any live runner with a free slot · {names}" if names else "no live runner yet",
        options=[Option(AUTO, AUTO, draft.runner == AUTO)]
        + [Option(r["id"], r["name"], r["id"] == draft.runner) for r in live],
    )


@dataclass(frozen=True)
class Change:
    label: str
    change: str


def changes(draft: Draft, profile: Row | None, policies: list[Row]) -> list[Change]:
    if profile is None:
        return []
    base = values_of(profile["spec"])
    labels = {policy["id"]: policy_label(policy) for policy in policies}
    rows = []
    for name in draft.edited(profile):
        before = labels.get(base[name], base[name]) or "none"
        after = labels.get(draft.values[name], draft.values[name]) or "none"
        unit = f" {NUMBERS[name]}" if name in NUMBERS else ""
        rows.append(Change(LABELS[name], f"{before} \u2192 {after}{unit}"))
    return rows


def update_lock(profile: Row | None) -> str:
    if profile is None or profile["active_run"] is None:
        return ""
    run = profile["active_run"]
    return (
        f"Update {profile['name']} is off: #{run['seq']} is {run['status']} on it. "
        "It frees once no run is active"
    )
