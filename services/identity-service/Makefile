GO ?= go

.PHONY: build run test test-unit test-integration lint vet clean

build:
	$(GO) build -o bin/identity-service ./cmd/server

run: build
	./bin/identity-service

## Tier 1: no external dependencies (CONVENTIONS.md).
test-unit:
	$(GO) test ./internal/... ./migrations/...

## Tier 2: requires Docker (testcontainers Postgres); auto-skips with a
## clear message when Docker is unavailable.
test-integration:
	$(GO) test -tags integration -timeout 600s ./test/integration/...

test: test-unit test-integration

vet:
	$(GO) vet ./...
	$(GO) vet -tags integration ./...

## golangci-lint if installed, otherwise fall back to go vet.
lint:
	@if command -v golangci-lint >/dev/null 2>&1; then \
		golangci-lint run --build-tags integration ./...; \
	else \
		echo "golangci-lint not installed; running go vet"; \
		$(MAKE) vet; \
	fi

clean:
	rm -rf bin
