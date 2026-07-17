GO ?= go
PKG := ./...

.PHONY: build test test-unit test-integration lint vet run clean

build:
	$(GO) build ./cmd/server

# Unit tier: no external dependencies; always runnable (test doubles only).
test-unit:
	$(GO) test -short -count=1 $(PKG)

# Integration tier: requires the dev stack (Redis, Redpanda, OPA, Postgres via
# deploy/docker-compose.dev.yml) + Docker for Testcontainers Postgres. Auto-skips
# with a clear message when infra is unavailable.
test-integration:
	$(GO) test -tags=integration -count=1 -timeout 15m ./test/integration/...

# Full suite: unit + integration.
test:
	$(GO) test -tags=integration -count=1 -timeout 15m $(PKG) ./test/integration/...

# Race checks on the concurrency-critical packages.
race:
	$(GO) test -race -count=1 ./internal/fanout/... ./internal/topics/...

vet:
	$(GO) vet $(PKG)

lint:
	@if command -v golangci-lint >/dev/null 2>&1; then \
		golangci-lint run; \
	else \
		echo "golangci-lint not installed; falling back to go vet"; \
		$(GO) vet $(PKG); \
	fi

run:
	$(GO) run ./cmd/server

clean:
	rm -f server
