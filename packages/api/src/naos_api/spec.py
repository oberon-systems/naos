import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PolicyKind(StrEnum):
    MOUNT = "mount"
    NETWORK = "network"
    SHELL = "shell"
    MCP = "mcp"


PolicyId = Annotated[str, Field(pattern=r"^[a-z]+_[0-9a-f]{32}$")]
ImageId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
RunnerId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_]{1,64}$")]


class ImageRef(StrictModel):
    id: ImageId
    digest: Digest


class RuntimeSpec(StrictModel):
    cpu: Annotated[StrictInt, Field(ge=1, le=64)]
    memory_mib: Annotated[StrictInt, Field(ge=256, le=262144)]
    disk_gib: Annotated[StrictInt, Field(ge=1, le=2048)]


class PolicyRef(StrictModel):
    policy: PolicyId | None = None


class MergeSpec(StrictModel):
    policy: Literal["always", "ask", "never"] = "ask"


class ProfileSpec(StrictModel):
    runtime: RuntimeSpec
    mounts: PolicyRef = PolicyRef()
    network: PolicyRef = PolicyRef()
    shell: PolicyRef = PolicyRef()
    mcp: PolicyRef = PolicyRef()
    merge: MergeSpec = MergeSpec()
    timeout: Annotated[StrictInt, Field(ge=60, le=86400)]

    def policy_refs(self) -> dict[PolicyKind, str | None]:
        return {
            PolicyKind.MOUNT: self.mounts.policy,
            PolicyKind.NETWORK: self.network.policy,
            PolicyKind.SHELL: self.shell.policy,
            PolicyKind.MCP: self.mcp.policy,
        }


class RunSpec(ProfileSpec):
    image: ImageRef
    # Unset lets any runner claim the Run.
    runner: RunnerId | None = None


def digest_of(document: Mapping[str, Any]) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()
