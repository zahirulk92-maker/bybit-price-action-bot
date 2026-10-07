# ThesisEdge V2 Phase 2 — Dynamic Scanner Funnel Review

Phase 2 is an observation-only scanner. It broadens discovery beyond the frozen V1 watchlist, but it has no signal, sizing, risk, management, or order authority.

## Funnel contract

1. **Eligible exchange universe** — active Bybit linear USDT perpetuals with standard underlyings and at least 30 days of listing history.
2. **Fast pre-scan** — require at least 1,000,000 USDT 24h turnover, 250,000 USDT open interest, and no more than 0.15% top-of-book spread. A 25% or larger absolute 24h move is flagged as anomalous.
3. **Stable candidate pool** — keep up to 30 qualifying symbols without padding. An incumbent is displaced only when a newcomer has at least a 5% quality-score advantage. A transient filter miss receives a two-refresh exit grace before removal.
4. **Deep-analysis pool** — keep up to 15 candidates that are within 1% of a known support/resistance location or are protected because they are armed/open.
5. **Action queue** — keep up to five non-anomalous deep-analysis candidates. This queue is evidence only and cannot enter a trade.
6. **Protected tracking** — any armed/open symbol remains on the every-scan tracking schedule even if it is no longer eligible for discovery.

The shadow funnel refreshes every five minutes by default. This cadence is independent of the frozen V1 universe's daily refresh.

The quality score ranks liquidity, open interest, spread, and 24h movement. The snapshot also classifies 24h range volatility as normal, high (12% or more), or abnormal (35% or more). These are scanner diagnostics, not a probability, confidence claim, or entry signal.

## Provisional operational limits

- Candidate churn: at or below 25% between comparable refreshes.
- Scanner-funnel latency: at or below 2,000 ms, including the two exchange-universe requests measured by the worker.
- Pool sizes are ceilings, not targets. A small valid pool is correct and must never be filled with weak symbols.

## Safety boundary

- `V2_UNIVERSE_MODE` accepts only `off` or `shadow`.
- The worker still executes and evaluates the frozen V1 universe selected by the existing `select_symbols` path.
- Phase 2 failures are caught and audited without interrupting V1 universe refresh.
- Every stored snapshot declares `v2_execution_authority=false`.

## Exit review

Restart with `start.bat`, open **System → V2 Scanner Funnel**, and collect several snapshots across normal and active market periods. Approve Phase 2 only after confirming:

- no forced candidate count;
- exclusions match actual instrument/ticker conditions;
- candidate churn stays within the provisional limit or has an explainable market cause;
- funnel latency stays within the provisional limit;
- armed/open symbols remain visible in tracking during eligibility changes;
- V1 watchlist, signals, and orders remain unaffected.

## Locked review record — 2026-10-08

Phase 2 was approved and locked after two consecutive Bybit Demo shadow snapshots:

| Evidence | Snapshot 1 | Snapshot 2 |
|---|---:|---:|
| Eligible symbols | 289 | 289 |
| Candidate pool | 30 | 30 |
| Deep-analysis pool | 6 | 8 |
| Action queue | 5 | 5 |
| Candidate churn | 0% | 0% |
| Funnel latency | 1,585 ms | 1,174 ms |
| API calls | 2 | 2 |

The candidate pool remained stable while the deep-analysis pool and action queue changed with market location. Both latency samples were below the 2,000 ms limit. Database, public API, and authenticated private API diagnostics were healthy, and no new engine-cycle error appeared after the Phase 2 snapshots.

Protected armed/open tracking passed deterministic and worker-integration tests. No live protected position existed during this review sample, so that specific runtime case remains an observation item rather than an exit blocker.

Locked outcome:

- V1 universe, signals, risk, and orders remain unchanged.
- Phase 2 remains `shadow` with `v2_execution_authority=false`.
- Phase 2 parameters may not change without a new reviewed policy version.
- Phase 3 may begin only after explicit user approval.
