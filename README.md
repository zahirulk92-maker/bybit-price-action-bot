# Bybit Price-Action Bot + Dashboard

Single-worker Bybit USDT perpetual bot. It runs in **signal-only mode by default** and uses Bybit Demo Trading when order placement is enabled.

The repository also includes an authenticated web dashboard with real Bybit 5m, 15m, 1h, and 4h candlestick/volume charts, 1h support/resistance overlays, a dynamic watchlist of up to 10 aligned symbols, worker health, positions, history, strategy settings, and pausing/resuming new entries.

## V2 locked roadmap

The approved V2 direction is documented as **ThesisEdge V2 — Context-Aware Price-Action Intelligence Engine**. Its locked architecture, safety boundaries, phased implementation gates, verification requirements, and change-control policy are in [`docs/THESISEDGE_V2_MASTER_PLAN.md`](docs/THESISEDGE_V2_MASTER_PLAN.md). Implementation status is tracked separately in [`docs/THESISEDGE_V2_PROGRESS.md`](docs/THESISEDGE_V2_PROGRESS.md).

Phase 0 adds append-only, deduplicated candle archives and versioned V1 decision records for deterministic replay. It does not change the 5m signal, order sizing, entry, exit, or risk rules. Structure, portfolio, playbook, thesis, and management modules remain `off`/`shadow` only. The universe module was later explicitly promoted to Demo selection authority; it still has no Live authority. Replay recent stored decisions locally without contacting Bybit:

```powershell
python -m price_action_bot --replay-v1 100
```

Phase 1 adds a closed-candle, non-repainting market-structure map in `shadow` mode. It records major/internal swings, HH/HL or LH/LL state, protected levels, zone lifecycle, BOS/CHoCH, sweeps, and failed breaks. Enable or hide its chart evidence with the `V2 Structure` layer. The provisional parameters and required human exit review are documented in [`docs/THESISEDGE_PHASE1_REVIEW.md`](docs/THESISEDGE_PHASE1_REVIEW.md).

Phase 2 began as a separate shadow scanner and was explicitly promoted on 2026-10-08 to the single active selector for Bybit Demo. It pre-screens eligible linear USDT perpetuals, ranks up to 20 liquid candidates on closed 4h direction, and keeps up to 10 whose closed 1h direction agrees. The old fixed daily selector was removed. As of 2026-10-11, closed 15m candles own both final directional confirmation and the entry pattern/next-candle trigger; fee/slippage-adjusted minimum `2R`, position-risk, and execution safety gates still decide whether a Demo order can be placed. Current metrics and exclusions are visible on the System page. The original shadow review and promotion amendment are documented in [`docs/THESISEDGE_PHASE2_REVIEW.md`](docs/THESISEDGE_PHASE2_REVIEW.md).

Phase 3 adds an observation-only Portfolio Map from closed 1h returns. It records BTC beta/correlation, dynamic correlation clusters, data-confidence labels, relative strength, and effective open/armed exposure. Healthy same-direction opportunities in one cluster are ranked so only the highest quality is selected at the normal configured 1% risk; correlated peers and unresolved data receive 0% shadow risk. It cannot change V1 or place orders. The provisional contract and pending live review are documented in [`docs/THESISEDGE_PHASE3_REVIEW.md`](docs/THESISEDGE_PHASE3_REVIEW.md).

Phase 4 adds a shadow Regime/Location context and three explicit playbooks: Trend Pullback, Range Reversal, and Breakout Retest. Every match carries its required context, direction, reason code, and invalidation; insufficient context is `UNKNOWN`, while known context without an approved edge is `NO_MATCHING_PLAYBOOK`. Mid-range patterns cannot become setups. Current evidence is visible on System and the review contract is in [`docs/THESISEDGE_PHASE4_REVIEW.md`](docs/THESISEDGE_PHASE4_REVIEW.md).

## Strategy implemented

1. Pre-screen eligible Bybit linear USDT perpetuals for listing age, turnover, open interest, spread, and abnormal movement; then rank up to 20 on closed 4h directional strength. The pool is a ceiling and is never padded with weak symbols.
2. Keep up to 10 symbols only when closed 1h structure agrees with the 4h direction and a relevant confirmed 1h support/resistance zone exists.
3. Require closed 15m confirmation in the same direction: the 5-candle mean must be on the correct side of the 20-candle mean and the latest close must continue in that direction.
4. For a symbol whose closed 4h and 1h direction agree at the matching 1h zone, inspect closed 15m candles for bullish/bearish pin bars, engulfing patterns, morning/evening stars, or tweezer bottoms/tops.
5. Require confirmation-candle volume to be at least `1.2 ×` the previous 20 closed 15m candles' average.
6. Arm the signal for up to four closed 15m candles. Enter only after a later 15m candle breaks the pattern high/low and the next 1h target still offers at least `2R` after modeled fees and slippage.
7. Risk `1%` of equity per trade. At `+1R`, close 50% and move the stop to entry plus an estimated fee buffer. After `+1.5R`, trail behind recent closed 5m candles.

All thresholds are environment settings and should be changed only after reviewing demo results.

The active timeframe funnel may perform dozens of read-only market-data requests per refresh. Its operational latency threshold is 15 seconds; exceeding that threshold is surfaced as attention and never bypasses an entry or risk gate.

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

Every new Demo entry attempt also receives a stable `trade_id`. Its 4h/1h/15m
decision evidence and complete position-sizing calculation are stored once in
`trade_audits` and are never overwritten. `trade_lifecycle_events` appends the
setup, order, fill, protection, partial-exit, stop-change, local-close, and
Bybit-confirmed exit events under that same ID. Bybit exit identities are
persistently deduplicated, so a worker restart does not turn one partial exit
into multiple audit events. These tables are the data foundation for the
planned clickable Trade Replay UI; they do not add order authority or alter the
entry, exit, or risk rules.

While a captured trade is open, closed 5m management candles are appended to
`trade_price_candles`. At close, `trade_excursions` records side-aware maximum
favorable excursion (MFE), maximum adverse excursion (MAE), their timestamps,
percentage and initial-risk multiples, target progress, duration, and a data
quality label. This preserves how far price moved in favor before a stop or
other exit; the replay chart and human-readable verdict remain later UI steps.

Authenticated read-only forensic endpoints expose the captured evidence:
`GET /api/trades/audits`, `GET /api/trades/{trade_id}/audit`,
`GET /api/trades/{trade_id}/events`, and
`GET /api/trades/{trade_id}/candles`. They read only the local audit database
and never call an order create, modify, or close operation.

Closed local trades with a captured `trade_id` link to `/trades/{trade_id}`.
The authenticated read-only replay renders the archived 5m candle path with
entry, initial stop, target, exit, MFE/MAE levels and lifecycle markers, plus
the immutable thesis and position-sizing inputs. Legacy rows remain visible but
are explicitly labelled unavailable when their evidence was never captured.

The replay also shows a deterministic `trade-diagnosis-1.0` verdict generated
only from captured evidence. It checks timeframe alignment, entry slippage,
net 1:2 reward-to-risk, risk-budget/quantity arithmetic, and whether a losing
trade gave back at least +1R without captured protection. Verdicts are
`GOOD_TRADE`, `VALID_LOSS`, `EXECUTION_ISSUE`, `RISK_ISSUE`, `EXIT_ISSUE`, or
`INSUFFICIENT_DATA`. The diagnosis is read-only and has no strategy, risk-gate,
or order authority. It explicitly leaves initial-stop tight/wide quality
unknown when pre-entry ATR/context was not archived.

The Trade Audit page also exposes the read-only
`trade-diagnosis-report-1.0` cross-trade report. It ranks recurring issue codes,
shows verdict distribution, and compares symbols plus captured playbooks or
fallback strategy patterns. Group issue rates stay unavailable until at least
five conclusive captured trades exist, and incomplete legacy evidence remains
separate from conclusive diagnoses. `GET /api/trades/diagnosis-report` exposes
the same local-only report for a selected period and optional symbol; it does
not infer profitability or causality and has no trading authority.

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
