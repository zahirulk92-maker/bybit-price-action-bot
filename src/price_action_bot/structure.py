from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal

from .models import Candle


STRUCTURE_SCHEMA_VERSION = "thesisedge.structure.v1"
SwingKind = Literal["high", "low"]
SwingTier = Literal["major", "internal"]
ZoneState = Literal["fresh", "tested", "weakened", "broken", "flip-watch", "invalid"]


@dataclass(frozen=True)
class StructureParameters:
    """Research parameters kept explicit so replay never depends on hidden constants."""

    internal_swing_width: int = 2
    major_swing_width: int = 5
    atr_period: int = 14
    zone_atr_multiplier: float = 0.15
    zone_min_price_fraction: float = 0.0005
    weakened_after_touches: int = 2
    break_buffer_atr: float = 0.0
    invalidation_buffer_atr: float = 0.50
    failed_break_window: int = 3
    max_debug_zones: int = 12

    def validate(self) -> None:
        if self.internal_swing_width < 1:
            raise ValueError("internal_swing_width must be at least 1")
        if self.major_swing_width <= self.internal_swing_width:
            raise ValueError("major_swing_width must exceed internal_swing_width")
        if self.atr_period < 2:
            raise ValueError("atr_period must be at least 2")
        if self.zone_atr_multiplier <= 0 or self.zone_min_price_fraction <= 0:
            raise ValueError("zone widths must be positive")
        if self.weakened_after_touches < 2:
            raise ValueError("weakened_after_touches must be at least 2")
        if self.break_buffer_atr < 0 or self.invalidation_buffer_atr <= 0:
            raise ValueError("break/invalidation buffers are invalid")
        if self.failed_break_window < 1 or self.max_debug_zones < 1:
            raise ValueError("window and debug-zone limits must be positive")


@dataclass(frozen=True)
class ConfirmedSwing:
    swing_id: str
    tier: SwingTier
    kind: SwingKind
    price: float
    source_index: int
    source_time_ms: int
    confirmation_index: int
    confirmed_at_ms: int
    left_bars: int
    right_bars: int


def _stable_id(*parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _candle_hash(candles: list[Candle]) -> str:
    payload = [
        [item.timestamp_ms, item.open, item.high, item.low, item.close, item.volume]
        for item in candles
    ]
    encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_closed_candles(candles: list[Candle]) -> None:
    previous = -1
    for candle in candles:
        if candle.timestamp_ms <= previous:
            raise ValueError("Candles must be strictly increasing and deduplicated")
        if candle.high < max(candle.open, candle.close, candle.low):
            raise ValueError("Candle high is below its OHLC values")
        if candle.low > min(candle.open, candle.close, candle.high):
            raise ValueError("Candle low is above its OHLC values")
        previous = candle.timestamp_ms


def _true_range(candles: list[Candle], index: int) -> float:
    candle = candles[index]
    if index == 0:
        return candle.high - candle.low
    previous = candles[index - 1]
    return max(
        candle.high - candle.low,
        abs(candle.high - previous.close),
        abs(candle.low - previous.close),
    )


def _atr_at(candles: list[Candle], index: int, period: int) -> float:
    start = max(0, index - period + 1)
    values = [_true_range(candles, item) for item in range(start, index + 1)]
    return sum(values) / len(values) if values else 0.0


def confirmed_swings(
    candles: list[Candle], width: int, tier: SwingTier
) -> list[ConfirmedSwing]:
    """Confirm a pivot only after ``width`` closed candles exist to its right."""
    swings: list[ConfirmedSwing] = []
    for index in range(width, len(candles) - width):
        candle = candles[index]
        left = candles[index - width : index]
        right = candles[index + 1 : index + width + 1]
        confirmation_index = index + width
        if all(candle.high > item.high for item in left + right):
            swings.append(
                ConfirmedSwing(
                    swing_id=_stable_id(tier, "high", candle.timestamp_ms, width),
                    tier=tier,
                    kind="high",
                    price=candle.high,
                    source_index=index,
                    source_time_ms=candle.timestamp_ms,
                    confirmation_index=confirmation_index,
                    confirmed_at_ms=candles[confirmation_index].timestamp_ms,
                    left_bars=width,
                    right_bars=width,
                )
            )
        if all(candle.low < item.low for item in left + right):
            swings.append(
                ConfirmedSwing(
                    swing_id=_stable_id(tier, "low", candle.timestamp_ms, width),
                    tier=tier,
                    kind="low",
                    price=candle.low,
                    source_index=index,
                    source_time_ms=candle.timestamp_ms,
                    confirmation_index=confirmation_index,
                    confirmed_at_ms=candles[confirmation_index].timestamp_ms,
                    left_bars=width,
                    right_bars=width,
                )
            )
    return sorted(swings, key=lambda item: (item.confirmed_at_ms, item.source_time_ms, item.kind))


def _state_from(swings: list[ConfirmedSwing]) -> str:
    highs = [item for item in swings if item.kind == "high"]
    lows = [item for item in swings if item.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return "range"
    high_up = highs[-1].price > highs[-2].price
    low_up = lows[-1].price > lows[-2].price
    high_down = highs[-1].price < highs[-2].price
    low_down = lows[-1].price < lows[-2].price
    if high_up and low_up:
        return "HH_HL"
    if high_down and low_down:
        return "LH_LL"
    return "range"


def _labelled_swings(swings: list[ConfirmedSwing]) -> list[dict[str, object]]:
    previous: dict[tuple[str, str], float] = {}
    result: list[dict[str, object]] = []
    for swing in swings:
        key = (swing.tier, swing.kind)
        prior = previous.get(key)
        if prior is None:
            label = "H?" if swing.kind == "high" else "L?"
        elif swing.kind == "high":
            label = "HH" if swing.price > prior else "LH" if swing.price < prior else "EH"
        else:
            label = "HL" if swing.price > prior else "LL" if swing.price < prior else "EL"
        item = asdict(swing)
        item["label"] = label
        result.append(item)
        previous[key] = swing.price
    return result


def _event(
    kind: str,
    direction: str,
    candle: Candle,
    level: ConfirmedSwing,
    detail: str,
) -> dict[str, object]:
    return {
        "event_id": _stable_id(kind, direction, candle.timestamp_ms, level.swing_id),
        "kind": kind,
        "direction": direction,
        "level": level.price,
        "level_swing_id": level.swing_id,
        "level_source_time_ms": level.source_time_ms,
        "level_confirmed_at_ms": level.confirmed_at_ms,
        "source_time_ms": candle.timestamp_ms,
        "confirmed_at_ms": candle.timestamp_ms,
        "detail": detail,
    }


def _structure_events(
    candles: list[Candle], swings: list[ConfirmedSwing], params: StructureParameters
) -> list[dict[str, object]]:
    by_confirmation: dict[int, list[ConfirmedSwing]] = {}
    for swing in swings:
        by_confirmation.setdefault(swing.confirmation_index, []).append(swing)
    available: list[ConfirmedSwing] = []
    broken: set[str] = set()
    swept: set[tuple[str, int]] = set()
    pending_breaks: list[dict[str, object]] = []
    events: list[dict[str, object]] = []

    for index, candle in enumerate(candles):
        available.extend(by_confirmation.get(index, []))
        for pending in list(pending_breaks):
            if index > int(pending["expires_index"]):
                pending_breaks.remove(pending)
                continue
            level = pending["level"]
            returned = (
                pending["direction"] == "bullish" and candle.close < level.price
            ) or (
                pending["direction"] == "bearish" and candle.close > level.price
            )
            if index > int(pending["break_index"]) and returned:
                events.append(
                    _event(
                        "FAILED_BREAK",
                        str(pending["direction"]),
                        candle,
                        level,
                        "Close returned through the broken swing level",
                    )
                )
                pending_breaks.remove(pending)

        highs = [item for item in available if item.kind == "high"]
        lows = [item for item in available if item.kind == "low"]
        state = _state_from([item for item in available if item.tier == "major"])
        if state == "range":
            state = _state_from(available)
        for level, direction in (
            (highs[-1] if highs else None, "bullish"),
            (lows[-1] if lows else None, "bearish"),
        ):
            if level is None or index <= level.confirmation_index:
                continue
            atr_value = _atr_at(candles, index, params.atr_period)
            buffer = atr_value * params.break_buffer_atr
            wick_crossed = (
                direction == "bullish" and candle.high > level.price + buffer
            ) or (
                direction == "bearish" and candle.low < level.price - buffer
            )
            close_broke = (
                direction == "bullish" and candle.close > level.price + buffer
            ) or (
                direction == "bearish" and candle.close < level.price - buffer
            )
            sweep_key = (level.swing_id, candle.timestamp_ms)
            if wick_crossed and not close_broke and sweep_key not in swept:
                events.append(
                    _event(
                        "SWEEP",
                        direction,
                        candle,
                        level,
                        "Wick crossed the swing and the close returned inside",
                    )
                )
                swept.add(sweep_key)
            if close_broke and level.swing_id not in broken:
                opposite = (state == "HH_HL" and direction == "bearish") or (
                    state == "LH_LL" and direction == "bullish"
                )
                kind = "CHOCH" if opposite else "BOS"
                events.append(
                    _event(
                        kind,
                        direction,
                        candle,
                        level,
                        "Closed beyond a confirmed swing level",
                    )
                )
                broken.add(level.swing_id)
                pending_breaks.append(
                    {
                        "level": level,
                        "direction": direction,
                        "break_index": index,
                        "expires_index": index + params.failed_break_window,
                    }
                )
    return events


def _zone_from_swing(
    candles: list[Candle], swing: ConfirmedSwing, params: StructureParameters
) -> dict[str, object]:
    width = max(
        _atr_at(candles, swing.confirmation_index, params.atr_period)
        * params.zone_atr_multiplier,
        swing.price * params.zone_min_price_fraction,
    )
    lower, upper = swing.price - width, swing.price + width
    kind = "resistance" if swing.kind == "high" else "support"
    touches = 0
    touching = False
    state: ZoneState = "fresh"
    broken_at_ms = 0
    invalidated_at_ms = 0
    last_touched_at_ms = 0
    break_index = -1
    confirmation_index = swing.confirmation_index

    for index in range(confirmation_index + 1, len(candles)):
        candle = candles[index]
        overlaps = candle.low <= upper and candle.high >= lower
        close_broke = candle.close > upper if kind == "resistance" else candle.close < lower
        if break_index < 0:
            if overlaps and not touching:
                touches += 1
                last_touched_at_ms = candle.timestamp_ms
            touching = overlaps
            if close_broke:
                state = "broken"
                break_index = index
                broken_at_ms = candle.timestamp_ms
            elif touches >= params.weakened_after_touches:
                state = "weakened"
            elif touches:
                state = "tested"
            continue

        if overlaps:
            state = "flip-watch"
            last_touched_at_ms = candle.timestamp_ms
            continue
        invalidation = _atr_at(candles, index, params.atr_period) * params.invalidation_buffer_atr
        invalid = (
            kind == "resistance" and candle.close > upper + invalidation
        ) or (
            kind == "support" and candle.close < lower - invalidation
        )
        if invalid:
            state = "invalid"
            invalidated_at_ms = candle.timestamp_ms

    return {
        "zone_id": _stable_id("zone", swing.swing_id),
        "kind": kind,
        "state": state,
        "center": swing.price,
        "lower": lower,
        "upper": upper,
        "width": upper - lower,
        "source_swing_id": swing.swing_id,
        "source_time_ms": swing.source_time_ms,
        "confirmed_at_ms": swing.confirmed_at_ms,
        "age_bars": max(0, len(candles) - 1 - confirmation_index),
        "touch_count": touches,
        "last_touched_at_ms": last_touched_at_ms,
        "broken_at_ms": broken_at_ms,
        "invalidated_at_ms": invalidated_at_ms,
    }


def build_structure_map(
    candles: list[Candle], params: StructureParameters | None = None
) -> dict[str, object]:
    """Build a deterministic structure map from closed candles only."""
    parameters = params or StructureParameters()
    parameters.validate()
    _validate_closed_candles(candles)
    minimum = parameters.major_swing_width * 2 + 1
    if len(candles) < minimum:
        return {
            "schema_version": STRUCTURE_SCHEMA_VERSION,
            "snapshot_id": _stable_id("insufficient", _candle_hash(candles)),
            "computed_at_ms": candles[-1].timestamp_ms if candles else 0,
            "source": {
                "closed_candle_count": len(candles),
                "from_ms": candles[0].timestamp_ms if candles else 0,
                "to_ms": candles[-1].timestamp_ms if candles else 0,
                "sha256": _candle_hash(candles),
            },
            "parameters": asdict(parameters),
            "state": "unknown",
            "bias": "neutral",
            "protected_high": None,
            "protected_low": None,
            "major_swings": [],
            "internal_swings": [],
            "zones": [],
            "events": [],
            "data_quality": "insufficient",
        }

    internal = confirmed_swings(candles, parameters.internal_swing_width, "internal")
    major = confirmed_swings(candles, parameters.major_swing_width, "major")
    state = _state_from(major)
    bias = "bullish" if state == "HH_HL" else "bearish" if state == "LH_LL" else "neutral"
    highs = [item for item in major if item.kind == "high"]
    lows = [item for item in major if item.kind == "low"]
    protected_high = highs[-1].price if bias == "bearish" and highs else None
    protected_low = lows[-1].price if bias == "bullish" and lows else None
    zones = [_zone_from_swing(candles, item, parameters) for item in major]
    zones = zones[-parameters.max_debug_zones :]
    events = _structure_events(candles, major + internal, parameters)
    source = {
        "closed_candle_count": len(candles),
        "from_ms": candles[0].timestamp_ms,
        "to_ms": candles[-1].timestamp_ms,
        "sha256": _candle_hash(candles),
    }
    identity = {
        "schema_version": STRUCTURE_SCHEMA_VERSION,
        "source": source,
        "parameters": asdict(parameters),
    }
    snapshot_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": STRUCTURE_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "computed_at_ms": candles[-1].timestamp_ms,
        "source": source,
        "parameters": asdict(parameters),
        "state": state,
        "bias": bias,
        "protected_high": protected_high,
        "protected_low": protected_low,
        "major_swings": _labelled_swings(major),
        "internal_swings": _labelled_swings(internal),
        "zones": zones,
        "events": events,
        "data_quality": "complete",
    }


def confirmed_history_is_stable(
    earlier: dict[str, object], later: dict[str, object]
) -> bool:
    """Verify that previously confirmed swing identities and fields never move."""
    for key in ("major_swings", "internal_swings"):
        later_by_id = {item["swing_id"]: item for item in later[key]}
        for item in earlier[key]:
            if later_by_id.get(item["swing_id"]) != item:
                return False
    return True
