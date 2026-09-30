# Deployment

Synoptiq has three separately deployed concerns: the Vercel frontend, the Render FastAPI web service, and a Render Cron Job that refreshes live provider data every 15 minutes. PostgreSQL is the production source of truth. The Render filesystem is not used for permanent raw data.

## Render

Use `render.yaml` or create the web service and cron job separately. The blueprint selects the corrected `synoptiq-real-12m-20260930-aifs-unitfix` bundle under `artifacts/real_12m_aifs_corrected/`; deploy that directory with its manifest, models, calibration, metrics, and compressed corrected history seed together. Set `DATABASE_URL`, `SYNOPTIQ_CORS_ORIGINS`, `NASA_EARTHDATA_USERNAME`, and `NASA_EARTHDATA_PASSWORD` in Render secrets. Before the API starts, the version-guarded seed command imports corrected historical forecasts, observations, and frozen skill into PostgreSQL; it refuses a superseded/synthetic source and skips after that model version is seeded. The cron worker and API use the same corrected model/artifact environment. The live worker writes newly validated cycles to PostgreSQL and the API is ready when at least two real provider cycles are fresh. ECMWF AIFS retrieval tries Azure, Google Cloud, ECMWF, then AWS Open Data mirrors.

Historical training uses `TRAINING_DATABASE_URL` locally and never receives or changes the production `DATABASE_URL`. It writes a new immutable manifest and prototype/metrics version; promote those with the selected real models and calibration before scheduling ingestion.

## Vercel

Set only `VITE_API_BASE_URL` to the Render `/api/v1` URL and leave `VITE_APP_MODE=LIVE`. Do not add NOAA, ECMWF, NASA, CDS, PostgreSQL, or model credentials to Vercel.

## Local

Use the existing `D:\SIH2026\.venv`. Set `SYNOPTIQ_MODE=real` to use the corrected real bundle or `SYNOPTIQ_MODE=fake` to use the explicit local demo archive. Real mode requires PostgreSQL for normal serving; local validation can select `SYNOPTIQ_DATABASE_ROLE=TRAINING` with `TRAINING_DATABASE_URL` pointing to `data/real/training_aifs_corrected.sqlite3`.

For local automatic refresh, run `python training/realdata/live_refresh.py`; use `--once` for a single cycle. The worker records provider status, freshness, coverage, and mirror provenance with every ingestion.
