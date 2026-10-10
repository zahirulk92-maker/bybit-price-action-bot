from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Any

from .analysis import market_context
from .models import Candle, MarketContext
from .v2_foundation import fingerprint


EXCLUDED_BASE_COINS = {
    "USDC",
    "USDE",
    "FDUSD",
    "TUSD",
    "DAI",
    "XAUT",
    "XAU",
    "SOXL",
    "CL",
}

SCANNER_SCHEMA_VERSION = "thesisedge.phase2.v1"
DEMO_FUNNEL_SCHEMA_VERSION = "thesisedge.demo-funnel.v1"


@dataclass(frozen=True)
class ScannerFunnelParameters:
    min_listing_age_days: int = 30
    min_turnover_24h: float = 1_000_000.0
    min_open_interest: float = 250_000.0
    max_spread_fraction: float = 0.0015
    anomaly_move_fraction: float = 0.25
    candidate_max: int = 30
    deep_analysis_max: int = 15
    action_queue_max: int = 5
    newcomer_advantage_fraction: float = 0.05
    exit_grace_scans: int = 2
    near_zone_fraction: float = 0.01
    high_volatility_fraction: float = 0.12
    abnormal_volatility_fraction: float = 0.35
    churn_limit_fraction: float = 0.25
    latency_limit_ms: int = 2_000

    def validate(self) -> None:
        if self.min_listing_age_days < 1:
            raise ValueError("Scanner listing age must be at least one day")
        if min(self.min_turnover_24h, self.min_open_interest) < 0:
            raise ValueError("Scanner liquidity floors cannot be negative")
        if not 0 < self.max_spread_fraction < 0.05:
            raise ValueError("Scanner max spread must be between 0 and 0.05")
        if not 0 < self.anomaly_move_fraction <= 1:
            raise ValueError("Scanner anomaly threshold must be between 0 and 1")
        if not 1 <= self.action_queue_max <= self.deep_analysis_max <= self.candidate_max:
            raise ValueError("Scanner capacities must satisfy action <= deep <= candidate")
        if not 0 <= self.newcomer_advantage_fraction <= 0.50:
            raise ValueError("Scanner newcomer advantage must be between 0 and 0.50")
        if not 0 <= self.exit_grace_scans <= 10:
            raise ValueError("Scanner exit grace must be between 0 and 10 scans")
        if not 0 < self.near_zone_fraction <= 0.10:
            raise ValueError("Scanner near-zone fraction must be between 0 and 0.10")
        if not 0 < self.high_volatility_fraction < self.abnormal_volatility_fraction <= 1:
            raise ValueError("Scanner volatility thresholds must satisfy 0 < high < abnormal <= 1")
        if not 0 <= self.churn_limit_fraction <= 1:
            raise ValueError("Scanner churn limit must be between 0 and 1")
        if self.latency_limit_ms < 1:
            raise ValueError("Scanner latency limit must be positive")


def _number(value: object, default: float = 0.0) -> float:
    try:
        number = float(value or 0)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _scanner_row(
    ticker: dict[str, Any],
    anomaly_threshold: float,
    high_volatility_threshold: float,
    abnormal_volatility_threshold: float,
) -> dict[str, object]:
    bid = _number(ticker.get("bid1Price"))
    ask = _number(ticker.get("ask1Price"))
    last = _number(ticker.get("lastPrice"))
    turnover = _number(ticker.get("turnover24h"))
    open_interest = _number(ticker.get("openInterestValue"))
    move = abs(_number(ticker.get("price24hPcnt")))
    high = _number(ticker.get("highPrice24h"))
    low = _number(ticker.get("lowPrice24h"))
    midpoint = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
    spread = max(0.0, (ask - bid) / midpoint) if midpoint else 1.0
    range_fraction = max(0.0, (high - low) / last) if last > 0 and high >= low else 0.0
    quality_score = (
        6.0 * math.log10(max(turnover, 1.0))
        + 4.0 * math.log10(max(open_interest, 1.0))
        - 200.0 * spread
        - 5.0 * move
    )
    volatility_state = (
        "abnormal" if range_fraction >= abnormal_volatility_threshold or move >= anomaly_threshold else
        "high" if range_fraction >= high_volatility_threshold else
        "normal"
    )
    return {
        "symbol": str(ticker.get("symbol") or ""),
        "last_price": last,
        "turnover_24h": turnover,
        "open_interest": open_interest,
        "spread_fraction": spread,
        "move_24h_fraction": move,
        "range_24h_fraction": range_fraction,
        "volatility_state": volatility_state,
        "anomaly": move >= anomaly_threshold or range_fraction >= abnormal_volatility_threshold,
        "quality_score": round(quality_score, 6),
    }


def build_scanner_funnel(
    tickers: list[dict[str, Any]],
    instruments: list[dict[str, Any]],
    *,
    parameters: ScannerFunnelParameters | None = None,
    previous_snapshot: dict[str, Any] | None = None,
    protected_symbols: set[str] | None = None,
    market_locations: dict[str, dict[str, Any]] | None = None,
    now_ms: int | None = None,
    elapsed_ms: int = 0,
    api_calls: int = 2,
) -> dict[str, object]:
    """Build an auditable Phase-2 scanner funnel without changing V1 execution."""
    params = parameters or ScannerFunnelParameters()
    params.validate()
    now_ms = now_ms or int(time.time() * 1000)
    protected = {str(symbol).upper() for symbol in (protected_symbols or set()) if symbol}
    locations = market_locations or {}
    ticker_by_symbol = {
        str(item.get("symbol") or ""): item for item in tickers if item.get("symbol")
    }
    exclusions: dict[str, int] = {}

    def exclude(reason: str) -> None:
        exclusions[reason] = exclusions.get(reason, 0) + 1

    min_launch_ms = now_ms - params.min_listing_age_days * 24 * 60 * 60 * 1000
    eligible_rows: list[dict[str, object]] = []
    exchange_symbols = 0
    for instrument in instruments:
        symbol = str(instrument.get("symbol") or "")
        if instrument.get("quoteCoin") != "USDT" or instrument.get("contractType") != "LinearPerpetual":
            continue
        exchange_symbols += 1
        base = str(instrument.get("baseCoin") or "")
        if instrument.get("status") != "Trading":
            exclude("not_trading")
            continue
        if not symbol.endswith("USDT") or base in EXCLUDED_BASE_COINS:
            exclude("non_standard_underlying")
            continue
        if int(_number(instrument.get("launchTime"))) > min_launch_ms:
            exclude("listing_too_new")
            continue
        ticker = ticker_by_symbol.get(symbol)
        if not ticker:
            exclude("ticker_missing")
            continue
        row = _scanner_row(
            ticker,
            params.anomaly_move_fraction,
            params.high_volatility_fraction,
            params.abnormal_volatility_fraction,
        )
        if float(row["last_price"]) <= 0:
            exclude("price_missing")
        elif float(row["turnover_24h"]) < params.min_turnover_24h:
            exclude("turnover_below_floor")
        elif float(row["open_interest"]) < params.min_open_interest:
            exclude("open_interest_below_floor")
        elif float(row["spread_fraction"]) > params.max_spread_fraction:
            exclude("spread_too_wide")
        else:
            eligible_rows.append(row)

    eligible_rows.sort(key=lambda item: (-float(item["quality_score"]), str(item["symbol"])))
    eligible_by_symbol = {str(item["symbol"]): item for item in eligible_rows}
    previous_rows = {
        str(item.get("symbol") or ""): item
        for item in (previous_snapshot or {}).get("candidate_pool", [])
        if item.get("symbol")
    }
    previous_symbols = [
        str(item.get("symbol") or "")
        for item in (previous_snapshot or {}).get("candidate_pool", [])
        if item.get("symbol")
    ]
    selected = []
    for symbol in previous_symbols:
        if symbol in eligible_by_symbol:
            selected.append({
                **eligible_by_symbol[symbol],
                "eligible": True,
                "eligibility_state": "eligible",
                "missed_scans": 0,
            })
            continue
        previous = previous_rows[symbol]
        missed_scans = int(previous.get("missed_scans") or 0) + 1
        if missed_scans <= params.exit_grace_scans:
            selected.append({
                **previous,
                "eligible": False,
                "eligibility_state": "exit_grace",
                "missed_scans": missed_scans,
            })
    selected = selected[: params.candidate_max]
    selected_symbols = {str(item["symbol"]) for item in selected}
    newcomers = [
        {
            **item,
            "eligible": True,
            "eligibility_state": "eligible",
            "missed_scans": 0,
        }
        for item in eligible_rows
        if str(item["symbol"]) not in selected_symbols
    ]
    for newcomer in newcomers:
        if len(selected) < params.candidate_max:
            selected.append(newcomer)
            selected_symbols.add(str(newcomer["symbol"]))
            continue
        weakest = min(selected, key=lambda item: float(item["quality_score"]))
        required = float(weakest["quality_score"]) * (1 + params.newcomer_advantage_fraction)
        if float(newcomer["quality_score"]) <= required:
            continue
        selected.remove(weakest)
        selected_symbols.remove(str(weakest["symbol"]))
        selected.append(newcomer)
        selected_symbols.add(str(newcomer["symbol"]))
    selected.sort(key=lambda item: (-float(item["quality_score"]), str(item["symbol"])))

    previous_set = set(previous_symbols)
    candidate_set = {str(item["symbol"]) for item in selected}
    added = sorted(candidate_set - previous_set)
    removed = sorted(previous_set - candidate_set)
    baseline = not previous_symbols
    churn_rate = 0.0 if baseline else (len(added) + len(removed)) / max(1, len(previous_set))
    candidates: list[dict[str, object]] = []
    for rank, item in enumerate(selected, start=1):
        symbol = str(item["symbol"])
        location = locations.get(symbol) or {}
        price = float(item["last_price"])
        levels = [
            _number(location.get("support")),
            _number(location.get("resistance")),
        ]
        levels = [value for value in levels if value > 0 and price > 0]
        distance = min((abs(price - level) / price for level in levels), default=None)
        near_zone = distance is not None and distance <= params.near_zone_fraction
        candidates.append({
            **item,
            "rank": rank,
            "incumbent": symbol in previous_set,
            "protected": symbol in protected,
            "location_available": bool(levels),
            "distance_to_zone_fraction": round(distance, 8) if distance is not None else None,
            "near_zone": near_zone,
        })

    deep_candidates = [
        item for item in candidates if item["near_zone"] or item["protected"]
    ]
    deep_candidates.sort(key=lambda item: (
        not bool(item["protected"]),
        not bool(item["near_zone"]),
        float(item["distance_to_zone_fraction"]) if item["distance_to_zone_fraction"] is not None else 1.0,
        -float(item["quality_score"]),
    ))
    deep_pool = deep_candidates[: params.deep_analysis_max]
    action_queue = [
        item for item in deep_pool if item["eligible"] and not item["anomaly"]
    ][: params.action_queue_max]
    tracking_symbols = list(dict.fromkeys([str(item["symbol"]) for item in candidates] + sorted(protected)))
    schedule = []
    action_symbols = {str(item["symbol"]) for item in action_queue}
    deep_symbols = {str(item["symbol"]) for item in deep_pool}
    for item in candidates:
        symbol = str(item["symbol"])
        tier = "action" if symbol in action_symbols else "deep" if symbol in deep_symbols else "discovery"
        schedule.append({
            "symbol": symbol,
            "tier": tier,
            "cadence": "every_scan" if tier == "action" else "every_3_scans" if tier == "deep" else "location_refresh",
            "reason": "protected" if item["protected"] else "near_zone" if item["near_zone"] else "quality_rank",
        })
    for symbol in sorted(protected - candidate_set):
        schedule.insert(0, {"symbol": symbol, "tier": "protected", "cadence": "every_scan", "reason": "armed_or_open"})

    operational = {
        "churn_within_limit": baseline or churn_rate <= params.churn_limit_fraction,
        "latency_within_limit": elapsed_ms <= params.latency_limit_ms,
    }
    payload: dict[str, object] = {
        "schema_version": SCANNER_SCHEMA_VERSION,
        "mode": "shadow",
        "computed_at_ms": now_ms,
        "parameters": asdict(params),
        "metrics": {
            "exchange_symbols": exchange_symbols,
            "ticker_rows": len(tickers),
            "eligible_symbols": len(eligible_rows),
            "candidate_count": len(candidates),
            "deep_analysis_count": len(deep_pool),
            "action_queue_count": len(action_queue),
            "protected_tracking_count": len(protected),
            "anomaly_count": sum(bool(item["anomaly"]) for item in candidates),
            "exclusions": dict(sorted(exclusions.items())),
            "added": added,
            "removed": removed,
            "churn_rate": round(churn_rate, 6),
            "baseline": baseline,
            "elapsed_ms": int(elapsed_ms),
            "api_calls": int(api_calls),
            **operational,
        },
        "candidate_pool": candidates,
        "deep_analysis_pool": deep_pool,
        "action_queue": action_queue,
        "tracking_symbols": tracking_symbols,
        "scan_schedule": schedule,
        "v2_execution_authority": False,
    }
    payload["snapshot_id"] = fingerprint(payload)
    return payload


def _directional_score(candles: list[Candle], context: MarketContext) -> float | None:
    """Score a closed-candle trend without comparing raw price or volume across symbols."""
    if len(candles) < 30 or context.bias == "range":
        return None
    window = candles[-12:]
    closes = [float(candle.close) for candle in window]
    if min(closes) <= 0:
        return None
    direction = 1.0 if context.bias == "bullish" else -1.0
    signed_return = direction * (closes[-1] / closes[0] - 1.0)
    if signed_return <= 0:
        return None
    travelled = sum(abs(current - previous) for previous, current in zip(closes, closes[1:]))
    efficiency = abs(closes[-1] - closes[0]) / travelled if travelled > 0 else 0.0
    baseline_volume = fmean(float(candle.volume) for candle in candles[-21:-1])
    volume_ratio = float(candles[-1].volume) / baseline_volume if baseline_volume > 0 else 0.0
    return signed_return * 100.0 + efficiency * 10.0 + min(volume_ratio, 3.0)


def timeframe_confirmation(candles: list[Candle], side: str) -> tuple[bool, float]:
    """Confirm 15m direction with closed-candle fast/slow momentum."""
    if len(candles) < 21 or side not in {"Buy", "Sell"}:
        return False, 0.0
    closes = [float(candle.close) for candle in candles]
    if min(closes[-20:]) <= 0:
        return False, 0.0
    fast = fmean(closes[-5:])
    slow = fmean(closes[-20:])
    direction = 1.0 if side == "Buy" else -1.0
    aligned = direction * (fast - slow) > 0 and direction * (closes[-1] - closes[-2]) > 0
    strength = direction * (fast / slow - 1.0) * 100.0
    return aligned, round(strength, 6)


def build_demo_timeframe_funnel(
    tickers: list[dict[str, Any]],
    instruments: list[dict[str, Any]],
    *,
    candles_4h: dict[str, list[Candle]],
    candles_1h: dict[str, list[Candle]],
    candles_15m: dict[str, list[Candle]],
    parameters: ScannerFunnelParameters | None = None,
    previous_snapshot: dict[str, Any] | None = None,
    protected_symbols: set[str] | None = None,
    now_ms: int | None = None,
    elapsed_ms: int = 0,
    api_calls: int = 2,
    data_errors: dict[str, str] | None = None,
) -> dict[str, object]:
    """Build the single active Demo path: 4h top pool -> 1h aligned pool -> 15m setups."""
    params = parameters or ScannerFunnelParameters(
        candidate_max=20, deep_analysis_max=10, action_queue_max=10
    )
    params.validate()
    now_ms = now_ms or int(time.time() * 1000)
    compatible_previous = (
        previous_snapshot
        if (previous_snapshot or {}).get("schema_version") == DEMO_FUNNEL_SCHEMA_VERSION
        else None
    )
    base = build_scanner_funnel(
        tickers,
        instruments,
        parameters=params,
        previous_snapshot=compatible_previous,
        protected_symbols=protected_symbols,
        now_ms=now_ms,
        elapsed_ms=elapsed_ms,
        api_calls=api_calls,
    )

    four_hour_pool: list[dict[str, object]] = []
    contexts_4h: dict[str, MarketContext] = {}
    for row in base["candidate_pool"]:
        symbol = str(row["symbol"])
        series = candles_4h.get(symbol) or []
        try:
            context = market_context(series)
            trend_score = _directional_score(series, context)
        except ValueError:
            continue
        if trend_score is None:
            continue
        side = "Buy" if context.bias == "bullish" else "Sell"
        contexts_4h[symbol] = context
        four_hour_pool.append({
            **row,
            "side": side,
            "bias_4h": context.bias,
            "score_4h": round(float(row["quality_score"]) + trend_score, 6),
        })
    four_hour_pool.sort(key=lambda row: (-float(row["score_4h"]), str(row["symbol"])))
    four_hour_pool = four_hour_pool[: params.candidate_max]
    for rank, row in enumerate(four_hour_pool, start=1):
        row["rank"] = rank

    one_hour_pool: list[dict[str, object]] = []
    for row in four_hour_pool:
        symbol = str(row["symbol"])
        series = candles_1h.get(symbol) or []
        try:
            context = market_context(series)
            trend_score = _directional_score(series, context)
        except ValueError:
            continue
        if trend_score is None or context.bias != row["bias_4h"]:
            continue
        zone = context.support if row["side"] == "Buy" else context.resistance
        if zone is None or float(row["last_price"]) <= 0:
            continue
        distance = abs(float(row["last_price"]) - zone.center) / float(row["last_price"])
        one_hour_pool.append({
            **row,
            "bias_1h": context.bias,
            "entry_zone": zone.center,
            "distance_to_zone_fraction": round(distance, 8),
            "near_zone": distance <= params.near_zone_fraction,
            "score_1h": round(float(row["score_4h"]) + trend_score - distance * 100.0, 6),
        })
    one_hour_pool.sort(key=lambda row: (-float(row["score_1h"]), str(row["symbol"])))
    one_hour_pool = one_hour_pool[: params.deep_analysis_max]
    for rank, row in enumerate(one_hour_pool, start=1):
        row["rank"] = rank

    action_queue: list[dict[str, object]] = []
    for row in one_hour_pool:
        symbol = str(row["symbol"])
        confirmed, strength = timeframe_confirmation(candles_15m.get(symbol) or [], str(row["side"]))
        if not confirmed:
            continue
        action_queue.append({
            **row,
            "confirmation_15m": "fast_slow_momentum",
            "confirmation_strength_15m": strength,
        })
    action_queue = action_queue[: params.action_queue_max]

    previous_symbols = {
        str(row.get("symbol") or "")
        for row in (compatible_previous or {}).get("candidate_pool", [])
        if row.get("symbol")
    }
    current_symbols = {str(row["symbol"]) for row in four_hour_pool}
    churn_rate = (
        0.0 if not previous_symbols
        else len(previous_symbols.symmetric_difference(current_symbols)) / max(1, len(previous_symbols))
    )
    payload: dict[str, object] = {
        "schema_version": DEMO_FUNNEL_SCHEMA_VERSION,
        "mode": "demo",
        "computed_at_ms": now_ms,
        "parameters": asdict(params),
        "metrics": {
            **base["metrics"],
            "candidate_count": len(four_hour_pool),
            "deep_analysis_count": len(one_hour_pool),
            "action_queue_count": len(action_queue),
            "churn_rate": round(churn_rate, 6),
            "churn_within_limit": not previous_symbols or churn_rate <= params.churn_limit_fraction,
            "latency_within_limit": elapsed_ms <= params.latency_limit_ms,
            "elapsed_ms": int(elapsed_ms),
            "api_calls": int(api_calls),
            "data_error_count": len(data_errors or {}),
        },
        "candidate_pool": four_hour_pool,
        "deep_analysis_pool": one_hour_pool,
        "action_queue": action_queue,
        "tracking_symbols": list(dict.fromkeys(
            [str(row["symbol"]) for row in one_hour_pool]
            + sorted(str(symbol) for symbol in (protected_symbols or set()))
        )),
        "data_errors": dict(sorted((data_errors or {}).items())),
        "v2_execution_authority": True,
    }
    payload["snapshot_id"] = fingerprint(payload)
    return payload

