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

- Major swing markers are labelled `HH/LH swing` or `HL/LL swing` after their right-side confirmation bars close.
- BOS, CHoCH, sweep, and failed-break markers use the event candle timestamp.
- Active zone boundaries are muted; flip-watch zones turn amber.
- Protected high/low lines are amber and dashed.
- The `V2 Structure` checkbox removes every Phase 1 overlay without affecting the existing chart layers.

## Human review sample

Open the dashboard, select `BTCUSDT`, `ETHUSDT`, and one active altcoin, then inspect the `1h` chart with `V2 Structure` enabled. For each symbol verify:

1. A swing marker never appears before the required right-side bars have closed.
2. Refreshing or receiving a new candle does not move an already confirmed marker.
3. BOS/CHoCH is attached to a close beyond a confirmed swing, not merely a wick.
4. A sweep is a wick through a confirmed level followed by a close back inside.
5. Zone touch count and lifecycle visually match distinct visits.
6. The overlay is useful and not excessively noisy.

Record the reviewed symbols, candle range, and any disputed marker before approving the Phase 1 exit gate. Phase 2 must not start while this review remains unchecked.
