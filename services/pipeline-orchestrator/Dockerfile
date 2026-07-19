# pipeline-orchestrator — multi-stage build.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /srv
COPY libs/py-common /libs/py-common
COPY services/pipeline-orchestrator/pyproject.toml services/pipeline-orchestrator/uv.lock ./
RUN sed -i 's#\.\./\.\./libs/py-common#/libs/py-common#' pyproject.toml uv.lock \
    && uv export --no-dev --frozen --format requirements.txt -o requirements.txt \
    && uv pip install --system --no-cache -r requirements.txt

FROM python:3.12-slim
WORKDIR /app
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY services/pipeline-orchestrator/app ./app
COPY services/pipeline-orchestrator/migrations ./migrations
COPY services/pipeline-orchestrator/alembic.ini ./
EXPOSE 8087
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8087"]
