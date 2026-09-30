# Model card

Synoptiq is a meta-layer over external GFS, IFS, and AIFS forecasts. It does not replace those forecast systems. The intended production model set is nine LightGBM source-skill models: one per source and variable for precipitation, temperature, and wind, plus three calibrated bust-risk models.

Training must use real aligned forecast/verification pairs, chronological train/validation/test partitions, and an untouched test period. Reported metrics must come from the machine-readable real evaluation report. No target improvement is asserted by this repository until that report exists.

The current corrected bundle is `synoptiq-real-12m-20260930-aifs-unitfix`, trained on the real 2025-02-26 through 2026-02-25 window. The prior `synoptiq-real-12m-20260929` bundle is superseded: ECMWF Open Data exposes accumulated AIFS precipitation as `kg m**-2`, which the prior decoder treated as metres. Corrected AIFS precipitation is normalized before daily aggregation and all model/calibration/evaluation/inference artifacts were regenerated from the real training/validation/test split. The original artifacts remain available for audit and are not the active local model.

ECMWF IFS/AIFS retrieval uses the official Open Data Azure, Google Cloud, ECMWF, and AWS mirrors; mirror identity is recorded. AIFS source provenance identifies Single v1 before 2026-05-12 and Single v2 from 2026-05-12 onward. Its precipitation is unit-normalized from `kg m**-2` (numerically mm) before conversion to the pipeline's cumulative-metre contract.

The production regime detector remains transparent and rule-based, using real meteorological context. Trust scoring, abstention, calibration, bias correction, dynamic weighting, and explanations are operational outputs only when the corresponding validated artifacts are available.
