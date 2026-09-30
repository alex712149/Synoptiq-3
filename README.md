# Synoptiq

Synoptiq blends external NOAA GFS and ECMWF IFS/AIFS forecasts with a trained meta-layer. It is not a replacement forecast model. Backend mode is explicit: `SYNOPTIQ_MODE=real` uses only authentic data and real artifacts; `SYNOPTIQ_MODE=fake` uses only synthetic/demo data and artifacts. Legacy `LIVE`/`DEMO` values remain accepted aliases.

## Structure

- `backend/`: FastAPI API, database models, blending and versioned artifact loading.
- `frontend/`: React/TanStack frontend. LIVE mode reads the API; DEMO is opt-in and visibly identified.
- `training/`: real-data training and latest-cycle worker entrypoints.
- `data/real/` and `data/fake/`: isolated provider/training data and demo archive.
- `models/real/` and `models/fake/`: isolated trained model files.
- `artifacts/real_12m_aifs_corrected/`: active corrected REAL_12M models, calibration, inference, and seed bundle.
- `artifacts/real_12m/`: superseded AIFS-precipitation bundle retained for audit; do not deploy it.
- `artifacts/fake/`: isolated demo manifests, metrics and calibration.

## Local setup

Use the existing environment, not a new virtual environment:

```powershell
. D:\SIH2026\.venv\Scripts\Activate.ps1
D:\SIH2026\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
D:\SIH2026\.venv\Scripts\python.exe -m pip install -r training\requirements-realdata.txt
npm --prefix frontend install
```

Copy the root `.env.example` to `.env` and fill the provider and database values. Runtime Python configuration reads this root file only. Real mode requires PostgreSQL (or the isolated training role for local prototype validation), a validated real model manifest, and `SYNOPTIQ_MODEL_VERSION`. Fake mode uses only its local SQLite archive and fake artifacts.

## Real data

Configure NASA Earthdata username/password for IMERG. ERA5 uses the public Open-Meteo Historical Weather API with the ERA5 dataset. GFS, IFS, and AIFS use their public provider endpoints. Credentials belong only in backend/training secrets, never in the frontend. See [DATA_SOURCES.md](DATA_SOURCES.md).

## Training and serving

Run the real-data smoke test before training, then:

```powershell
D:\SIH2026\.venv\Scripts\python.exe training\run_real_training.py --end YYYY-MM-DD
D:\SIH2026\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000
npm --prefix frontend run dev
```

Training and live ingestion are separate processes. The API serves frozen validated artifacts; the scheduled worker retrieves the newest cycle and writes forecasts to PostgreSQL. See [TRAINING.md](TRAINING.md) and [DEPLOYMENT.md](DEPLOYMENT.md).

## Honest evaluation

Only the untouched real test-period report may support accuracy claims. The repository does not claim a percentage improvement without actual metrics. `/api/v1/system/status` reports readiness, model version, database state, and ingestion provenance without inventing values.
