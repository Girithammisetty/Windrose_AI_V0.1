GO ?= go
PKG := ./...

.PHONY: build test test-unit test-integration lint vet run clean

build:
	$(GO) build ./cmd/server

# Unit tier: no external dependencies; always runnable (integration package
# auto-skips under -short).
test-unit:
	$(GO) test -short -count=1 $(PKG)

# Integration tier: requires Docker infra (ClickHouse, Redpanda, Postgres,
# Redis, MinIO, OPA). Auto-skips with a clear message when a component is down.
test-integration:
	$(GO) test -count=1 -timeout 15m ./test/integration/...

# Full suite.
test:
	$(GO) test -count=1 -timeout 15m $(PKG)

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
