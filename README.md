# TrustDesk

A security-questionnaire knowledge base built on **Postgres + pgvector**, with a REST API and an **MCP server**. Load your security policies, then search them with hybrid (vector + full-text) retrieval from a browser, an HTTP client, or any MCP-capable AI assistant. Every result carries its source document, version and heading so answers can cite evidence.

## Features

- Plain-SQL migrations; no ORM. pgvector HNSW, GIN full-text and trigram indexes.
- Markdown, text and PDF ingest with heading-aware chunking. Re-ingesting is idempotent; edited files replace the old version.
- Hybrid retrieval: vector and full-text results fused with reciprocal rank fusion (RRF).
- REST API (`/api/search`, `/api/documents`) and a small search page at `/`.
- MCP server at `/mcp` (streamable HTTP) with `search_knowledge` and `get_policy_passage`.
- Works offline with a built-in demo embedder, or with any OpenAI-compatible embeddings endpoint.
- A fictional "Acme Cloudworks" policy pack for demos (all content is invented).

## Quick start

Requires Docker and [uv](https://docs.astral.sh/uv/) (or any Python 3.11+ environment).

```bash
cp .env.example .env              # then set POSTGRES_PASSWORD (and optionally the others); .env is git-ignored
docker compose up -d db
uv venv && uv pip install -e ".[dev]"
set -a; . ./.env; set +a
.venv/bin/trustdesk migrate
.venv/bin/trustdesk seed acme
.venv/bin/trustdesk search "how is customer data encrypted at rest"
.venv/bin/trustdesk serve         # http://localhost:8000, MCP at /mcp
```

If `TRUSTDESK_API_KEY` is empty, `serve` generates a random key and prints it once in the console.

Everything in containers: `docker compose up --build` (binds to `127.0.0.1` only; the generated key is in `docker compose logs api`).

### Use the API

```bash
export KEY=<your key>
curl -H "Authorization: Bearer $KEY" "http://localhost:8000/api/search?q=encryption+at+rest&limit=2"
curl -H "Authorization: Bearer $KEY" -F file=@policy.md http://localhost:8000/api/documents
```

### Connect an AI assistant over MCP

```bash
claude mcp add --transport http trustdesk http://localhost:8000/mcp --header "Authorization: Bearer $KEY"
```

Then ask: "Using trustdesk, how long do we retain customer data after termination?"

### Embeddings

With `LLM_BASE_URL` empty, a built-in **hash embedder** is used so the demo runs with no credentials. It matches on words, not meaning. For semantic search point `LLM_BASE_URL` at any OpenAI-compatible endpoint and set `LLM_API_KEY`, `EMBEDDING_MODEL` and `EMBEDDING_DIM`. The vector dimension is fixed when migrations first run, so changing it requires a fresh schema.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` / `DB_EXTERNAL_DSN` | local compose DB | Postgres URI. The role must be able to `CREATE EXTENSION vector` and `pg_trgm`. `DATABASE_URL` wins if both are set. |
| `TRUSTDESK_API_KEY` | generated at startup | Bearer key for `/api` and `/mcp`. |
| `TRUSTDESK_ALLOW_ANONYMOUS` | `false` | `true` disables authentication. Throwaway local demos only. |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Bind address. |
| `AUTO_SEED` | `false` | With `trustdesk start`, load the demo pack if the knowledge base is empty. |
| `LLM_BASE_URL`, `LLM_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_DIM` | empty / empty / `text-embedding-3-small` / `768` | Embeddings endpoint. |

## Container image

`.github/workflows/build-image.yml` lints, tests, then publishes `ghcr.io/<owner>/<repo>` (`latest`, `sha-*`, version tags) from `main`, with a nightly rebuild for base-image updates.

The image runs `trustdesk start`: connect to the database (retrying while a scaled-to-zero database wakes), apply migrations, optionally seed, then serve. It runs as a non-root user and sets `SSL_CERT_FILE` so `sslmode=verify-full` works with the bundled libpq.

## Tests

```bash
make test       # unit tests, no database needed
make test-db    # integration tests against a throwaway database on the compose Postgres
                # (needs POSTGRES_PASSWORD exported: set -a; . ./.env; set +a)
```

## Security

Read [SECURITY.md](SECURITY.md) before exposing this to a network. In short: authenticated by default and fail-closed, rate-limited key checks, strict CSP, bounded uploads, non-root container, no secrets in the image or repository. The API is single-tenant: anyone with the key can read and add every document.

## Design notes

- **Just Postgres:** SQL lives in `src/trustdesk/migrations/` and `src/trustdesk/search.py`.
- **Cold-start tolerant:** `db.connect` retries with backoff and `/readyz` reports `waking-database` rather than failing hard.
- **Read-only tools:** the MCP server only searches; retrieved text is data, never instructions.
- **Limits:** 10 MB uploads, 500-page PDFs, text extraction only (no OCR).

## License

Apache License 2.0. See [LICENSE](LICENSE).
