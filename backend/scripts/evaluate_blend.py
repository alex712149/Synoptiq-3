"""
Held-out temporal evaluation (Section 13 step 15, Section 21 headline proof
point) — LEAKAGE-FREE VERSION.

Everything used here was frozen before this script runs: the per-source
meta-models and bust classifiers (trained on TRAIN only), the calibration
(fit on VALIDATION only, using the frozen meta-model's own predictions), and
the skill table (built from TRAIN+VALIDATION only). This script itself only
ever reads TEST-split rows, and the "historical_skill_expanding"/
"climatological_anomaly_norm" features those rows carry were computed via
groupby().expanding().shift(1) — i.e. every TEST row's features are built
only from what was already known strictly before that row's own valid_time,
never from the test outcome being scored or from anything after it.

Writes artifacts/blend_eval_<region>_<variable>.json, which
/verification/scorecard reads — so the API only ever reports a number this
script actually measured, never a placeholder.
"""
import sys, pathlib, json, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
from app.database import SessionLocal
from app.data_prep import prepare_variable_frame, build_batch_blend_inputs
from app.blending.train_meta_model import get_global_split_boundaries
from app.blending.blend import batch_blend
from app.blending.calibration import apply_quantile_map
from app.skill_engine import _csi_pod_far
from app.config import ACTIVE_METRICS_DIR, PILOT_ZONES, THRESHOLDS


def evaluate(db, region: str, val_start, test_start, variable: str):
    df = prepare_variable_frame(db, variable, val_start, test_start)
    if df.empty:
        return None
    test_df = df[(df["split"] == "test") & (df["region"] == region)]
    if test_df.empty:
        return None

    feats_by_model, values_by_model, truth, context_meta = build_batch_blend_inputs(test_df, variable)
    if not feats_by_model or len(truth) < 10:
        return None

    fallback_skill = {m: context_meta["historical_skill_expanding"].to_numpy() for m in feats_by_model}
    blended_raw, _, source = batch_blend(variable, feats_by_model, values_by_model, fallback_skill)
    blended_calibrated = np.array([apply_quantile_map(v, variable, region=region) for v in blended_raw])

    result = {
        "region": region,
        "variable": variable,
        "n_test_contexts": int(len(truth)),
        "weight_source": source,
        "split_boundaries": {"val_start": str(val_start), "test_start": str(test_start)},
    }

    model_errors = {}
    if variable == "precipitation":
        thresh = THRESHOLDS[variable]["heavy"]
        synoptiq_csi, _, _ = _csi_pod_far(blended_calibrated, truth, thresh)
        per_model = {}
        for m, preds in values_by_model.items():
            csi_m, _, _ = _csi_pod_far(preds, truth, thresh)
            per_model[m] = {
                "csi": float(csi_m) if not np.isnan(csi_m) else 0.0,
                "mae": float(np.mean(np.abs(preds - truth))),
                "rmse": float(np.sqrt(np.mean((preds - truth) ** 2))),
            }
        best_model = max(per_model, key=lambda model: per_model[model]["csi"])
        best_single = per_model[best_model]["csi"]
        synoptiq_csi = 0.0 if np.isnan(synoptiq_csi) else float(synoptiq_csi)
        rel_improve = ((synoptiq_csi - best_single) / best_single * 100.0) if best_single > 0 else 0.0
        naive = np.mean(np.vstack(list(values_by_model.values())), axis=0)
        result.update({
            "metric": f"CSI@{thresh:g}mm",
            "best_single_model": best_model,
            "best_single_model_csi": round(best_single, 4),
            "synoptiq_csi": round(synoptiq_csi, 4),
            "synoptiq_mae": round(float(np.mean(np.abs(blended_calibrated - truth))), 4),
            "synoptiq_rmse": round(float(np.sqrt(np.mean((blended_calibrated - truth) ** 2))), 4),
            "per_model_metrics": per_model,
            "naive_average_mae": round(float(np.mean(np.abs(naive - truth))), 4),
            "naive_average_rmse": round(float(np.sqrt(np.mean((naive - truth) ** 2))), 4),
            "relative_csi_improvement_pct": round(float(rel_improve), 2),
            "fss_metric_type": "not applicable at this threshold summary; FSS-proxy remains diagnostic only",
        })
    else:
        blend_rmse = float(np.sqrt(np.mean((blended_calibrated - truth) ** 2)))
        per_model = {
            m: {
                "mae": float(np.mean(np.abs(preds - truth))),
                "rmse": float(np.sqrt(np.mean((preds - truth) ** 2))),
                "bias": float(np.mean(preds - truth)),
            }
            for m, preds in values_by_model.items()
        }
        best_model = min(per_model, key=lambda model: per_model[model]["rmse"])
        best_single_rmse = per_model[best_model]["rmse"]
        rel_improve = ((best_single_rmse - blend_rmse) / best_single_rmse * 100.0) if best_single_rmse > 0 else 0.0
        naive = np.mean(np.vstack(list(values_by_model.values())), axis=0)
        result.update({
            "metric": "RMSE",
            "best_single_model": best_model,
            "best_single_model_rmse": round(best_single_rmse, 3),
            "synoptiq_rmse": round(blend_rmse, 3),
            "synoptiq_mae": round(float(np.mean(np.abs(blended_calibrated - truth))), 3),
            "synoptiq_bias": round(float(np.mean(blended_calibrated - truth)), 3),
            "per_model_metrics": per_model,
            "naive_average_mae": round(float(np.mean(np.abs(naive - truth))), 3),
            "naive_average_rmse": round(float(np.sqrt(np.mean((naive - truth) ** 2))), 3),
            "relative_rmse_improvement_pct": round(float(rel_improve), 2),
        })

    ACTIVE_METRICS_DIR.mkdir(exist_ok=True, parents=True)
    out_path = ACTIVE_METRICS_DIR / f"blend_eval_{region}_{variable}.json"
    out_path.write_text(json.dumps(result, indent=2))
    return result


def main():
    t0 = time.time()
    db = SessionLocal()
    val_start, test_start = get_global_split_boundaries(db)
    print(f"Evaluating on TEST split only (valid_time >= {test_start}).")
    for region in PILOT_ZONES:
        for variable in ("precipitation", "temperature", "wind_speed"):
            r = evaluate(db, region, val_start, test_start, variable)
            if not r:
                print(f"{region} [{variable}]: not enough held-out test samples to evaluate.")
                continue
            if variable == "precipitation":
                print(
                    f"{region} [{variable}]: Synoptiq CSI@50mm={r['synoptiq_csi']} vs "
                    f"{r['best_single_model']}={r['best_single_model_csi']} "
                    f"({r['relative_csi_improvement_pct']:+.1f}%, n={r['n_test_contexts']})"
                )
            else:
                print(
                    f"{region} [{variable}]: Synoptiq RMSE={r['synoptiq_rmse']} vs "
                    f"{r['best_single_model']}={r['best_single_model_rmse']} "
                    f"({r['relative_rmse_improvement_pct']:+.1f}%, n={r['n_test_contexts']})"
                )
    print(f"[evaluation time: {time.time() - t0:.1f}s]")
    db.close()


if __name__ == "__main__":
    main()
