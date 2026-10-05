from __future__ import annotations

import math
import time
from typing import Any


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

