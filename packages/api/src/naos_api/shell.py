from typing import Annotated, Literal

from pydantic import Field

from naos_api.errors import PolicyError
from naos_api.spec import StrictModel

Capability = Literal["read_file", "list_dir", "grep", "git_status", "git_diff"]

CAPABILITIES: tuple[Capability, ...] = (
    "read_file",
    "list_dir",
    "grep",
    "git_status",
    "git_diff",
)


class ShellPolicyIn(StrictModel):
    allow: Annotated[list[Capability], Field(max_length=len(CAPABILITIES))] = []


class ShellPolicy(StrictModel):
    allow: list[Capability]


def resolve_shell_policy(policy: ShellPolicyIn) -> ShellPolicy:
    if not policy.allow:
        raise PolicyError("a shell policy must grant at least one capability")
    if len(set(policy.allow)) != len(policy.allow):
        raise PolicyError("a shell policy must not repeat a capability")
    # Ordered by CAPABILITIES so documents granting the same set share one digest.
    return ShellPolicy(allow=[name for name in CAPABILITIES if name in policy.allow])
