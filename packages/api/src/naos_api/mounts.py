import re
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import Field

from naos_api.errors import PolicyError
from naos_api.spec import StrictModel

GUEST_HOME = PurePosixPath("/home/naos")
WORKSPACE_ROOT = PurePosixPath("/naos")

_PROJECT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SEGMENT = re.compile(r"[A-Za-z0-9._-]{1,255}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

Mode = Literal["ro", "rw"]
RawPath = Annotated[str, Field(min_length=1, max_length=4096)]


class WorkspaceMountIn(StrictModel):
    host_path: RawPath
    mode: Mode = "rw"


class HomeMountIn(StrictModel):
    host_path: RawPath
    guest_path: RawPath
    mode: Mode = "ro"


class MountPolicyIn(StrictModel):
    workspace: WorkspaceMountIn
    home: Annotated[list[HomeMountIn], Field(max_length=32)] = []


class Mount(StrictModel):
    host_path: str
    guest_path: str
    mode: Mode


class MountPolicy(StrictModel):
    workdir: str
    mounts: list[Mount]


def _host_path(raw: str) -> PurePosixPath:
    parts = raw.split("/")
    if _CONTROL.search(raw) or parts[0] != "" or any(p in ("", ".", "..") for p in parts[1:]):
        raise PolicyError(f"host path {raw!r} must be absolute and normalized")
    return PurePosixPath(raw)


def _guest_home_path(raw: str) -> PurePosixPath:
    parts = raw.split("/")
    if any(p in (".", "..") or not _SEGMENT.fullmatch(p) for p in parts):
        raise PolicyError(f"guest path {raw!r} must be relative to {GUEST_HOME}")
    return GUEST_HOME.joinpath(*parts)


def _authorized(raw: str, roots: Sequence[PurePosixPath]) -> PurePosixPath:
    path = _host_path(raw)
    if not any(path.is_relative_to(root) for root in roots):
        raise PolicyError(f"host path {raw!r} is outside the allowed mount roots")
    return path


def resolve_mount_policy(policy: MountPolicyIn, allowed_roots: Sequence[str]) -> MountPolicy:
    roots = [_host_path(root) for root in allowed_roots]
    workspace_host = _authorized(policy.workspace.host_path, roots)
    if not _PROJECT_NAME.fullmatch(workspace_host.name):
        raise PolicyError(f"workspace name {workspace_host.name!r} is not a valid project name")

    workdir = WORKSPACE_ROOT / workspace_host.name
    mounts = [
        Mount(host_path=str(workspace_host), guest_path=str(workdir), mode=policy.workspace.mode)
    ]
    for item in policy.home:
        mounts.append(
            Mount(
                host_path=str(_authorized(item.host_path, roots)),
                guest_path=str(_guest_home_path(item.guest_path)),
                mode=item.mode,
            )
        )

    guest_paths = [mount.guest_path for mount in mounts]
    if len(set(guest_paths)) != len(guest_paths):
        raise PolicyError("guest paths must be unique")
    return MountPolicy(workdir=str(workdir), mounts=mounts)
