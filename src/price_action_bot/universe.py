from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass
from typing import Any

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


def select_symbols(
    tickers: list[dict[str, Any]],
    instruments: list[dict[str, Any]],
    size: int = 10,
    now_ms: int | None = None,
) -> list[str]:
    """Pick liquid, established USDT perpetual contracts; BTC/ETH occupy two fixed slots."""
    now_ms = now_ms or int(time.time() * 1000)
    min_launch_ms = now_ms - 30 * 24 * 60 * 60 * 1000
    tradable: set[str] = set()
    for item in instruments:
        symbol = item.get("symbol", "")
        base = item.get("baseCoin", "")
        if (
            item.get("status") == "Trading"
            and item.get("quoteCoin") == "USDT"
            and item.get("contractType") == "LinearPerpetual"
            and int(item.get("launchTime") or 0) <= min_launch_ms
            and base not in EXCLUDED_BASE_COINS
            and symbol.endswith("USDT")
        ):
            tradable.add(symbol)

    ranked: list[tuple[float, str]] = []
    for item in tickers:
        symbol = item.get("symbol", "")
        if symbol not in tradable:
            continue
        bid = float(item.get("bid1Price") or 0)
        ask = float(item.get("ask1Price") or 0)
        turnover = float(item.get("turnover24h") or 0)
        open_interest = float(item.get("openInterestValue") or 0)
        midpoint = (bid + ask) / 2 if bid and ask else 0
        spread = (ask - bid) / midpoint if midpoint else 1
        if turnover <= 0 or open_interest <= 0 or spread > 0.0015:
            continue
        score = 0.60 * math.log1p(turnover) + 0.40 * math.log1p(open_interest) - spread * 100
        ranked.append((score, symbol))

    ranked.sort(reverse=True)
    selected = [symbol for symbol in ("BTCUSDT", "ETHUSDT") if symbol in tradable]
    for _, symbol in ranked:
        if symbol not in selected:
            selected.append(symbol)
        if len(selected) >= size:
            break
    return selected

