.PHONY: up down migrate seed test test-db serve lint kit-local
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
