# Bybit Price-Action Bot + Dashboard

Single-worker Bybit USDT perpetual bot. It runs in **signal-only mode by default** and uses Bybit Demo Trading when order placement is enabled.

The repository also includes an authenticated web dashboard with real Bybit candlestick/volume charts, 1h support/resistance overlays, a selectable 10-symbol watchlist, worker health, positions, history, strategy settings, and pausing/resuming new entries.

## V2 locked roadmap

The approved V2 direction is documented as **ThesisEdge V2 — Context-Aware Price-Action Intelligence Engine**. Its locked architecture, safety boundaries, phased implementation gates, verification requirements, and change-control policy are in [`docs/THESISEDGE_V2_MASTER_PLAN.md`](docs/THESISEDGE_V2_MASTER_PLAN.md). V2 is planning-only until its phases are implemented and promoted through shadow testing; the document does not enable or modify current trading behaviour.

## Strategy implemented

1. Refresh a 10-symbol universe daily. `BTCUSDT` and `ETHUSDT` stay fixed; the other eight are ranked by 24h turnover, open interest and bid/ask spread. Listings younger than 30 days and non-standard underlyings are excluded.
2. Use closed 1h candles to classify higher-high/higher-low, lower-high/lower-low, or range structure and create confirmed swing support/resistance zones.
3. At a matching zone, inspect closed 5m candles for bullish/bearish pin bars, engulfing patterns, morning/evening stars, or tweezer bottoms/tops.
4. Require confirmation-candle volume to be at least `1.2 ×` the previous 20 closed 5m candles' average.
5. Arm the signal for the next three 5m candles. Enter only after the pattern high/low breaks and the next 1h target still offers at least `1.5R`.
6. Risk `1%` of equity per trade. At `+1R`, close 50% and move the stop to entry plus an estimated fee buffer. After `+1.5R`, trail behind recent closed 5m candles.

All thresholds are environment settings and should be changed only after reviewing demo results.

## Windows setup

Requires Python 3.10 or newer.

Double-click `start.bat` to create `.venv`, install dependencies, open the dashboard at `http://127.0.0.1:8000`, and run the bot with Bybit Demo order placement enabled. It forces Demo mode and 5× leverage; it does not authorize live trading. Press `Ctrl+C` in its terminal to stop it.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
Copy-Item .env.example .env
```

Create the API key from **Bybit mainnet account → Demo Trading → API**. Do not use a Testnet key. Put the demo key and secret in `.env`.

First verify public data and signals without sending orders:

```powershell
python -m price_action_bot --once
python -m price_action_bot
```

Run the dashboard locally in a second terminal after setting `DASHBOARD_PASSWORD` in `.env`:

```powershell
uvicorn price_action_bot.web:app --reload
```

Open `http://127.0.0.1:8000` and sign in with the dashboard username/password. Pausing from the dashboard blocks new entries while existing positions continue to be managed.

Events are recorded in `trading_bot.db`. Stop the process with `Ctrl+C`.

View the local status without contacting Bybit:

```powershell
python -m price_action_bot --status
```

Optional Telegram alerts can be enabled by setting both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`. Alerts are sent for armed signals, triggered signal-only entries, opened/closed positions and partial profit events. Alert failure never authorizes or blocks an order.

After signal-only operation is verified, enable actual **demo** orders:

```dotenv
BYBIT_DEMO=true
ENABLE_ORDER_PLACEMENT=true
```

Then restart the bot. It sets 5× leverage before entry, places the exchange-side stop-loss immediately after confirming the fill, and manages TP1/TP2/TP3 as staged exits. Configure the account/contract for isolated margin and one-way position mode in Bybit Demo Trading before enabling orders.

The worker now reconciles local open trades against all Bybit USDT perpetual positions at startup, every minute, and immediately before every entry. Quantity and exchange stop changes are synchronized for tracked positions. An exchange position with no matching local trade, or a side mismatch, is never adopted automatically: that symbol is entry-blocked and a dashboard/Telegram warning is raised for manual review.

The dashboard's **Real P&L · Bybit** section comes from Bybit's closed-PnL endpoint, not estimated candle prices or local event labels. It shows today's Asia/Dhaka net realized P&L, fees, wins/losses, win rate, and recent exchange-confirmed exits. Unrealized P&L remains separately visible in the wallet card.

Open `/audit` from the dashboard's **Trade audit** button for a filterable 1/3/7-day record. The page deliberately labels Bybit closed-PnL rows as exchange truth and local strategy trades/events as local audit data. Telegram Alerts V3 also sends each newly observed Bybit closed-PnL row once, with actual net P&L and reported trading fees, while setup expiry, invalidation, risk blocks, and trailing-stop moves receive separate lifecycle alerts.

The audit page can download **Daily CSV/PDF** and **Weekly CSV/PDF** reports. Daily reports begin at 00:00 Asia/Dhaka; weekly reports begin Monday at 00:00. CSV files are UTF-8 Excel-friendly flat exit records, while PDFs include the exchange-confirmed P&L summary, exit table, and reconciliation status.

Entry orders use a deterministic Bybit `orderLinkId` and up to `ORDER_RETRY_ATTEMPTS=3` safe attempts. Before a submit or retry, the gateway checks Bybit open/recent orders and order history for that same ID. An ambiguous timeout is reconciled first, so the bot does not blindly send a duplicate market order.

The enforced daily loss guard uses Bybit's exchange-confirmed `closedPnl` from 00:00 Asia/Dhaka. The day-start capital is estimated as current wallet balance minus today's realized P&L. At a net loss of `5%` of that capital, new entries are locked until the next Dhaka midnight; existing positions continue to receive stop, partial-profit, trailing-stop, and reversal management. If Bybit P&L or wallet data cannot be verified, order-mode entries fail closed. With three allowed positions at 1% each, maximum configured simultaneous open risk is 3%.

Chart support and resistance are confirmed 1h swing levels, not moving averages, so they do not follow every price tick. They update after a new closed 1h swing is confirmed. If live price crosses one first, the chart marks it as a broken support/resistance flip-watch level instead of silently moving the line.

## Risk-engine rollout plan (3 + 3 + 2)

The eight planned controls will not be enabled together. They are operational safety controls, not additional entry-confirmation rules. Each phase should first run in `MONITOR_ONLY` mode on Demo so its warnings and effect on trade frequency can be reviewed before enforcement.

### Phase 1 — essential protection (3)

1. Daily loss limit: implemented at `5%` of estimated start-of-day capital, reset at midnight Asia/Dhaka. It locks only new entries and fails closed if exchange risk data is unavailable.
2. Consecutive-loss cooldown: proposed starting point is three losses followed by a 60-minute entry pause.
3. Stop-loss verification: confirm the protective stop exists at Bybit after every fill; emergency-close an unprotected position.

### Phase 2 — exchange-quality guards (3)

4. Exchange reconciliation: implemented for positions at startup, every minute, and before entry; unknown or side-mismatched positions block that symbol instead of being adopted automatically. Entry retries also reconcile the deterministic client order ID against recent orders and order history.
5. Slippage/spread guard: proposed starting maximum slippage is `0.15%`; tune it from Demo execution data rather than treating it as a permanent value.
6. Liquidation-distance guard: reject a setup only when the planned stop does not have a safe buffer from liquidation at the configured leverage.

### Phase 3 — emergency controls (2)

7. Maximum drawdown lock: proposed starting threshold `5%`, requiring manual review before resuming entries.
8. Emergency kill switch: cancel pending signals and pause new entries, with a separate explicit action for closing Demo positions.

Roll out one phase at a time and measure signal count, executed trades, blocked trades, win rate and drawdown. If a control blocks normal setups too often, adjust that control from Demo evidence instead of weakening the price-action strategy.

## Tests

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests -v
```

## Live lock

Live trading is intentionally locked. Switching `BYBIT_DEMO=false` also requires this exact acknowledgement:

```dotenv
LIVE_TRADING_ACK=I_UNDERSTAND_LIVE_RISK
```

Do not enable live mode based only on a few winning demo trades. API keys must never include withdrawal permission.

## Render deployment

`render.yaml` defines two Singapore-region resources for the demo phase:

- a free FastAPI dashboard web service that also runs the trading engine in one process;
- a free Postgres database shared by the dashboard and worker.

Configure cron-job.org (or another external monitor) to request the public `/health` endpoint every 5 minutes. Render normally spins down a free web service after 15 minutes without inbound traffic. External pings reduce idle sleep, but Render can still restart or suspend a free instance, so this arrangement is for demo testing—not unattended live trading.

The free Postgres database expires after 30 days and has no backups. Upgrade or replace it before relying on retained history. A Postgres advisory lock ensures only one trading-engine loop runs if Render briefly overlaps instances during a deploy.

Before applying the Blueprint, push this repository to GitHub/GitLab/Bitbucket. In Render, fill `DASHBOARD_PASSWORD`, `BYBIT_API_KEY`, and `BYBIT_API_SECRET`. The Blueprint starts with `ENABLE_ORDER_PLACEMENT=false`; verify signals before changing the web service environment variable to `true`.
