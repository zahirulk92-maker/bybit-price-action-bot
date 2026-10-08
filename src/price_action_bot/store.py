from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import Candle, SignalState, Trade


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

    def _executemany(self, statement: str, params: list[tuple[Any, ...]]) -> None:
        if not params:
            return
        with self._lock:
            if self._postgres:
                with self.connection.cursor() as cursor:
                    cursor.executemany(self._sql(statement), params)
            else:
                self.connection.executemany(statement, params)
                self.connection.commit()

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
            f"""
            CREATE TABLE IF NOT EXISTS signal_journal (
                id {id_column}, symbol TEXT NOT NULL, status TEXT NOT NULL,
                payload TEXT NOT NULL, created_at_ms BIGINT NOT NULL, updated_at_ms BIGINT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_signal_journal_updated ON signal_journal(updated_at_ms)",
            """
            CREATE TABLE IF NOT EXISTS thesisedge_candles (
                symbol TEXT NOT NULL, interval TEXT NOT NULL, timestamp_ms BIGINT NOT NULL,
                open DOUBLE PRECISION NOT NULL, high DOUBLE PRECISION NOT NULL,
                low DOUBLE PRECISION NOT NULL, close DOUBLE PRECISION NOT NULL,
                volume DOUBLE PRECISION NOT NULL,
                PRIMARY KEY(symbol, interval, timestamp_ms)
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS thesisedge_decisions (
                id {id_column}, decision_id TEXT NOT NULL UNIQUE,
                created_at_ms BIGINT NOT NULL, symbol TEXT NOT NULL,
                candle_time_ms BIGINT NOT NULL, policy_version TEXT NOT NULL,
                schema_version TEXT NOT NULL, plan_version TEXT NOT NULL,
                stage TEXT NOT NULL, input_payload TEXT NOT NULL,
                decision_payload TEXT NOT NULL, candle_references TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_thesisedge_decisions_symbol_time "
            "ON thesisedge_decisions(symbol, candle_time_ms)",
            f"""
            CREATE TABLE IF NOT EXISTS thesisedge_structure_snapshots (
                id {id_column}, snapshot_id TEXT NOT NULL UNIQUE,
                created_at_ms BIGINT NOT NULL, symbol TEXT NOT NULL,
                candle_time_ms BIGINT NOT NULL, schema_version TEXT NOT NULL,
                mode TEXT NOT NULL, payload TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_thesisedge_structure_symbol_time "
            "ON thesisedge_structure_snapshots(symbol, candle_time_ms)",
            f"""
            CREATE TABLE IF NOT EXISTS thesisedge_universe_snapshots (
                id {id_column}, snapshot_id TEXT NOT NULL UNIQUE,
                created_at_ms BIGINT NOT NULL, computed_at_ms BIGINT NOT NULL,
                schema_version TEXT NOT NULL, mode TEXT NOT NULL, payload TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_thesisedge_universe_time "
            "ON thesisedge_universe_snapshots(computed_at_ms)",
            f"""
            CREATE TABLE IF NOT EXISTS thesisedge_portfolio_snapshots (
                id {id_column}, snapshot_id TEXT NOT NULL UNIQUE,
                created_at_ms BIGINT NOT NULL, computed_at_ms BIGINT NOT NULL,
                schema_version TEXT NOT NULL, mode TEXT NOT NULL, payload TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_thesisedge_portfolio_time "
            "ON thesisedge_portfolio_snapshots(computed_at_ms)",
            f"""
            CREATE TABLE IF NOT EXISTS thesisedge_playbook_snapshots (
                id {id_column}, snapshot_id TEXT NOT NULL UNIQUE,
                created_at_ms BIGINT NOT NULL, symbol TEXT NOT NULL,
                computed_at_ms BIGINT NOT NULL, schema_version TEXT NOT NULL,
                mode TEXT NOT NULL, payload TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_thesisedge_playbook_symbol_time "
            "ON thesisedge_playbook_snapshots(symbol, computed_at_ms)",
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

    def create_signal_journal(self, symbol: str, status: str, **payload: object) -> int:
        now = int(time.time() * 1000)
        encoded = json.dumps(payload, sort_keys=True)
        if self._postgres:
            row = self._execute(
                "INSERT INTO signal_journal(symbol, status, payload, created_at_ms, updated_at_ms) "
                "VALUES (?, ?, ?, ?, ?) RETURNING id",
                (symbol, status, encoded, now, now),
                fetch="one",
            )
            return int(self._dict(row)["id"])
        self._execute(
            "INSERT INTO signal_journal(symbol, status, payload, created_at_ms, updated_at_ms) VALUES (?, ?, ?, ?, ?)",
            (symbol, status, encoded, now, now),
        )
        row = self._execute("SELECT last_insert_rowid() AS id", fetch="one")
        return int(self._dict(row)["id"])

    def update_signal_journal(
        self, signal_id: int | None, symbol: str, status: str, **updates: object
    ) -> None:
        row = self._execute(
            "SELECT id, payload FROM signal_journal WHERE id=?" if signal_id else
            "SELECT id, payload FROM signal_journal WHERE symbol=? ORDER BY id DESC LIMIT 1",
            (signal_id,) if signal_id else (symbol,),
            fetch="one",
        )
        if not row:
            return
        current = json.loads(self._dict(row)["payload"])
        current.update(updates)
        self._execute(
            "UPDATE signal_journal SET status=?, payload=?, updated_at_ms=? WHERE id=?",
            (status, json.dumps(current, sort_keys=True), int(time.time() * 1000), self._dict(row)["id"]),
        )

    def expire_armed_signal_journal(self, reason: str) -> int:
        """Close orphaned in-memory signals when a worker starts with no armed state."""
        rows = self._execute(
            "SELECT id, payload FROM signal_journal WHERE status='ARMED'",
            fetch="all",
        )
        now = int(time.time() * 1000)
        for row in rows:
            item = self._dict(row)
            payload = json.loads(item["payload"])
            payload["reason"] = reason
            self._execute(
                "UPDATE signal_journal SET status='EXPIRED_RESTART', payload=?, updated_at_ms=? WHERE id=?",
                (json.dumps(payload, sort_keys=True), now, item["id"]),
            )
        return len(rows)

    def recent_signal_journal(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT id, symbol, status, payload, created_at_ms, updated_at_ms "
            "FROM signal_journal ORDER BY id DESC LIMIT ?",
            (limit,), fetch="all",
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            item = self._dict(row)
            payload = json.loads(item.pop("payload"))
            item.update(payload)
            result.append(item)
        return result

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

    def close_trade(
        self, symbol: str, reason: str = "UNKNOWN", **details: object
    ) -> None:
        now = int(time.time() * 1000)
        row = self._execute(
            "SELECT id, payload FROM trades WHERE symbol=? AND status='open' ORDER BY id DESC LIMIT 1",
            (symbol,),
            fetch="one",
        )
        if not row:
            return
        item = self._dict(row)
        payload = json.loads(item["payload"])
        payload["close_reason"] = reason
        payload["close_details"] = details
        self._execute(
            "UPDATE trades SET status='closed', payload=?, updated_at_ms=?, closed_at_ms=? WHERE id=?",
            (json.dumps(payload), now, now, item["id"]),
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
            "SELECT id, created_at_ms, symbol, event_type, payload FROM events ORDER BY id DESC LIMIT ?",
            (limit,), fetch="all",
        )
        result = []
        for row in rows:
            item = self._dict(row)
            item["payload"] = json.loads(item["payload"])
            result.append(item)
        return result

    def events_since(self, since_ms: int, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT id, created_at_ms, symbol, event_type, payload FROM events "
            "WHERE created_at_ms >= ? ORDER BY id ASC LIMIT ?",
            (since_ms, limit), fetch="all",
        )
        result = []
        for row in rows:
            item = self._dict(row)
            item["payload"] = json.loads(item["payload"])
            result.append(item)
        return result

    def recent_trades(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._execute(
            """SELECT id, symbol, status, payload, opened_at_ms, updated_at_ms, closed_at_ms
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

    def archive_thesisedge_candles(
        self, symbol: str, interval: str, candles: list[Candle]
    ) -> None:
        latest_row = self._execute(
            "SELECT MAX(timestamp_ms) AS latest FROM thesisedge_candles "
            "WHERE symbol=? AND interval=?",
            (symbol, interval),
            fetch="one",
        )
        latest = self._dict(latest_row).get("latest")
        pending = [
            candle for candle in candles
            if latest is None or candle.timestamp_ms > int(latest)
        ]
        self._executemany(
            """INSERT INTO thesisedge_candles
               (symbol, interval, timestamp_ms, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, interval, timestamp_ms) DO NOTHING""",
            [
                (
                    symbol,
                    interval,
                    candle.timestamp_ms,
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                )
                for candle in pending
            ],
        )

    def record_thesisedge_decision(
        self,
        record: dict[str, object],
        candles_5m: list[Candle],
        candles_1h: list[Candle],
    ) -> None:
        """Persist a deduplicated, append-only Phase-0 replay record."""
        symbol = str(record["symbol"])
        self.archive_thesisedge_candles(symbol, "5m", candles_5m)
        self.archive_thesisedge_candles(symbol, "1h", candles_1h)
        self._execute(
            """INSERT INTO thesisedge_decisions
               (decision_id, created_at_ms, symbol, candle_time_ms, policy_version,
                schema_version, plan_version, stage, input_payload,
                decision_payload, candle_references)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(decision_id) DO NOTHING""",
            (
                str(record["decision_id"]),
                int(time.time() * 1000),
                symbol,
                int(record["candle_time_ms"]),
                str(record["policy_version"]),
                str(record["schema_version"]),
                str(record["plan_version"]),
                str(record["stage"]),
                json.dumps(record["input_payload"], sort_keys=True),
                json.dumps(record["decision_payload"], sort_keys=True),
                json.dumps(record["candle_references"], sort_keys=True),
            ),
        )

    def thesisedge_decisions(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._execute(
            """SELECT decision_id, created_at_ms, symbol, candle_time_ms,
                      policy_version, schema_version, plan_version, stage,
                      input_payload, decision_payload, candle_references
               FROM thesisedge_decisions ORDER BY id DESC LIMIT ?""",
            (limit,),
            fetch="all",
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            item = self._dict(row)
            for key in ("input_payload", "decision_payload", "candle_references"):
                item[key] = json.loads(item[key])
            result.append(item)
        return result

    def thesisedge_candles(
        self, symbol: str, interval: str, from_ms: int, to_ms: int
    ) -> list[Candle]:
        rows = self._execute(
            """SELECT timestamp_ms, open, high, low, close, volume
               FROM thesisedge_candles
               WHERE symbol=? AND interval=? AND timestamp_ms BETWEEN ? AND ?
               ORDER BY timestamp_ms""",
            (symbol, interval, from_ms, to_ms),
            fetch="all",
        )
        return [
            Candle(
                int(self._dict(row)["timestamp_ms"]),
                float(self._dict(row)["open"]),
                float(self._dict(row)["high"]),
                float(self._dict(row)["low"]),
                float(self._dict(row)["close"]),
                float(self._dict(row)["volume"]),
            )
            for row in rows
        ]

    def record_thesisedge_structure(
        self, symbol: str, snapshot: dict[str, object], mode: str = "shadow"
    ) -> None:
        """Persist an append-only, deduplicated Phase-1 structure snapshot."""
        self._execute(
            """INSERT INTO thesisedge_structure_snapshots
               (snapshot_id, created_at_ms, symbol, candle_time_ms,
                schema_version, mode, payload)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(snapshot_id) DO NOTHING""",
            (
                str(snapshot["snapshot_id"]),
                int(time.time() * 1000),
                symbol,
                int(snapshot["computed_at_ms"]),
                str(snapshot["schema_version"]),
                mode,
                json.dumps(snapshot, sort_keys=True),
            ),
        )

    def latest_thesisedge_structure(self, symbol: str) -> dict[str, Any] | None:
        row = self._execute(
            """SELECT payload, mode, created_at_ms
               FROM thesisedge_structure_snapshots
               WHERE symbol=? ORDER BY candle_time_ms DESC, id DESC LIMIT 1""",
            (symbol,),
            fetch="one",
        )
        if row is None:
            return None
        item = self._dict(row)
        payload = json.loads(item["payload"])
        payload["mode"] = item["mode"]
        payload["stored_at_ms"] = item["created_at_ms"]
        return payload

    def record_thesisedge_universe(
        self, snapshot: dict[str, object], mode: str = "shadow"
    ) -> None:
        """Persist an append-only, deduplicated Phase-2 scanner snapshot."""
        self._execute(
            """INSERT INTO thesisedge_universe_snapshots
               (snapshot_id, created_at_ms, computed_at_ms, schema_version, mode, payload)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(snapshot_id) DO NOTHING""",
            (
                str(snapshot["snapshot_id"]),
                int(time.time() * 1000),
                int(snapshot["computed_at_ms"]),
                str(snapshot["schema_version"]),
                mode,
                json.dumps(snapshot, sort_keys=True),
            ),
        )

    def latest_thesisedge_universe(self) -> dict[str, Any] | None:
        row = self._execute(
            """SELECT payload, mode, created_at_ms
               FROM thesisedge_universe_snapshots
               ORDER BY computed_at_ms DESC, id DESC LIMIT 1""",
            fetch="one",
        )
        if row is None:
            return None
        item = self._dict(row)
        payload = json.loads(item["payload"])
        payload["mode"] = item["mode"]
        payload["stored_at_ms"] = item["created_at_ms"]
        return payload

    def record_thesisedge_portfolio(
        self, snapshot: dict[str, object], mode: str = "shadow"
    ) -> None:
        """Persist an append-only, deduplicated Phase-3 portfolio snapshot."""
        self._execute(
            """INSERT INTO thesisedge_portfolio_snapshots
               (snapshot_id, created_at_ms, computed_at_ms, schema_version, mode, payload)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(snapshot_id) DO NOTHING""",
            (
                str(snapshot["snapshot_id"]),
                int(time.time() * 1000),
                int(snapshot["computed_at_ms"]),
                str(snapshot["schema_version"]),
                mode,
                json.dumps(snapshot, sort_keys=True),
            ),
        )

    def latest_thesisedge_portfolio(self) -> dict[str, Any] | None:
        row = self._execute(
            """SELECT payload, mode, created_at_ms
               FROM thesisedge_portfolio_snapshots
               ORDER BY computed_at_ms DESC, id DESC LIMIT 1""",
            fetch="one",
        )
        if row is None:
            return None
        item = self._dict(row)
        payload = json.loads(item["payload"])
        payload["mode"] = item["mode"]
        payload["stored_at_ms"] = item["created_at_ms"]
        return payload

    def record_thesisedge_playbook(self, snapshot: dict[str, object], mode: str = "shadow") -> None:
        self._execute(
            """INSERT INTO thesisedge_playbook_snapshots
               (snapshot_id, created_at_ms, symbol, computed_at_ms, schema_version, mode, payload)
               VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(snapshot_id) DO NOTHING""",
            (
                str(snapshot["snapshot_id"]), int(time.time() * 1000), str(snapshot["symbol"]),
                int(snapshot["computed_at_ms"]), str(snapshot["schema_version"]), mode,
                json.dumps(snapshot, sort_keys=True),
            ),
        )

    def latest_thesisedge_playbooks(self) -> list[dict[str, Any]]:
        rows = self._execute(
            """SELECT payload, mode, created_at_ms FROM thesisedge_playbook_snapshots p
               WHERE id = (SELECT id FROM thesisedge_playbook_snapshots
                           WHERE symbol=p.symbol ORDER BY computed_at_ms DESC, id DESC LIMIT 1)
               ORDER BY symbol""",
            fetch="all",
        )
        output = []
        for row in rows:
            item = self._dict(row)
            payload = json.loads(item["payload"])
            payload["mode"] = item["mode"]
            payload["stored_at_ms"] = item["created_at_ms"]
            output.append(payload)
        return output
