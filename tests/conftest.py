from __future__ import annotations

import pytest

from flight_monitor import db
from flight_monitor.settings import Settings


@pytest.fixture
def dbpath(tmp_path) -> str:
    p = str(tmp_path / "t.db")
    db.init_db(p)
    return p


@pytest.fixture
def settings(dbpath) -> Settings:
    return Settings(
        telegram_bot_token="t", telegram_chat_id=1, serpapi_api_key="k", fm_db=dbpath,
        _env_file=None,  # type: ignore[call-arg]
    )
