# agent-runtime + agent-registry (BRD 14). Serves the API and (in-process) the
# Temporal worker. Real adapters by default (AR_USE_REAL_ADAPTERS=true).
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/root/.local/bin:$PATH"

RUN pip install --no-cache-dir uv

WORKDIR /app

# py-common path dependency must be present at build context root ../../libs.
COPY libs/py-common /libs/py-common
COPY services/agent-runtime/pyproject.toml services/agent-runtime/uv.lock* ./
# Rewrite the path source to the copied location.
RUN sed -i 's#../../libs/py-common#/libs/py-common#' pyproject.toml

RUN uv sync --no-dev || uv sync

COPY services/agent-runtime/ ./

EXPOSE 8086

# app.main:app wires REAL adapters by default (CONVENTIONS END STATE).
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8086"]
