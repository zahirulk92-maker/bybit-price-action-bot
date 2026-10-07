# ThesisEdge V2 Phase 1 — Human Chart Review

Phase 1 runs in **shadow mode only**. Its swings, zones, and events are debug evidence; they do not change V1 entries, exits, sizing, risk, or order execution.

## Provisional parameter contract

| Parameter | Initial value | Meaning |
|---|---:|---|
| Internal swing width | 2 closed bars each side | Faster local structure |
| Major swing width | 5 closed bars each side | Higher-confidence structure |
| ATR period | 14 | Zone-width and invalidation normalization |
| Zone half-width | max(0.15 ATR, 0.05% price) | Prevents a swing from becoming a single-price line |
| Weakened threshold | 2 distinct visits | Repeated tests reduce zone quality |
| Break buffer | 0 ATR | First research baseline; close must cross the zone boundary |
| Invalidation buffer | 0.50 ATR | Marks a broken zone invalid after continuation |
| Failed-break window | 3 closed bars | Return through the swing level inside this window |
| Debug zones | latest 12 | Limits overlay noise; full swings/events remain in snapshots |

These are **not locked trading parameters**. Approval only confirms that the map is visually reasonable enough to begin shadow research.

## What the overlay shows

- Major swing markers are labelled `HH/LH` or `HL/LL` after their right-side confirmation bars close.
- BOS, CHoCH, sweep, and failed-break markers use the event candle timestamp.
- Active zone boundaries are muted; flip-watch zones turn amber. Nearby overlapping zones are visually clustered with an `×N` count while the raw zones remain in the snapshot.
- Protected high/low lines are amber and dashed.
- `Zones`, `Major swings`, and `Events` can be toggled independently without changing stored structure data.

## Human review sample

Open the dashboard, select `BTCUSDT`, `ETHUSDT`, and one active altcoin, then inspect the `1h` chart with `V2 Structure` enabled. For each symbol verify:

1. A swing marker never appears before the required right-side bars have closed.
2. Refreshing or receiving a new candle does not move an already confirmed marker.
3. BOS/CHoCH is attached to a close beyond a confirmed swing, not merely a wick.
4. A sweep is a wick through a confirmed level followed by a close back inside.
5. Zone touch count and lifecycle visually match distinct visits.
6. The overlay is useful and not excessively noisy.

Record the reviewed symbols, candle range, and any disputed marker before approving the Phase 1 exit gate. Phase 2 must not start while this review remains unchecked.

## Review record — 2026-10-08 (Asia/Dhaka)

- Reviewed `BTCUSDT`, `ETHUSDT`, and active altcoin `ZECUSDT` on the `1h` chart.
- Confirmed swings remained fixed across refreshed BTC samples; automated prefix replay independently verifies the same non-repainting invariant.
- BOS/CHoCH, sweep, and failed-break markers were visually attached to relevant confirmed structure areas.
- The first sample exposed excessive nearby SWEEP labels and overlapping zone lines.
- Display-only cleanup now keeps event priority `CHoCH > BOS > FAILED_BREAK > SWEEP`, collapses repeated nearby events, and clusters nearby zones. Raw events and zones remain unchanged for audit and replay.
- Post-cleanup ETH and ZEC samples were readable, retained the important events, and displayed clustered zone counts.
- Disputed markers remaining: none for the Phase 1 shadow-research gate.

**Result:** Human chart review passed. The provisional parameters are approved for continued shadow research only; this approval grants no V2 order authority.
