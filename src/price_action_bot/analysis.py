from __future__ import annotations

from statistics import fmean

from .models import Bias, Candle, MarketContext, PatternSignal, Side, Zone


def sma(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"Need {period} values, received {len(values)}")
    return fmean(values[-period:])


def atr(candles: list[Candle], period: int = 14) -> float:
    if len(candles) < period + 1:
        raise ValueError(f"Need at least {period + 1} candles")
    true_ranges: list[float] = []
    for previous, current in zip(candles[-period - 1 : -1], candles[-period:]):
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return fmean(true_ranges)


def _pivots(candles: list[Candle], width: int = 2) -> tuple[list[float], list[float]]:
    highs: list[float] = []
    lows: list[float] = []
    for index in range(width, len(candles) - width):
        candle = candles[index]
        neighbours = candles[index - width : index] + candles[index + 1 : index + width + 1]
        if all(candle.high > other.high for other in neighbours):
            highs.append(candle.high)
        if all(candle.low < other.low for other in neighbours):
            lows.append(candle.low)
    return highs, lows


def market_context(candles_1h: list[Candle]) -> MarketContext:
    if len(candles_1h) < 30:
        raise ValueError("At least 30 closed 1h candles are required")
    volatility = atr(candles_1h)
    swing_highs, swing_lows = _pivots(candles_1h)
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return MarketContext("range", None, None, volatility)

    last_highs = swing_highs[-2:]
    last_lows = swing_lows[-2:]
    bias: Bias = "range"
    if last_highs[1] > last_highs[0] and last_lows[1] > last_lows[0]:
        bias = "bullish"
    elif last_highs[1] < last_highs[0] and last_lows[1] < last_lows[0]:
        bias = "bearish"

    width = max(volatility * 0.15, candles_1h[-1].close * 0.0005)
    support_center = last_lows[-1]
    resistance_center = last_highs[-1]
    support = Zone(support_center, support_center - width, support_center + width, "support")
    resistance = Zone(
        resistance_center,
        resistance_center - width,
        resistance_center + width,
        "resistance",
    )
    return MarketContext(bias, support, resistance, volatility)


def _volume_ratio(candles: list[Candle], index: int, period: int = 20) -> float:
    if index < period:
        return 0.0
    baseline = fmean(c.volume for c in candles[index - period : index])
    return candles[index].volume / baseline if baseline > 0 else 0.0


def _pin_bar(candle: Candle, side: Side) -> bool:
    if candle.range <= 0 or candle.body <= 0:
        return False
    body_floor = min(candle.open, candle.close)
    body_ceiling = max(candle.open, candle.close)
    lower_wick = body_floor - candle.low
    upper_wick = candle.high - body_ceiling
    if side == "Buy":
        return candle.bullish and lower_wick >= 2 * candle.body and upper_wick <= candle.body
    return candle.bearish and upper_wick >= 2 * candle.body and lower_wick <= candle.body


def _engulfing(previous: Candle, current: Candle, side: Side) -> bool:
    if side == "Buy":
        return (
            previous.bearish
            and current.bullish
            and current.open <= previous.close
            and current.close >= previous.open
        )
    return (
        previous.bullish
        and current.bearish
        and current.open >= previous.close
        and current.close <= previous.open
    )


def _star(first: Candle, middle: Candle, last: Candle, side: Side) -> bool:
    if first.body <= 0 or middle.body > first.body * 0.5:
        return False
    midpoint = (first.open + first.close) / 2
    if side == "Buy":
        return first.bearish and last.bullish and last.close > midpoint
    return first.bullish and last.bearish and last.close < midpoint


def _tweezer(previous: Candle, current: Candle, side: Side, tolerance: float) -> bool:
    if side == "Buy":
        return previous.bearish and current.bullish and abs(previous.low - current.low) <= tolerance
    return previous.bullish and current.bearish and abs(previous.high - current.high) <= tolerance


def _pattern_candidate(
    candles_5m: list[Candle], context: MarketContext
) -> PatternSignal | None:
    """Return the price-action candidate before applying the volume gate."""
    if len(candles_5m) < 23:
        return None
    current = candles_5m[-1]
    previous = candles_5m[-2]
    first = candles_5m[-3]
    index = len(candles_5m) - 1
    ratio = _volume_ratio(candles_5m, index)

    candidates: list[tuple[Side, Zone | None]] = []
    if context.bias in {"bullish", "range"}:
        candidates.append(("Buy", context.support))
    if context.bias in {"bearish", "range"}:
        candidates.append(("Sell", context.resistance))

    for side, zone in candidates:
        pattern_window = (first, previous, current)
        window_low = min(candle.low for candle in pattern_window)
        window_high = max(candle.high for candle in pattern_window)
        if zone is None or not (window_low <= zone.upper and window_high >= zone.lower):
            continue
        tolerance = max(context.atr * 0.02, current.close * 0.0001)
        name = ""
        relevant = (current,)
        if _pin_bar(current, side):
            name = "bullish_pin_bar" if side == "Buy" else "bearish_pin_bar"
        elif _engulfing(previous, current, side):
            name = "bullish_engulfing" if side == "Buy" else "bearish_engulfing"
            relevant = (previous, current)
        elif _star(first, previous, current, side):
            name = "morning_star" if side == "Buy" else "evening_star"
            relevant = (first, previous, current)
        elif _tweezer(previous, current, side, tolerance):
            name = "tweezer_bottom" if side == "Buy" else "tweezer_top"
            relevant = (previous, current)
        if not name:
            continue

        buffer = max(current.close * 0.00005, context.atr * 0.01)
        trigger = current.high + buffer if side == "Buy" else current.low - buffer
        relevant_low = min(candle.low for candle in relevant)
        relevant_high = max(candle.high for candle in relevant)
        stop = relevant_low - buffer if side == "Buy" else relevant_high + buffer
        return PatternSignal(
            name=name,
            side=side,
            timestamp_ms=current.timestamp_ms,
            trigger=trigger,
            stop=stop,
            pattern_high=relevant_high,
            pattern_low=relevant_low,
            volume_ratio=ratio,
        )
    return None


def detect_pattern(
    candles_5m: list[Candle],
    context: MarketContext,
    volume_multiplier: float = 1.2,
) -> PatternSignal | None:
    """Return a closed-candle pattern only when zone, shape and volume all confirm."""
    candidate = _pattern_candidate(candles_5m, context)
    if candidate is None or candidate.volume_ratio < volume_multiplier:
        return None
    return candidate


def detect_trade_reversal(
    candles_5m: list[Candle], side: Side, volume_multiplier: float = 1.2
) -> dict[str, object] | None:
    """Return a closed-candle reversal that invalidates an open trade direction."""
    if len(candles_5m) < 23:
        return None
    previous, current = candles_5m[-2], candles_5m[-1]
    bullish_engulfing = (
        current.bullish
        and previous.bearish
        and current.open <= previous.close
        and current.close >= previous.open
    )
    bearish_engulfing = (
        current.bearish
        and previous.bullish
        and current.open >= previous.close
        and current.close <= previous.open
    )
    opposite_pattern = bullish_engulfing if side == "Sell" else bearish_engulfing
    ratio = _volume_ratio(candles_5m, len(candles_5m) - 1)
    structure_break = (
        side == "Sell"
        and current.close > max(candle.high for candle in candles_5m[-4:-1])
    ) or (
        side == "Buy"
        and current.close < min(candle.low for candle in candles_5m[-4:-1])
    )
    if (opposite_pattern or structure_break) and ratio >= volume_multiplier:
        return {
            "pattern": "bullish_engulfing" if side == "Sell" and opposite_pattern else
            "bearish_engulfing" if side == "Buy" and opposite_pattern else "structure_break",
            "volume_ratio": ratio,
            "candle_time_ms": current.timestamp_ms,
            "reason": "Opposite engulfing candle with volume" if opposite_pattern else
            "Opposite 5m structure break with volume",
        }
    return None


def setup_checklist(
    candles_5m: list[Candle],
    context: MarketContext,
    volume_multiplier: float = 1.2,
    min_reward_risk: float = 1.5,
) -> dict[str, object]:
    """Explain the current setup decision without changing strategy behavior."""
    if len(candles_5m) < 23:
        return {
            "summary": "Waiting for enough closed 5m candles",
            "candle_time_ms": 0,
            "checks": [],
        }

    current = candles_5m[-1]
    window = candles_5m[-3:]
    window_low = min(candle.low for candle in window)
    window_high = max(candle.high for candle in window)
    candidate_zones: list[tuple[Side, Zone | None]] = []
    if context.bias in {"bullish", "range"}:
        candidate_zones.append(("Buy", context.support))
    if context.bias in {"bearish", "range"}:
        candidate_zones.append(("Sell", context.resistance))
    touched = [
        (side, zone)
        for side, zone in candidate_zones
        if zone is not None and window_low <= zone.upper and window_high >= zone.lower
    ]
    candidate = _pattern_candidate(candles_5m, context)
    ratio = _volume_ratio(candles_5m, len(candles_5m) - 1)

    if context.bias == "bullish":
        structure_detail = "Bullish — looking for long setups near support"
    elif context.bias == "bearish":
        structure_detail = "Bearish — looking for short setups near resistance"
    else:
        structure_detail = "Range — either edge can produce a setup"

    if touched:
        side, zone = touched[0]
        zone_detail = f"{zone.kind.title()} touched — {side} setup is eligible"
        zone_status = "pass"
    else:
        zone_names = " / ".join(
            f"{zone.kind} {zone.center:.8g}" for _, zone in candidate_zones if zone is not None
        )
        zone_detail = f"Price is outside the relevant zone ({zone_names or 'not available'})"
        zone_status = "wait"

    pattern_status = "pass" if candidate else "wait"
    pattern_detail = (
        candidate.name.replace("_", " ").title()
        if candidate
        else "No approved reversal candle in the active zone"
    )
    volume_status = "pass" if ratio >= volume_multiplier else "wait"
    volume_detail = f"{ratio:.2f}× vs required {volume_multiplier:.2f}×"

    rr_value = 0.0
    rr_status = "wait"
    rr_detail = "Calculated after a valid pattern defines entry and stop"
    if candidate:
        target = (
            context.resistance.center
            if candidate.side == "Buy" and context.resistance
            else context.support.center
            if candidate.side == "Sell" and context.support
            else 0.0
        )
        rr_value = reward_risk(candidate.trigger, candidate.stop, target, candidate.side)
        rr_status = "pass" if target > 0 and rr_value >= min_reward_risk else "fail"
        rr_detail = f"1:{rr_value:.2f} vs required 1:{min_reward_risk:.2f}"

    if not touched:
        summary = "Waiting for price to reach the 1h target zone"
    elif not candidate:
        summary = "Zone reached — waiting for an approved 5m reversal candle"
    elif ratio < volume_multiplier:
        summary = "Pattern found — volume confirmation is too weak"
    elif rr_status == "fail":
        summary = "Pattern confirmed — reward-to-risk is below the minimum"
    else:
        summary = "Setup qualifies — waiting for the trigger break"

    return {
        "summary": summary,
        "candle_time_ms": current.timestamp_ms,
        "direction": candidate.side if candidate else touched[0][0] if touched else "",
        "pattern": candidate.name if candidate else "",
        "volume_ratio": ratio,
        "reward_risk": rr_value,
        "checks": [
            {"key": "structure", "label": "1H structure", "status": "pass", "detail": structure_detail},
            {"key": "zone", "label": "Target zone", "status": zone_status, "detail": zone_detail},
            {"key": "pattern", "label": "5m reversal candle", "status": pattern_status, "detail": pattern_detail},
            {"key": "volume", "label": "Volume confirmation", "status": volume_status, "detail": volume_detail},
            {"key": "rr", "label": "Reward to risk", "status": rr_status, "detail": rr_detail},
            {"key": "trigger", "label": "Entry trigger", "status": "wait", "detail": "Available after the setup is armed"},
        ],
    }


def reward_risk(entry: float, stop: float, target: float, side: Side) -> float:
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    reward = target - entry if side == "Buy" else entry - target
    return max(reward, 0.0) / risk
