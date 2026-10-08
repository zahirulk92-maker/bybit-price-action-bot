# ThesisEdge V2 Phase 3 — Portfolio Correlation and Relative Strength Review

Phase 3 is an observation-only portfolio map. It measures whether apparently separate setups are actually the same directional market bet. It cannot change a signal, position size, risk limit, trade-management decision, or order.

## Provisional contract

- Inputs are closed 1h candles for BTC, Phase 2 deep-analysis symbols, armed signals, and open positions.
- Default lookback is 72 closed hourly returns.
- A result needs at least 36 aligned returns; 36–59 is labelled `weak`, and 60 or more is `healthy`.
- Positive return correlation of `0.70` or greater joins symbols into a dynamic cluster.
- Every symbol records rolling BTC correlation, BTC beta, basket correlation, lookback return, cluster, data confidence, and relative-strength rank.
- Open and armed theses are summarized as effective gross, long, short, and same-cluster same-direction exposure.
- In each healthy same-direction cluster, the highest-quality opportunity is `SELECTED` at the configured normal 1% risk and the rest are `QUEUED_CORRELATED` at 0% recommended risk.
- Weak, missing, or direction-unknown comparisons are `UNRESOLVED_DATA` at 0% recommended risk.
- Phase 3 never increases risk and has no execution authority.

## Operational scope

The 15-minute portfolio refresh deliberately analyzes the Phase 2 deep pool plus every armed/open symbol and BTC. This avoids downloading hourly history for the entire exchange universe while preserving every symbol that could create real exposure. Per-symbol history failures are recorded and do not interrupt V1 or discard the remaining portfolio snapshot.

## Automated exit evidence

- Deterministic replay groups BTC, ETH, and SOL correlated longs as one shared directional exposure.
- The same replay selects only the highest-quality setup, queues the others, and never recommends more than configured 1% risk.
- Relative strength is ranked inside each correlation cluster.
- Weak and insufficient history remain explicitly labelled and cannot produce a risk recommendation.
- A partial snapshot survives an individual candle-history timeout.
- Persistence is append-only and deduplicated by snapshot fingerprint.
- Dashboard/API expose clusters, confidence, effective exposure, shadow selection, latency, API use, and data errors.

## Human exit review

Restart with `start.bat`, open **System → V2 Portfolio Map**, and collect at least two snapshots separated by the configured 15-minute refresh. Before Phase 3 can be locked, verify:

- [x] BTC beta/correlation and confidence labels are plausible for the displayed symbols.
- [x] Correlated assets appear together while unrelated or insufficient-data symbols are not presented as certain.
- [x] Relative-strength ordering is plausible inside at least one multi-symbol cluster.
- [x] Any simultaneous open/armed same-direction correlated theses appear as shared exposure, or the empty state is truthful if none exist.
- [x] Only one healthy same-direction opportunity per cluster is selected; peers are queued.
- [x] Selected risk is never above 1%, queued/unresolved risk is 0%, and the panel states shadow/no authority.
- [x] Refresh latency and API usage remain operationally acceptable for two consecutive snapshots.
- [x] No new engine-cycle failure is caused by Phase 3.

Phase 3 was approved and locked on 2026-10-08. The reviewed Demo snapshots both had seven healthy symbols, no weak/insufficient data, no candle errors, and latency of 1,221 ms then 963 ms. BTC, SOL, ETH, XRP, and DOGE formed a plausible correlated cluster; BTC was selected while same-cluster action opportunities were queued at 0%. No position or armed signal existed, so the 0% effective-exposure state was truthful. The user's explicit instruction to start Phase 4 approved the Phase 3 exit.
