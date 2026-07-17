GO ?= go
PKG := ./...

.PHONY: build test test-unit test-integration lint vet run clean

build:
	$(GO) build ./...

# Unit tier: no external dependencies; always runnable (domain state machine,
# dedup, projection truncation, bulk validation).
test-unit:
	$(GO) test -short -count=1 $(PKG)

# Integration tier: requires Docker + the dev stack (Postgres RLS, Redpanda,
# Redis, OPA, OpenSearch). Auto-skips with a clear message when unavailable.
test-integration:
	$(GO) test -count=1 -timeout 20m ./test/integration/...

# Full suite (unit + integration).
test:
	$(GO) test -count=1 -timeout 20m $(PKG)

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
