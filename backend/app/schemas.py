"""
Pydantic schemas. ForecastRecord mirrors the Section 15 "minimal data contract"
table in the blueprint exactly (model, run_time, valid_time, lead_hours,
variable, lat/lon, forecast_value, regime_probs, historical_skill,
model_weight, trust_score, bust_probability) so every module agrees on the
same input/output shape end to end.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, ConfigDict


class ForecastRecord(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    model: str = Field(..., description="Forecast source identifier, e.g. GFS/IFS/AIFS")
    run_time: datetime
    valid_time: datetime
    lead_hours: int
    variable: str
    lat: float
    lon: float
    forecast_value: float
    regime_probs: dict[str, float] = Field(default_factory=dict)
    historical_skill: Optional[float] = None
    model_weight: Optional[float] = None
    trust_score: Optional[float] = None
    bust_probability: Optional[float] = None


class RawForecastResponse(BaseModel):
    region: str
    variable: str
    valid_time: datetime
    lead_hours: int
    sources: list[ForecastRecord]
    disagreement: float


class WeightExplanation(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    model: str
    weight: float
    top_drivers: list[dict] = Field(
        default_factory=list,
        description="SHAP-style {feature, contribution, direction} facts, "
        "highest |contribution| first. This is the source of truth for 'why'.",
    )


class BlendedForecastResponse(BaseModel):
    region: str
    variable: str
    valid_time: datetime
    lead_hours: int
    regime: str
    regime_probs: dict[str, float]
    raw_sources: list[ForecastRecord]
    weights: list[WeightExplanation]
    blended_value_raw: float
    blended_value_calibrated: float
    exceedance_probabilities: dict[str, float]
    trust_score: float
    disagreement: float
    bust_probability: float
    abstain: bool
    narrative: str
    analogues: list[dict] = Field(default_factory=list)


class TrustBreakdown(BaseModel):
    trust_score: float
    disagreement: float
    bust_probability: float
    abstain: bool
    signals: dict[str, float]


class ScorecardRow(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    model: str
    region: str
    lead_hours: int
    rmse: float
    mae: float
    bias: float
    csi_50mm: Optional[float] = None
    pod_50mm: Optional[float] = None
    far_50mm: Optional[float] = None
    fss_50mm: Optional[float] = None


class VerificationResponse(BaseModel):
    region: str
    variable: str
    threshold_label: str
    rows: list[ScorecardRow]
    synoptiq_csi: float
    best_single_model_csi: float
    relative_csi_improvement_pct: float
    target_relative_csi_improvement_pct: float
    meets_target: bool
    # --- added for leakage-fix transparency (additive, back-compatible) ---
    headline_metrics_period: str = "test (held-out, never used in training/calibration)"
    scorecard_rows_period: str = "train+validation (diagnostic skill profile; frozen before test evaluation)"
    fss_metric_note: str = "FSS is available only when computed from real gridded verification data."
    is_synthetic_prototype_result: bool = False


class ProviderStatusResponse(BaseModel):
    status: Literal["LIVE", "INVALID", "STALE", "TIMEOUT", "AUTH_ERROR", "UNAVAILABLE"]
    validation_result: str
    run_time: Optional[datetime] = None
    latest_available_time: Optional[datetime] = None
    age_minutes: Optional[int] = None
    validation_reason: str
    timestamp: Optional[datetime] = None
    reason: str
    is_real: bool
    fresh: bool = False
    complete: bool = False
    finite: bool = False
    source: Optional[str] = None
    source_model: Optional[str] = None
    source_transport: Optional[str] = None
    retrieved_at: Optional[datetime] = None
    source_run_time: Optional[datetime] = None
    run_time_basis: Optional[str] = None
    coverage: dict[str, Any] = {}
    fallback_used: bool = False


class SystemStatusResponse(BaseModel):
    mode: Literal["real", "fake"]
    ready: bool
    live_state: str = "REAL INPUTS UNAVAILABLE"
    active_model_version: Optional[str] = None
    training_period: Any = None
    last_successful_ingestion_time: Optional[datetime] = None
    latest_source_run: Optional[datetime] = None
    latest_source_runs: dict[str, Optional[datetime]]
    providers: dict[str, ProviderStatusResponse]
    supported_lead_times: list[int]
    test_period: Any = None
    last_verification_update: Optional[datetime] = None
    database: dict[str, Any]
    artifacts: dict[str, Any]


class ReplayEventSummary(BaseModel):
    event_id: str
    region: str
    label: str
    valid_time: datetime
    variable: str
    severity: str


class CounterfactualRequest(BaseModel):
    region: str
    variable: str = "precipitation"
    lead_hours: int = 72
    regime_override: Optional[str] = None
    season_override: Optional[str] = None
