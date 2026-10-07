from __future__ import annotations

import csv
import io
import logging
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from .config import Settings
from .engine import TradingEngine
from .exchange import BybitGateway
from .notify import TelegramNotifier
from .store import Store
from .structure import build_structure_map


settings = Settings.from_env()
store = Store(settings.database_path, settings.database_url)
chart_gateway = BybitGateway(settings.api_key, settings.api_secret, settings.demo)
security = HTTPBasic(auto_error=False)
static_dir = Path(__file__).with_name("static")
LOGGER = logging.getLogger(__name__)
engine_host_stop = threading.Event()
engine_ref: dict[str, TradingEngine] = {}
chart_cache_lock = threading.Lock()
chart_cache: dict[tuple[str, str], tuple[float, dict[str, object]]] = {}
wallet_cache_lock = threading.Lock()
wallet_cache: tuple[float, dict[str, object]] | None = None
pnl_cache_lock = threading.Lock()
pnl_cache: tuple[float, dict[str, object]] | None = None
audit_cache_lock = threading.Lock()
audit_pnl_cache: dict[int, tuple[float, dict[str, object]]] = {}
DHAKA = timezone(timedelta(hours=6))


def _wallet_status() -> dict[str, object]:
    global wallet_cache
    if not settings.api_key or not settings.api_secret:
        return {"available": False, "error": "Demo API credentials are not configured"}
    now = time.monotonic()
    with wallet_cache_lock:
        if wallet_cache and wallet_cache[0] > now:
            return wallet_cache[1]
    try:
        payload: dict[str, object] = {"available": True, **chart_gateway.wallet_summary()}
    except Exception as exc:
        LOGGER.warning("Wallet summary unavailable: %s", exc)
        payload = {"available": False, "error": "Bybit wallet temporarily unavailable"}
    with wallet_cache_lock:
        wallet_cache = (now + 15, payload)
    return payload


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _pnl_status_since(start_ms: int, limit: int = 500) -> dict[str, object]:
    """Summarize authoritative Bybit closed-PnL rows since a timestamp."""
    try:
        raw_rows = chart_gateway.closed_pnl(start_ms, limit=limit)
        rows = [
            {
                "symbol": str(row.get("symbol") or ""),
                "side": str(row.get("side") or ""),
                "qty": _number(row.get("qty")),
                "entry": _number(row.get("avgEntryPrice")),
                "exit": _number(row.get("avgExitPrice")),
                "closed_pnl": _number(row.get("closedPnl")),
                "fees": abs(_number(row.get("openFee"))) + abs(_number(row.get("closeFee"))),
                "updated_at_ms": int(_number(row.get("updatedTime") or row.get("createdTime"))),
                "order_id": str(row.get("orderId") or ""),
            }
            for row in raw_rows
        ]
        rows.sort(key=lambda row: int(row["updated_at_ms"]), reverse=True)
        wins = sum(_number(row["closed_pnl"]) > 0 for row in rows)
        losses = sum(_number(row["closed_pnl"]) < 0 for row in rows)
        decided = wins + losses
        gross_profit = sum(max(0.0, _number(row["closed_pnl"])) for row in rows)
        gross_loss = abs(sum(min(0.0, _number(row["closed_pnl"])) for row in rows))
        realized_pnl = sum(_number(row["closed_pnl"]) for row in rows)
        payload: dict[str, object] = {
            "available": True,
            "source": "Bybit closed P&L",
            "realized_pnl": realized_pnl,
            "fees": sum(_number(row["fees"]) for row in rows),
            "closed_count": len(rows),
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / decided * 100) if decided else 0.0,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "profit_factor": (gross_profit / gross_loss) if gross_loss else None,
            "average_trade": (realized_pnl / len(rows)) if rows else None,
            "best_trade": max((_number(row["closed_pnl"]) for row in rows), default=None),
            "worst_trade": min((_number(row["closed_pnl"]) for row in rows), default=None),
            "since_ms": start_ms,
            "rows": rows,
        }
    except Exception as exc:
        LOGGER.warning("Real P&L unavailable: %s", exc)
        payload = {"available": False, "error": "Bybit closed P&L temporarily unavailable", "rows": []}
    return payload


def _real_pnl_status() -> dict[str, object]:
    """Summarize today's authoritative Bybit closed-PnL rows."""
    global pnl_cache
    if not settings.api_key or not settings.api_secret:
        return {"available": False, "error": "Demo API credentials are not configured", "rows": []}
    now = time.monotonic()
    with pnl_cache_lock:
        if pnl_cache and pnl_cache[0] > now:
            return pnl_cache[1]
    dhaka = timezone(timedelta(hours=6))
    local_now = datetime.now(dhaka)
    start = datetime.combine(local_now.date(), datetime.min.time(), tzinfo=dhaka)
    payload = _pnl_status_since(int(start.timestamp() * 1000), limit=200)
    payload["rows"] = list(payload.get("rows") or [])[:30]
    with pnl_cache_lock:
        pnl_cache = (now + 15, payload)
    return payload


def _today_summary(
    open_trades: list[dict[str, object]], pnl: dict[str, object] | None = None
) -> dict[str, object]:
    now = datetime.now(DHAKA)
    start = datetime.combine(now.date(), datetime.min.time(), tzinfo=DHAKA)
    events = store.events_since(int(start.timestamp() * 1000), limit=2000)
    counts: dict[str, int] = {}
    for event in events:
        event_type = str(event["event_type"])
        counts[event_type] = counts.get(event_type, 0) + 1
    open_rows = [row for row in open_trades if row.get("status") == "open"]
    trailing = sum(
        str((row.get("trade") or {}).get("state", "")) == "TRAILING" for row in open_rows
    )
    tp_closed = counts.get("TP3_CLOSED", 0)
    sl_closed = counts.get("STOP_LOSS_CLOSED", 0)
    trailing_closed = counts.get("TRAILING_STOP_CLOSED", 0)
    reversal_closed = counts.get("EARLY_EXIT_REVERSAL", 0)
    other_closed = counts.get("POSITION_CLOSED", 0)
    local_opened = counts.get("POSITION_OPENED", 0)
    local_closed = tp_closed + sl_closed + trailing_closed + reversal_closed + other_closed
    pnl = pnl or {}
    exchange_available = bool(pnl.get("available"))
    return {
        # Backwards-compatible aliases remain explicitly local.
        "opened": local_opened,
        "closed": local_closed,
        "local_opened": local_opened,
        "local_closed": local_closed,
        "exchange_available": exchange_available,
        "exchange_exits": int(pnl.get("closed_count", 0) or 0) if exchange_available else None,
        "exchange_wins": int(pnl.get("wins", 0) or 0) if exchange_available else None,
        "exchange_losses": int(pnl.get("losses", 0) or 0) if exchange_available else None,
        "remaining": len(open_rows),
        "tp_closed": tp_closed,
        "sl_closed": sl_closed,
        "trailing_closed": trailing_closed,
        "reversal_closed": reversal_closed,
        "other_closed": other_closed,
        "tp1_hits": counts.get("PARTIAL_TP", 0),
        "tp2_hits": counts.get("PARTIAL_TP2", 0),
        "trailing_active": trailing,
        "sources": {
            "local": "Worker lifecycle events · Asia/Dhaka today",
            "exchange": "Bybit closed P&L · Asia/Dhaka today",
        },
    }


def _reconciled_signal_journal(
    rows: list[dict[str, object]],
    markets: list[dict[str, object]],
    worker_online: bool,
) -> list[dict[str, object]]:
    """Prevent persisted ARMED rows from outliving the worker's active state."""
    active_symbols = {
        str(row.get("symbol") or "")
        for row in markets
        if worker_online and row.get("signal_state") == "ARMED"
    }
    reconciled: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        if item.get("status") == "ARMED" and item.get("symbol") not in active_symbols:
            item["status"] = "EXPIRED_RESTART"
            item["reason"] = "No longer active in the current worker state"
        reconciled.append(item)
    return reconciled


def _run_embedded_engine() -> None:
    while not engine_host_stop.is_set():
        if not store.try_acquire_engine_lock():
            store.heartbeat("standby", reason="another instance owns the engine lock")
            engine_host_stop.wait(5)
            continue
        try:
            engine = TradingEngine(
                settings,
                BybitGateway(settings.api_key, settings.api_secret, settings.demo),
                store,
                TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id),
            )
            engine_ref["engine"] = engine
            engine.run_forever()
        except Exception:
            LOGGER.exception("Embedded trading engine stopped unexpectedly")
            store.heartbeat("error", reason="embedded engine stopped")
        finally:
            engine_ref.pop("engine", None)
            store.release_engine_lock()
        if not engine_host_stop.is_set():
            engine_host_stop.wait(5)


@asynccontextmanager
async def lifespan(_: FastAPI):
    thread: threading.Thread | None = None
    if settings.run_engine_in_web:
        engine_host_stop.clear()
        thread = threading.Thread(target=_run_embedded_engine, name="trading-engine", daemon=True)
        thread.start()
    try:
        yield
    finally:
        engine_host_stop.set()
        if engine := engine_ref.get("engine"):
            engine.stop()
        if thread:
            thread.join(timeout=55)

app = FastAPI(title="Price Action Control", docs_url=None, redoc_url=None, lifespan=lifespan)


def require_auth(credentials: HTTPBasicCredentials | None = Depends(security)) -> str:
    if settings.dashboard_allow_insecure_local:
        return "local-preview"
    if not settings.dashboard_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard credentials are not configured",
        )
    valid = bool(credentials) and secrets.compare_digest(
        credentials.username.encode(), settings.dashboard_username.encode()
    ) and secrets.compare_digest(
        credentials.password.encode(), settings.dashboard_password.encode()
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid dashboard credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


class ControlRequest(BaseModel):
    enabled: bool


def _connection_test() -> dict[str, object]:
    """Run read-only database and Bybit connectivity checks without placing orders."""
    checks: list[dict[str, object]] = []

    def run(name: str, operation, detail) -> object | None:
        started = time.perf_counter()
        try:
            value = operation()
            checks.append({
                "name": name,
                "status": "passed",
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "detail": detail(value),
            })
            return value
        except Exception as exc:
            LOGGER.warning("Connection test %s failed: %s", name, exc)
            checks.append({
                "name": name,
                "status": "failed",
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "detail": "Connection unavailable; check the service log",
            })
            return None

    run(
        "Local database",
        store.get_heartbeat,
        lambda value: "Readable; worker heartbeat found" if value else "Readable; waiting for heartbeat",
    )
    server_time = run(
        "Bybit public API",
        chart_gateway.server_time_ms,
        lambda value: f"Server clock drift {abs(int(time.time() * 1000) - int(value))} ms",
    )
    if settings.api_key and settings.api_secret:
        run(
            "Bybit private API",
            chart_gateway.wallet_summary,
            lambda value: f"Authenticated; equity {_number(value.get('equity')):.4f} USDT",
        )
    else:
        checks.append({
            "name": "Bybit private API",
            "status": "skipped",
            "latency_ms": 0,
            "detail": "Demo API credentials are not configured",
        })
    failed = any(check["status"] == "failed" for check in checks)
    skipped = any(check["status"] == "skipped" for check in checks)
    return {
        "status": "failed" if failed else "attention" if skipped else "healthy",
        "environment": "demo" if settings.demo else "live",
        "checked_at_ms": int(time.time() * 1000),
        "bybit_server_time_ms": server_time,
        "checks": checks,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=FileResponse)
def dashboard(_: str = Depends(require_auth)) -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/audit", response_class=FileResponse)
def audit_dashboard(_: str = Depends(require_auth)) -> FileResponse:
    return FileResponse(static_dir / "audit.html")


@app.get("/{page}", response_class=FileResponse)
def dashboard_page(page: str, _: str = Depends(require_auth)) -> FileResponse:
    """Serve one authenticated app shell while preserving page-specific URLs."""
    if page not in {
        "overview", "structure", "signals", "positions", "performance", "journal", "system"
    }:
        raise HTTPException(status_code=404, detail="Dashboard page not found")
    return FileResponse(static_dir / "index.html")


def _audit_payload(
    since_ms: int,
    wanted: str,
    pnl: dict[str, object],
    *,
    generated_at_ms: int | None = None,
    period: str = "custom",
) -> dict[str, object]:
    now_ms = generated_at_ms or int(time.time() * 1000)
    events = store.events_since(since_ms, limit=2000)
    signals = [
        row for row in store.recent_signal_journal(1000)
        if int(row.get("updated_at_ms") or 0) >= since_ms
    ]
    trades = [
        row for row in store.recent_trades(1000)
        if int(row.get("updated_at_ms") or 0) >= since_ms
    ]
    pnl_rows = list(pnl.get("rows") or [])
    if wanted:
        events = [row for row in events if row.get("symbol") == wanted]
        signals = [row for row in signals if row.get("symbol") == wanted]
        trades = [row for row in trades if row.get("symbol") == wanted]
        pnl_rows = [row for row in pnl_rows if row.get("symbol") == wanted]
    realized = sum(_number(row.get("closed_pnl")) for row in pnl_rows)
    fees = sum(_number(row.get("fees")) for row in pnl_rows)
    wins = sum(_number(row.get("closed_pnl")) > 0 for row in pnl_rows)
    losses = sum(_number(row.get("closed_pnl")) < 0 for row in pnl_rows)
    heartbeat = store.get_heartbeat()
    reconciliation = (heartbeat.get("details") or {}).get("reconciliation", {})
    return {
        "generated_at_ms": now_ms,
        "since_ms": since_ms,
        "period": period,
        "symbol": wanted,
        "summary": {
            "realized_pnl": realized,
            "fees": fees,
            "exchange_exits": len(pnl_rows),
            "wins": wins,
            "losses": losses,
            "win_rate": wins / (wins + losses) * 100 if wins + losses else 0.0,
            "local_trades": len(trades),
            "events": len(events),
            "signals": len(signals),
        },
        "pnl_available": bool(pnl.get("available")),
        "pnl_error": pnl.get("error", ""),
        "reconciliation": reconciliation,
        "exchange_exits": pnl_rows[:500],
        "trades": trades,
        "events": list(reversed(events)),
        "signals": signals,
    }


def _report_window(period: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    local_now = now or datetime.now(DHAKA)
    if period == "daily":
        start = datetime.combine(local_now.date(), datetime.min.time(), tzinfo=DHAKA)
    elif period == "weekly":
        week_start = local_now.date() - timedelta(days=local_now.weekday())
        start = datetime.combine(week_start, datetime.min.time(), tzinfo=DHAKA)
    else:
        raise HTTPException(status_code=404, detail="Report period must be daily or weekly")
    return start, local_now


def _report_payload(period: str) -> tuple[dict[str, object], datetime, datetime]:
    start, end = _report_window(period)
    since_ms = int(start.timestamp() * 1000)
    if not settings.api_key or not settings.api_secret:
        pnl = {"available": False, "error": "Demo API credentials are not configured", "rows": []}
    else:
        pnl = _pnl_status_since(since_ms)
    return _audit_payload(
        since_ms,
        "",
        pnl,
        generated_at_ms=int(end.timestamp() * 1000),
        period=period,
    ), start, end


def _report_csv(payload: dict[str, object], start: datetime, end: datetime) -> bytes:
    output = io.StringIO(newline="")
    fields = [
        "record_type", "report_period", "period_start", "period_end", "closed_at", "symbol",
        "side", "quantity", "avg_entry", "avg_exit", "trading_fees", "net_realized_pnl",
        "wins", "losses", "win_rate_percent", "exchange_exits", "local_events", "order_id",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    summary = payload.get("summary", {})
    writer.writerow({
        "record_type": "SUMMARY",
        "report_period": payload["period"],
        "period_start": start.isoformat(timespec="seconds"),
        "period_end": end.isoformat(timespec="seconds"),
        "trading_fees": summary.get("fees", 0),
        "net_realized_pnl": summary.get("realized_pnl", 0),
        "wins": summary.get("wins", 0),
        "losses": summary.get("losses", 0),
        "win_rate_percent": summary.get("win_rate", 0),
        "exchange_exits": summary.get("exchange_exits", 0),
        "local_events": summary.get("events", 0),
    })
    for row in payload.get("exchange_exits", []):
        closed_at = datetime.fromtimestamp(
            int(row.get("updated_at_ms") or 0) / 1000, DHAKA
        ).isoformat(timespec="seconds") if row.get("updated_at_ms") else ""
        writer.writerow({
            "record_type": "EXIT",
            "report_period": payload["period"],
            "period_start": start.isoformat(timespec="seconds"),
            "period_end": end.isoformat(timespec="seconds"),
            "closed_at": closed_at,
            "symbol": row.get("symbol", ""),
            "side": row.get("side", ""),
            "quantity": row.get("qty", 0),
            "avg_entry": row.get("entry", 0),
            "avg_exit": row.get("exit", 0),
            "trading_fees": row.get("fees", 0),
            "net_realized_pnl": row.get("closed_pnl", 0),
            "order_id": row.get("order_id", ""),
        })
    return output.getvalue().encode("utf-8-sig")


def _report_pdf(payload: dict[str, object], start: datetime, end: datetime) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle, Paragraph

    output = io.BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=13 * mm,
        bottomMargin=13 * mm,
        title=f"{str(payload['period']).title()} Trading Report",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="MetaRight", parent=styles["Normal"], alignment=TA_RIGHT, textColor=colors.HexColor("#527083"), fontSize=8))
    styles["Title"].textColor = colors.HexColor("#092535")
    styles["Title"].fontSize = 19
    story = [
        Table([[Paragraph("Price Action Trading Report", styles["Title"]), Paragraph(f"{str(payload['period']).upper()}<br/>{start:%d %b %Y %H:%M} - {end:%d %b %Y %H:%M} (Asia/Dhaka)", styles["MetaRight"])]], colWidths=[160 * mm, 95 * mm]),
        Spacer(1, 6 * mm),
    ]
    summary = payload.get("summary", {})
    cards = [
        ["Net realized P&L", "Trading fees", "Exchange exits", "Win rate", "Local audit events"],
        [
            f"{_number(summary.get('realized_pnl')):+.4f} USDT",
            f"{_number(summary.get('fees')):.4f} USDT",
            str(summary.get("exchange_exits", 0)),
            f"{_number(summary.get('win_rate')):.1f}%",
            str(summary.get("events", 0)),
        ],
    ]
    summary_table = Table(cards, colWidths=[51 * mm] * 5, rowHeights=[8 * mm, 11 * mm])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0c2f40")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#8fded0")),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#edf7f7")),
        ("TEXTCOLOR", (0, 1), (-1, 1), colors.HexColor("#092535")),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b9d4da")),
    ]))
    story.extend([summary_table, Spacer(1, 7 * mm), Paragraph("Bybit-confirmed exits", styles["Heading2"]), Spacer(1, 2 * mm)])
    rows = [["Closed", "Symbol", "Side", "Qty", "Avg entry", "Avg exit", "Fees", "Net P&L", "Order ID"]]
    for row in payload.get("exchange_exits", []):
        closed = datetime.fromtimestamp(int(row.get("updated_at_ms") or 0) / 1000, DHAKA).strftime("%d %b %H:%M") if row.get("updated_at_ms") else "-"
        rows.append([
            closed, str(row.get("symbol") or "-"), str(row.get("side") or "-"),
            f"{_number(row.get('qty')):.8g}", f"{_number(row.get('entry')):.8g}",
            f"{_number(row.get('exit')):.8g}", f"{_number(row.get('fees')):.5f}",
            f"{_number(row.get('closed_pnl')):+.5f}", str(row.get("order_id") or "-"),
        ])
    if len(rows) == 1:
        rows.append(["No exchange-confirmed exits in this period."] + [""] * 8)
    exits_table = Table(rows, repeatRows=1, colWidths=[28*mm, 25*mm, 18*mm, 23*mm, 29*mm, 29*mm, 24*mm, 26*mm, 52*mm])
    exits_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0c2f40")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f7f8")]),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#bed2d8")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (3, 1), (7, -1), "RIGHT"),
        ("SPAN", (0, 1), (-1, 1)) if len(rows) == 2 and rows[1][0].startswith("No exchange") else ("LEFTPADDING", (0, 0), (0, 0), 4),
    ]))
    reconciliation = payload.get("reconciliation", {})
    story.extend([
        exits_table,
        Spacer(1, 6 * mm),
        Paragraph(
            f"Exchange reconciliation: {str(reconciliation.get('status', 'pending')).upper()} | "
            f"Tracked open: {reconciliation.get('tracked_open', 0)} | Exchange open: {reconciliation.get('exchange_open', 0)}",
            styles["Normal"],
        ),
        Spacer(1, 2 * mm),
        Paragraph("P&L and exits above come from Bybit closed P&L. Local events explain bot decisions and are not treated as exchange fills.", styles["Italic"]),
    ])
    document.build(story)
    return output.getvalue()


@app.get("/api/audit")
def api_audit(
    days: int = Query(7, ge=1, le=7),
    symbol: str = Query("", max_length=30),
    _: str = Depends(require_auth),
) -> dict[str, object]:
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - days * 24 * 60 * 60 * 1000
    wanted = symbol.strip().upper()
    now = time.monotonic()
    with audit_cache_lock:
        cached = audit_pnl_cache.get(days)
        if cached and cached[0] > now:
            pnl = cached[1]
        elif not settings.api_key or not settings.api_secret:
            pnl = {"available": False, "error": "Demo API credentials are not configured", "rows": []}
        else:
            pnl = _pnl_status_since(since_ms)
            audit_pnl_cache[days] = (now + 30, pnl)
    payload = _audit_payload(since_ms, wanted, pnl, generated_at_ms=now_ms, period=f"{days}-day")
    payload["days"] = days
    return payload


@app.get("/api/report/{period}.csv")
def report_csv(period: str, _: str = Depends(require_auth)) -> Response:
    payload, start, end = _report_payload(period)
    filename = f"trading-report-{period}-{end:%Y-%m-%d}.csv"
    return Response(
        _report_csv(payload, start, end),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/report/{period}.pdf")
def report_pdf(period: str, _: str = Depends(require_auth)) -> Response:
    payload, start, end = _report_payload(period)
    filename = f"trading-report-{period}-{end:%Y-%m-%d}.pdf"
    return Response(
        _report_pdf(payload, start, end),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/status")
def api_status(_: str = Depends(require_auth)) -> dict[str, object]:
    heartbeat = store.get_heartbeat()
    now_ms = int(time.time() * 1000)
    heartbeat_age = now_ms - int(heartbeat.get("updated_at_ms", 0)) if heartbeat else None
    heartbeat_details = heartbeat.get("details", {}) if heartbeat else {}
    daily_loss = heartbeat_details.get("daily_loss", {
        "available": not settings.enable_order_placement,
        "breached": False,
        "limit_fraction": settings.daily_max_net_loss,
    })
    last_scan_at_ms = int(heartbeat_details.get("last_scan_at_ms", 0) or 0)
    scanner_age = now_ms - last_scan_at_ms if last_scan_at_ms else None
    scanner_status = str(heartbeat_details.get("scanner_status", "starting"))
    scanner_stale_after_ms = max(90_000, settings.poll_seconds * 3_000)
    if scanner_status == "healthy" and scanner_age is not None and scanner_age > scanner_stale_after_ms:
        scanner_status = "stale"
    scanner = {
        "status": scanner_status,
        "last_scan_at_ms": last_scan_at_ms,
        "last_scan_age_ms": scanner_age,
        "next_scan_at_ms": int(heartbeat_details.get("next_scan_at_ms", 0) or 0),
        "last_scan_duration_ms": int(heartbeat_details.get("last_scan_duration_ms", 0) or 0),
        "scanned_symbols": int(heartbeat_details.get("scanned_symbols", 0) or 0),
        "stale_after_ms": scanner_stale_after_ms,
    }
    safety = {
        "environment": "DEMO LOCKED" if settings.demo else "LIVE ACKNOWLEDGED",
        "execution": "SIGNAL ONLY" if not settings.enable_order_placement else "ORDERS ENABLED",
        "entries": (
            "DAILY LOSS LOCK" if daily_loss.get("breached") else
            "RISK DATA UNAVAILABLE" if settings.enable_order_placement and not daily_loss.get("available") else
            "ENABLED" if store.trading_enabled() else "PAUSED"
        ),
        "risk_guard": "ACTIVE",
        "live_lock": not settings.demo and settings.live_trading_ack == "I_UNDERSTAND_LIVE_RISK",
        "max_positions": settings.max_open_positions,
        "max_total_risk": settings.max_total_open_risk,
        "daily_loss": daily_loss,
    }
    trades = store.recent_trades(100)
    pnl = _real_pnl_status()
    markets = store.market_snapshots()
    worker_online = heartbeat_age is not None and heartbeat_age < 90_000
    signal_journal = _reconciled_signal_journal(
        store.recent_signal_journal(100), markets, worker_online
    )
    return {
        "environment": "demo" if settings.demo else "live",
        "execution": "orders" if settings.enable_order_placement else "signals",
        "worker_online": worker_online,
        "heartbeat": heartbeat,
        "scanner": scanner,
        "wallet": _wallet_status(),
        "pnl": pnl,
        "today": _today_summary(trades, pnl),
        "reconciliation": heartbeat_details.get(
            "reconciliation",
            {
                "status": "pending",
                "last_checked_at_ms": 0,
                "exchange_open": 0,
                "tracked_open": 0,
                "blocked_symbols": [],
                "issues": [],
            },
        ),
        "safety": safety,
        "trading_enabled": store.trading_enabled(),
        "markets": markets,
        "decisions": store.decision_snapshots(),
        "signal_journal": signal_journal,
        "open_trades": trades,
        "events": store.recent_events(40),
        "strategy": {
            "leverage": settings.leverage,
            "risk_per_trade": settings.risk_per_trade,
            "daily_max_net_loss": settings.daily_max_net_loss,
            "max_positions": settings.max_open_positions,
            "min_reward_risk": settings.min_reward_risk,
            "volume_multiplier": settings.volume_multiplier,
            "universe_size": settings.universe_size,
            "order_retry_attempts": settings.order_retry_attempts,
        },
    }


@app.post("/api/connection-test")
def api_connection_test(_: str = Depends(require_auth)) -> dict[str, object]:
    return _connection_test()


@app.get("/api/chart/{symbol}")
def api_chart(
    symbol: str,
    interval: str = "5",
    limit: int = 160,
    _: str = Depends(require_auth),
) -> dict[str, object]:
    symbol = symbol.upper()
    if interval not in {"5", "15", "60"}:
        raise HTTPException(status_code=400, detail="Supported intervals: 5, 15, 60")
    markets = {row["symbol"]: row for row in store.market_snapshots()}
    if symbol not in markets:
        raise HTTPException(status_code=404, detail="Symbol is not in the active universe")
    limit = max(60, min(limit, 240))
    key = (symbol, interval)
    now = time.monotonic()
    with chart_cache_lock:
        cached = chart_cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
    candles = chart_gateway.candles(symbol, interval, limit)
    market = markets[symbol]
    structure = None
    if settings.v2_structure_mode == "shadow":
        try:
            structure_candles = candles if interval == "60" else chart_gateway.candles(symbol, "60", 200)
            structure = build_structure_map(structure_candles, settings.structure_parameters())
            structure["mode"] = "shadow"
        except Exception as exc:
            structure = {"mode": "shadow", "data_quality": "error", "error": str(exc)}
    payload: dict[str, object] = {
        "symbol": symbol,
        "interval": interval,
        "bias": market["bias"],
        "support": market["support"],
        "resistance": market["resistance"],
        "structure": structure,
        "candles": [
            {
                "time": candle.timestamp_ms,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            }
            for candle in candles
        ],
    }
    with chart_cache_lock:
        chart_cache[key] = (now + 15, payload)
    return payload


@app.post("/api/control")
def api_control(request: ControlRequest, _: str = Depends(require_auth)) -> dict[str, bool]:
    store.set_trading_enabled(request.enabled)
    return {"enabled": store.trading_enabled()}
