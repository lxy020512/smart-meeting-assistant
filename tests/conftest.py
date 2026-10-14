from pathlib import Path

import pytest

from app.config import Settings
from app.storage import Store


@pytest.fixture
def config(tmp_path):
    return Settings(tmp_path, tmp_path / "test.sqlite3", "https://model.invalid/v1", "demo", "", .1, 1, .01, 30, embedded_worker=False)


@pytest.fixture
def store(config):
    return Store(config.database)
