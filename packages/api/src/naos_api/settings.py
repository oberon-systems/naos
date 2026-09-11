from fastapi import Request
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NAOS_")

    database_url: str = "sqlite:///./naos.db"
    allowed_mount_roots: list[str] = []


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings
