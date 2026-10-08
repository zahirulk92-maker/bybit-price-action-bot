from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any

from .models import Candle


PLAYBOOK_SCHEMA_VERSION = "thesisedge.phase4.v1"


@dataclass(frozen=True)
class PlaybookParameters:
    min_1h_candles: int = 60
    min_5m_candles: int = 30
    zone_proximity_atr: float = 0.25
    breakout_retest_max_age_hours: int = 8
    volatility_recent_bars: int = 5
    volatility_baseline_bars: int = 30

    def validate(self) -> None:
        if not 30 <= self.min_1h_candles <= 500:
            raise ValueError("Playbook minimum 1h candles must be between 30 and 500")
        if not 20 <= self.min_5m_candles <= 500:
            raise ValueError("Playbook minimum 5m candles must be between 20 and 500")
        if not 0 <= self.zone_proximity_atr <= 2:
            raise ValueError("Playbook zone proximity must be between 0 and 2 ATR")
        if not 1 <= self.breakout_retest_max_age_hours <= 48:
            raise ValueError("Breakout retest age must be between 1 and 48 hours")
        if not 2 <= self.volatility_recent_bars < self.volatility_baseline_bars:
            raise ValueError("Playbook volatility windows are invalid")


def _atr(candles: list[Candle], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    ranges = []
    for previous, candle in zip(candles, candles[1:]):
        ranges.append(max(candle.high - candle.low, abs(candle.high - previous.close), abs(candle.low - previous.close)))
    rows = ranges[-period:]
    return sum(rows) / len(rows) if rows else 0.0


def _volatility(candles: list[Candle], params: PlaybookParameters) -> tuple[str, float]:
    ranges = [max(candle.high - candle.low, 0.0) for candle in candles]
    if len(ranges) < params.volatility_baseline_bars:
        return "unknown", 0.0
    recent = sum(ranges[-params.volatility_recent_bars:]) / params.volatility_recent_bars
    baseline = sum(ranges[-params.volatility_baseline_bars:]) / params.volatility_baseline_bars
    ratio = recent / baseline if baseline > 0 else 0.0
    state = "compressed" if ratio < 0.70 else "normal" if ratio <= 1.50 else "expanded" if ratio <= 2.50 else "abnormal"
    return state, round(ratio, 6)


def _regime(structure: dict[str, Any]) -> tuple[str, str]:
    state = str(structure.get("state") or "unknown")
    if state == "HH_HL":
        return "trend", "bullish"
    if state == "LH_LL":
        return "trend", "bearish"
    if state == "range":
        return "range", "neutral"
    return "unknown", "neutral"


def _nearest_location(
    price: float, atr: float, structure: dict[str, Any], params: PlaybookParameters
) -> tuple[str, dict[str, Any] | None, float | None]:
    zones = [
        zone for zone in structure.get("zones", [])
        if zone.get("state") not in {"broken", "invalid"} and float(zone.get("center") or 0) > 0
    ]
    if not zones:
        return "unknown", None, None
    nearest = min(zones, key=lambda zone: abs(price - float(zone["center"])))
    distance = abs(price - float(nearest["center"]))
    tolerance = max(float(nearest.get("width") or 0) / 2, atr * params.zone_proximity_atr)
    if distance > tolerance:
        return "mid_range", nearest, distance
    kind, state = str(nearest.get("kind")), str(nearest.get("state"))
    if state == "flip-watch":
        return "breakout_retest", nearest, distance
    return ("support_edge" if kind == "support" else "resistance_edge"), nearest, distance


def _latest_break(structure: dict[str, Any]) -> dict[str, Any] | None:
    events = [event for event in structure.get("events", []) if event.get("kind") in {"BOS", "CHOCH"}]
    return max(events, key=lambda event: int(event.get("confirmed_at_ms") or 0), default=None)


def _result(
    name: str, matched: bool, direction: str, required_context: list[str],
    invalidation: dict[str, Any] | None, reason_codes: list[str]
) -> dict[str, Any]:
    return {
        "playbook": name,
        "matched": matched,
        "direction": direction,
        "required_context": required_context,
        "invalidation": invalidation,
        "reason_codes": reason_codes,
    }


def build_playbook_context(
    symbol: str,
    candles_1h: list[Candle],
    candles_5m: list[Candle],
    structure: dict[str, Any],
    parameters: PlaybookParameters | None = None,
) -> dict[str, Any]:
    """Classify context and match one symmetric playbook without execution authority."""
    params = parameters or PlaybookParameters()
    params.validate()
    symbol = symbol.upper()
    computed_at_ms = candles_5m[-1].timestamp_ms if candles_5m else 0
    enough = (
        len(candles_1h) >= params.min_1h_candles
        and len(candles_5m) >= params.min_5m_candles
        and structure.get("data_quality") == "complete"
    )
    data_quality = "healthy" if enough else "insufficient"
    price = float(candles_5m[-1].close) if candles_5m else 0.0
    atr = _atr(candles_1h)
    volatility, volatility_ratio = _volatility(candles_1h, params)
    regime, bias = _regime(structure)
    location, active_zone, distance = _nearest_location(price, atr, structure, params) if price else ("unknown", None, None)
    latest_break = _latest_break(structure)

    zone_kind = str((active_zone or {}).get("kind") or "")
    zone_state = str((active_zone or {}).get("state") or "")
    zone_invalidation = None
    if active_zone:
        side = "below" if zone_kind == "support" else "above"
        boundary = float(active_zone["lower"] if zone_kind == "support" else active_zone["upper"])
        zone_invalidation = {"type": "close_beyond_zone", "side": side, "price": boundary}

    trend_direction = "Buy" if bias == "bullish" else "Sell" if bias == "bearish" else "Unknown"
    trend_location = "support_edge" if bias == "bullish" else "resistance_edge"
    trend_match = enough and regime == "trend" and location == trend_location and zone_state in {"fresh", "tested", "weakened"}
    trend = _result(
        "TREND_PULLBACK", trend_match, trend_direction,
        [f"regime=trend", f"bias={bias}", f"location={trend_location}", "zone=live"],
        zone_invalidation if trend_match else None,
        ["TREND_CONTEXT_MATCHED"] if trend_match else ["TREND_CONTEXT_NOT_MATCHED"],
    )

    range_direction = "Buy" if location == "support_edge" else "Sell" if location == "resistance_edge" else "Unknown"
    range_match = enough and regime == "range" and location in {"support_edge", "resistance_edge"} and zone_state in {"fresh", "tested", "weakened"}
    range_result = _result(
        "RANGE_REVERSAL", range_match, range_direction,
        ["regime=range", "location=range_edge", "zone=live"],
        zone_invalidation if range_match else None,
        ["RANGE_EDGE_MATCHED"] if range_match else ["RANGE_EDGE_NOT_MATCHED" if location != "mid_range" else "MID_RANGE_NO_EDGE"],
    )

    break_direction = str((latest_break or {}).get("direction") or "unknown")
    break_age = computed_at_ms - int((latest_break or {}).get("confirmed_at_ms") or 0)
    recent_break = latest_break is not None and 0 <= break_age <= params.breakout_retest_max_age_hours * 3_600_000
    correct_flip = (break_direction == "bullish" and zone_kind == "resistance") or (break_direction == "bearish" and zone_kind == "support")
    breakout_match = enough and location == "breakout_retest" and recent_break and correct_flip
    breakout_direction = "Buy" if break_direction == "bullish" else "Sell" if break_direction == "bearish" else "Unknown"
    breakout = _result(
        "BREAKOUT_RETEST", breakout_match, breakout_direction,
        ["recent_confirmed_break", "location=broken_level_retest", "flip_zone=holding"],
        zone_invalidation if breakout_match else None,
        ["BREAKOUT_RETEST_MATCHED"] if breakout_match else ["BREAKOUT_RETEST_NOT_MATCHED"],
    )

    evaluations = [trend, range_result, breakout]
    matched = [item for item in evaluations if item["matched"]]
    if not enough or regime == "unknown" or volatility == "unknown":
        status, selected, reason = "UNKNOWN", None, "INSUFFICIENT_OR_UNKNOWN_CONTEXT"
    elif matched:
        # Retest is more specific than generic trend/range location.
        selected = next((item for item in matched if item["playbook"] == "BREAKOUT_RETEST"), matched[0])
        status, reason = "MATCHED", str(selected["reason_codes"][0])
    else:
        status, selected = "NO_MATCHING_PLAYBOOK", None
        reason = "MID_RANGE_NO_EDGE" if location == "mid_range" else "CONTEXT_HAS_NO_APPROVED_PLAYBOOK"

    context = {
        "regime": regime,
        "bias": bias,
        "location": location,
        "volatility": volatility,
        "volatility_ratio": volatility_ratio,
        "data_quality": data_quality,
        "price": price,
        "atr_1h": round(atr, 8),
        "distance_to_zone": round(distance, 8) if distance is not None else None,
        "active_zone": active_zone,
        "latest_break": latest_break,
    }
    identity = {
        "schema_version": PLAYBOOK_SCHEMA_VERSION,
        "symbol": symbol,
        "computed_at_ms": computed_at_ms,
        "parameters": asdict(params),
        "context": context,
        "status": status,
        "selected_playbook": selected,
        "evaluations": evaluations,
    }
    snapshot_id = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return {
        **identity,
        "snapshot_id": snapshot_id,
        "reason_code": reason,
        "v2_execution_authority": False,
        "risk_authority": False,
    }
