.PHONY: install lint fmt test-unit test-integration test run worker migrate no-stub-gate

install:
	uv sync --group dev

lint:
	uv run ruff check .

fmt:
	uv run ruff check --fix .

test-unit:
	uv run pytest tests/unit -q

test-integration:
	uv run pytest tests/integration -q -m integration

test: test-unit test-integration

# app.main:app wires REAL adapters by default (CONVENTIONS END STATE).
run:
	AR_USE_REAL_ADAPTERS=true uv run uvicorn app.main:app --host 0.0.0.0 --port 8086

worker:
	AR_USE_REAL_ADAPTERS=true uv run python -m app.runtime.temporalx.run_worker

migrate:
	uv run alembic upgrade head

# CI no-stub gate (CONVENTIONS END STATE). Three checks:
#  1. forbidden stub markers anywhere in app/ (excl. the unit-tier double files);
#  2. fake/mock/stub identifiers must not leak into domain/graph/adapter/api code
#     (the wiring seam container.py legitimately constructs doubles behind the
#     use_real_adapters=False guard, so it + the double files are excluded here);
#  3. container.py / main.py must not import a double at MODULE scope (imports are
#     function-scoped inside the else guard) — a module-scope import would mean a
#     double is loaded on the real path.
# The live-probe assertion (README "Verification") independently proves the real
# default constructs no Fake/InMemory adapter, closing the wiring-seam gap.
DOUBLE_EXCLUDE := tests/|/adapters/fakes\.py|/store/memory\.py
no-stub-gate:
	@! grep -RInE "NotImplementedError|ErrNotWired|raise NotImplemented|TODO[: ]*real impl|FIXME[: ]*stub|placeholder return|hard-?coded (fake|stub|response)" \
		app/ --include='*.py' | grep -vE "$(DOUBLE_EXCLUDE)" \
		|| (echo "STUB MARKER FOUND IN RUNTIME" && exit 1)
	@! grep -RInE "class (Fake|Mock|Stub|Dummy)[A-Za-z0-9_]*|=[ ]*(Fake|Mock|Stub|Dummy)[A-Za-z0-9_]*\(" \
		app/ --include='*.py' | grep -vE "$(DOUBLE_EXCLUDE)|/container\.py" \
		|| (echo "FAKE/MOCK/STUB IDENTIFIER LEAKED OUTSIDE THE WIRING SEAM" && exit 1)
	@! grep -nE "^(from|import) app\.(adapters\.fakes|store\.memory)" \
		app/container.py app/main.py \
		|| (echo "RUNTIME WIRING IMPORTS A DOUBLE AT MODULE SCOPE" && exit 1)
	@echo "no-stub gate: clean"
