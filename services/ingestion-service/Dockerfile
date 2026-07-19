# ingestion-service — multi-stage build (MASTER-FR-072).
# TODO(wave-2): move the runtime stage to a distroless Python base once the
# platform base images are published.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
WORKDIR /srv
COPY libs/py-common /libs/py-common
COPY services/ingestion-service/pyproject.toml services/ingestion-service/uv.lock ./
RUN sed -i 's#\.\./\.\./libs/py-common#/libs/py-common#' pyproject.toml uv.lock \
    && uv sync --frozen --no-dev --no-install-project
COPY services/ingestion-service/app ./app
COPY services/ingestion-service/migrations ./migrations
COPY services/ingestion-service/alembic.ini ./
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime
RUN useradd --uid 10001 --no-create-home windrose
WORKDIR /srv
COPY --from=build /srv /srv
ENV PATH="/srv/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8083
HEALTHCHECK CMD ["python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8083/healthz')"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8083"]
