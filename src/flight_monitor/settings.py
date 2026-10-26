"""環境設定；必填欄位缺少時 pydantic 直接報錯。"""

from __future__ import annotations

import os

from pydantic_settings import BaseSettings, SettingsConfigDict

# 6 小時 × 3600 秒 ÷ 20 秒 ≈ 1080，取整數保留餘裕。
FRESH_HOURS = 6
MAX_ACTIVE_COMBOS = 1000


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    telegram_bot_token: str
    telegram_chat_id: int
    serpapi_api_key: str

    fm_db: str = "flight_monitor.db"
    fm_origin: str = "TPE"
    fm_scan_delay_seconds: float = 20
    # 每天最多查證幾個候選特價（每次查證最多 3 次 SerpApi 呼叫）
    fm_serpapi_daily_verify: int = 2
    # 免費方案 250 次/月，保留 10 次給手動 probe
    fm_serpapi_monthly_limit: int = 240


def load_settings() -> Settings:
    return Settings(_env_file=os.environ.get("FM_SECRETS", "secrets.env"))  # type: ignore[call-arg]
