from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass
from typing import Any

from .models import Candle
from .v2_foundation import fingerprint


PORTFOLIO_SCHEMA_VERSION = "thesisedge.phase3.v1"


@dataclass(frozen=True)
class PortfolioParameters:
    lookback_hours: int = 72
    min_overlap: int = 36
    healthy_overlap: int = 60
    cluster_correlation: float = 0.70
    risk_per_trade: float = 0.01

    def validate(self) -> None:
        if not 24 <= self.lookback_hours <= 720:
            raise ValueError("Portfolio lookback must be between 24 and 720 hours")
        if not 12 <= self.min_overlap <= self.healthy_overlap <= self.lookback_hours:
            raise ValueError("Portfolio overlap must satisfy 12 <= min <= healthy <= lookback")
        if not 0.30 <= self.cluster_correlation <= 0.95:
            raise ValueError("Portfolio cluster correlation must be between 0.30 and 0.95")
        if not 0 < self.risk_per_trade <= 0.02:
            raise ValueError("Portfolio risk per trade must be positive and no more than 2%")


def _returns(candles: list[Candle], lookback: int) -> dict[int, float]:
    closes = {
        int(candle.timestamp_ms): float(candle.close)
        for candle in candles
        if candle.timestamp_ms and math.isfinite(candle.close) and candle.close > 0
    }
    rows = sorted(closes.items())[-(lookback + 1):]
    output: dict[int, float] = {}
    for previous, current in zip(rows, rows[1:]):
        if previous[1] > 0 and current[1] > 0:
            output[current[0]] = math.log(current[1] / previous[1])
    return output


def _aligned(left: dict[int, float], right: dict[int, float]) -> tuple[list[float], list[float]]:
    timestamps = sorted(set(left) & set(right))
    return [left[item] for item in timestamps], [right[item] for item in timestamps]


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_var = sum((value - left_mean) ** 2 for value in left)
    right_var = sum((value - right_mean) ** 2 for value in right)
    if left_var <= 0 or right_var <= 0:
        return None
    return max(-1.0, min(1.0, numerator / math.sqrt(left_var * right_var)))


def _beta(asset: list[float], benchmark: list[float]) -> float | None:
    if len(asset) != len(benchmark) or len(asset) < 2:
        return None
    asset_mean = sum(asset) / len(asset)
    benchmark_mean = sum(benchmark) / len(benchmark)
    variance = sum((value - benchmark_mean) ** 2 for value in benchmark)
    if variance <= 0:
        return None
    covariance = sum(
        (asset_value - asset_mean) * (benchmark_value - benchmark_mean)
        for asset_value, benchmark_value in zip(asset, benchmark)
    )
    return covariance / variance


def _confidence(overlap: int, parameters: PortfolioParameters) -> str:
    if overlap < parameters.min_overlap:
        return "insufficient"
    if overlap < parameters.healthy_overlap:
        return "weak"
    return "healthy"


def _side(value: object) -> str:
    normalized = str(value or "").lower()
    if normalized in {"buy", "bullish", "long"}:
        return "Buy"
    if normalized in {"sell", "bearish", "short"}:
        return "Sell"
    return "Unknown"


def build_portfolio_map(
    candle_series: dict[str, list[Candle]],
    *,
    opportunities: list[dict[str, Any]] | None = None,
    active_exposures: list[dict[str, Any]] | None = None,
    parameters: PortfolioParameters | None = None,
    now_ms: int | None = None,
    elapsed_ms: int = 0,
    api_calls: int = 0,
    data_errors: dict[str, str] | None = None,
) -> dict[str, object]:
    """Build a deterministic Phase-3 portfolio map with no execution authority."""
    params = parameters or PortfolioParameters()
    params.validate()
    now_ms = now_ms or int(time.time() * 1000)
    series = {
        str(symbol).upper(): _returns(candles, params.lookback_hours)
        for symbol, candles in candle_series.items()
        if symbol
    }
    symbols = sorted(series)
    btc_returns = series.get("BTCUSDT", {})

    basket: dict[int, float] = {}
    for timestamp in sorted({stamp for values in series.values() for stamp in values}):
        values = [returns[timestamp] for returns in series.values() if timestamp in returns]
        if len(values) >= 2:
            basket[timestamp] = sum(values) / len(values)

    features: list[dict[str, object]] = []
    feature_by_symbol: dict[str, dict[str, object]] = {}
    for symbol in symbols:
        values = series[symbol]
        asset_btc, aligned_btc = _aligned(values, btc_returns)
        asset_basket, aligned_basket = _aligned(values, basket)
        overlap = len(asset_btc)
        confidence = _confidence(overlap, params)
        total_return = math.expm1(sum(values.values())) if values else 0.0
        btc_total_return = math.expm1(sum(btc_returns.values())) if btc_returns else 0.0
        btc_correlation = _pearson(asset_btc, aligned_btc)
        item: dict[str, object] = {
            "symbol": symbol,
            "return_count": len(values),
            "btc_overlap": overlap,
            "data_confidence": confidence,
            "btc_correlation": round(btc_correlation, 6) if btc_correlation is not None else None,
            "btc_beta": round(_beta(asset_btc, aligned_btc), 6) if btc_correlation is not None else None,
            "market_correlation": (
                round(value, 6) if (value := _pearson(asset_basket, aligned_basket)) is not None else None
            ),
            "lookback_return_fraction": round(total_return, 8),
            "btc_return_fraction": round(btc_total_return, 8),
            "market_alignment": (
                "unknown" if confidence == "insufficient" else
                "aligned" if total_return == 0 or btc_total_return == 0 or total_return * btc_total_return > 0 else
                "conflicting"
            ),
        }
        features.append(item)
        feature_by_symbol[symbol] = item

    parent = {symbol: symbol for symbol in symbols}

    def find(symbol: str) -> str:
        while parent[symbol] != symbol:
            parent[symbol] = parent[parent[symbol]]
            symbol = parent[symbol]
        return symbol

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    pairwise: list[dict[str, object]] = []
    for index, left_symbol in enumerate(symbols):
        for right_symbol in symbols[index + 1:]:
            left, right = _aligned(series[left_symbol], series[right_symbol])
            correlation = _pearson(left, right)
            overlap = len(left)
            confidence = _confidence(overlap, params)
            if correlation is not None:
                pairwise.append({
                    "left": left_symbol,
                    "right": right_symbol,
                    "correlation": round(correlation, 6),
                    "overlap": overlap,
                    "data_confidence": confidence,
                })
            if (
                correlation is not None
                and confidence != "insufficient"
                and correlation >= params.cluster_correlation
            ):
                union(left_symbol, right_symbol)

    groups: dict[str, list[str]] = {}
    for symbol in symbols:
        groups.setdefault(find(symbol), []).append(symbol)
    ordered_groups = sorted((sorted(group) for group in groups.values()), key=lambda group: group[0])
    clusters: list[dict[str, object]] = []
    cluster_for_symbol: dict[str, str] = {}
    for index, members in enumerate(ordered_groups, start=1):
        cluster_id = f"cluster-{index:02d}"
        for symbol in members:
            cluster_for_symbol[symbol] = cluster_id
        returns = [float(feature_by_symbol[symbol]["lookback_return_fraction"]) for symbol in members]
        cluster_mean = sum(returns) / len(returns) if returns else 0.0
        ranked = sorted(
            members,
            key=lambda symbol: (
                -float(feature_by_symbol[symbol]["lookback_return_fraction"]), symbol
            ),
        )
        confidence_values = [str(feature_by_symbol[symbol]["data_confidence"]) for symbol in members]
        cluster_confidence = (
            "insufficient" if "insufficient" in confidence_values else
            "weak" if "weak" in confidence_values else
            "healthy"
        )
        clusters.append({
            "cluster_id": cluster_id,
            "members": members,
            "member_count": len(members),
            "data_confidence": cluster_confidence,
            "cluster_return_fraction": round(cluster_mean, 8),
            "relative_strength_order": ranked,
        })
        for rank, symbol in enumerate(ranked, start=1):
            feature_by_symbol[symbol]["cluster_id"] = cluster_id
            feature_by_symbol[symbol]["relative_strength_rank"] = rank
            feature_by_symbol[symbol]["relative_strength_fraction"] = round(
                float(feature_by_symbol[symbol]["lookback_return_fraction"]) - cluster_mean, 8
            )

    exposures: list[dict[str, object]] = []
    for raw in active_exposures or []:
        symbol = str(raw.get("symbol") or "").upper()
        if not symbol:
            continue
        risk = max(0.0, min(float(raw.get("risk_fraction") or params.risk_per_trade), params.risk_per_trade))
        exposures.append({
            "symbol": symbol,
            "side": _side(raw.get("side")),
            "state": str(raw.get("state") or "unknown"),
            "risk_fraction": round(risk, 8),
            "cluster_id": cluster_for_symbol.get(symbol),
            "data_confidence": str(feature_by_symbol.get(symbol, {}).get("data_confidence", "insufficient")),
        })
    grouped_exposure: dict[tuple[str, str], list[dict[str, object]]] = {}
    for exposure in exposures:
        cluster_id, side = exposure.get("cluster_id"), str(exposure["side"])
        if cluster_id and side != "Unknown":
            grouped_exposure.setdefault((str(cluster_id), side), []).append(exposure)
    shared_groups = [
        {
            "cluster_id": cluster_id,
            "side": side,
            "symbols": [str(item["symbol"]) for item in rows],
            "position_count": len(rows),
            "effective_risk_fraction": round(sum(float(item["risk_fraction"]) for item in rows), 8),
            "classification": "shared_directional_exposure",
        }
        for (cluster_id, side), rows in sorted(grouped_exposure.items())
        if len(rows) >= 2
    ]

    quality = {
        str(item.get("symbol") or "").upper(): float(item.get("quality_score") or 0)
        for item in opportunities or []
    }
    opportunity_rows: list[dict[str, object]] = []
    grouped_opportunities: dict[tuple[str, str], list[dict[str, object]]] = {}
    for raw in opportunities or []:
        symbol = str(raw.get("symbol") or "").upper()
        if not symbol:
            continue
        row = {
            "symbol": symbol,
            "side": _side(raw.get("side")),
            "side_source": str(raw.get("side_source") or "unknown"),
            "quality_score": quality.get(symbol, 0.0),
            "cluster_id": cluster_for_symbol.get(symbol),
            "data_confidence": str(feature_by_symbol.get(symbol, {}).get("data_confidence", "insufficient")),
        }
        opportunity_rows.append(row)
        key = (str(row["cluster_id"] or symbol), str(row["side"]))
        grouped_opportunities.setdefault(key, []).append(row)
    selection: list[dict[str, object]] = []
    for (_, side), rows in sorted(grouped_opportunities.items()):
        ranked = sorted(rows, key=lambda row: (-float(row["quality_score"]), str(row["symbol"])))
        can_compare = side != "Unknown" and all(row["data_confidence"] == "healthy" for row in ranked)
        for index, row in enumerate(ranked):
            selected = index == 0 and can_compare
            selection.append({
                **row,
                "decision": (
                    "SELECTED" if selected else
                    "QUEUED_CORRELATED" if can_compare else
                    "UNRESOLVED_DATA"
                ),
                "reason": (
                    "highest_quality_in_correlated_cluster" if selected and can_compare else
                    "correlated_same_direction_higher_quality_exists" if can_compare else
                    "weak_or_missing_direction_or_correlation_evidence"
                ),
                "recommended_risk_fraction": params.risk_per_trade if selected else 0.0,
            })
    decision_order = {"SELECTED": 0, "QUEUED_CORRELATED": 1, "UNRESOLVED_DATA": 2}
    selection.sort(
        key=lambda row: (decision_order.get(str(row["decision"]), 99), str(row["symbol"]))
    )

    gross_risk = sum(float(item["risk_fraction"]) for item in exposures)
    payload: dict[str, object] = {
        "schema_version": PORTFOLIO_SCHEMA_VERSION,
        "mode": "shadow",
        "computed_at_ms": now_ms,
        "parameters": asdict(params),
        "metrics": {
            "requested_symbols": len(set(series) | set(data_errors or {})),
            "analyzed_symbols": len(symbols),
            "healthy_symbols": sum(item["data_confidence"] == "healthy" for item in features),
            "weak_symbols": sum(item["data_confidence"] == "weak" for item in features),
            "insufficient_symbols": sum(item["data_confidence"] == "insufficient" for item in features),
            "cluster_count": len(clusters),
            "shared_exposure_group_count": len(shared_groups),
            "elapsed_ms": int(elapsed_ms),
            "api_calls": int(api_calls),
        },
        "features": features,
        "pairwise_correlations": pairwise,
        "clusters": clusters,
        "effective_exposure": {
            "positions": exposures,
            "gross_risk_fraction": round(gross_risk, 8),
            "long_risk_fraction": round(sum(float(item["risk_fraction"]) for item in exposures if item["side"] == "Buy"), 8),
            "short_risk_fraction": round(sum(float(item["risk_fraction"]) for item in exposures if item["side"] == "Sell"), 8),
            "shared_directional_groups": shared_groups,
        },
        "opportunity_selection": selection,
        "data_errors": dict(sorted((data_errors or {}).items())),
        "v2_execution_authority": False,
        "risk_increase_authority": False,
    }
    payload["snapshot_id"] = fingerprint(payload)
    return payload
