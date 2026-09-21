from functools import cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NAOS_WEB_")

    # What an operator's browser shows in the top bar, and what this process calls.
    api_base_url: str = "http://127.0.0.1:8080"
    api_url: str = "http://127.0.0.1:8080"
    operator_token_file: Path | None = None
    api_timeout_seconds: float = 10.0


@cache
def get_settings() -> Settings:
    return Settings()
