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
    risk_per_trade: float = 0.01
    max_open_positions: int = 3
    max_total_open_risk: float = 0.03
    daily_max_net_loss: float = 0.05
    min_reward_risk: float = 1.5
    volume_multiplier: float = 1.2
    universe_size: int = 10
    poll_seconds: int = 20
    order_retry_attempts: int = 3
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
    v2_instrumentation_enabled: bool = True
    v2_structure_mode: str = "shadow"
    v2_universe_mode: str = "off"
    v2_portfolio_mode: str = "off"
    v2_playbook_mode: str = "off"
    v2_thesis_mode: str = "off"
    v2_management_mode: str = "off"
    v2_taker_fee_rate: float = 0.00055
    v2_slippage_rate: float = 0.00020
    v2_max_friction_risk_fraction: float = 0.25
    structure_internal_swing_width: int = 2
    structure_major_swing_width: int = 5
    structure_atr_period: int = 14
    structure_zone_atr_multiplier: float = 0.15
    structure_zone_min_price_fraction: float = 0.0005
    structure_weakened_after_touches: int = 2
    structure_break_buffer_atr: float = 0.0
    structure_invalidation_buffer_atr: float = 0.50
    structure_failed_break_window: int = 3
    structure_max_debug_zones: int = 12

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
            risk_per_trade=float(os.getenv("RISK_PER_TRADE", "0.01")),
            max_open_positions=int(os.getenv("MAX_OPEN_POSITIONS", "3")),
            max_total_open_risk=float(os.getenv("MAX_TOTAL_OPEN_RISK", "0.03")),
            daily_max_net_loss=float(os.getenv("DAILY_MAX_NET_LOSS", "0.05")),
            min_reward_risk=float(os.getenv("MIN_REWARD_RISK", "1.5")),
            volume_multiplier=float(os.getenv("VOLUME_MULTIPLIER", "1.2")),
            universe_size=int(os.getenv("UNIVERSE_SIZE", "10")),
            poll_seconds=int(os.getenv("POLL_SECONDS", "20")),
            order_retry_attempts=int(os.getenv("ORDER_RETRY_ATTEMPTS", "3")),
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
            v2_instrumentation_enabled=_bool("V2_INSTRUMENTATION_ENABLED", True),
            v2_structure_mode=os.getenv("V2_STRUCTURE_MODE", "shadow").strip().lower(),
            v2_universe_mode=os.getenv("V2_UNIVERSE_MODE", "off").strip().lower(),
            v2_portfolio_mode=os.getenv("V2_PORTFOLIO_MODE", "off").strip().lower(),
            v2_playbook_mode=os.getenv("V2_PLAYBOOK_MODE", "off").strip().lower(),
            v2_thesis_mode=os.getenv("V2_THESIS_MODE", "off").strip().lower(),
            v2_management_mode=os.getenv("V2_MANAGEMENT_MODE", "off").strip().lower(),
            v2_taker_fee_rate=float(os.getenv("V2_TAKER_FEE_RATE", "0.00055")),
            v2_slippage_rate=float(os.getenv("V2_SLIPPAGE_RATE", "0.00020")),
            v2_max_friction_risk_fraction=float(
                os.getenv("V2_MAX_FRICTION_RISK_FRACTION", "0.25")
            ),
            structure_internal_swing_width=int(os.getenv("STRUCTURE_INTERNAL_SWING_WIDTH", "2")),
            structure_major_swing_width=int(os.getenv("STRUCTURE_MAJOR_SWING_WIDTH", "5")),
            structure_atr_period=int(os.getenv("STRUCTURE_ATR_PERIOD", "14")),
            structure_zone_atr_multiplier=float(os.getenv("STRUCTURE_ZONE_ATR_MULTIPLIER", "0.15")),
            structure_zone_min_price_fraction=float(os.getenv("STRUCTURE_ZONE_MIN_PRICE_FRACTION", "0.0005")),
            structure_weakened_after_touches=int(os.getenv("STRUCTURE_WEAKENED_AFTER_TOUCHES", "2")),
            structure_break_buffer_atr=float(os.getenv("STRUCTURE_BREAK_BUFFER_ATR", "0.0")),
            structure_invalidation_buffer_atr=float(os.getenv("STRUCTURE_INVALIDATION_BUFFER_ATR", "0.50")),
            structure_failed_break_window=int(os.getenv("STRUCTURE_FAILED_BREAK_WINDOW", "3")),
            structure_max_debug_zones=int(os.getenv("STRUCTURE_MAX_DEBUG_ZONES", "12")),
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
        if not 0 < self.daily_max_net_loss <= 0.10:
            raise ValueError("DAILY_MAX_NET_LOSS must be > 0 and <= 0.10")
        if self.universe_size < 2 or self.universe_size > 25:
            raise ValueError("UNIVERSE_SIZE must be between 2 and 25")
        if self.order_retry_attempts < 1 or self.order_retry_attempts > 5:
            raise ValueError("ORDER_RETRY_ATTEMPTS must be between 1 and 5")
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
        feature_modes = {
            "V2_STRUCTURE_MODE": self.v2_structure_mode,
            "V2_UNIVERSE_MODE": self.v2_universe_mode,
            "V2_PORTFOLIO_MODE": self.v2_portfolio_mode,
            "V2_PLAYBOOK_MODE": self.v2_playbook_mode,
            "V2_THESIS_MODE": self.v2_thesis_mode,
            "V2_MANAGEMENT_MODE": self.v2_management_mode,
        }
        for name, mode in feature_modes.items():
            if mode not in {"off", "shadow"}:
                raise ValueError(f"{name} must be off or shadow until explicitly promoted")
        if min(self.v2_taker_fee_rate, self.v2_slippage_rate) < 0:
            raise ValueError("V2 fee and slippage rates cannot be negative")
        if not 0 <= self.v2_max_friction_risk_fraction < 1:
            raise ValueError("V2_MAX_FRICTION_RISK_FRACTION must be between 0 and 1")
        self.structure_parameters().validate()

    def recovery_policy(self):
        from .management import RecoveryPolicy

        return RecoveryPolicy(
            taker_fee_rate=self.v2_taker_fee_rate,
            slippage_rate=self.v2_slippage_rate,
            max_friction_risk_fraction=self.v2_max_friction_risk_fraction,
        )

    def v2_feature_modes(self) -> dict[str, str]:
        return {
            "structure": self.v2_structure_mode,
            "universe": self.v2_universe_mode,
            "portfolio": self.v2_portfolio_mode,
            "playbooks": self.v2_playbook_mode,
            "thesis": self.v2_thesis_mode,
            "management": self.v2_management_mode,
        }

    def structure_parameters(self):
        from .structure import StructureParameters

        return StructureParameters(
            internal_swing_width=self.structure_internal_swing_width,
            major_swing_width=self.structure_major_swing_width,
            atr_period=self.structure_atr_period,
            zone_atr_multiplier=self.structure_zone_atr_multiplier,
            zone_min_price_fraction=self.structure_zone_min_price_fraction,
            weakened_after_touches=self.structure_weakened_after_touches,
            break_buffer_atr=self.structure_break_buffer_atr,
            invalidation_buffer_atr=self.structure_invalidation_buffer_atr,
            failed_break_window=self.structure_failed_break_window,
            max_debug_zones=self.structure_max_debug_zones,
        )
