from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

from .models import Side


def floor_to_step(value: float, step: float) -> float:
    if step <= 0:
        raise ValueError("step must be positive")
    value_decimal = Decimal(str(value))
    step_decimal = Decimal(str(step))
    units = (value_decimal / step_decimal).to_integral_value(rounding=ROUND_DOWN)
    return float(units * step_decimal)


def position_size(
    equity: float,
    risk_fraction: float,
    entry: float,
    stop: float,
    qty_step: float,
    min_qty: float,
    max_qty: float,
    side: Side | None = None,
) -> float:
    if equity <= 0 or not 0 < risk_fraction <= 1:
        raise ValueError("Invalid equity or risk fraction")
    if side == "Buy" and stop >= entry:
        raise ValueError("Buy stop must be below entry")
    if side == "Sell" and stop <= entry:
        raise ValueError("Sell stop must be above entry")
    distance = abs(entry - stop)
    if distance <= 0:
        raise ValueError("Entry and stop cannot be equal")
    raw_qty = equity * risk_fraction / distance
    qty = floor_to_step(min(raw_qty, max_qty), qty_step)
    if qty < min_qty:
        return 0.0
    return qty


def entry_within_trigger_boundary(
    entry: float,
    trigger: float,
    side: Side,
    max_slippage_rate: float,
) -> bool:
    """Require live/fill price to stay on the trigger side without chasing it."""
    if min(entry, trigger) <= 0 or max_slippage_rate < 0:
        return False
    if side == "Buy":
        return trigger <= entry <= trigger * (1 + max_slippage_rate)
    return trigger * (1 - max_slippage_rate) <= entry <= trigger


def net_reward_risk(
    entry: float,
    stop: float,
    target: float,
    side: Side,
    taker_fee_rate: float,
    slippage_rate: float,
) -> float:
    """Return reward/risk after modeled entry, exit, and stop slippage costs."""
    if min(entry, stop, target) <= 0:
        return 0.0
    if side == "Buy":
        if stop >= entry or target <= entry:
            return 0.0
        price_reward = target - entry
    else:
        if stop <= entry or target >= entry:
            return 0.0
        price_reward = entry - target
    exit_cost_rate = taker_fee_rate + slippage_rate
    modeled_risk = abs(entry - stop) + entry * taker_fee_rate + stop * exit_cost_rate
    modeled_reward = price_reward - entry * taker_fee_rate - target * exit_cost_rate
    if modeled_risk <= 0 or modeled_reward <= 0:
        return 0.0
    return modeled_reward / modeled_risk


def one_r_price(entry: float, stop: float, side: str) -> float:
    risk = abs(entry - stop)
    return entry + risk if side == "Buy" else entry - risk

