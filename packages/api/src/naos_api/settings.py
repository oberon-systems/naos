import os
from functools import cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_image_store_path() -> str:
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return str(Path(data_home) / "naos" / "images")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NAOS_")

    database_url: str
    allowed_mount_roots: list[str] = []
    runner_enrollment_token_sha256: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")] = None
    lease_ttl_seconds: Annotated[int, Field(ge=5, le=3600)] = 60
    runner_token_ttl_seconds: Annotated[int, Field(ge=60, le=604800)] = 86400
    lease_sweep_interval_seconds: Annotated[int, Field(ge=1, le=3600)] = 15
    image_store: Literal["fs"] = "fs"
    image_store_path: str = Field(default_factory=_default_image_store_path)
    image_source_url: str | None = None
    image_source_allowed_hosts: list[str] = []
    image_max_bytes: Annotated[int, Field(ge=1)] = 8 * 1024**3
    image_download_timeout_seconds: Annotated[int, Field(ge=1, le=3600)] = 30


@cache
def get_settings() -> Settings:
    return Settings()
