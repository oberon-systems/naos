from typing import Any

import pytest
from sqlmodel import Session

from naos_api.errors import PolicyError
from naos_api.mounts import MountPolicyIn, resolve_mount_policy
from naos_api.policies import create_mount_policy
from naos_api.settings import Settings
from naos_api.spec import PolicyKind

ROOTS = ["/srv/projects", "/srv/agent-home"]


def _policy(host_path: str, home: list[dict[str, str]] | None = None) -> MountPolicyIn:
    return MountPolicyIn.model_validate({"workspace": {"host_path": host_path}, "home": home or []})


def test_workspace_becomes_workdir_under_naos() -> None:
    resolved = resolve_mount_policy(_policy("/srv/projects/alpha"), ROOTS)

    assert resolved.workdir == "/naos/alpha"
    assert resolved.mounts[0].model_dump() == {
        "host_path": "/srv/projects/alpha",
        "guest_path": "/naos/alpha",
        "mode": "rw",
    }


def test_home_mounts_land_in_naos_home(mount_body: dict[str, Any]) -> None:
    resolved = resolve_mount_policy(MountPolicyIn.model_validate(mount_body), ROOTS)

    assert resolved.mounts[1].model_dump() == {
        "host_path": "/srv/agent-home/claude",
        "guest_path": "/home/naos/.claude",
        "mode": "ro",
    }


@pytest.mark.parametrize(
    "host_path",
    [
        "srv/projects/alpha",
        "/srv/projects/../../etc",
        "/srv/projects/./alpha",
        "/srv/projects//alpha",
        "/srv/projects/alpha/",
        "/",
        "/srv/projects-evil/alpha",
        "/srv",
        "/etc/beta",
        "/srv/projects/al\x00pha",
        "/srv/projects/al\npha",
        "/srv/projects/.hidden",
        "/srv/projects/-alpha",
    ],
)
def test_bad_workspace_is_rejected(host_path: str) -> None:
    with pytest.raises(PolicyError):
        resolve_mount_policy(_policy(host_path), ROOTS)


@pytest.mark.parametrize(
    "guest_path", ["../etc", "/etc/passwd", ".", "..", "a/../../etc", "a//b", "a/", "a b"]
)
def test_home_mount_cannot_escape_naos_home(guest_path: str) -> None:
    home = [{"host_path": "/srv/agent-home/claude", "guest_path": guest_path}]

    with pytest.raises(PolicyError):
        resolve_mount_policy(_policy("/srv/projects/alpha", home), ROOTS)


def test_home_host_path_needs_allowed_root() -> None:
    home = [{"host_path": "/etc/beta", "guest_path": ".beta"}]

    with pytest.raises(PolicyError, match="outside"):
        resolve_mount_policy(_policy("/srv/projects/alpha", home), ROOTS)


def test_duplicate_guest_paths_are_rejected() -> None:
    home = [
        {"host_path": "/srv/agent-home/a", "guest_path": ".claude"},
        {"host_path": "/srv/agent-home/b", "guest_path": ".claude"},
    ]

    with pytest.raises(PolicyError, match="unique"):
        resolve_mount_policy(_policy("/srv/projects/alpha", home), ROOTS)


@pytest.mark.parametrize("roots", [[], ["/"], ["srv/projects"]])
def test_missing_or_unsafe_roots_deny_every_mount(roots: list[str]) -> None:
    with pytest.raises(PolicyError):
        resolve_mount_policy(_policy("/srv/projects/alpha"), roots)


def test_mount_policy_is_resolved_and_deduplicated(
    session: Session, settings: Settings, mount_body: dict[str, Any]
) -> None:
    policy = MountPolicyIn.model_validate(mount_body)

    first, first_created = create_mount_policy(session, policy, ROOTS)
    again, again_created = create_mount_policy(session, policy, ROOTS)

    assert first_created and not again_created
    assert again.id == first.id
    assert first.id.startswith("mntpol_")
    assert first.kind is PolicyKind.MOUNT
    assert first.document["workdir"] == "/naos/alpha"


def test_different_mounts_get_different_policies(session: Session, settings: Settings) -> None:
    alpha, _ = create_mount_policy(session, _policy("/srv/projects/alpha"), ROOTS)
    beta, _ = create_mount_policy(session, _policy("/srv/projects/beta"), ROOTS)

    assert alpha.id != beta.id
