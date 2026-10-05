from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


Side = Literal["Buy", "Sell"]
Bias = Literal["bullish", "bearish", "range"]


class SignalState(str, Enum):
    SCAN = "SCAN"
    WAIT_FOR_ZONE = "WAIT_FOR_ZONE"
    WAIT_FOR_PATTERN = "WAIT_FOR_PATTERN"
    ARMED = "ARMED"
    ORDER_PENDING = "ORDER_PENDING"
    POSITION_OPEN = "POSITION_OPEN"
    PARTIAL_TP = "PARTIAL_TP"
    TRAILING = "TRAILING"
    COOLDOWN = "COOLDOWN"
    ERROR_LOCK = "ERROR_LOCK"


@dataclass(frozen=True)
class Candle:
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return max(self.high - self.low, 0.0)

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open


@dataclass(frozen=True)
class Zone:
    center: float
    lower: float
    upper: float
    kind: Literal["support", "resistance"]

    def contains(self, price: float) -> bool:
        return self.lower <= price <= self.upper


@dataclass(frozen=True)
class MarketContext:
    bias: Bias
    support: Zone | None
    resistance: Zone | None
    atr: float


@dataclass(frozen=True)
class PatternSignal:
    name: str
    side: Side
    timestamp_ms: int
    trigger: float
    stop: float
    pattern_high: float
    pattern_low: float
    volume_ratio: float


@dataclass
class ArmedSignal:
    pattern: PatternSignal
    armed_at_ms: int
    expires_at_ms: int
    target: float


@dataclass
class Trade:
    symbol: str
    side: Side
    qty: float
    entry: float
    stop: float
    target: float
    one_r_target: float
    state: SignalState = SignalState.POSITION_OPEN
    order_id: str = ""
    partial_taken: bool = False

