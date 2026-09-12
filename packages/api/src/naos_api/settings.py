from typing import Annotated

from fastapi import Request
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NAOS_")

    database_url: str = "sqlite:///./naos.db"
    allowed_mount_roots: list[str] = []
    runner_enrollment_token_sha256: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")] = None
    lease_ttl_seconds: Annotated[int, Field(ge=5, le=3600)] = 60
    runner_token_ttl_seconds: Annotated[int, Field(ge=60, le=604800)] = 86400
    lease_sweep_interval_seconds: Annotated[int, Field(ge=1, le=3600)] = 15


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings
