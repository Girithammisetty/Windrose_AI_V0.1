# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY libs/py-common /libs/py-common
COPY services/experiment-service/pyproject.toml services/experiment-service/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY services/experiment-service/ ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM gcr.io/distroless/python3-debian12
WORKDIR /app
COPY --from=build /app /app
COPY --from=build /libs/py-common /libs/py-common
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH="/app"
EXPOSE 8086
ENTRYPOINT ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8086"]
