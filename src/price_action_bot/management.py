from __future__ import annotations

from dataclasses import asdict, dataclass

from .models import Side
from .risk import floor_to_step


RECOVERY_POLICY_VERSION = "recovery-70-15-15.v1"


@dataclass(frozen=True)
class RecoveryPolicy:
    """Versioned V2 management policy with an all-in risk budget."""

    version: str = RECOVERY_POLICY_VERSION
    tp1_fraction: float = 0.70
    tp2_fraction: float = 0.15
    runner_fraction: float = 0.15
    tp1_min_r: float = 1.50
    tp2_r: float = 2.00
    taker_fee_rate: float = 0.00055
    slippage_rate: float = 0.00020
    max_friction_risk_fraction: float = 0.25

    def validate(self) -> None:
        if abs(self.tp1_fraction + self.tp2_fraction + self.runner_fraction - 1.0) > 1e-9:
            raise ValueError("Recovery exit fractions must total 100%")
        if not 0 < self.tp1_fraction < 1:
            raise ValueError("TP1 fraction must be between 0 and 1")
        if self.tp1_min_r <= 0 or self.tp2_r <= self.tp1_min_r:
            raise ValueError("TP2 R must be greater than minimum TP1 R")
        if min(self.taker_fee_rate, self.slippage_rate) < 0:
            raise ValueError("Execution costs cannot be negative")
        if not 0 <= self.max_friction_risk_fraction < 1:
            raise ValueError("Maximum friction share must be between 0 and 1")


@dataclass(frozen=True)
class RecoveryPlan:
    accepted: bool
    reason_code: str
    reason: str
    policy_version: str
    side: Side
    risk_budget: float
    quantity: float
    initial_stop: float
    estimated_stop_loss: float
    friction_at_stop: float
    friction_risk_fraction: float
    tp1: float
    tp2: float
    break_even_stop: float
    obstacle: float
    tp1_fraction: float
    tp2_fraction: float
    runner_fraction: float

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def r_price(entry: float, stop: float, side: Side, multiple: float) -> float:
    distance = abs(entry - stop)
    if distance <= 0:
        raise ValueError("Entry and stop cannot be equal")
    return entry + distance * multiple if side == "Buy" else entry - distance * multiple


def break_even_with_costs(
    entry: float,
    side: Side,
    taker_fee_rate: float,
    slippage_rate: float,
) -> float:
    """Price where a round trip is approximately net-flat after modeled costs."""
    exit_cost = taker_fee_rate + slippage_rate
    if side == "Buy":
        return entry * (1 + taker_fee_rate) / (1 - exit_cost)
    return entry * (1 - taker_fee_rate) / (1 + exit_cost)


def _per_unit_stop_loss(
    entry: float,
    stop: float,
    taker_fee_rate: float,
    slippage_rate: float,
) -> tuple[float, float]:
    price_loss = abs(entry - stop)
    friction = entry * taker_fee_rate + stop * (taker_fee_rate + slippage_rate)
    return price_loss + friction, friction


def _recovery_tp1(
    *,
    entry: float,
    side: Side,
    quantity: float,
    risk_budget: float,
    close_fraction: float,
    taker_fee_rate: float,
    slippage_rate: float,
) -> float:
    """Solve a TP1 that realizes the full initial risk budget net of modeled costs."""
    cost = taker_fee_rate + slippage_rate
    per_unit_required = risk_budget / quantity
    if side == "Buy":
        numerator = per_unit_required + close_fraction * entry + entry * taker_fee_rate
        return numerator / (close_fraction * (1 - cost))
    numerator = close_fraction * entry - entry * taker_fee_rate - per_unit_required
    return numerator / (close_fraction * (1 + cost))


def build_recovery_plan(
    *,
    equity: float,
    risk_fraction: float,
    entry: float,
    stop: float,
    obstacle: float,
    side: Side,
    qty_step: float,
    min_qty: float,
    max_qty: float,
    policy: RecoveryPolicy | None = None,
) -> RecoveryPlan:
    """Build or reject a fee-aware 70/15/15 recovery plan without placing orders."""
    policy = policy or RecoveryPolicy()
    policy.validate()
    if equity <= 0 or not 0 < risk_fraction <= 1:
        raise ValueError("Invalid equity or risk fraction")
    if entry <= 0 or stop <= 0 or obstacle <= 0:
        raise ValueError("Entry, stop and obstacle must be positive")
    if (side == "Buy" and stop >= entry) or (side == "Sell" and stop <= entry):
        raise ValueError("Stop must be on the losing side of entry")

    risk_budget = equity * risk_fraction
    per_unit_loss, per_unit_friction = _per_unit_stop_loss(
        entry, stop, policy.taker_fee_rate, policy.slippage_rate
    )
    raw_qty = risk_budget / per_unit_loss
    quantity = floor_to_step(min(raw_qty, max_qty), qty_step)
    estimated_stop_loss = quantity * per_unit_loss
    friction_at_stop = quantity * per_unit_friction
    friction_share = friction_at_stop / estimated_stop_loss if estimated_stop_loss else 1.0

    reason_code = "ACCEPTED"
    reason = "Fee-aware recovery plan fits before the nearest obstacle"
    if quantity < min_qty:
        reason_code = "MIN_QTY"
        reason = "All-in risk budget cannot fund the exchange minimum quantity"

    tp1 = 0.0
    tp2 = 0.0
    be_stop = 0.0
    if quantity >= min_qty:
        recovery_tp = _recovery_tp1(
            entry=entry,
            side=side,
            quantity=quantity,
            risk_budget=risk_budget,
            close_fraction=policy.tp1_fraction,
            taker_fee_rate=policy.taker_fee_rate,
            slippage_rate=policy.slippage_rate,
        )
        minimum_tp = r_price(entry, stop, side, policy.tp1_min_r)
        tp1 = max(recovery_tp, minimum_tp) if side == "Buy" else min(recovery_tp, minimum_tp)
        tp2 = r_price(entry, stop, side, policy.tp2_r)
        be_stop = break_even_with_costs(
            entry, side, policy.taker_fee_rate, policy.slippage_rate
        )

        if friction_share > policy.max_friction_risk_fraction:
            reason_code = "FRICTION_TOO_HIGH"
            reason = "Fees and slippage consume too much of the all-in risk budget"
        elif (side == "Buy" and tp1 >= tp2) or (side == "Sell" and tp1 <= tp2):
            reason_code = "RECOVERY_BEYOND_TP2"
            reason = "Net 1R recovery would not occur before the planned 2R target"
        elif (side == "Buy" and tp1 > obstacle) or (side == "Sell" and tp1 < obstacle):
            reason_code = "OBSTACLE_BEFORE_RECOVERY"
            reason = "Nearest 1h obstacle comes before the net-recovery TP1"

    return RecoveryPlan(
        accepted=reason_code == "ACCEPTED",
        reason_code=reason_code,
        reason=reason,
        policy_version=policy.version,
        side=side,
        risk_budget=risk_budget,
        quantity=quantity if quantity >= min_qty else 0.0,
        initial_stop=stop,
        estimated_stop_loss=estimated_stop_loss,
        friction_at_stop=friction_at_stop,
        friction_risk_fraction=friction_share,
        tp1=tp1,
        tp2=tp2,
        break_even_stop=be_stop,
        obstacle=obstacle,
        tp1_fraction=policy.tp1_fraction,
        tp2_fraction=policy.tp2_fraction,
        runner_fraction=policy.runner_fraction,
    )
