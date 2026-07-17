UV ?= /opt/homebrew/bin/uv
export PATH := /opt/homebrew/bin:$(PATH)

.PHONY: install test-unit test-integration test lint fmt run migrate

install:
	$(UV) sync --group dev

test-unit: install
	$(UV) run pytest tests/unit -q

test-integration: install
	$(UV) run pytest tests/integration -q -m integration

test: install
	$(UV) run pytest tests -q

lint: install
	$(UV) run ruff check app tests migrations

fmt: install
	$(UV) run ruff check --fix app tests migrations
	$(UV) run ruff format app tests migrations

run: install
	$(UV) run uvicorn app.main:app --host 0.0.0.0 --port 8084

migrate: install
	$(UV) run alembic upgrade head
