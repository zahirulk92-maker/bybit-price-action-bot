# ThesisEdge V2 — Master Plan and Implementation Roadmap

**Product:** Bybit Price-Action Bot  
**V2 name:** **ThesisEdge V2**  
**Subtitle:** Context-Aware Price-Action Intelligence Engine  
**Plan status:** **LOCKED**  
**Locked on:** 2026-10-07  
**Execution status:** Planning only; this document does not enable V2 trading logic.

## 1. Purpose

ThesisEdge V2 will evolve the current pattern-driven bot into a context-aware trading system. It must identify market regime, map structure and location, select an appropriate playbook, build a testable trade thesis, resolve conflicting evidence, rank opportunities across a dynamic universe, and manage an open trade according to its original thesis.

V2 is not intended to predict an unpredictable market. Its purpose is to make disciplined, explainable decisions under uncertainty while preserving deterministic risk and execution controls.

## 2. Locked design principles

1. **Analysis may adapt; safety may not.** Risk limits, exchange integrity, duplicate-order protection, reconciliation, and emergency controls remain deterministic.
2. **Context precedes pattern.** Analysis order is: regime → structure → location → event → confirmation → entry.
3. **No forced trade and no forced symbol count.** `NO TRADE`, `WAIT`, and a smaller eligible universe are valid outcomes.
4. **Every trade starts as a thesis.** A thesis records why the setup exists, what confirms it, what invalidates it, when it expires, and how it should be managed.
5. **Portfolio risk is not position count.** Correlated same-direction positions are treated as shared exposure.
6. **No live self-modification.** V2 may collect evidence and propose changes; it may not rewrite weights, thresholds, playbooks, code, or safety settings by itself.
7. **No fake precision.** Setup readiness, historical probability, expectancy, and data confidence are separate values. Unknown stays `Unknown`.
8. **Closed-candle and non-repainting decisions.** Signals use only data that would have been available at decision time.
9. **Deterministic replay.** The same version, configuration, and market snapshot must produce the same structured decision.
10. **LLM outside the order path.** An LLM may explain, summarize, and audit; it may not directly authorize, size, submit, or close an order.

## 3. Scope boundaries

### V2 will do

- Dynamically scan eligible Bybit USDT perpetuals.
- Maintain a stable, quality-filtered candidate universe.
- Detect market regime, structure, location, liquidity events, volatility, and data quality.
- Compare correlated opportunities and select the strongest portfolio-compatible setup.
- Support three initial playbooks: Trend Pullback, Range Reversal, and Breakout Retest.
- Create versioned, expiring trade theses with explicit conflicts and invalidation.
- Manage positions according to the entry playbook and original thesis.
- Run in shadow mode before receiving any execution authority.
- Record decisions, rejected alternatives, MAE/MFE, slippage, fees, and exchange-confirmed outcomes.

### V2 will not do

- Promise profit or treat a readiness score as win probability.
- Trade from chart screenshots or free-form LLM opinions.
- Chase a missed entry.
- Infer unavailable data.
- Adopt an unknown exchange position automatically.
- Override daily-loss, exposure, live-lock, reconciliation, or stop-protection controls.
- Change production policy from a small recent sample.
- enable live trading automatically.

## 4. Target architecture

```text
Eligible Exchange Universe
          ↓
Dynamic Scanner Funnel
          ↓
Correlation & Portfolio Map
          ↓
Market Structure Map
          ↓
Regime + Location Engine
          ↓
Playbook Matcher
          ↓
Trade Thesis Builder
          ↓
Conflict + Anomaly Resolver
          ↓
Opportunity Ranker / Queue
          ↓
Hard Safety and Execution Gate
          ↓
Order Execution + Exchange Reconciliation
          ↓
Playbook-Aware Trade Manager
          ↓
Outcome, Counterfactual and Shadow Learning Store
```

This is one structured decision system with modular analyzers, not a collection of independent agents voting with uncontrolled authority.

## 5. Core modules

### 5.1 Dynamic Scanner Funnel

The scanner separates market coverage from expensive analysis:

1. **Exchange universe:** all eligible Bybit linear USDT perpetuals.
2. **Fast pre-scan:** listing age, turnover, spread, open interest, abnormal moves, and data availability.
3. **Candidate pool:** normally 20–30 instruments, but never forced to a fixed count.
4. **Deep-analysis pool:** normally the best 10–15 candidates near meaningful locations.
5. **Action queue:** normally the top 3–5 non-duplicative opportunities.

Universe stability rules prevent churn. An existing candidate is not removed for a marginal rank change; a newcomer must show a meaningful quality advantage. Symbols with an armed thesis or open position remain tracked regardless of ranking.

### 5.2 Correlation and Portfolio Map

V2 measures rolling return correlation, directional beta to BTC, and current market behaviour. Fixed labels may seed the model, but live clustering must be evidence-driven.

Portfolio decisions use:

- same-side correlated exposure;
- BTC and market-basket alignment;
- current open and armed risk;
- relative strength/weakness inside each cluster;
- symbol liquidity and execution quality.

**Initial locked policy:** when multiple highly correlated same-direction setups compete, select the highest-quality candidate at normal configured risk and queue or reject the others. V2 will not introduce confidence-based position sizing until shadow evidence supports a separately approved rule.

### 5.3 Market Structure Map

The structure engine must represent:

- major and internal confirmed swings;
- HH/HL, LH/LL, and range state;
- protected highs/lows;
- BOS and CHoCH events;
- fresh, tested, weakened, broken, and flip-watch zones;
- liquidity sweeps and failed breakouts;
- zone age, touch count, width, and invalidation.

Exact swing and zone parameters are configurable research decisions, not assumptions hidden in code. All structure objects store their source candles and confirmation time so replay can prove that they did not repaint.

### 5.4 Regime and Location Engine

Each symbol receives a structured context:

```text
regime: trend | range | breakout | compression | chaotic | unknown
bias_1h: bullish | bearish | neutral
location: support | resistance | mid_range | extended | unknown
volatility: low | normal | high | abnormal
liquidity_event: none | high_sweep | low_sweep | failed_break
market_alignment: aligned | conflicting | unknown
data_quality: healthy | stale | incomplete
```

An `unknown` or contradictory state lowers authority; it is not silently converted into a neutral score.

### 5.5 Initial Playbook Library

#### Trend Pullback

- Clear 1h directional structure.
- Pullback into a valid fresh or acceptable tested zone.
- Entry is not extended and does not chase price.
- Closed 5m rejection/reclaim confirms continuation.
- Management follows continuation structure.

#### Range Reversal

- Confirmed 1h range with usable boundaries.
- Setup occurs at an edge, never in the middle.
- Sweep/rejection and reclaim are preferred evidence.
- Targets and management respect internal range structure.

#### Breakout Retest

- Confirmed close outside a meaningful level.
- Breakout quality and anomaly checks pass.
- No immediate chase.
- The broken level is retested and held before confirmation.
- Failed reclaim invalidates quickly.

No fourth playbook may be added merely to increase trade frequency. It requires its own hypothesis, replay tests, shadow sample, and approval.

### 5.6 Trade Thesis Contract

Every candidate thesis stores at least:

```text
thesis_id and policy_version
symbol, direction, playbook, created_at, expires_at
market regime and 1h bias
structure and zone references
location and liquidity event
required confirmation and entry trigger
ideal entry and maximum chase distance
stop, invalidation, targets, and post-fee R:R
supporting evidence and conflicting evidence
setup readiness, historical probability, expectancy, data confidence
correlation cluster and effective portfolio impact
decision: approve | wait | reject | conflicted | expired | missed
reason codes and human-readable explanation
```

Thesis expiry, missed-entry rules, and invalidation are first-class states. An expired or missed thesis cannot be revived without a new thesis ID.

### 5.7 Decision Policy

The decision sequence is:

1. **Hard veto:** stale data, exchange mismatch, unknown position, invalid stop, expired thesis, missed entry, daily-loss lock, exposure breach, abnormal spread/slippage, or market-shock mode.
2. **Playbook requirements:** correct regime, location, confirmation, logical invalidation, and acceptable post-fee R:R.
3. **Soft evidence:** volume quality, candle quality, relative strength, BTC alignment, liquidity sweep, volatility, and multi-timeframe agreement.
4. **Conflict resolution:** important opposing evidence is preserved and may produce `WAIT` or `CONFLICTED` even when supporting evidence is strong.

Soft evidence must not recreate sharp arbitrary gates such as rejecting `1.19×` volume only because a nominal threshold is `1.20×`. Critical integrity conditions remain hard gates.

### 5.8 Opportunity Ranker and Queue

The ranker compares only compatible, valid theses using:

- playbook completeness;
- zone and structure quality;
- relative strength/weakness;
- post-fee reward-to-risk;
- entry freshness and distance;
- liquidity/spread/slippage;
- data confidence;
- correlation and portfolio impact;
- thesis expiry urgency.

`First signal wins` is prohibited. Rankings can change before entry, but the versioned reasons must remain auditable.

### 5.9 Hard Risk and Execution Boundary

Current baseline controls remain outside adaptive analysis:

- 1% configured risk per trade;
- 3% configured maximum simultaneous nominal open risk;
- 5% daily net-loss lock based on exchange-confirmed P&L;
- maximum position count;
- deterministic order IDs and duplicate protection;
- stop placement and verification;
- exchange reconciliation;
- live-trading acknowledgement lock;
- emergency entry pause and kill controls.

Correlation controls may reduce the number of accepted trades, but the analysis engine may never increase these limits. Any future risk-size change requires a separate approved risk specification.

### 5.10 Playbook-Aware Trade Manager

The open trade retains its original thesis and policy version. Management states are explicit:

```text
OPEN → PROTECTED → TP1/TP2 → TRAILING → CLOSED
                     ↘ THESIS_INVALIDATED / EMERGENCY_EXIT
```

- A single opposite candle does not force an exit.
- Early exit requires thesis invalidation or an approved combination of opposite structure break, meaningful close, and adverse participation.
- Trend, range, and breakout trades use their own management policy.
- Emergency exchange/data integrity rules remain independent of price-action judgement.
- Restarts restore the original thesis, partial state, stop basis, and management policy from persistent storage.

### 5.11 Market Shock Mode

V2 does not need to understand every news headline. It must detect market impact:

- extreme candle range relative to recent volatility;
- extreme volume percentile;
- rapid BTC move or cross-market liquidation behaviour;
- abnormal spread/slippage;
- stale, divergent, or missing feeds;
- exchange instability.

Shock mode pauses new entries, preserves position protection, raises an alert, and waits for deterministic stabilization criteria.

### 5.12 Learning, Audit, and Governance

V2 records approved, rejected, waiting, missed, and alternative decisions. Outcomes include:

- exchange-confirmed net P&L and fees;
- MAE and MFE;
- entry and exit efficiency;
- slippage and time in trade;
- rule/thesis compliance;
- outcome in R;
- counterfactual outcome for shadow alternatives where it can be measured without look-ahead leakage.

Performance is segmented by playbook, regime, symbol class, direction, volatility, and policy version. Recent results cannot overwrite production policy automatically. Proposed changes require sufficient samples, walk-forward validation, documented trade-frequency impact, and human approval.

## 6. Confidence vocabulary

The UI, journal, and Telegram messages must not combine these concepts:

- **Setup readiness:** percentage of the selected playbook's observable requirements currently present.
- **Historical probability:** calibrated result from comparable out-of-sample observations; otherwise `Unknown`.
- **Expected value:** historical average R after estimated costs for comparable observations; otherwise `Unknown`.
- **Data confidence:** quality and completeness of the current inputs.
- **Decision:** `APPROVE`, `WAIT`, `REJECT`, `CONFLICTED`, `EXPIRED`, or `MISSED`.

## 7. Phase-wise implementation roadmap

No phase receives execution authority merely because its code is complete. Each phase has an exit gate and can be disabled independently.

### Phase 0 — Baseline Freeze and Decision Instrumentation

**Goal:** preserve V1 behaviour and create a trustworthy comparison baseline.

Deliverables:

- Tag/freeze the current V1 policy and configuration.
- Add policy-version and decision-version identifiers.
- Define schemas for context, structure, thesis, conflicts, ranking, and outcomes.
- Record the data snapshot used for every decision.
- Add feature flags for every V2 module; all default to shadow/off.
- Create deterministic replay fixtures from stored closed candles.

Exit gate:

- Existing V1 tests pass unchanged.
- Replaying the same snapshot produces the same V1 result.
- No V2 component can submit or alter an order.

### Phase 1 — Non-Repainting Market Structure Map

**Goal:** objectively represent swings, zones, breaks, and liquidity events.

Deliverables:

- Major/internal confirmed swing model.
- Zone lifecycle: fresh, tested, weakened, broken, flip-watch, invalid.
- BOS, CHoCH, sweep, and failed-break events.
- Source-candle and confirmation-time audit fields.
- Visual debug overlay and replay tests.

Exit gate:

- No unclosed candle influences a confirmed structure event.
- Historical replay shows no backward-moving confirmed swing.
- Human chart review agrees on a documented sample before parameters are locked.

### Phase 2 — Dynamic Universe and Scanner Funnel

**Goal:** expand coverage without forcing low-quality symbols or overloading deep analysis.

Deliverables:

- Eligible exchange-universe loader.
- Fast liquidity, spread, age, volatility, and anomaly pre-scan.
- Stable candidate pool with entry/exit hysteresis.
- Near-zone prioritization and scan scheduling.
- Metrics for exclusions, scan latency, API use, and candidate churn.

Exit gate:

- No forced universe count.
- Armed/open symbols cannot disappear from tracking.
- Candidate churn and scan latency stay within agreed operational limits.

### Phase 3 — Portfolio Correlation and Relative Strength

**Goal:** prevent multiple symbols from creating one hidden BTC-directional bet.

Deliverables:

- Rolling BTC beta and return-correlation features.
- Dynamic correlation clusters with data-confidence labels.
- Relative strength/weakness ranking inside clusters.
- Effective exposure view for open and armed theses.
- Highest-quality-per-cluster selection in shadow mode.

Exit gate:

- Replay proves that three correlated longs are identified as shared exposure.
- Missing/weak correlation data cannot be presented as certainty.
- The module never increases risk or position size.

### Phase 4 — Regime, Location, and Three Playbooks

**Goal:** build structured context and match only the appropriate trading behaviour.

Deliverables:

- Regime/location/volatility/data-quality context object.
- Trend Pullback matcher.
- Range Reversal matcher.
- Breakout Retest matcher.
- Explicit `UNKNOWN` and `NO MATCHING PLAYBOOK` states.
- Per-playbook replay suite for bullish and bearish cases.

Exit gate:

- Every match identifies its required context and invalidation.
- Mid-range patterns do not masquerade as edge setups.
- Bullish and bearish behaviour is symmetric where intended.

### Phase 5 — Thesis, Conflict, Expiry, and Opportunity Queue

**Goal:** turn a matched setup into an explainable, time-bounded decision.

Deliverables:

- Complete Trade Thesis contract and persistence.
- Hard-veto, required-evidence, soft-evidence, and conflict layers.
- Expired, missed, and do-not-chase states.
- Cross-symbol opportunity ranking and queue.
- Separate readiness/probability/expectancy/data-confidence UI fields.
- Human-readable dashboard and Telegram explanations.

Exit gate:

- Every outcome has machine reason codes and a readable explanation.
- Conflicting evidence remains visible instead of being averaged away.
- `First signal wins` is absent.

### Phase 6 — Playbook-Aware Management and Recovery

**Goal:** manage positions according to the thesis that created them.

Deliverables:

- Versioned management policy per playbook.
- Hold/protect/partial/trailing/invalidation/emergency state machine.
- Multi-evidence reversal exit for long and short positions.
- Restart recovery of thesis and management state.
- Market Shock Mode and stabilization rules.

Locked recovery policy (`recovery-70-15-15.v1`):

- The configured 1% risk is an all-in loss ceiling: price loss, entry/exit taker fees,
  modeled slippage, and exchange quantity-step rounding are included before entry.
- TP1 closes 70%. It is never below 1.5R and is extended when necessary so the
  modeled net TP1 realization recovers the full initial 1R risk budget.
- A setup is rejected when friction consumes more than 25% of the all-in risk,
  when recovery would lie beyond TP2, or when the nearest confirmed 1h obstacle
  comes before the recovery price.
- Only an exchange-confirmed TP1 fill may move the remaining 30% stop to
  break-even plus the modeled fee/slippage buffer.
- TP2 is 2R and closes 15%. Only an exchange-confirmed TP2 fill may move the
  final 15% runner stop to TP1.
- The runner has no fixed TP3. Its stop advances only from confirmed closed 5m
  swings, never from the still-forming candle, and never back through TP1.
- The policy version and original stop/risk basis are persisted with the trade;
  a restart cannot silently substitute another management policy.

Exit gate:

- A restart does not change the management policy of an open trade.
- One opposite candle cannot independently close a healthy thesis.
- Missing protection or reconciliation failure still follows emergency policy.

### Phase 7 — Parallel Shadow Evaluation

**Goal:** compare V1 actions with V2 decisions without changing orders.

Deliverables:

- V1-versus-V2 decision comparison journal.
- Counterfactual tracking for approved/rejected/wait alternatives.
- MAE/MFE, fees, slippage, R outcome, and decision-quality reports.
- Playbook/regime/policy-version performance segmentation.
- False rejection, false approval, conflict, and trade-frequency analysis.

Minimum review sample before authority discussion:

- at least 200 completed/expired V2 theses;
- adequate bullish and bearish representation;
- at least 30 resolved observations for any playbook/regime segment used to make a claim;
- multiple volatility conditions and no single-day result dominating conclusions.

Exit gate:

- Walk-forward/out-of-sample results are reviewed.
- Trade frequency, expectancy, drawdown, and failure modes are documented.
- No unexplained material mismatch remains between replay and shadow decisions.

### Phase 8 — Limited Demo Authority

**Goal:** allow V2 to approve Demo entries under strict rollback controls.

Deliverables:

- V2 entry authority feature flag, Demo-only.
- Highest-quality-per-correlation-cluster enforcement.
- V1 fallback/rollback path.
- Dashboard comparison and reason display.
- Alert for every veto, conflict, authority change, and rollback.

Rollout sequence:

1. One playbook and one position maximum.
2. Expand to the second/third playbook only after review.
3. Expand portfolio concurrency only after correlation controls are verified.

Exit gate:

- Minimum agreed Demo duration and sample are completed.
- Exchange reconciliation, protection, and recovery drills pass.
- No unresolved duplicate, unknown-position, or unprotected-position incident exists.

### Phase 9 — Production Hardening and Live Readiness Review

**Goal:** decide whether V2 deserves a separate, tightly limited live pilot.

Deliverables:

- Failure injection for API timeout, stale feed, restart, exchange mismatch, and partial fill.
- Performance/latency and API-rate-limit verification.
- Policy/configuration migration and rollback procedure.
- Security and secret-handling review.
- Live-pilot proposal with independently approved capital and risk limits.

Exit gate:

- This phase ends in a review decision, not automatic live activation.
- Live mode remains locked unless a separate explicit approval is recorded.

## 8. Required verification strategy

Every module requires:

- unit tests for boundaries and symmetry;
- deterministic replay tests;
- no-look-ahead/non-repainting tests;
- stale/missing/bad-data tests;
- long and short cases;
- restart and persistence tests where stateful;
- shadow metrics before enforcement;
- observable reason codes and versioned configuration.

Backtests are necessary but not sufficient. Promotion decisions use replay, shadow forward testing, exchange-confirmed Demo execution, fees, slippage, and operational failures.

## 9. Success metrics

V2 is evaluated on more than win rate:

- expectancy in R after costs;
- maximum drawdown and correlated-loss events;
- profit factor and payoff ratio;
- MAE/MFE and entry/exit efficiency;
- slippage and protection reliability;
- false approval and false rejection rates;
- duplicate and expired signal rate;
- candidate-universe stability;
- playbook/regime sample coverage;
- replay determinism;
- worker, data, and exchange integrity;
- percentage of decisions with complete explanations.

## 10. Primary risk register

1. Overfitting structure, playbook, or ranking parameters.
2. Repainting swings or leaking future candle information.
3. Treating readiness as probability.
4. Hiding conflict inside an average score.
5. Underestimating correlated BTC exposure.
6. Forcing universe size or loosening quality merely to obtain trades.
7. Overreacting to recent wins/losses.
8. Mismanaging old positions after restart or policy upgrade.
9. Ignoring fees, funding, spread, slippage, and partial fills.
10. Giving an LLM or adaptive module order/risk authority.
11. Adding playbooks faster than they can be independently validated.
12. Allowing UI explanations to diverge from the actual machine decision.

## 11. Change-control rule

This document is the locked V2 baseline. Changes require:

1. a written change proposal;
2. the gap or evidence motivating it;
3. affected modules and phases;
4. safety and trade-frequency impact;
5. test and rollback requirements;
6. explicit human approval;
7. a dated change-log entry and plan-version increment.

Implementation details and evidence-based numeric parameters may be refined inside this architecture. The safety boundary, thesis-first model, correlation-aware selection, non-repainting requirement, shadow-first rollout, and prohibition on autonomous live self-modification cannot be silently weakened.

## 12. Locked implementation order

```text
P0 Baseline + instrumentation
 → P1 Structure
 → P2 Dynamic universe
 → P3 Portfolio intelligence
 → P4 Context + playbooks
 → P5 Thesis + conflicts + ranking
 → P6 Management + recovery
 → P7 Shadow evaluation
 → P8 Limited Demo authority
 → P9 Live-readiness review
```

No phase should be skipped to obtain more signals sooner.
