GO ?= go
PKG := ./...

.PHONY: build build-registry build-gateway test test-unit test-integration vet lint run-registry run-gateway clean

build: build-registry build-gateway

build-registry:
	$(GO) build -o bin/tool-registry ./cmd/registry

build-gateway:
	$(GO) build -o bin/mcp-gateway ./cmd/gateway

# Unit tier: no external dependencies; always runnable.
test-unit:
	$(GO) test -short -count=1 $(PKG)

# Integration tier: requires the dev stack (Postgres via Testcontainers pgvector,
# plus the running Redis/Redpanda/OPA/Ollama). Auto-skips with a clear message
# when a dependency is unavailable.
test-integration:
	$(GO) test -count=1 -timeout 15m ./test/integration/...

# Full suite (unit + integration).
test:
	$(GO) test -count=1 -timeout 15m $(PKG)

# Race detector on the enforcement pipeline.
test-race:
	$(GO) test -race -count=1 ./internal/enforce/...

vet:
	$(GO) vet $(PKG)

# Real lint gate (golangci-lint v2); clean at 0 issues.
lint:
	@if command -v golangci-lint >/dev/null 2>&1; then golangci-lint run ./...; else echo "golangci-lint not installed; falling back to go vet"; $(GO) vet $(PKG); fi

run-registry:
	$(GO) run ./cmd/registry

run-gateway:
	$(GO) run ./cmd/gateway

clean:
	rm -rf bin
