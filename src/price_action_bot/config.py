from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    api_key: str
    api_secret: str
    demo: bool = True
    enable_order_placement: bool = False
    live_trading_ack: str = ""
    leverage: int = 5
    risk_per_trade: float = 0.005
    max_open_positions: int = 3
    max_total_open_risk: float = 0.015
    min_reward_risk: float = 1.5
    volume_multiplier: float = 1.2
    universe_size: int = 10
    poll_seconds: int = 20
    database_path: str = "trading_bot.db"
    database_url: str = ""
    log_level: str = "INFO"
    dashboard_username: str = "admin"
    dashboard_password: str = ""
    dashboard_allow_insecure_local: bool = False
    run_engine_in_web: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    daily_report_hour: int = 23
    daily_report_minute: int = 55

    @classmethod
    def from_env(cls, env_path: str = ".env") -> "Settings":
        _load_env_file(Path(env_path))
        settings = cls(
            api_key=os.getenv("BYBIT_API_KEY", ""),
            api_secret=os.getenv("BYBIT_API_SECRET", ""),
            demo=_bool("BYBIT_DEMO", True),
            enable_order_placement=_bool("ENABLE_ORDER_PLACEMENT", False),
            live_trading_ack=os.getenv("LIVE_TRADING_ACK", ""),
            leverage=int(os.getenv("LEVERAGE", "5")),
            risk_per_trade=float(os.getenv("RISK_PER_TRADE", "0.005")),
            max_open_positions=int(os.getenv("MAX_OPEN_POSITIONS", "3")),
            max_total_open_risk=float(os.getenv("MAX_TOTAL_OPEN_RISK", "0.015")),
            min_reward_risk=float(os.getenv("MIN_REWARD_RISK", "1.5")),
            volume_multiplier=float(os.getenv("VOLUME_MULTIPLIER", "1.2")),
            universe_size=int(os.getenv("UNIVERSE_SIZE", "10")),
            poll_seconds=int(os.getenv("POLL_SECONDS", "20")),
            database_path=os.getenv("DATABASE_PATH", "trading_bot.db"),
            database_url=os.getenv("DATABASE_URL", ""),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            dashboard_username=os.getenv("DASHBOARD_USERNAME", "admin"),
            dashboard_password=os.getenv("DASHBOARD_PASSWORD", ""),
            dashboard_allow_insecure_local=_bool("DASHBOARD_ALLOW_INSECURE_LOCAL", False),
            run_engine_in_web=_bool("RUN_ENGINE_IN_WEB", False),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            daily_report_hour=int(os.getenv("DAILY_REPORT_HOUR", "23")),
            daily_report_minute=int(os.getenv("DAILY_REPORT_MINUTE", "55")),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.leverage < 1 or self.leverage > 10:
            raise ValueError("LEVERAGE must be between 1 and 10")
        if not 0 < self.risk_per_trade <= 0.02:
            raise ValueError("RISK_PER_TRADE must be > 0 and <= 0.02")
        if self.max_open_positions < 1:
            raise ValueError("MAX_OPEN_POSITIONS must be at least 1")
        if self.max_total_open_risk < self.risk_per_trade:
            raise ValueError("MAX_TOTAL_OPEN_RISK cannot be below RISK_PER_TRADE")
        if self.max_total_open_risk > 0.05:
            raise ValueError("MAX_TOTAL_OPEN_RISK cannot exceed 0.05")
        if self.universe_size < 2 or self.universe_size > 25:
            raise ValueError("UNIVERSE_SIZE must be between 2 and 25")
        if self.enable_order_placement and (not self.api_key or not self.api_secret):
            raise ValueError("API credentials are required when order placement is enabled")
        if not self.demo and self.live_trading_ack != "I_UNDERSTAND_LIVE_RISK":
            raise ValueError(
                "Live mode is locked. Set LIVE_TRADING_ACK=I_UNDERSTAND_LIVE_RISK explicitly."
            )
        if bool(self.telegram_bot_token) != bool(self.telegram_chat_id):
            raise ValueError("Set both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, or leave both blank")
        if not 0 <= self.daily_report_hour <= 23:
            raise ValueError("DAILY_REPORT_HOUR must be between 0 and 23")
        if not 0 <= self.daily_report_minute <= 59:
            raise ValueError("DAILY_REPORT_MINUTE must be between 0 and 59")
