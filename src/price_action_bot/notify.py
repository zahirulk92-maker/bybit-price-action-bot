from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request


LOGGER = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, token: str = "", chat_id: str = "") -> None:
        self.token = token
        self.chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, message: str) -> None:
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        body = urllib.parse.urlencode({"chat_id": self.chat_id, "text": message}).encode()
        request = urllib.request.Request(url, data=body, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not payload.get("ok"):
                LOGGER.warning("Telegram rejected alert: %s", payload)
        except Exception:
            LOGGER.exception("Telegram alert failed")

