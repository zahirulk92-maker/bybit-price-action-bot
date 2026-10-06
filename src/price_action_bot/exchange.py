from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .models import Candle, Side
from .risk import floor_to_step


@dataclass(frozen=True)
class InstrumentRules:
    qty_step: float
    min_qty: float
    max_market_qty: float
    tick_size: float


class BybitGateway:
    def __init__(self, api_key: str, api_secret: str, demo: bool = True) -> None:
        try:
            from pybit.unified_trading import HTTP
        except ImportError as exc:
            raise RuntimeError("Install dependencies first: python -m pip install -e .") from exc

        kwargs: dict[str, Any] = {"testnet": False, "demo": demo}
        if api_key and api_secret:
            kwargs.update(api_key=api_key, api_secret=api_secret)
        self.session = HTTP(**kwargs)
        self._rules: dict[str, InstrumentRules] = {}

    @staticmethod
    def _check(response: dict[str, Any]) -> dict[str, Any]:
        if response.get("retCode") != 0:
            raise RuntimeError(f"Bybit error {response.get('retCode')}: {response.get('retMsg')}")
        return response

    def server_time_ms(self) -> int:
        response = self._check(self.session.get_server_time())
        return int(response["time"])

    def tickers(self) -> list[dict[str, Any]]:
        response = self._check(self.session.get_tickers(category="linear"))
        return response["result"]["list"]

    def instruments(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        cursor = ""
        while True:
            params: dict[str, Any] = {"category": "linear", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            response = self._check(self.session.get_instruments_info(**params))
            result = response["result"]
            results.extend(result["list"])
            cursor = result.get("nextPageCursor", "")
            if not cursor:
                return results

    def candles(self, symbol: str, interval: str, limit: int = 200) -> list[Candle]:
        response = self._check(
            self.session.get_kline(category="linear", symbol=symbol, interval=interval, limit=limit)
        )
        interval_ms = int(interval) * 60_000
        now_ms = int(response.get("time") or time.time() * 1000)
        candles = [
            Candle(
                timestamp_ms=int(item[0]),
                open=float(item[1]),
                high=float(item[2]),
                low=float(item[3]),
                close=float(item[4]),
                volume=float(item[5]),
            )
            for item in response["result"]["list"]
            if int(item[0]) + interval_ms <= now_ms
        ]
        return sorted(candles, key=lambda candle: candle.timestamp_ms)

    def instrument_rules(self, symbol: str) -> InstrumentRules:
        if symbol in self._rules:
            return self._rules[symbol]
        response = self._check(
            self.session.get_instruments_info(category="linear", symbol=symbol)
        )
        item = response["result"]["list"][0]
        lot = item["lotSizeFilter"]
        rules = InstrumentRules(
            qty_step=float(lot["qtyStep"]),
            min_qty=float(lot["minOrderQty"]),
            max_market_qty=float(lot.get("maxMktOrderQty") or lot["maxOrderQty"]),
            tick_size=float(item["priceFilter"]["tickSize"]),
        )
        self._rules[symbol] = rules
        return rules

    def equity_usdt(self) -> float:
        response = self._check(self.session.get_wallet_balance(accountType="UNIFIED"))
        accounts = response["result"]["list"]
        if not accounts:
            raise RuntimeError("No unified account balance returned")
        return float(accounts[0]["totalEquity"])

    def set_leverage(self, symbol: str, leverage: int) -> None:
        try:
            self._check(
                self.session.set_leverage(
                    category="linear",
                    symbol=symbol,
                    buyLeverage=str(leverage),
                    sellLeverage=str(leverage),
                )
            )
        except Exception as exc:
            if "leverage not modified" not in str(exc).lower():
                raise

    def place_market_order(self, symbol: str, side: Side, qty: float, order_link_id: str) -> str:
        response = self._check(
            self.session.place_order(
                category="linear",
                symbol=symbol,
                side=side,
                orderType="Market",
                qty=str(qty),
                positionIdx=0,
                orderLinkId=order_link_id[:36],
            )
        )
        return str(response["result"]["orderId"])

    def position(self, symbol: str) -> dict[str, Any] | None:
        response = self._check(self.session.get_positions(category="linear", symbol=symbol))
        for position in response["result"]["list"]:
            if float(position.get("size") or 0) > 0:
                return position
        return None

    def wait_for_position(self, symbol: str, attempts: int = 8) -> dict[str, Any]:
        for _ in range(attempts):
            position = self.position(symbol)
            if position:
                return position
            time.sleep(0.4)
        raise RuntimeError(f"Order accepted but no position appeared for {symbol}")

    def set_protection(self, symbol: str, stop: float, target: float | None = None) -> None:
        rules = self.instrument_rules(symbol)
        stop = floor_to_step(stop, rules.tick_size)
        payload = {
            "category": "linear", "symbol": symbol, "tpslMode": "Full",
            "positionIdx": 0, "stopLoss": str(stop), "slTriggerBy": "MarkPrice",
        }
        if target is not None:
            payload["takeProfit"] = str(floor_to_step(target, rules.tick_size))
            payload["tpTriggerBy"] = "MarkPrice"
        self._check(self.session.set_trading_stop(**payload))

    def move_stop(self, symbol: str, stop: float) -> None:
        rules = self.instrument_rules(symbol)
        stop = floor_to_step(stop, rules.tick_size)
        self._check(
            self.session.set_trading_stop(
                category="linear",
                symbol=symbol,
                tpslMode="Full",
                positionIdx=0,
                stopLoss=str(stop),
                slTriggerBy="MarkPrice",
            )
        )

    def close_partial(self, symbol: str, open_side: Side, qty: float) -> str:
        close_side: Side = "Sell" if open_side == "Buy" else "Buy"
        rules = self.instrument_rules(symbol)
        rounded_qty = floor_to_step(qty, rules.qty_step)
        if rounded_qty < rules.min_qty:
            return ""
        response = self._check(
            self.session.place_order(
                category="linear",
                symbol=symbol,
                side=close_side,
                orderType="Market",
                qty=str(rounded_qty),
                positionIdx=0,
                reduceOnly=True,
            )
        )
        return str(response["result"]["orderId"])
