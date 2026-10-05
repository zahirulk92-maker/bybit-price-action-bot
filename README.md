# Bybit Price-Action Bot + Dashboard

Single-worker Bybit USDT perpetual bot. It runs in **signal-only mode by default** and uses Bybit Demo Trading when order placement is enabled.

The repository also includes an authenticated web dashboard for worker health, dynamic market states, open positions, history, strategy settings, and pausing/resuming new entries.

## Strategy implemented

1. Refresh a 10-symbol universe daily. `BTCUSDT` and `ETHUSDT` stay fixed; the other eight are ranked by 24h turnover, open interest and bid/ask spread. Listings younger than 30 days and non-standard underlyings are excluded.
2. Use closed 1h candles to classify higher-high/higher-low, lower-high/lower-low, or range structure and create confirmed swing support/resistance zones.
3. At a matching zone, inspect closed 5m candles for bullish/bearish pin bars, engulfing patterns, morning/evening stars, or tweezer bottoms/tops.
4. Require confirmation-candle volume to be at least `1.2 ×` the previous 20 closed 5m candles' average.
5. Arm the signal for the next three 5m candles. Enter only after the pattern high/low breaks and the next 1h target still offers at least `1.5R`.
6. Risk `0.5%` of equity per trade. At `+1R`, close 50% and move the stop to entry plus an estimated fee buffer. After `+1.5R`, trail behind recent closed 5m candles.

All thresholds are environment settings and should be changed only after reviewing demo results.

## Windows setup

Requires Python 3.10 or newer.

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

Then restart the bot. It sets 3× leverage before entry and places exchange-side stop-loss/take-profit protection immediately after confirming the fill. Configure the account/contract for isolated margin and one-way position mode in Bybit Demo Trading before enabling orders.

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

`render.yaml` defines three Singapore-region resources:

- a free FastAPI dashboard web service;
- an always-on background worker using the lowest paid worker plan;
- a free Postgres database shared by the dashboard and worker.

The free Postgres database expires after 30 days and has no backups. Upgrade it before relying on retained history. The worker is deliberately separate from the free web service because free web services sleep when idle.

Before applying the Blueprint, push this repository to GitHub/GitLab/Bitbucket. In Render, fill `DASHBOARD_PASSWORD`, `BYBIT_API_KEY`, and `BYBIT_API_SECRET`. The Blueprint starts with `ENABLE_ORDER_PLACEMENT=false`; verify signals before changing that worker environment variable to `true`.
