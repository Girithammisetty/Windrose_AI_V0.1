SERVICES := $(wildcard services/*)

.PHONY: dev-up dev-down test test-unit lint e2e e2e-keep up down

# Capstone: provision the WHOLE platform locally and open it in a browser for
# hands-on end-user testing. Preflight -> infra -> migrate+boot all 22 services
# -> platform seed (tenant, 4 RBAC-gated personas) -> claims-vertical demo seed
# (claims dataset, triage queue, a pending proposal, a trained+promoted model)
# -> print a banner with the URL + logins.
#   make up                        full platform + claims demo
#   make up ARGS=--core             RAM-constrained: documented claims-showcase profile
#   make up ARGS=--platform-only    tenant + personas only, no vertical demo data
up:
	deploy/local/up.sh $(ARGS)

# Stop every native service. `make down ARGS=--infra` also stops Docker infra.
down:
	deploy/local/down.sh $(ARGS)

# Repo-level end-to-end proof: boots the full real stack (infra + every money-path
# service, no fakes in the path) and drives the insurance claims triage-and-governance
# journey with real evidence at each step (real MinIO/Iceberg object, real Ollama
# tokens + rationale, forged-grant rejection, real case.disposition_applied Kafka
# event, real RAG chunk in pgvector). Stops services on exit; leave infra up.
e2e:
	deploy/e2e/run.sh

# Same, but leave all services running afterward (for inspection).
e2e-keep:
	deploy/e2e/run.sh --no-teardown

dev-up:
	docker compose -f deploy/docker-compose.dev.yml up -d

dev-down:
	docker compose -f deploy/docker-compose.dev.yml down

test:
	@set -e; for s in $(SERVICES); do \
		if [ -f $$s/Makefile ]; then echo "=== $$s ==="; $(MAKE) -C $$s test; fi; \
	done

test-unit:
	@set -e; for s in $(SERVICES); do \
		if [ -f $$s/Makefile ]; then echo "=== $$s ==="; $(MAKE) -C $$s test-unit; fi; \
	done

lint:
	@set -e; for s in $(SERVICES); do \
		if [ -f $$s/Makefile ]; then echo "=== $$s ==="; $(MAKE) -C $$s lint || true; fi; \
	done
