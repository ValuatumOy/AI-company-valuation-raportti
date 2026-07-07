# Backend image. Build context = repo root (so scripts/ + template/ are present
# for the JSON→HTML report generator). Frontend deploys separately to Vercel.
FROM python:3.11-slim

# node → runs scripts/generate.js;  Google Chrome (not Debian's `chromium`
# package) → PDF; fonts → report rendering.
#
# Debian trixie's `chromium` 150.0.7871.46 hard-crashes (SIGTRAP) on every
# single `--print-to-pdf` invocation in this container, headless=new or
# legacy, root or non-root — confirmed live via `railway ssh` by reproducing
# the exact command against the running deployment. Google's own Chrome
# 150.0.7871.46 build (same upstream version, different packaging/patches)
# renders a valid PDF with the identical command and flags. The noisy
# "Failed to connect to the bus" dbus errors in the logs are unrelated noise
# — present in both the crashing and the working build.
RUN apt-get update && apt-get install -y --no-install-recommends \
        nodejs \
        fonts-dejavu-core \
        fonts-liberation \
        ca-certificates \
        wget \
    && wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
        -O /tmp/chrome.deb \
    && apt-get install -y /tmp/chrome.deb \
    && rm -f /tmp/chrome.deb \
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
