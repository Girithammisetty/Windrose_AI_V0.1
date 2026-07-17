# bff-graphql — Node 20 + pnpm. node@20 is keg-only on macOS; put it on PATH.
export PATH := /opt/homebrew/opt/node@20/bin:/opt/homebrew/bin:$(PATH)

.PHONY: install dev run build typecheck lint test test-unit test-integration docker

install:            ## install deps (pnpm)
	pnpm install

dev:                ## watch-mode dev server
	pnpm dev

run:                ## start the server
	pnpm start

build: typecheck    ## build == strict typecheck (runtime is tsx)

typecheck:          ## tsc --noEmit (strict)
	pnpm typecheck

lint:               ## eslint (enforces the no-DB/no-Kafka boundary)
	pnpm lint

test:               ## unit + integration
	pnpm test

test-unit:          ## unit tests only (HTTP boundary mocked)
	pnpm test:unit

test-integration:   ## real JWT/JWKS + real downstream HTTP servers
	pnpm test:integration

docker:             ## build the container image
	docker build -t bff-graphql .
