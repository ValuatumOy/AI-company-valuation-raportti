# Backend image. Build context = repo root (so scripts/ + template/ are present
# for the JSON→HTML report generator). Frontend deploys separately to Vercel.
FROM python:3.11-slim

# node → runs scripts/generate.js;  chromium → PDF;  fonts → report rendering.
RUN apt-get update && apt-get install -y --no-install-recommends \
        nodejs \
        chromium \
        fonts-dejavu-core \
        fonts-liberation \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data

ENV PYTHONUNBUFFERED=1 \
    PIPELINE_DB=/data/pipeline.db

WORKDIR /app
COPY pipeline-runner/backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Report generator + design template (report.py resolves these at ../../ from backend).
COPY scripts   /app/scripts
COPY template  /app/template
# Backend app + vendored Valuatum kit.
COPY pipeline-runner/backend /app/pipeline-runner/backend

WORKDIR /app/pipeline-runner/backend
EXPOSE 8080
# Shell form so $PORT (Railway sets it) is honored; defaults to 8080 (Fly).
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
