FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend ./backend
COPY data/real/training.sqlite3 ./data/real/training.sqlite3
COPY data/fake/demo/synoptiq.db ./data/fake/demo/synoptiq.db
COPY models ./models
COPY artifacts ./artifacts
ENV SYNOPTIQ_MODE=real
CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
