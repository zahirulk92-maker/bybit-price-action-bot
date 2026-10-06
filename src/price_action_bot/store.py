from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import SignalState, Trade


class Store:
    """Small SQLite/Postgres persistence layer shared by worker and dashboard."""

    def __init__(self, path: str = "trading_bot.db", database_url: str = "") -> None:
        self._lock = threading.RLock()
        self._postgres = database_url.startswith(("postgres://", "postgresql://"))
        if self._postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("Postgres requires psycopg: pip install -e .") from exc
            self.connection = psycopg.connect(database_url, autocommit=True, row_factory=dict_row)
        else:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(path, check_same_thread=False)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _sql(self, statement: str) -> str:
        return statement.replace("?", "%s") if self._postgres else statement

    def _execute(
        self, statement: str, params: tuple[Any, ...] = (), fetch: str = ""
    ) -> Any:
        with self._lock:
            cursor = self.connection.execute(self._sql(statement), params)
            result = cursor.fetchone() if fetch == "one" else cursor.fetchall() if fetch == "all" else None
            if not self._postgres:
                self.connection.commit()
            return result

    def _init_schema(self) -> None:
        id_column = "BIGSERIAL PRIMARY KEY" if self._postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
        statements = [
            f"""
            CREATE TABLE IF NOT EXISTS events (
                id {id_column}, created_at_ms BIGINT NOT NULL,
                symbol TEXT NOT NULL DEFAULT '', event_type TEXT NOT NULL, payload TEXT NOT NULL
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS trades (
                id {id_column}, symbol TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
                opened_at_ms BIGINT NOT NULL, updated_at_ms BIGINT NOT NULL, closed_at_ms BIGINT
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status, updated_at_ms)",
            """
            CREATE TABLE IF NOT EXISTS bot_control (
                id INTEGER PRIMARY KEY, trading_enabled INTEGER NOT NULL, updated_at_ms BIGINT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS worker_heartbeat (
                id INTEGER PRIMARY KEY, status TEXT NOT NULL, details TEXT NOT NULL,
                updated_at_ms BIGINT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS market_snapshots (
                symbol TEXT PRIMARY KEY, price DOUBLE PRECISION NOT NULL, bias TEXT NOT NULL,
                support DOUBLE PRECISION, resistance DOUBLE PRECISION,
                signal_state TEXT NOT NULL, updated_at_ms BIGINT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS decision_snapshots (
                symbol TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at_ms BIGINT NOT NULL
            )
            """,
        ]
        for statement in statements:
            self._execute(statement)
        now = int(time.time() * 1000)
        if self._execute("SELECT id FROM bot_control WHERE id=1", fetch="one") is None:
            self._execute(
                "INSERT INTO bot_control(id, trading_enabled, updated_at_ms) VALUES (1, 1, ?)",
                (now,),
            )

    def try_acquire_engine_lock(self) -> bool:
        """Ensure only one web instance runs the engine during overlapping deploys."""
        if not self._postgres:
            return True
        row = self._execute(
            "SELECT pg_try_advisory_lock(2090163205) AS acquired", fetch="one"
        )
        return bool(self._dict(row).get("acquired", False))

    def release_engine_lock(self) -> None:
        if self._postgres:
            self._execute("SELECT pg_advisory_unlock(2090163205)")

    @staticmethod
    def _dict(row: Any) -> dict[str, Any]:
        return dict(row) if row is not None else {}

    def event(self, event_type: str, symbol: str = "", **payload: object) -> None:
        self._execute(
            "INSERT INTO events(created_at_ms, symbol, event_type, payload) VALUES (?, ?, ?, ?)",
            (int(time.time() * 1000), symbol, event_type, json.dumps(payload, sort_keys=True)),
        )

    def save_trade(self, trade: Trade, status: str = "open") -> None:
        payload = asdict(trade)
        payload["state"] = trade.state.value
        encoded = json.dumps(payload)
        now = int(time.time() * 1000)
        row = self._execute(
            "SELECT id FROM trades WHERE symbol=? AND status='open' ORDER BY id DESC LIMIT 1",
            (trade.symbol,), fetch="one",
        )
        if row:
            self._execute(
                "UPDATE trades SET status=?, payload=?, updated_at_ms=? WHERE id=?",
                (status, encoded, now, self._dict(row)["id"]),
            )
        else:
            self._execute(
                """INSERT INTO trades(symbol, status, payload, opened_at_ms, updated_at_ms)
                   VALUES (?, ?, ?, ?, ?)""",
                (trade.symbol, status, encoded, now, now),
            )

    def close_trade(self, symbol: str) -> None:
        now = int(time.time() * 1000)
        self._execute(
            """UPDATE trades SET status='closed', updated_at_ms=?, closed_at_ms=?
               WHERE symbol=? AND status='open'""",
            (now, now, symbol),
        )

    def load_open_trades(self) -> dict[str, Trade]:
        rows = self._execute("SELECT payload FROM trades WHERE status='open' ORDER BY id", fetch="all")
        trades: dict[str, Trade] = {}
        for row in rows:
            data = json.loads(self._dict(row)["payload"])
            data["state"] = SignalState(data["state"])
            trade = Trade(**data)
            trades[trade.symbol] = trade
        return trades

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT created_at_ms, symbol, event_type, payload FROM events ORDER BY id DESC LIMIT ?",
            (limit,), fetch="all",
        )
        result = []
        for row in rows:
            item = self._dict(row)
            item["payload"] = json.loads(item["payload"])
            result.append(item)
        return result

    def recent_trades(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._execute(
            """SELECT symbol, status, payload, opened_at_ms, updated_at_ms, closed_at_ms
               FROM trades ORDER BY id DESC LIMIT ?""",
            (limit,), fetch="all",
        )
        result = []
        for row in rows:
            item = self._dict(row)
            item["trade"] = json.loads(item.pop("payload"))
            result.append(item)
        return result

    def set_trading_enabled(self, enabled: bool) -> None:
        self._execute(
            "UPDATE bot_control SET trading_enabled=?, updated_at_ms=? WHERE id=1",
            (1 if enabled else 0, int(time.time() * 1000)),
        )
        self.event("BOT_RESUMED" if enabled else "BOT_PAUSED")

    def trading_enabled(self) -> bool:
        row = self._execute("SELECT trading_enabled FROM bot_control WHERE id=1", fetch="one")
        return bool(self._dict(row).get("trading_enabled", 0))

    def heartbeat(self, status: str, **details: object) -> None:
        now = int(time.time() * 1000)
        encoded = json.dumps(details, sort_keys=True)
        if self._execute("SELECT id FROM worker_heartbeat WHERE id=1", fetch="one"):
            self._execute(
                "UPDATE worker_heartbeat SET status=?, details=?, updated_at_ms=? WHERE id=1",
                (status, encoded, now),
            )
        else:
            self._execute(
                "INSERT INTO worker_heartbeat(id, status, details, updated_at_ms) VALUES (1, ?, ?, ?)",
                (status, encoded, now),
            )

    def get_heartbeat(self) -> dict[str, Any]:
        row = self._execute(
            "SELECT status, details, updated_at_ms FROM worker_heartbeat WHERE id=1", fetch="one"
        )
        if not row:
            return {}
        result = self._dict(row)
        result["details"] = json.loads(result["details"])
        return result

    def market_snapshot(
        self, symbol: str, price: float, bias: str, support: float | None,
        resistance: float | None, signal_state: str,
    ) -> None:
        now = int(time.time() * 1000)
        exists = self._execute(
            "SELECT symbol FROM market_snapshots WHERE symbol=?", (symbol,), fetch="one"
        )
        if exists:
            self._execute(
                """UPDATE market_snapshots SET price=?, bias=?, support=?, resistance=?,
                   signal_state=?, updated_at_ms=? WHERE symbol=?""",
                (price, bias, support, resistance, signal_state, now, symbol),
            )
        else:
            self._execute(
                """INSERT INTO market_snapshots
                   (symbol, price, bias, support, resistance, signal_state, updated_at_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (symbol, price, bias, support, resistance, signal_state, now),
            )

    def market_snapshots(self) -> list[dict[str, Any]]:
        rows = self._execute("SELECT * FROM market_snapshots ORDER BY symbol", fetch="all")
        return [self._dict(row) for row in rows]

    def decision_snapshot(self, symbol: str, decision: dict[str, object]) -> None:
        now = int(time.time() * 1000)
        encoded = json.dumps(decision, sort_keys=True)
        exists = self._execute(
            "SELECT symbol FROM decision_snapshots WHERE symbol=?", (symbol,), fetch="one"
        )
        if exists:
            self._execute(
                "UPDATE decision_snapshots SET payload=?, updated_at_ms=? WHERE symbol=?",
                (encoded, now, symbol),
            )
        else:
            self._execute(
                "INSERT INTO decision_snapshots(symbol, payload, updated_at_ms) VALUES (?, ?, ?)",
                (symbol, encoded, now),
            )

    def decision_snapshots(self) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT symbol, payload, updated_at_ms FROM decision_snapshots ORDER BY symbol",
            fetch="all",
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            item = self._dict(row)
            decision = json.loads(item["payload"])
            decision["symbol"] = item["symbol"]
            decision["updated_at_ms"] = item["updated_at_ms"]
            result.append(decision)
        return result
