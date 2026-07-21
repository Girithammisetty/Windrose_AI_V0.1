SERVICES := $(wildcard services/*)

.PHONY: dev-up dev-down test test-unit lint e2e e2e-keep up up-platform down reset doctor soak \
        demo-list demo-load demo-clean demo-clean-all

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

# Boot the platform with NO vertical demo data seeded THIS boot (tenant +
# personas only), then load exactly the pack you want with `demo-load`.
# NOTE: this does not remove data already in the DBs — use `make reset` for that.
up-platform:
	deploy/local/up.sh --platform-only $(ARGS)

# Stop every native service. `make down ARGS=--infra` also stops Docker infra.
down:
	deploy/local/down.sh $(ARGS)

# TRUE clean slate: delete ALL persistent data (drops the Docker data volumes
# that survive `make down`) so the next `make up` starts genuinely empty. Wipes
# every tenant/case/dataset/model/dashboard/audit record. Prompts unless FORCE=1.
#   make reset            # confirm, then wipe
#   make reset FORCE=1    # no prompt
reset:
	FORCE=$(FORCE) deploy/local/reset.sh

# Health doctor: detect the "fragile after a restart" failure class (lost data
# volumes / unbuilt projections) before it bites, per active tenant.
#   make doctor           # check only (read-only)
#   make doctor HEAL=1    # check, then rebuild any missing projections
doctor:
	HEAL=$(HEAL) deploy/local/doctor.sh

# Restart-survival soak: with the stack up, prove it survives an infra restart
# with data + projections intact (baseline doctor GREEN -> restart stateful
# containers, volumes preserved -> doctor must STILL be GREEN). Catches any
# ephemeral-store / lost-projection regression. Run `make up` first.
soak:
	deploy/local/soak.sh

# ---- Demo pack control -----------------------------------------------------
# Load ONE vertical pack (+ its demo data + per-role logins) into a throwaway
# `wr-demo-<pack>` tenant for a demo, then tear it down cleanly afterwards. The
# main `demo.windrose` tenant is never touched. Needs the stack up (`make up`).
#   make demo-list                       # packs you can load + demos loaded now
#   make demo-load PACK=card-disputes    # spin up wr-demo-card-disputes
#   make demo-clean PACK=card-disputes   # tear it down
#   make demo-clean-all                  # tear down every wr-demo-* tenant
demo-list:
	@packs/demo.sh list

demo-load:
	@[ -n "$(PACK)" ] || { echo "usage: make demo-load PACK=<pack>  (see: make demo-list)"; exit 2; }
	@packs/demo.sh load $(PACK)

demo-clean:
	@[ -n "$(PACK)" ] || { echo "usage: make demo-clean PACK=<pack>  (see: make demo-list)"; exit 2; }
	@packs/demo.sh clean $(PACK)

demo-clean-all:
	@packs/demo.sh clean-all

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
