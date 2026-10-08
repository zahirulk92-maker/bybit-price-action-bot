# ThesisEdge V2 Phase 4 — Regime, Location, and Three Playbooks Review

Phase 4 converts the closed-candle structure map into explicit market context and a playbook match. It is shadow-only and cannot arm a signal, alter risk, manage a position, or place an order.

## Context contract

Each symbol records:

- regime: `trend`, `range`, or `unknown`;
- bias: `bullish`, `bearish`, or `neutral`;
- location: `support_edge`, `resistance_edge`, `breakout_retest`, `mid_range`, or `unknown`;
- volatility: `compressed`, `normal`, `expanded`, `abnormal`, or `unknown`;
- data quality, current price, 1h ATR, active zone, distance to zone, and latest confirmed break.

## Approved playbooks

- `TREND_PULLBACK`: bullish trend at live support or bearish trend at live resistance.
- `RANGE_REVERSAL`: range support for Buy or range resistance for Sell.
- `BREAKOUT_RETEST`: a recent confirmed bullish break retesting former resistance, or bearish break retesting former support.

Every match includes required context, direction, machine reason code, and a close-beyond-zone invalidation. A specific breakout retest takes precedence over a generic trend location.

## Safety states

- `UNKNOWN`: structure, volatility, or candle history is insufficient.
- `NO_MATCHING_PLAYBOOK`: context is known but no approved edge exists.
- Mid-range is explicitly `NO_MATCHING_PLAYBOOK` and cannot become a setup because of a candle pattern alone.
- All snapshots set execution and risk authority to false.

## Exit review

- [x] Deterministic bullish and bearish replay passes for all three playbooks.
- [x] Every replayed match includes required context and invalidation.
- [x] Mid-range replay returns `NO_MATCHING_PLAYBOOK`.
- [x] Insufficient history returns `UNKNOWN`.
- [x] Snapshot persistence is deduplicated and the worker integration has no order call.
- [ ] Review live Demo contexts across trend, range, and breakout-retest examples.
- [ ] Confirm dashboard context/location/playbook labels against the 1h chart.

Phase 4 remains under shadow review until the live examples are approved.
