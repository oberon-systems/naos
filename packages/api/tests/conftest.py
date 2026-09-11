from pathlib import Path

import pytest

from naos_api.settings import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(database_url=f"sqlite:///{tmp_path / 'naos.db'}")
