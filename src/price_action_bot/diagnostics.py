from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def _number(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result


def diagnose_trade(audit: dict[str, Any]) -> dict[str, object]:
    """Derive a versioned, read-only verdict from captured trade evidence."""
    snapshot = audit.get("snapshot") or {}
    strategy = snapshot.get("strategy") or {}
    sizing = snapshot.get("sizing") or {}
    decision = snapshot.get("decision") or {}
    funnel = decision.get("funnel") or {}
    events = list(audit.get("events") or [])
    excursion = audit.get("excursion") or {}
    side = str(audit.get("side") or snapshot.get("side") or "")
    checks: list[dict[str, object]] = []
    issues: list[str] = []

    def check(key: str, label: str, state: str, detail: str, **evidence: object) -> None:
        checks.append({
            "key": key,
            "label": label,
            "status": state,
            "detail": detail,
            "evidence": evidence,
        })

    fill = next((row for row in events if row.get("event_type") == "FILL_CONFIRMED"), None)
    exchange_exit = next(
        (row for row in reversed(events) if row.get("event_type") == "EXCHANGE_EXIT_CONFIRMED"),
        None,
    )
    local_exit = next(
        (row for row in reversed(events) if row.get("event_type") == "CLOSED_LOCAL"),
        None,
    )
    partial_or_protected = any(
        row.get("event_type") in {
            "PARTIAL_TP", "PARTIAL_TP2", "TRAILING_STOP_MOVED", "STOP_MOVED",
        }
        for row in events
    )

    candidate = funnel.get("candidate_pool") or {}
    deep = funnel.get("deep_analysis_pool") or {}
    action = funnel.get("action_queue") or {}
    expected_frames = strategy.get("direction_timeframes") == ["4h", "1h"]
    aligned = bool(
        candidate
        and deep
        and action
        and str(candidate.get("side") or side) == side
        and str(deep.get("side") or side) == side
        and str(action.get("side") or side) == side
        and deep.get("bias_4h") == deep.get("bias_1h")
    )
    if aligned and expected_frames:
        check(
            "timeframe_alignment", "4h / 1h / 15m alignment", "pass",
            "The captured funnel selected the same direction through all three stages.",
            bias_4h=deep.get("bias_4h"), bias_1h=deep.get("bias_1h"),
            confirmation_15m=action.get("confirmation_15m"), side=side,
        )
    elif candidate or deep or action:
        issues.append("TIMEFRAME_ALIGNMENT_MISMATCH")
        check(
            "timeframe_alignment", "4h / 1h / 15m alignment", "fail",
            "Captured timeframe evidence is incomplete or directionally inconsistent.",
            candidate=bool(candidate), deep=bool(deep), action=bool(action), side=side,
        )
    else:
        check(
            "timeframe_alignment", "4h / 1h / 15m alignment", "unknown",
            "No funnel snapshot was captured for this trade.",
        )

    trigger = _number(strategy.get("trigger"))
    actual_entry = _number((fill or {}).get("payload", {}).get("actual_entry"))
    slippage_limit = _number(sizing.get("slippage_rate"))
    if fill and trigger and actual_entry and slippage_limit is not None:
        chase_fraction = (
            (actual_entry - trigger) / trigger
            if side == "Buy"
            else (trigger - actual_entry) / trigger
        )
        if 0 <= chase_fraction <= slippage_limit + 1e-12:
            check(
                "entry_execution", "Entry execution", "pass",
                "Actual fill stayed inside the captured trigger/slippage boundary.",
                trigger=trigger, actual_entry=actual_entry,
                chase_fraction=chase_fraction, allowed_slippage_fraction=slippage_limit,
            )
        else:
            issues.append("ENTRY_OUTSIDE_CAPTURED_BOUNDARY")
            check(
                "entry_execution", "Entry execution", "fail",
                "Actual fill was outside the captured trigger/slippage boundary.",
                trigger=trigger, actual_entry=actual_entry,
                chase_fraction=chase_fraction, allowed_slippage_fraction=slippage_limit,
            )
    else:
        check(
            "entry_execution", "Entry execution", "unknown",
            "Trigger, fill, or slippage evidence is missing.",
        )

    risk_budget = _number(sizing.get("risk_budget_usdt"))
    expected_loss = _number(sizing.get("expected_loss_at_stop_usdt"))
    stop_fees = _number(sizing.get("estimated_stop_fees_usdt")) or 0.0
    final_quantity = _number(sizing.get("final_quantity"))
    actual_quantity = _number((fill or {}).get("payload", {}).get("actual_quantity"))
    quantity_step = _number(sizing.get("quantity_step")) or 0.0
    if risk_budget and expected_loss is not None:
        price_risk_ok = expected_loss <= risk_budget + 1e-9
        fee_adjusted_risk = expected_loss + stop_fees
        fee_risk_ok = fee_adjusted_risk <= risk_budget * 1.02
        quantity_ok = (
            actual_quantity is None
            or final_quantity is None
            or abs(actual_quantity - final_quantity) <= max(quantity_step, 1e-12)
        )
        if price_risk_ok and fee_risk_ok and quantity_ok:
            check(
                "position_sizing", "Position sizing", "pass",
                "Quantity and modeled stop loss stayed within the captured risk budget.",
                risk_budget_usdt=risk_budget, price_risk_usdt=expected_loss,
                fee_adjusted_risk_usdt=fee_adjusted_risk,
                planned_quantity=final_quantity, actual_quantity=actual_quantity,
            )
        else:
            if not price_risk_ok:
                issues.append("PRICE_RISK_OVER_BUDGET")
            if not fee_risk_ok:
                issues.append("FEE_ADJUSTED_RISK_OVER_BUDGET")
            if not quantity_ok:
                issues.append("FILL_QUANTITY_MISMATCH")
            check(
                "position_sizing", "Position sizing", "fail",
                "Captured sizing exceeded its risk budget or the fill quantity changed materially.",
                risk_budget_usdt=risk_budget, price_risk_usdt=expected_loss,
                fee_adjusted_risk_usdt=fee_adjusted_risk,
                planned_quantity=final_quantity, actual_quantity=actual_quantity,
            )
    else:
        check(
            "position_sizing", "Position sizing", "unknown",
            "Risk-budget or expected-loss evidence is missing.",
        )

    expected_rr = _number(strategy.get("expected_net_reward_risk"))
    actual_rr = _number((fill or {}).get("payload", {}).get("net_reward_risk"))
    rr = actual_rr if actual_rr is not None else expected_rr
    if rr is None:
        check("reward_risk", "Net reward to risk", "unknown", "Net R:R evidence is missing.")
    elif rr >= 2.0:
        check(
            "reward_risk", "Net reward to risk", "pass",
            "The captured net reward-to-risk met the 1:2 minimum.", net_reward_risk=rr,
        )
    else:
        issues.append("NET_REWARD_RISK_BELOW_2")
        check(
            "reward_risk", "Net reward to risk", "fail",
            "The captured net reward-to-risk was below the 1:2 minimum.",
            net_reward_risk=rr,
        )

    mfe_r = _number(excursion.get("mfe_r"))
    mae_r = _number(excursion.get("mae_r"))
    closed_pnl = _number((exchange_exit or {}).get("payload", {}).get("closed_pnl"))
    local_reason = str((local_exit or {}).get("payload", {}).get("reason") or "")
    losing = closed_pnl is not None and closed_pnl < 0
    winning = closed_pnl is not None and closed_pnl > 0
    stop_like = "STOP" in local_reason or "SL" in local_reason
    exit_issue = bool(
        (losing or stop_like)
        and mfe_r is not None
        and mfe_r >= 1.0
        and not partial_or_protected
    )
    if not excursion:
        check(
            "price_journey", "Price journey and exit", "unknown",
            "No finalized MFE/MAE evidence is available.",
        )
    elif exit_issue:
        issues.append("PROFIT_NOT_PROTECTED_AFTER_1R")
        check(
            "price_journey", "Price journey and exit", "fail",
            "The trade reached at least +1R, then closed as a loss without captured protection.",
            mfe_r=mfe_r, mae_r=mae_r, closed_pnl=closed_pnl,
            partial_or_protected=partial_or_protected,
        )
    elif winning or partial_or_protected:
        check(
            "price_journey", "Price journey and exit", "pass",
            "The captured outcome was profitable or lifecycle protection was applied.",
            mfe_r=mfe_r, mae_r=mae_r, closed_pnl=closed_pnl,
            partial_or_protected=partial_or_protected,
        )
    elif losing or stop_like:
        check(
            "price_journey", "Price journey and exit", "pass",
            "Price never reached +1R before the captured loss; this is classified as a valid loss.",
            mfe_r=mfe_r, mae_r=mae_r, closed_pnl=closed_pnl,
        )
    else:
        check(
            "price_journey", "Price journey and exit", "unknown",
            "Price-path evidence exists, but the authoritative exchange outcome is unavailable.",
            mfe_r=mfe_r, mae_r=mae_r,
        )

    check(
        "initial_stop_quality", "Initial stop quality", "unknown",
        "ATR and full pre-entry 15m context were not archived, so tight/wide stop quality is not inferred.",
        stop=strategy.get("stop"), entry=actual_entry,
    )

    required = bool(
        snapshot
        and fill
        and excursion
        and (exchange_exit or local_exit)
        and candidate
        and deep
        and action
        and risk_budget
        and expected_loss is not None
        and rr is not None
    )
    if not required:
        verdict = "INSUFFICIENT_DATA"
        summary = "The captured record is incomplete; no conclusive trade-quality verdict is safe."
        severity = "unknown"
    elif any(code in issues for code in {
        "PRICE_RISK_OVER_BUDGET", "FEE_ADJUSTED_RISK_OVER_BUDGET", "FILL_QUANTITY_MISMATCH",
    }):
        verdict = "RISK_ISSUE"
        summary = "Position sizing or fee-adjusted stop risk exceeded the captured risk budget."
        severity = "critical"
    elif any(code in issues for code in {
        "ENTRY_OUTSIDE_CAPTURED_BOUNDARY", "NET_REWARD_RISK_BELOW_2", "TIMEFRAME_ALIGNMENT_MISMATCH",
    }):
        verdict = "EXECUTION_ISSUE"
        summary = "Entry execution or captured setup geometry violated an approved trade boundary."
        severity = "review"
    elif "PROFIT_NOT_PROTECTED_AFTER_1R" in issues:
        verdict = "EXIT_ISSUE"
        summary = "The trade gave back at least +1R without captured profit protection."
        severity = "review"
    elif losing or stop_like:
        verdict = "VALID_LOSS"
        summary = "Captured rules were respected and the loss remained a normal strategy outcome."
        severity = "good"
    else:
        verdict = "GOOD_TRADE"
        summary = "Captured execution, risk, and management evidence shows no diagnosed fault."
        severity = "good"

    known = sum(row["status"] != "unknown" for row in checks)
    return {
        "schema_version": "thesisedge.trade-diagnosis.v1",
        "policy_version": "trade-diagnosis-1.0",
        "read_only": True,
        "verdict": verdict,
        "severity": severity,
        "summary": summary,
        "confidence": "complete" if required else "partial" if known else "insufficient",
        "issue_codes": issues,
        "checks": checks,
        "authority": "diagnostic_only",
    }


def _strategy_group(audit: dict[str, Any]) -> tuple[str, str]:
    """Return an honestly labelled playbook or fallback pattern grouping."""
    snapshot = audit.get("snapshot") or {}
    decision = snapshot.get("decision") or {}
    playbook = decision.get("playbook") or snapshot.get("playbook")
    if isinstance(playbook, dict):
        selected = playbook.get("selected_playbook") or playbook
        if isinstance(selected, dict) and selected.get("playbook"):
            return str(selected["playbook"]), "playbook"
    elif playbook:
        return str(playbook), "playbook"
    pattern = (snapshot.get("strategy") or {}).get("pattern")
    if pattern:
        return str(pattern), "pattern"
    return "UNAVAILABLE", "unavailable"


def build_trade_diagnosis_report(
    audits: list[dict[str, Any]],
    *,
    minimum_group_sample: int = 5,
) -> dict[str, object]:
    """Aggregate deterministic diagnoses without inferring profitability or causality."""
    minimum_sample = max(1, int(minimum_group_sample))
    records: list[dict[str, object]] = []
    verdict_counts: Counter[str] = Counter()
    issue_trades: dict[str, list[dict[str, str]]] = defaultdict(list)
    symbol_groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    strategy_groups: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)

    for audit in audits:
        diagnosis = diagnose_trade(audit)
        verdict = str(diagnosis["verdict"])
        symbol = str(audit.get("symbol") or "UNKNOWN")
        strategy_name, strategy_kind = _strategy_group(audit)
        record: dict[str, object] = {
            "trade_id": str(audit.get("trade_id") or ""),
            "symbol": symbol,
            "side": str(audit.get("side") or ""),
            "closed_at_ms": audit.get("closed_at_ms"),
            "strategy_group": strategy_name,
            "strategy_group_kind": strategy_kind,
            "verdict": verdict,
            "confidence": diagnosis["confidence"],
            "issue_codes": list(diagnosis["issue_codes"]),
        }
        records.append(record)
        verdict_counts[verdict] += 1
        symbol_groups[symbol].append(record)
        strategy_groups[(strategy_kind, strategy_name)].append(record)
        for issue_code in diagnosis["issue_codes"]:
            issue_trades[str(issue_code)].append({
                "trade_id": str(record["trade_id"]),
                "symbol": symbol,
            })

    conclusive_verdicts = {
        "GOOD_TRADE", "VALID_LOSS", "EXECUTION_ISSUE", "RISK_ISSUE", "EXIT_ISSUE",
    }
    issue_verdicts = {"EXECUTION_ISSUE", "RISK_ISSUE", "EXIT_ISSUE"}

    def group_rows(
        groups: dict[object, list[dict[str, object]]],
        *,
        strategy: bool = False,
    ) -> list[dict[str, object]]:
        output: list[dict[str, object]] = []
        for key, rows in groups.items():
            counts = Counter(str(row["verdict"]) for row in rows)
            conclusive = sum(counts[name] for name in conclusive_verdicts)
            issues = sum(counts[name] for name in issue_verdicts)
            item: dict[str, object] = {
                "key": key[1] if strategy else key,
                "sample_count": len(rows),
                "conclusive_count": conclusive,
                "insufficient_count": counts["INSUFFICIENT_DATA"],
                "diagnosed_issue_count": issues,
                "diagnosed_issue_rate": issues / conclusive if conclusive >= minimum_sample else None,
                "sample_status": "sufficient" if conclusive >= minimum_sample else "insufficient_sample",
                "verdicts": dict(sorted(counts.items())),
            }
            if strategy:
                item["group_kind"] = key[0]
            output.append(item)
        return sorted(
            output,
            key=lambda row: (-int(row["sample_count"]), str(row["key"])),
        )

    total = len(records)
    conclusive = sum(verdict_counts[name] for name in conclusive_verdicts)
    diagnosed_issues = sum(verdict_counts[name] for name in issue_verdicts)
    recurring_issues = []
    for code, trades in issue_trades.items():
        recurring_issues.append({
            "issue_code": code,
            "count": len(trades),
            "recurring": len(trades) >= 2,
            "symbols": sorted({row["symbol"] for row in trades}),
            "trade_ids": [row["trade_id"] for row in trades],
        })
    recurring_issues.sort(key=lambda row: (-int(row["count"]), str(row["issue_code"])))

    return {
        "schema_version": "thesisedge.trade-diagnosis-report.v1",
        "policy_version": "trade-diagnosis-report-1.0",
        "read_only": True,
        "authority": "diagnostic_only",
        "minimum_group_sample": minimum_sample,
        "summary": {
            "total_captured_trades": total,
            "conclusive_trades": conclusive,
            "insufficient_data_trades": verdict_counts["INSUFFICIENT_DATA"],
            "diagnosed_issue_trades": diagnosed_issues,
            "diagnosed_issue_rate": diagnosed_issues / conclusive if conclusive else None,
            "interpretation": "sufficient" if conclusive >= minimum_sample else "insufficient_sample",
        },
        "verdict_distribution": [
            {
                "verdict": verdict,
                "count": count,
                "rate": count / total if total else None,
            }
            for verdict, count in sorted(verdict_counts.items())
        ],
        "recurring_issues": recurring_issues,
        "by_symbol": group_rows(symbol_groups),
        "by_strategy": group_rows(strategy_groups, strategy=True),
        "records": records,
        "notes": [
            "Rates describe deterministic diagnosis frequency, not profitability or causality.",
            "Group issue rates remain unavailable until the minimum conclusive sample is met.",
            "A strategy pattern is not labelled as a playbook unless the audit captured a playbook.",
        ],
    }
