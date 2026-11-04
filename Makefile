.PHONY: up down migrate seed test test-db test-aidb serve lint kit-local
up:        ; docker compose up -d db
down:      ; docker compose down
migrate:   ; .venv/bin/trustdesk migrate
seed:      ; .venv/bin/trustdesk seed acme
serve:     ; .venv/bin/trustdesk serve
kit-local: ; uv pip install -e ../agentpg-kit   # use a sibling checkout of the kit instead of the pinned release
lint:      ; .venv/bin/ruff check src tests
test:      ; .venv/bin/pytest -q
# Needs POSTGRES_PASSWORD exported (set -a; . ./.env; set +a). Uses a throwaway database named trustdesk_test on the compose Postgres (dropped and recreated).
test-db:   ; docker compose exec -T db psql -U trustdesk -c "DROP DATABASE IF EXISTS trustdesk_test" -c "CREATE DATABASE trustdesk_test" && \
	TEST_DATABASE_URL=postgresql://trustdesk:$$POSTGRES_PASSWORD@127.0.0.1:5432/trustdesk_test .venv/bin/pytest -q

# AIDB mode tests (marker `aidb`). Needs the image aidb-postgres:17 (docker/aidb-postgres in the ecosystem repository; it needs the EDB
# repository token as a BuildKit secret, so CI cannot build it and these tests skip there). Starts a throwaway container, runs the whole
# suite against it (the plain-Postgres tests and the AIDB ones) and stops it. AIDB_CALLBACK_HOST: an address of this machine that the
# container can reach (Docker Desktop: 192.168.65.254); the scripted chat model listens there. No real chat model is run.
AIDB_NAME ?= trustdesk-aidb-test
AIDB_PORT ?= 55462
AIDB_IMAGE ?= aidb-postgres:17
AIDB_CALLBACK_HOST ?= 192.168.65.254
test-aidb:
	-docker stop $(AIDB_NAME) >/dev/null 2>&1
	docker run -d --rm --name $(AIDB_NAME) -p 127.0.0.1:$(AIDB_PORT):5432 -e POSTGRES_PASSWORD=postgres $(AIDB_IMAGE) -c aidb.allow_insecure_egress=on >/dev/null
	@for i in $$(seq 1 40); do docker exec $(AIDB_NAME) pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done; sleep 3
	docker exec $(AIDB_NAME) psql -U postgres -c "CREATE DATABASE trustdesk_test" >/dev/null
	TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:$(AIDB_PORT)/trustdesk_test AIDB_TEST_URL=postgresql://postgres:postgres@127.0.0.1:$(AIDB_PORT)/postgres AIDB_TEST_CALLBACK_HOST=$(AIDB_CALLBACK_HOST) .venv/bin/pytest -q; rc=$$?; docker stop $(AIDB_NAME) >/dev/null; exit $$rc
