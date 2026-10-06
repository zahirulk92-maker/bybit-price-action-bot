import unittest
import time
from dataclasses import replace
from unittest.mock import patch

from fastapi import HTTPException

from price_action_bot import web
from price_action_bot.models import Candle


class ChartApiTests(unittest.TestCase):
    def setUp(self):
        web.chart_cache.clear()
        web.pnl_cache = None
        web.audit_pnl_cache.clear()
        self.market = {
            "symbol": "BTCUSDT",
            "price": 100.0,
            "bias": "bullish",
            "support": 95.0,
            "resistance": 110.0,
            "signal_state": "SCAN",
            "updated_at_ms": 1,
        }

    def test_chart_returns_closed_candles_and_levels(self):
        candles = [Candle(1, 100, 104, 98, 102, 50)]
        with patch.object(web.store, "market_snapshots", return_value=[self.market]), patch.object(
            web.chart_gateway, "candles", return_value=candles
        ):
            result = web.api_chart("btcusdt", "5", 160, "test")
        self.assertEqual(result["symbol"], "BTCUSDT")
        self.assertEqual(result["support"], 95.0)
        self.assertEqual(result["candles"][0]["close"], 102)

    def test_chart_rejects_unknown_interval(self):
        with self.assertRaises(HTTPException) as raised:
            web.api_chart("BTCUSDT", "1", 160, "test")
        self.assertEqual(raised.exception.status_code, 400)

    def test_audit_page_is_available(self):
        response = web.audit_dashboard("test")
        self.assertTrue(str(response.path).endswith("audit.html"))

    def test_status_exposes_scanner_heartbeat(self):
        now = int(time.time() * 1000)
        heartbeat = {
            "status": "running",
            "updated_at_ms": now,
            "details": {
                "scanner_status": "healthy",
                "last_scan_at_ms": now - 800,
                "next_scan_at_ms": now + 19_200,
                "last_scan_duration_ms": 800,
                "scanned_symbols": 10,
            },
        }
        with patch.object(web.store, "get_heartbeat", return_value=heartbeat), patch.object(
            web.store, "trading_enabled", return_value=True
        ), patch.object(web.store, "market_snapshots", return_value=[]), patch.object(
            web.store, "recent_trades", return_value=[]
        ), patch.object(
            web.store, "events_since", return_value=[]
        ), patch.object(web.store, "recent_events", return_value=[]):
            with patch.object(
                web.chart_gateway,
                "wallet_summary",
                return_value={
                    "equity": 1000.0,
                    "wallet_balance": 990.0,
                    "available_balance": 900.0,
                    "unrealized_pnl": 10.0,
                },
            ):
                web.wallet_cache = None
                result = web.api_status("test")
        self.assertEqual(result["scanner"]["status"], "healthy")
        self.assertEqual(result["scanner"]["scanned_symbols"], 10)
        self.assertLess(result["scanner"]["last_scan_age_ms"], 10_000)
        self.assertEqual(result["safety"]["execution"], "SIGNAL ONLY")
        self.assertEqual(result["safety"]["risk_guard"], "ACTIVE")
        if web.settings.api_key and web.settings.api_secret:
            self.assertEqual(result["wallet"]["equity"], 1000.0)
        self.assertEqual(result["today"]["opened"], 0)

    def test_today_summary_counts_exit_types_and_trailing(self):
        events = [
            {"event_type": "POSITION_OPENED"},
            {"event_type": "POSITION_OPENED"},
            {"event_type": "TP3_CLOSED"},
            {"event_type": "STOP_LOSS_CLOSED"},
            {"event_type": "PARTIAL_TP"},
        ]
        trades = [{"status": "open", "trade": {"state": "TRAILING"}}]
        with patch.object(web.store, "events_since", return_value=events):
            result = web._today_summary(trades)
        self.assertEqual(result["opened"], 2)
        self.assertEqual(result["closed"], 2)
        self.assertEqual(result["tp_closed"], 1)
        self.assertEqual(result["sl_closed"], 1)
        self.assertEqual(result["trailing_active"], 1)

    def test_real_pnl_uses_bybit_closed_rows(self):
        rows = [
            {
                "symbol": "BTCUSDT", "side": "Sell", "qty": "0.01",
                "avgEntryPrice": "100", "avgExitPrice": "90", "closedPnl": "0.095",
                "openFee": "-0.002", "closeFee": "-0.003", "updatedTime": "1000",
            },
            {
                "symbol": "ETHUSDT", "side": "Buy", "qty": "0.2",
                "avgEntryPrice": "20", "avgExitPrice": "19", "closedPnl": "-0.21",
                "openFee": "-0.004", "closeFee": "-0.006", "updatedTime": "2000",
            },
        ]
        configured = replace(web.settings, api_key="demo-key", api_secret="demo-secret")
        with patch.object(web, "settings", configured), patch.object(
            web.chart_gateway, "closed_pnl", return_value=rows
        ):
            result = web._real_pnl_status()
        self.assertTrue(result["available"])
        self.assertAlmostEqual(result["realized_pnl"], -0.115)
        self.assertAlmostEqual(result["fees"], 0.015)
        self.assertEqual(result["wins"], 1)
        self.assertEqual(result["losses"], 1)
        self.assertEqual(result["win_rate"], 50.0)
        self.assertEqual(result["rows"][0]["symbol"], "ETHUSDT")

    def test_audit_combines_exchange_and_local_sources(self):
        configured = replace(web.settings, api_key="demo-key", api_secret="demo-secret")
        closed = [{
            "symbol": "BTCUSDT", "side": "Buy", "qty": "0.1",
            "avgEntryPrice": "100", "avgExitPrice": "110", "closedPnl": "0.9",
            "openFee": "-0.04", "closeFee": "-0.06", "updatedTime": str(int(time.time() * 1000)),
            "orderId": "close-1",
        }]
        local_trade = {
            "id": 1, "symbol": "BTCUSDT", "status": "closed",
            "updated_at_ms": int(time.time() * 1000), "trade": {"side": "Buy"},
        }
        with patch.object(web, "settings", configured), patch.object(
            web.chart_gateway, "closed_pnl", return_value=closed
        ), patch.object(web.store, "events_since", return_value=[]), patch.object(
            web.store, "recent_signal_journal", return_value=[]
        ), patch.object(web.store, "recent_trades", return_value=[local_trade]), patch.object(
            web.store, "get_heartbeat", return_value={"details": {"reconciliation": {"status": "healthy"}}}
        ):
            result = web.api_audit(3, "BTCUSDT", "test")
        self.assertEqual(result["summary"]["realized_pnl"], 0.9)
        self.assertEqual(result["summary"]["exchange_exits"], 1)
        self.assertEqual(result["summary"]["local_trades"], 1)
        self.assertEqual(result["reconciliation"]["status"], "healthy")


if __name__ == "__main__":
    unittest.main()
