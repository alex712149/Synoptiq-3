"""
Train the adaptive meta-model + bust classifier (TRAIN split only), then fit
calibration on the VALIDATION split using the now-frozen meta-model's own
predictions (never refit on data the meta-model was trained on, never
touching TEST). Finally freezes the skill table from train+validation only.

This is the leakage-free replacement for the old train_model.py, which used
to (a) loop per-context calling a DB-backed skill lookup, and (b) rebuild the
skill table from the FULL archive (including test) right before evaluation.
"""
import sys, pathlib, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
from app.database import SessionLocal
from app.blending.train_meta_model import train_all, get_global_split_boundaries, freeze_skill_table
from app.blending.blend import batch_blend
from app.blending.calibration import (
    fit_quantile_map, fit_exceedance_calibrator, load_quantile_map, MIN_SAMPLES_FOR_REGIONAL_FIT,
)
from app.data_prep import prepare_variable_frame, build_feature_frame, build_batch_blend_inputs
from app.config import VARIABLES, THRESHOLDS, MODELS


def fit_calibration_on_validation(db, variable: str, val_start, test_start):
    df = prepare_variable_frame(db, variable, val_start, test_start)
    if df.empty:
        print(f"  [{variable}] no data, skipping calibration.")
        return
    val_df = df[df["split"] == "val"]

    # 1) Global (pooled across regions) calibrator — used only as a fallback
    #    for a region with too little validation data to fit its own.
    feats_by_model, values_by_model, truth, context_meta = build_batch_blend_inputs(val_df, variable)
    if feats_by_model and len(truth) >= 20:
        fallback_skill = {m: context_meta["historical_skill_expanding"].to_numpy() for m in feats_by_model}
        blended, _, source = batch_blend(variable, feats_by_model, values_by_model, fallback_skill)
        fit_quantile_map(blended, truth, variable, region=None)
        for thresh_key in THRESHOLDS.get(variable, {}):
            if thresh_key == "unit":
                continue
            fit_exceedance_calibrator(blended, truth, variable, thresh_key, region=None)
        print(f"  [{variable}] global calibrator fit on {len(blended)} contexts (weight source: {source}).")

    # 2) Per-region calibrator, SHRUNK toward the global curve by sample size
    #    (alpha = n / (n + shrink_k)) — captures each region's own
    #    climatology where there's enough validation data to trust it, while
    #    a small-sample region leans back toward the pooled global curve
    #    instead of overfitting its own noisy quantiles.
    global_curve = load_quantile_map(variable, region=None)
    for region in val_df["region"].unique():
        region_val_df = val_df[val_df["region"] == region]
        r_feats, r_values, r_truth, r_ctx = build_batch_blend_inputs(region_val_df, variable)
        if not r_feats or len(r_truth) < MIN_SAMPLES_FOR_REGIONAL_FIT:
            print(f"  [{variable}/{region}] only {len(r_truth) if r_feats else 0} validation contexts "
                  f"(<{MIN_SAMPLES_FOR_REGIONAL_FIT}) — using global calibrator as fallback.")
            continue
        r_fallback_skill = {m: r_ctx["historical_skill_expanding"].to_numpy() for m in r_feats}
        r_blended, _, r_source = batch_blend(variable, r_feats, r_values, r_fallback_skill)
        fit_quantile_map(r_blended, r_truth, variable, region=region, shrink_toward=global_curve)
        for thresh_key in THRESHOLDS.get(variable, {}):
            if thresh_key == "unit":
                continue
            fit_exceedance_calibrator(r_blended, r_truth, variable, thresh_key, region=region)
        print(f"  [{variable}/{region}] regional calibrator (shrunk toward global) fit on "
              f"{len(r_blended)} contexts (weight source: {r_source}).")


def main():
    t0 = time.time()
    db = SessionLocal()

    print("Training per-source skill-score models + bust classifiers (TRAIN split only)...")
    report = train_all(db)
    for var, models in report["variables"].items():
        print(f"  {var}: {models}")
    print(f"  split boundaries -> val_start={report['val_start']}  test_start={report['test_start']}")
    t1 = time.time()
    print(f"  [training time: {t1 - t0:.1f}s]")

    print("Fitting bias-correction (quantile mapping) + exceedance calibration on VALIDATION split...")
    from app.blending.train_meta_model import get_global_split_boundaries
    val_start, test_start = get_global_split_boundaries(db)
    for variable in VARIABLES:
        fit_calibration_on_validation(db, variable, val_start, test_start)
    t2 = time.time()
    print(f"  [calibration time: {t2 - t1:.1f}s]")

    print("Freezing skill table from train+validation only (valid_time < test_start)...")
    n = freeze_skill_table(db, test_start)
    print(f"  wrote {n} frozen skill-table rows, built_through={test_start}")

    db.close()
    print(f"Done. Total time: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
