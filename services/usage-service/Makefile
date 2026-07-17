GO ?= go
PKG := ./...

.PHONY: build test test-unit test-integration lint vet run clean nostub

build:
	$(GO) build ./cmd/server

# Unit tier: no external dependencies; always runnable. Integration tests
# auto-skip under -short.
test-unit:
	$(GO) test -short -count=1 $(PKG)

# Integration tier: requires the real local infra (deploy/docker-compose.dev.yml):
# Postgres :5432, Redpanda :9092, Redis :6379, OPA :8281. Auto-skips with a clear
# message when the infra is unavailable.
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

# No-stub gate (CONVENTIONS END STATE): no forbidden stubs in runtime source.
nostub:
	@if grep -RInE 'NotImplementedError|ErrNotWired|panic\("TODO"\)|TODO real impl' \
		--include='*.go' cmd internal | grep -v '_test.go'; then \
		echo "no-stub gate FAILED"; exit 1; \
	else echo "no-stub gate passed"; fi

run:
	$(GO) run ./cmd/server

clean:
	rm -f server
