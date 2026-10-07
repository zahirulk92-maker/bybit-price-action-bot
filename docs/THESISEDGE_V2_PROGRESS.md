# ThesisEdge V2 — Implementation Progress

This file records implementation status only. The locked design remains in [`THESISEDGE_V2_MASTER_PLAN.md`](THESISEDGE_V2_MASTER_PLAN.md).

## Current status

| Phase | Status | Notes |
|---|---|---|
| Phase 0 — Baseline and instrumentation | Complete | Exit gate passed locally on 2026-10-07; 39/39 tests and deterministic replay checks passed |
| Phase 1 — Market Structure Map | Complete | Exit gate passed on 2026-10-08 after BTC, ETH, and ZEC 1h chart review and overlay-noise cleanup; shadow only |
| Phase 2 — Dynamic scanner funnel | Complete | Exit gate passed on 2026-10-08 after two live Demo snapshots, stable candidate pool, dynamic deep/action pools, and in-limit latency; shadow only |
| Phase 3 — Portfolio intelligence | Not started | — |
| Phase 4 — Context and playbooks | Not started | — |
| Phase 5 — Thesis, conflicts, ranking | Not started | — |
| Phase 6 — Management and recovery | Policy prototype ready; execution not started | `recovery-70-15-15.v1` pure planner + optional shadow audit; no order authority |
| Phase 7 — Shadow evaluation | Not started | — |
| Phase 8 — Limited Demo authority | Not started | — |
| Phase 9 — Live-readiness review | Not started | — |

## Phase 0 exit checklist

- [x] Current V1 policy has a stable version identifier.
- [x] Context, structure, conflict, thesis, and outcome schemas exist.
- [x] Every V2 feature initially defaulted to `off` and can only be set to `off` or `shadow`.
- [x] V2 feature modes expose no order authority.
- [x] Closed 5m/1h candle inputs are archived once and referenced by hash.
- [x] Base V1 checklist decisions are append-only, versioned, and deduplicated.
- [x] A local `--replay-v1` command can replay records without contacting Bybit.
- [x] Full regression suite passes after implementation (`39/39`).
- [x] Deterministic replay and tamper-detection verification pass on stored fixture decisions.
- [x] Final diff confirms no V1 entry, exit, sizing, or risk rule changed.

## Phase 0 verification record

- Python compile check: passed
- Existing V1 tests: `36/36` passed before implementation
- Full suite after implementation: `39/39` passed
- Added tests: replay equality, input-tamper detection, record deduplication, default feature locks, and zero V2 execution authority
- V1 strategy/risk changes: none
- V2 order authority: none
- Next eligible phase: Phase 1 — Non-Repainting Market Structure Map (approved on 2026-10-07)

## Phase 0 locked identifiers

- V1 policy: `v1.0.0-frozen`
- V2 plan: `thesisedge-v2.0-locked`
- Decision schema: `thesisedge.phase0.v1`
- V2 execution authority: `false`

## Phase 1 exit checklist

- [x] Separate major and internal confirmed swings include source and confirmation timestamps.
- [x] HH/HL, LH/LL, range, protected-high, and protected-low fields are deterministic.
- [x] Zone states include fresh, tested, weakened, broken, flip-watch, and invalid.
- [x] BOS, CHoCH, sweep, and failed-break events reference their confirmed swing level.
- [x] Every parameter is explicit, validated, serialized into the snapshot, and environment-configurable.
- [x] Structure runs only in `shadow` mode and cannot modify V1 decisions or orders.
- [x] Append-only snapshots and their closed 1h source candles are available for replay.
- [x] Dashboard has a removable `V2 Structure` debug layer.
- [x] Automated prefix replay verifies that confirmed swings never move backward.
- [x] Automated tests verify an unclosed right-side window cannot confirm a swing.
- [x] Human chart review approves the documented sample and provisional parameters.

Review procedure and parameter contract: [`THESISEDGE_PHASE1_REVIEW.md`](THESISEDGE_PHASE1_REVIEW.md).

## Phase 1 automated verification record

- Python compile check: passed
- Full regression suite at final review: `56/56` passed
- Dashboard inline JavaScript syntax: passed
- New replay checks: delayed confirmation, prefix stability, deterministic snapshot equality, persistence deduplication
- V1 strategy/risk changes: none
- Phase 1 order authority: none (`shadow` only)
- Human chart sample: `BTCUSDT`, `ETHUSDT`, and `ZECUSDT` on 1h, reviewed 2026-10-08
- Visual cleanup: nearby duplicate events and overlapping zones are clustered for display only; raw audit data is unchanged
- Remaining exit gate: none
- Next eligible phase: Phase 2 — Dynamic scanner funnel (approved and started on 2026-10-08)

## Phase 2 implementation checklist

- [x] Load and evaluate all eligible Bybit linear USDT perpetual instruments.
- [x] Apply explicit listing-age, turnover, open-interest, spread, and anomaly pre-scan rules.
- [x] Keep a non-forced candidate pool with incumbent/newcomer hysteresis.
- [x] Prioritize near-zone and protected symbols into deep-analysis and action queues.
- [x] Keep armed/open symbols in the tracking schedule even when they fail discovery filters.
- [x] Persist append-only, deduplicated scanner snapshots and exclusion metrics.
- [x] Expose candidate, deep, action, churn, latency, API-use, and exclusion evidence on the System page.
- [x] Preserve the frozen V1 10-symbol execution universe and zero V2 order authority.
- [x] Observe live Demo snapshots and approve candidate churn and scan latency against provisional limits.

Review procedure and parameter contract: [`THESISEDGE_PHASE2_REVIEW.md`](THESISEDGE_PHASE2_REVIEW.md).

## Phase 2 automated verification record

- Python compile check: passed
- Dashboard inline JavaScript syntax: passed
- Full regression suite after implementation: `66/66` passed
- V1 strategy/risk changes: none
- Phase 2 order authority: none (`shadow` only)
- Candidate count: capped, never padded to a target
- Protected tracking: armed/open symbols cannot disappear from the tracking schedule
- Provisional operational limits: churn at or below 25%; funnel latency at or below 2,000 ms
- Live Demo review: two consecutive snapshots had 289 eligible symbols, 30 stable candidates, 0% churn, two API calls, and funnel latency of 1,585 ms then 1,174 ms
- Dynamic prioritization review: deep-analysis pool changed from 6 to 8 and the action queue reprioritized while the candidate pool remained stable
- Connection review: local database, Bybit public API, and Bybit private API were healthy; no new engine-cycle error appeared after the Phase 2 snapshots
- Protected tracking: deterministic and integration tests cover armed/open symbols; no live protected position existed during the review sample
- Remaining exit gate: none
- Next eligible phase: Phase 3 — Portfolio correlation and relative strength (not started; requires explicit approval)
