import unittest
from unittest.mock import MagicMock, patch

from price_action_bot.exchange import BybitAPIError, BybitGateway


def ok(rows=None):
    return {"retCode": 0, "result": {"list": rows or []}}


class OrderIdempotencyTests(unittest.TestCase):
    def gateway(self):
        gateway = object.__new__(BybitGateway)
        gateway.session = MagicMock()
        gateway._rules = {}
        return gateway

    def test_existing_link_id_prevents_second_order(self):
        gateway = self.gateway()
        gateway.session.get_open_orders.return_value = ok([{"orderId": "existing-1"}])

        order_id = gateway.place_market_order("BTCUSDT", "Buy", 0.01, "stable-link", attempts=3)

        self.assertEqual(order_id, "existing-1")
        gateway.session.place_order.assert_not_called()

    def test_timeout_recovers_accepted_order_without_resubmitting(self):
        gateway = self.gateway()
        gateway.session.get_open_orders.side_effect = [ok(), ok([{"orderId": "accepted-1"}])]
        gateway.session.get_order_history.return_value = ok()
        gateway.session.place_order.side_effect = TimeoutError("connection lost after submit")

        order_id = gateway.place_market_order("BTCUSDT", "Sell", 0.02, "stable-link", attempts=3)

        self.assertEqual(order_id, "accepted-1")
        self.assertEqual(gateway.session.place_order.call_count, 1)

    @patch("price_action_bot.exchange.time.sleep", return_value=None)
    def test_transient_bybit_error_retries_with_same_link_id(self, _sleep):
        gateway = self.gateway()
        gateway.order_by_link_id = MagicMock(return_value=None)
        gateway.session.place_order.side_effect = [
            {"retCode": 10016, "retMsg": "service restarting"},
            {"retCode": 0, "result": {"orderId": "retry-ok"}},
        ]

        order_id = gateway.place_market_order("ETHUSDT", "Buy", 0.1, "same-link", attempts=3)

        self.assertEqual(order_id, "retry-ok")
        self.assertEqual(gateway.session.place_order.call_count, 2)
        link_ids = [call.kwargs["orderLinkId"] for call in gateway.session.place_order.call_args_list]
        self.assertEqual(link_ids, ["same-link", "same-link"])

    def test_non_retryable_error_is_not_retried(self):
        gateway = self.gateway()
        gateway.order_by_link_id = MagicMock(return_value=None)
        gateway.session.place_order.return_value = {"retCode": 10001, "retMsg": "bad request"}

        with self.assertRaises(BybitAPIError):
            gateway.place_market_order("BTCUSDT", "Buy", 0.01, "stable-link", attempts=3)

        self.assertEqual(gateway.session.place_order.call_count, 1)


if __name__ == "__main__":
    unittest.main()
