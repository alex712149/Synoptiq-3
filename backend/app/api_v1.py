from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import (ACTIVE_METRICS_DIR, ACTIVE_MODEL_VERSION, CALIBRATION_DIR, LEAD_HOURS,
                        MANIFEST_PATH, MODELS, MODELS_DIR, PILOT_ZONES, RUNTIME_MODE,
                        THRESHOLDS, VARIABLES, LIVE_FRESHNESS_MINUTES,
                        TARGET_RELATIVE_CSI_IMPROVEMENT)
from app.database import get_db
from app.models_db import ForecastRow, GroundTruthRow, LiveForecast
from app.pipeline import run_blend_pipeline
from app.replay import real_test_replay_cases
from app.schemas import SystemStatusResponse

router = APIRouter(prefix="/api/v1", tags=["Synoptiq API v1"])

PUBLIC_REGION = {
    "KWG": "kerala_western_ghats",
    "BOB": "bay_of_bengal_east_coast",
    "IGP": "indo_gangetic_plains",
}
INTERNAL_TO_PUBLIC = {v: k for k, v in PUBLIC_REGION.items()}
REGION_LABELS = {
    "KWG": "Kerala / Western Ghats",
    "BOB": "Bay of Bengal / East Coast",
    "IGP": "Indo-Gangetic Plains / NW India",
}
COASTAL = {"KWG": True, "BOB": True, "IGP": False}
UNITS = {"precipitation": "mm/24h", "temperature": "deg_C", "wind_speed": "km/h"}
MODEL_CYCLE_HOURS = {"GFS": 6, "IFS": 6, "AIFS": 12}
MODEL_PUBLISH_GRACE_HOURS = {"GFS": 4, "IFS": 8, "AIFS": 9}


def _optional_metric(value: object) -> float | None:
    if value is None:
        return None
    try:
        metric = float(value)
    except (TypeError, ValueError):
        return None
    return metric if math.isfinite(metric) else None


def _confidence_decay(points: list[dict]) -> list[dict[str, float | int]]:
    series = []
    for point in points:
        trust = _optional_metric(point.get("trust_score"))
        lead_hours = point.get("lead_hours")
        if trust is not None and isinstance(lead_hours, int):
            series.append({"lead_hours": lead_hours, "trust": trust})
    return series


def _internal_region(code: str) -> str:
    if code not in PUBLIC_REGION:
        raise HTTPException(404, f"Unknown region '{code}'. Use KWG, BOB, or IGP.")
    return PUBLIC_REGION[code]


def _public_region(internal: str) -> str:
    return INTERNAL_TO_PUBLIC.get(internal, internal)


def _manifest() -> dict:
    p = MANIFEST_PATH
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _valid_real_manifest(manifest: dict) -> bool:
    if (manifest.get("data_mode") != "real"
            or manifest.get("synthetic_data_used") is not False
            or manifest.get("model_version") != ACTIVE_MODEL_VERSION):
        return False
    provenance = json.dumps({
        key: manifest.get(key)
        for key in ("forecast_sources", "truth_sources", "model_generations", "notes")
    }).lower()
    return "synthetic" not in provenance


def _is_real() -> bool:
    m = _manifest()
    if m.get("data_mode") == "real":
        return _valid_real_manifest(m)
    return any("synthetic" not in str(x).lower() for x in m.get("truth_sources", [])) if m else False


def _require_real_production() -> dict:
    manifest = _manifest()
    if RUNTIME_MODE == "fake":
        return manifest
    if not ACTIVE_MODEL_VERSION or not _valid_real_manifest(manifest):
        raise HTTPException(503, "REAL mode is unavailable: no validated real model manifest is active.")
    return manifest


def _require_live_inputs(db: Session) -> None:
    if RUNTIME_MODE != "real":
        return
    status = system_status(db)
    if status["ready"]:
        return
    raise HTTPException(
        503,
        detail={
            "code": "REAL_INPUTS_UNAVAILABLE",
            "message": "Current real provider cycles are stale, incomplete, or unavailable.",
            "providers": status["providers"],
        },
    )


def _latest_context(db: Session, internal_region: str, variable: str, lead_hours: int,
                    season: str | None = None) -> datetime:
    q = db.query(ForecastRow).filter(
        ForecastRow.region == internal_region,
        ForecastRow.variable == variable,
        ForecastRow.lead_hours == lead_hours,
    )
    if season:
        q = q.filter(ForecastRow.season == season)
    row = q.order_by(ForecastRow.valid_time.desc()).first()
    if not row:
        raise HTTPException(404, "No forecast context is available for this selection.")
    return row.valid_time


def _blend_public(db: Session, region_code: str, variable: str, lead_hours: int,
                  valid_time: datetime | None = None, regime_override: str | None = None,
                  season_override: str | None = None):
    _require_real_production()
    internal = _internal_region(region_code)
    if variable not in VARIABLES:
        raise HTTPException(422, f"Unknown variable '{variable}'.")
    if lead_hours not in LEAD_HOURS:
        raise HTTPException(422, f"Unsupported lead time {lead_hours}. Available: {LEAD_HOURS}")
    if valid_time is None:
        valid_time = _latest_context(db, internal, variable, lead_hours)
    try:
        result = run_blend_pipeline(db, internal, variable, valid_time, lead_hours,
                                    regime_override=regime_override, season_override=season_override)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    source_map = {s.model: s for s in result.raw_sources}
    sources = []
    for model in MODELS:
        s = source_map.get(model)
        if s is None:
            continue
        sources.append({
            "model": model,
            "model_generation": getattr(s, "model_generation", None),
            "forecast_value": s.forecast_value,
            "weight": s.model_weight,
            "historical_skill": s.historical_skill,
        })

    explanations = []
    for w in result.weights:
        for d in w.top_drivers[:3]:
            contribution = float(d.get("contribution", 0.0))
            direction = "increases_weight" if contribution >= 0 else "decreases_weight"
            explanations.append({
                "feature": d.get("feature", "unknown"),
                "contribution": contribution,
                "direction": direction,
                "detail": d.get("readable", d.get("feature", "")),
            })

    # BlendedForecastResponse stores the detailed signals indirectly only through
    # the computed score, so reconstruct the five public components from the same
    # weights used in app.blending.trust.
    from app.blending.trust import compute_trust
    hist = {s.model: float(s.historical_skill or 0.5) for s in result.raw_sources}
    weights = {w.model: w.weight for w in result.weights}
    trust = compute_trust(
        variable=variable,
        weights=weights,
        historical_skill_by_model=hist,
        disagreement=result.disagreement,
        lead_hours=lead_hours,
        regime_probs=result.regime_probs,
        n_sources_available=len(result.raw_sources),
        n_sources_expected=len(MODELS),
        context_features=None,
    )

    context_row = db.query(ForecastRow).filter(
        ForecastRow.region == internal,
        ForecastRow.variable == variable,
        ForecastRow.lead_hours == lead_hours,
        ForecastRow.valid_time == valid_time,
    ).first()
    season = (season_override or (context_row.season if context_row else None) or "normal")
    run_time = result.raw_sources[0].run_time.isoformat() if result.raw_sources else valid_time.isoformat()
    bust_flag = result.bust_probability > 0.6
    manifest = _require_real_production()
    provenance = {
        "GFS": "NOAA GFS",
        "IFS": "ECMWF IFS",
        "AIFS": "ECMWF AIFS",
        "regime_detector": "rule-based context detector",
        "explanation_backend": "SHAP TreeExplainer",
        "data_mode": manifest.get("data_mode", RUNTIME_MODE),
    }

    return {
        "region": region_code,
        "region_name": REGION_LABELS[region_code],
        "variable": variable,
        "unit": UNITS[variable],
        "run_time": run_time,
        "valid_time": result.valid_time.isoformat(),
        "lead_hours": lead_hours,
        "season": season,
        "regime": result.regime,
        "regime_probs": result.regime_probs,
        "sources": sources,
        "disagreement": result.disagreement,
        "raw_blend_value": result.blended_value_raw,
        "bias_corrected_value": result.blended_value_calibrated,
        "final_value": result.blended_value_calibrated,
        "calibration": {
            "status": "CALIBRATED" if len(sources) == len(MODELS) else "NOT_AVAILABLE",
            "reason": None if len(sources) == len(MODELS)
            else "The calibration artifacts were fitted on the complete three-source blend.",
        },
        "trust": {
            "historical_skill_component": trust["signals"]["historical_skill"],
            "disagreement_component": trust["signals"]["model_agreement"],
            "lead_time_component": trust["signals"]["lead_time_confidence"],
            "regime_stability_component": trust["signals"]["regime_stability"],
            "data_quality_component": trust["signals"]["data_quality"],
            "trust_score": result.trust_score,
        },
        "bust_probability": result.bust_probability,
        "bust_flag": bust_flag,
        "abstain": result.abstain,
        "explanation": explanations,
        "fallback_used": len(sources) < len(MODELS),
        "provenance": provenance,
    }


@router.get("/health")
def health(db: Session = Depends(get_db)):
    n = db.query(ForecastRow).count()
    manifest = _manifest()
    skill_dir = MODELS_DIR / "skill"
    bust_dir = MODELS_DIR / "bust"
    calibration_dir = CALIBRATION_DIR
    skill_models = len(list(skill_dir.glob("skillscore_*.joblib")))
    bust_models = sum((bust_dir / f"bust_{variable}.joblib").exists() for variable in VARIABLES)
    from app.models_db import ReplayEvent
    replay_exists = db.query(ReplayEvent).count() > 0
    return {
        "status": "ok" if n and (RUNTIME_MODE == "fake" or manifest.get("data_mode") == "real") else "not_ready",
        "historical_data_cached": n > 0,
        "blend_model_trained": skill_models >= 9,
        "bust_model_trained": bust_models >= 3,
        "replay_events_cached": replay_exists,
        "data_mode": manifest.get("data_mode", RUNTIME_MODE),
        "model_version": manifest.get("model_version", ACTIVE_MODEL_VERSION or None),
        "forecast_rows_cached": n,
        "calibration_artifacts": len(list(calibration_dir.glob("*.joblib"))),
    }


@router.get("/system/status", response_model=SystemStatusResponse)
def system_status(db: Session = Depends(get_db)):
    """Expose deployment readiness without inventing operational values."""
    manifest = _manifest()
    skill_dir = MODELS_DIR / "skill"
    bust_dir = MODELS_DIR / "bust"
    calibration_dir = CALIBRATION_DIR
    skill_count = len(list(skill_dir.glob("skillscore_*.joblib")))
    bust_count = sum((bust_dir / f"bust_{variable}.joblib").exists() for variable in VARIABLES)
    calibration_count = len(list(calibration_dir.glob("*.joblib")))
    latest = db.query(ForecastRow).order_by(ForecastRow.run_time.desc()).first()
    latest_ingestion = db.query(LiveForecast).order_by(LiveForecast.ingestion_time.desc()).first()
    latest_verification = db.query(GroundTruthRow).order_by(GroundTruthRow.valid_time.desc()).first()
    latest_by_model = {
        model: db.query(ForecastRow.run_time)
        .filter(ForecastRow.model == model)
        .order_by(ForecastRow.run_time.desc()).first()
        for model in MODELS
    }
    real_manifest = RUNTIME_MODE == "fake" or _valid_real_manifest(manifest)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    providers = {}
    for model, latest_run_row in latest_by_model.items():
        latest_run = latest_run_row[0] if latest_run_row else None
        latest_available = None
        latest_run_utc = (
            latest_run.replace(tzinfo=timezone.utc)
            if latest_run and latest_run.tzinfo is None
            else latest_run.astimezone(timezone.utc) if latest_run else None
        )
        is_real = RUNTIME_MODE == "real" and real_manifest
        if RUNTIME_MODE != "real":
            status, validation, reason = "UNAVAILABLE", "NOT_EVALUATED", "Provider checks are disabled in explicit fake mode."
        elif not real_manifest:
            status, validation, reason = "UNAVAILABLE", "NOT_EVALUATED", "The active real artifact manifest is not validated."
        elif latest_run is None:
            status, validation, reason = "UNAVAILABLE", "NO_RECORDS", "No forecast run is recorded for this source."
        else:
            rows = db.query(ForecastRow).filter(
                ForecastRow.model == model, ForecastRow.run_time == latest_run
            ).all()
            values = [row.forecast_value for row in rows]
            has_invalid_values = any(
                value is None or not math.isfinite(float(value)) for value in values
            )
            source_time = latest_run_utc.replace(tzinfo=None)
            age = now - source_time
            expected_coverage = {
                (region, variable, lead)
                for region in PILOT_ZONES
                for variable in VARIABLES
                for lead in LEAD_HOURS
            }
            observed_coverage = {
                (row.region, row.variable, row.lead_hours) for row in rows
            }
            latest_available = max((row.valid_time for row in rows), default=None)
            freshness_window = timedelta(
                hours=MODEL_CYCLE_HOURS[model] + MODEL_PUBLISH_GRACE_HOURS[model]
            )
            if age < timedelta(0):
                status, validation, reason = "INVALID", "FUTURE_RUN_TIME", "The recorded provider run time is in the future relative to UTC."
            elif age > freshness_window:
                status, validation, reason = "STALE", "STALE", f"The latest recorded source cycle exceeds the {freshness_window.total_seconds() / 3600:g}-hour update-cycle freshness limit."
            elif has_invalid_values:
                status, validation, reason = "INVALID", "NON_FINITE", "The latest recorded source cycle contains non-finite forecast values."
            elif observed_coverage != expected_coverage:
                status, validation, reason = "INVALID", "INCOMPLETE_HORIZON", "The latest recorded source cycle is missing a required region, variable, or lead."
            else:
                status, validation, reason = "LIVE", "PASS", "The latest recorded source cycle is finite, complete, and within its provider update-cycle freshness limit."
        providers[model] = {
            "status": status,
            "validation_result": validation,
            "run_time": latest_run_utc.isoformat() if latest_run_utc else None,
            "latest_available_time": latest_available.isoformat() if latest_available else None,
            "age_minutes": max(0, int((now - latest_run_utc.replace(tzinfo=None)).total_seconds() / 60)) if latest_run_utc else None,
            "validation_reason": reason,
            "timestamp": latest_run_utc.isoformat() if latest_run_utc else None,
            "reason": reason,
            "is_real": is_real,
        }
    live_provider_count = sum(
        provider["status"] == "LIVE" and provider["is_real"]
        for provider in providers.values()
    )
    live_payload = latest_ingestion.payload if latest_ingestion and isinstance(latest_ingestion.payload, dict) else {}
    live_states = live_payload.get("providers", {}) if isinstance(live_payload, dict) else {}
    live_age_minutes = None
    if latest_ingestion:
        live_age_minutes = max(0, int((now - latest_ingestion.ingestion_time).total_seconds() / 60))
    if live_states:
        live_provider_count = 0
        for model in MODELS:
            state = live_states.get(model, {})
            source_timestamp = state.get("source_run_time") or state.get("retrieved_at")
            source_age_minutes = None
            source_within_cycle = True
            if source_timestamp:
                try:
                    source_time = datetime.fromisoformat(source_timestamp)
                    if source_time.tzinfo is not None:
                        source_time = source_time.astimezone(timezone.utc).replace(tzinfo=None)
                    source_age = now - source_time
                    source_age_minutes = int(source_age.total_seconds() / 60)
                    source_within_cycle = timedelta(0) <= source_age <= timedelta(
                        hours=MODEL_CYCLE_HOURS[model] + MODEL_PUBLISH_GRACE_HOURS[model]
                    )
                except (TypeError, ValueError):
                    source_within_cycle = False
            fresh = bool(
                state.get("status") == "LIVE"
                and live_age_minutes is not None
                and live_age_minutes <= LIVE_FRESHNESS_MINUTES
                and source_within_cycle
            )
            if fresh:
                live_provider_count += 1
            providers[model].update({
                "status": "LIVE" if fresh else ("STALE" if state.get("status") == "LIVE" else state.get("status", "UNAVAILABLE")),
                "validation_result": "PASS" if fresh else state.get("reason", "live provider unavailable"),
                "fresh": fresh,
                "complete": bool(state.get("complete", False)),
                "finite": bool(state.get("finite", False)),
                "source": state.get("source"),
                "source_model": state.get("source_model"),
                "source_transport": state.get("source_transport"),
                "retrieved_at": state.get("retrieved_at"),
                "source_run_time": state.get("source_run_time"),
                "run_time_basis": state.get("run_time_basis"),
                "coverage": state.get("coverage", {}),
                "fallback_used": bool(state.get("fallback_used", False)),
                "age_minutes": source_age_minutes if source_age_minutes is not None else live_age_minutes,
                "reason": state.get("reason", "fresh validated real provider cycle" if fresh else "live cycle is stale"),
            })
    if live_provider_count == len(MODELS):
        live_state = "REAL LIVE"
    elif live_provider_count >= 2:
        live_state = "DEGRADED REAL"
    else:
        live_state = "REAL INPUTS UNAVAILABLE"
    return {
        "mode": RUNTIME_MODE,
        "live_state": live_state,
        "ready": bool(
            real_manifest
            and skill_dir.exists() and bust_dir.exists()
            and calibration_dir.exists()
            and skill_count >= 9 and bust_count >= 3 and calibration_count > 0
            and (
                (RUNTIME_MODE == "fake" and all(latest_by_model.values()))
                or (RUNTIME_MODE == "real" and live_provider_count >= 2)
            )
        ),
        "active_model_version": ACTIVE_MODEL_VERSION or None,
        "training_period": manifest.get("training_period"),
        "last_successful_ingestion_time": latest_ingestion.ingestion_time.isoformat() if latest_ingestion else None,
        "latest_source_run": latest.run_time.isoformat() if latest else None,
        "latest_source_runs": {
            model: row[0].isoformat() if row else None
            for model, row in latest_by_model.items()
        },
        "providers": providers,
        "supported_lead_times": LEAD_HOURS,
        "test_period": manifest.get("test_period"),
        "last_verification_update": latest_verification.valid_time.isoformat() if latest_verification else None,
        "database": {"status": "connected", "forecast_rows": db.query(ForecastRow).count()},
        "artifacts": {
            "models": skill_dir.exists() and bust_dir.exists(),
            "calibration": calibration_dir.exists(),
            "skill_model_count": skill_count,
            "bust_model_count": bust_count,
            "calibration_count": calibration_count,
        },
    }


@router.get("/regions")
def regions():
    return [
        {
            "code": code,
            "name": REGION_LABELS[code],
            "lat": round(sum(PILOT_ZONES[internal]["lat_range"]) / 2, 3),
            "lon": round(sum(PILOT_ZONES[internal]["lon_range"]) / 2, 3),
            "emphasis": PILOT_ZONES[internal]["emphasis"],
            "coastal": COASTAL[code],
        }
        for code, internal in PUBLIC_REGION.items()
    ]


@router.get("/sources")
def sources():
    return {
        "GFS": {"kind": "nwp", "provider": "NOAA", "role": "global forecast source"},
        "IFS": {"kind": "nwp", "provider": "ECMWF", "role": "global forecast source"},
        "AIFS": {"kind": "ai_forecast", "provider": "ECMWF", "role": "machine-learning forecast source"},
    }


@router.get("/variables")
def variables():
    return [
        {"variable": v, "unit": UNITS[v], "extreme_threshold": next(
            x for k, x in THRESHOLDS[v].items() if k != "unit"
        )}
        for v in VARIABLES
    ]


@router.get("/lead-times")
def lead_times():
    return LEAD_HOURS


@router.get("/forecast/blend")
def forecast_blend(region: str, variable: str, lead_hours: int,
                   valid_time: datetime | None = None, db: Session = Depends(get_db)):
    return _blend_public(db, region, variable, lead_hours, valid_time)


@router.get("/weights/map")
def weights_map(region: str, variable: str, season: str, regime: str,
                db: Session = Depends(get_db)):
    points = []
    for lead in LEAD_HOURS:
        try:
            internal = _internal_region(region)
            # The requested season/regime describe the context we want to
            # simulate, not necessarily the season/regime stored on the
            # selected forecast row. Use the latest available forecast for
            # the region/variable/lead and override the context in the
            # blending pipeline.
            vt = _latest_context(db, internal, variable, lead)
            data = _blend_public(db, region, variable, lead, vt, regime_override=regime, season_override=season)
        except HTTPException:
            continue
        points.append({
            "lead_hours": lead,
            "weights": {
                s["model"]: _optional_metric(s["weight"])
                for s in data["sources"]
            },
            "trust_score": data["trust"]["trust_score"],
        })
    if not points:
        raise HTTPException(404, "No forecast contexts match the requested season/regime.")
    return {
        "region": region,
        "variable": variable,
        "regime": regime,
        "season": season,
        "points": points,
        "confidence_decay": _confidence_decay(points),
    }


@router.get("/skill/verification")
def verification(region: str | None = None, variable: str | None = None):
    rows = []
    for path in sorted(ACTIVE_METRICS_DIR.glob("blend_eval_*_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        internal = payload.get("region")
        var = payload.get("variable")
        public = _public_region(internal)
        if region and public != region:
            continue
        if variable and var != variable:
            continue
        contexts = payload.get("n_test_contexts")
        try:
            test_contexts = int(contexts) if contexts is not None else None
        except (TypeError, ValueError):
            test_contexts = None

        if var == "precipitation":
            best = _optional_metric(payload.get("best_single_model_csi"))
            skill = _optional_metric(payload.get("synoptiq_csi"))
            rel_pct = _optional_metric(payload.get("relative_csi_improvement_pct"))
            rel = rel_pct / 100.0 if rel_pct is not None else None
            metric = str(payload.get("metric", ""))
            metric_threshold = re.search(r"CSI@\s*(\d+(?:\.\d+)?)\s*mm", metric, re.IGNORECASE)
            threshold = (
                float(metric_threshold.group(1))
                if metric_threshold
                else payload.get("threshold")
            )
            rows.append({
                "region": public,
                "variable": var,
                "metric": metric or (f"CSI@{float(threshold):g}mm" if threshold is not None else "CSI"),
                "threshold": float(threshold) if threshold is not None else None,
                "best_single_model": payload.get("best_single_model", "unknown"),
                "best_single_model_score": best,
                "synoptiq_score": skill,
                "relative_improvement": rel,
                "meets_target": (
                    rel >= TARGET_RELATIVE_CSI_IMPROVEMENT if rel is not None else None
                ),
                "target_relative_improvement": TARGET_RELATIVE_CSI_IMPROVEMENT,
                "best_single_model_csi": best,
                "synoptiq_csi": skill,
                "relative_csi_improvement": rel,
                "test_contexts": test_contexts,
            })
        else:
            best = _optional_metric(payload.get("best_single_model_rmse"))
            skill = _optional_metric(payload.get("synoptiq_rmse"))
            rel_pct = _optional_metric(payload.get("relative_rmse_improvement_pct"))
            rel = rel_pct / 100.0 if rel_pct is not None else None
            rows.append({
                "region": public,
                "variable": var,
                "metric": payload.get("metric", "RMSE"),
                "threshold": None,
                "best_single_model": payload.get("best_single_model", "unknown"),
                "best_single_model_score": best,
                "synoptiq_score": skill,
                "relative_improvement": rel,
                "meets_target": None,
                "target_relative_improvement": None,
                "best_single_model_csi": None,
                "synoptiq_csi": None,
                "relative_csi_improvement": None,
                "test_contexts": test_contexts,
            })
    return rows


@router.get("/extreme/guidance")
def extreme_guidance(region: str, lead_hours: int, db: Session = Depends(get_db)):
    guidance = []
    valid_time = None
    for variable in VARIABLES:
        try:
            internal = _internal_region(region)
            current_time = _latest_context(db, internal, variable, lead_hours)
            if valid_time is None:
                valid_time = current_time
            data = _blend_public(db, region, variable, lead_hours, current_time)
            internal_result = run_blend_pipeline(db, internal, variable, current_time, lead_hours)
        except HTTPException:
            continue
        threshold_key = next(k for k in THRESHOLDS[variable] if k != "unit")
        calibration_dir = CALIBRATION_DIR
        calibrated = (
            len(data.get("sources", [])) == len(MODELS)
            and (
                (calibration_dir / f"iso_{variable}_{threshold_key}.joblib").exists()
                or (calibration_dir / f"iso_{variable}_{threshold_key}_{internal}.joblib").exists()
            )
        )
        forecast_value = data["final_value"]
        probability_reason = None if calibrated else (
            "No fitted real validation calibrator is available for this threshold. "
            "The forecast-to-threshold comparison is deterministic; probability is withheld."
        )
        guidance.append({
            "variable": variable,
            "threshold": THRESHOLDS[variable][threshold_key],
            "unit": THRESHOLDS[variable]["unit"],
            "probability": (
                float(internal_result.exceedance_probabilities[threshold_key])
                if calibrated and threshold_key in internal_result.exceedance_probabilities
                else None
            ),
            "calibrated": calibrated,
            "probability_reason": probability_reason,
            "forecast_value": forecast_value,
            "threshold_exceeded": forecast_value >= THRESHOLDS[variable][threshold_key],
        })
    if not guidance:
        raise HTTPException(404, "No forecast data available for extreme guidance.")
    return {"region": region, "lead_hours": lead_hours, "valid_time": valid_time.isoformat() if valid_time else None,
            "guidance": guidance}


@router.get("/replay/events")
def replay_events(db: Session = Depends(get_db)):
    from app.models_db import ReplayEvent
    events = db.query(ReplayEvent).order_by(ReplayEvent.valid_time.desc()).all()
    out = []
    event_ids = set()
    for summary, _ in real_test_replay_cases():
        event_ids.add(summary["event_id"])
        out.append({**summary, "region": _public_region(summary["region"])})
    for e in events:
        payload = e.payload or {}
        headline = payload.get("headline")
        if not headline:
            headline = "Historical replay case"
        if e.event_id in event_ids:
            continue
        out.append({
            "event_id": e.event_id,
            "region": _public_region(e.region),
            "variable": e.variable,
            "valid_time": e.valid_time.isoformat(),
            "label": e.label,
            "headline": headline,
            "outcome": payload.get("outcome", "MIXED"),
            "severity": e.severity,
        })
    out.sort(key=lambda event: event["valid_time"], reverse=True)
    return out


@router.get("/replay/events/{event_id}")
def replay_event(event_id: str, db: Session = Depends(get_db)):
    from app.models_db import ReplayEvent
    e = db.get(ReplayEvent, event_id)
    if not e:
        for summary, detail in real_test_replay_cases():
            if summary["event_id"] == event_id:
                payload = dict(detail)
                payload["region"] = _public_region(payload["region"])
                return payload
        raise HTTPException(404, f"Replay event '{event_id}' not found")
    payload = dict(e.payload or {})
    payload.setdefault("event_id", e.event_id)
    payload["region"] = _public_region(payload.get("region", e.region))
    payload.setdefault("variable", e.variable)
    payload.setdefault("valid_time", e.valid_time.isoformat())
    payload.setdefault("label", e.label)
    payload.setdefault("headline", payload["label"] or "Historical replay case")
    payload.setdefault("unit", UNITS.get(e.variable, ""))

    observed = payload.get("reference_value", payload.get("observed_value"))
    if observed is not None and math.isfinite(float(observed)):
        payload["reference_value"] = float(observed)

    raw_sources = payload.get("raw_sources")
    if not isinstance(raw_sources, list):
        raw_sources = []
    normalized_sources = []
    for source in raw_sources:
        if not isinstance(source, dict):
            continue
        normalized = dict(source)
        normalized.setdefault("weight", normalized.get("model_weight"))
        normalized_sources.append(normalized)
    payload["raw_sources"] = normalized_sources

    if "naive_average" not in payload and normalized_sources:
        source_values = [float(source["forecast_value"]) for source in normalized_sources]
        payload["naive_average"] = sum(source_values) / len(source_values)

    choice = payload.get("single_model_choice")
    if not isinstance(choice, dict):
        eligible_sources = [
            source for source in normalized_sources
            if source.get("historical_skill") is not None
            and math.isfinite(float(source["historical_skill"]))
            and source.get("forecast_value") is not None
            and math.isfinite(float(source["forecast_value"]))
        ]
        if eligible_sources:
            best_source = max(eligible_sources, key=lambda source: float(source["historical_skill"]))
            choice = {
                "model": best_source["model"],
                "forecast_value": float(best_source["forecast_value"]),
            }
            payload["single_model_choice"] = choice

    if payload.get("reference_value") is not None:
        reference = float(payload["reference_value"])
        blended_value = payload.get(
            "synoptiq_blend",
            payload.get("blended_value_calibrated", payload.get("blended_value_raw")),
        )
        if blended_value is not None and math.isfinite(float(blended_value)):
            payload["synoptiq_blend"] = float(blended_value)
            payload.setdefault("synoptiq_error", abs(float(blended_value) - reference))
        if choice and choice.get("forecast_value") is not None:
            payload.setdefault(
                "single_model_error",
                abs(float(choice["forecast_value"]) - reference),
            )
        if payload.get("naive_average") is not None:
            payload.setdefault(
                "naive_average_error",
                abs(float(payload["naive_average"]) - reference),
            )

    narrative = payload.get("narrative")
    if isinstance(narrative, str):
        payload["narrative"] = [narrative]
    elif not isinstance(narrative, list):
        payload["narrative"] = []
    return payload
