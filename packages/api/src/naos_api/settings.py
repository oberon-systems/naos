from functools import cache
from typing import Annotated

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

TokenHash = Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NAOS_")

    database_url: str
    allowed_mount_roots: list[str] = []
    operator_token_sha256: TokenHash = None
    runner_enrollment_token_sha256: TokenHash = None
    lease_ttl_seconds: Annotated[int, Field(ge=5, le=3600)] = 60
    runner_token_ttl_seconds: Annotated[int, Field(ge=60, le=604800)] = 86400
    run_credential_ttl_seconds: Annotated[int, Field(ge=60, le=3600)] = 300
    lease_sweep_interval_seconds: Annotated[int, Field(ge=1, le=3600)] = 15


@cache
def get_settings() -> Settings:
    return Settings()
