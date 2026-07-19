# Build: resolve deps into a self-contained venv with uv
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /srv
COPY libs/py-common /libs/py-common
COPY services/semantic-service/pyproject.toml services/semantic-service/uv.lock ./
RUN sed -i 's#\.\./\.\./libs/py-common#/libs/py-common#' pyproject.toml uv.lock \
    && uv export --no-dev --frozen --format requirements.txt -o requirements.txt \
    && uv pip install --system --no-cache -r requirements.txt
COPY services/semantic-service/app ./app
COPY services/semantic-service/migrations ./migrations
COPY services/semantic-service/alembic.ini ./

# Runtime: distroless (MASTER-FR-072)
FROM gcr.io/distroless/python3-debian12:nonroot
WORKDIR /srv
COPY --from=build /usr/local/lib/python3.12/site-packages /srv/site-packages
COPY --from=build /srv/app /srv/app
COPY --from=build /srv/migrations /srv/migrations
COPY --from=build /srv/alembic.ini /srv/alembic.ini
ENV PYTHONPATH=/srv:/srv/site-packages
EXPOSE 8086
ENTRYPOINT ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8086"]
