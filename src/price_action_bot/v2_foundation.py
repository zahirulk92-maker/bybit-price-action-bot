from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .analysis import market_context, setup_checklist
from .models import Candle, MarketContext


V1_POLICY_VERSION = "v1.0.0-frozen"
V2_PLAN_VERSION = "thesisedge-v2.0-locked"
DECISION_SCHEMA_VERSION = "thesisedge.phase0.v1"
FeatureMode = Literal["off", "shadow"]


def canonical_json(value: object) -> str:
    """Return stable JSON used by replay fingerprints and equality checks."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def candle_payload(candle: Candle) -> dict[str, int | float]:
    return {
        "timestamp_ms": candle.timestamp_ms,
        "open": float(candle.open),
        "high": float(candle.high),
        "low": float(candle.low),
        "close": float(candle.close),
        "volume": float(candle.volume),
    }


def context_payload(context: MarketContext) -> dict[str, object]:
    return asdict(context)


@dataclass(frozen=True)
class V2FeatureFlags:
    structure: FeatureMode = "off"
    universe: FeatureMode = "off"
    portfolio: FeatureMode = "off"
    playbooks: FeatureMode = "off"
    thesis: FeatureMode = "off"
    management: FeatureMode = "off"

    def __post_init__(self) -> None:
        for name, mode in asdict(self).items():
            if mode not in {"off", "shadow"}:
                raise ValueError(f"ThesisEdge feature {name} must be off or shadow in Phase 0")

    def as_dict(self) -> dict[str, FeatureMode]:
        return asdict(self)

    @property
    def has_execution_authority(self) -> bool:
        # Phase 0 deliberately has no authority-bearing mode.
        return False


@dataclass(frozen=True)
class StructureSnapshot:
    regime: str = "unknown"
    major_swings: tuple[dict[str, object], ...] = ()
    internal_swings: tuple[dict[str, object], ...] = ()
    zones: tuple[dict[str, object], ...] = ()
    events: tuple[dict[str, object], ...] = ()
    data_confidence: str = "unknown"


@dataclass(frozen=True)
class ContextSnapshot:
    regime: str = "unknown"
    bias_1h: str = "neutral"
    location: str = "unknown"
    volatility: str = "unknown"
    liquidity_event: str = "none"
    market_alignment: str = "unknown"
    data_quality: str = "incomplete"


@dataclass(frozen=True)
class ConflictEvidence:
    code: str
    detail: str
    severity: Literal["info", "warning", "veto"] = "warning"


@dataclass(frozen=True)
class TradeThesisDraft:
    thesis_id: str
    symbol: str
    direction: str
    playbook: str
    decision: str = "WAIT"
    created_at_ms: int = 0
    expires_at_ms: int = 0
    entry_trigger: float = 0.0
    stop: float = 0.0
    targets: tuple[float, ...] = ()
    invalidation: str = ""
    supporting_evidence: tuple[str, ...] = ()
    conflicts: tuple[ConflictEvidence, ...] = ()
    setup_readiness: float | None = None
    historical_probability: float | None = None
    expectancy_r: float | None = None
    data_confidence: str = "unknown"
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class OutcomeSnapshot:
    thesis_id: str
    status: str
    net_pnl: float | None = None
    fees: float | None = None
    result_r: float | None = None
    mae_r: float | None = None
    mfe_r: float | None = None
    exit_reason: str = ""
    exchange_confirmed: bool = False


def build_v1_decision_audit(
    *,
    symbol: str,
    candles_5m: list[Candle],
    candles_1h: list[Candle],
    context: MarketContext,
    decision: dict[str, object],
    policy_settings: dict[str, object],
    runtime_state: dict[str, object],
    feature_flags: V2FeatureFlags,
) -> dict[str, object]:
    if not candles_5m or not candles_1h:
        raise ValueError("Decision audit requires both 5m and 1h candle inputs")
    five_payload = [candle_payload(item) for item in candles_5m]
    hour_payload = [candle_payload(item) for item in candles_1h]
    input_payload = {
        "context": context_payload(context),
        "policy_settings": policy_settings,
        "runtime_state": runtime_state,
        "feature_flags": feature_flags.as_dict(),
        "v2_execution_authority": feature_flags.has_execution_authority,
    }
    references = {
        "5m": {
            "from_ms": candles_5m[0].timestamp_ms,
            "to_ms": candles_5m[-1].timestamp_ms,
            "count": len(candles_5m),
            "sha256": fingerprint(five_payload),
        },
        "1h": {
            "from_ms": candles_1h[0].timestamp_ms,
            "to_ms": candles_1h[-1].timestamp_ms,
            "count": len(candles_1h),
            "sha256": fingerprint(hour_payload),
        },
    }
    identity = {
        "schema_version": DECISION_SCHEMA_VERSION,
        "policy_version": V1_POLICY_VERSION,
        "symbol": symbol,
        "candle_time_ms": candles_5m[-1].timestamp_ms,
        "inputs": references,
        "runtime_state": runtime_state,
        "decision": decision,
    }
    return {
        "decision_id": fingerprint(identity),
        "schema_version": DECISION_SCHEMA_VERSION,
        "policy_version": V1_POLICY_VERSION,
        "plan_version": V2_PLAN_VERSION,
        "symbol": symbol,
        "stage": "V1_CHECKLIST_BASELINE",
        "candle_time_ms": candles_5m[-1].timestamp_ms,
        "input_payload": input_payload,
        "decision_payload": decision,
        "candle_references": references,
    }


def replay_v1_checklist(
    record: dict[str, object], candles_5m: list[Candle], candles_1h: list[Candle]
) -> dict[str, object]:
    """Replay a Phase-0 V1 checklist without exchange or order side effects."""
    references = record["candle_references"]
    actual_hashes = {
        "5m": fingerprint([candle_payload(item) for item in candles_5m]),
        "1h": fingerprint([candle_payload(item) for item in candles_1h]),
    }
    inputs_match = all(
        actual_hashes[interval] == references[interval]["sha256"]
        and len(candles) == references[interval]["count"]
        for interval, candles in (("5m", candles_5m), ("1h", candles_1h))
    )
    settings = record["input_payload"]["policy_settings"]
    replayed = setup_checklist(
        candles_5m,
        market_context(candles_1h),
        float(settings["volume_multiplier"]),
        float(settings["min_reward_risk"]),
    )
    expected = record["decision_payload"]
    return {
        "decision_id": record["decision_id"],
        "inputs_match": inputs_match,
        "decision_matches": canonical_json(replayed) == canonical_json(expected),
        "expected_sha256": fingerprint(expected),
        "replayed_sha256": fingerprint(replayed),
    }
