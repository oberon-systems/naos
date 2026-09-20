from functools import cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NAOS_WEB_")

    api_base_url: str = "http://127.0.0.1:8000"


@cache
def get_settings() -> Settings:
    return Settings()
