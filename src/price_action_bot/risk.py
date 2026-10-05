from __future__ import annotations

from decimal import Decimal, ROUND_DOWN


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
) -> float:
    if equity <= 0 or not 0 < risk_fraction <= 1:
        raise ValueError("Invalid equity or risk fraction")
    distance = abs(entry - stop)
    if distance <= 0:
        raise ValueError("Entry and stop cannot be equal")
    raw_qty = equity * risk_fraction / distance
    qty = floor_to_step(min(raw_qty, max_qty), qty_step)
    if qty < min_qty:
        return 0.0
    return qty


def one_r_price(entry: float, stop: float, side: str) -> float:
    risk = abs(entry - stop)
    return entry + risk if side == "Buy" else entry - risk

