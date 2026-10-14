<p align="center"><img src="src/trustdesk/static/logo.svg" alt="TrustDesk logo" width="96" height="96"></p>

<h1 align="center">TrustDesk</h1>

A security-questionnaire knowledge base built on **Postgres + pgvector**, with a REST API and an **MCP server**. Load your security policies, then search them with hybrid (vector + full-text) retrieval from a browser, an HTTP client, or any MCP-capable AI assistant. Every result carries its source document, version and heading so answers can cite evidence.

> [!WARNING]
> **Hosted sandboxes can pause themselves and then answer `503`.**
> If you deploy TrustDesk on a platform whose free tier auto-pauses idle workloads (AgentPG sandboxes do), the app stops after a period of inactivity and the platform returns an empty **HTTP 503** for every URL until you resume it. Your data is safe (it lives in the database), but the app is unreachable and a **generated API key changes on every restart**.
>
> - **Resume it:** `agentpg sandbox resume <sandbox-id>`, then read the new key from `agentpg sandbox logs <sandbox-id>`.
> - **Plan for it:** resume a few minutes before any demo, and don't hand out a URL you expect to stay up on a free tier.
> - **Details and fixes:** see [Troubleshooting](#troubleshooting) and [Deploy on AgentPG](#deploy-on-agentpg).

## Features

- Plain-SQL migrations; no ORM. pgvector HNSW, GIN full-text and trigram indexes.
- Markdown, text and PDF ingest with heading-aware chunking. Re-ingesting is idempotent; edited files replace the old version.
- Hybrid retrieval: vector and full-text results fused with reciprocal rank fusion (RRF).
- A web app at `/`: search with explainable results, drag-and-drop document upload, document viewer, one-click demo data, and copy-paste MCP setup for popular clients. Works in light and dark mode and on phones.
- REST API (`/api/search`, `/api/documents`, `/api/stats`, `/api/seed`).
- MCP server at `/mcp` (streamable HTTP) with `search_knowledge`, `get_policy_passage` and `list_documents`.
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

### Embeddings

With `LLM_BASE_URL` empty, a built-in demo embedder is used so everything runs with no credentials. It matches on words, not meaning. For real semantic search, see [Using external LLMs](#using-external-llms-openai-openrouter-glm-kimi-claude-and-others).

## Using external LLMs (OpenAI, OpenRouter, GLM, Kimi, Claude and others)

There are two different jobs a language model can do here, and they are configured in different places.

| Job | Who does it today | How to choose the model |
|---|---|---|
| **Embeddings**: turning documents and questions into vectors for search | TrustDesk calls an **OpenAI-compatible `/embeddings` endpoint** | Environment variables below |
| **Answering**: reading the retrieved passages and writing the reply | **Your AI assistant**, through [MCP](#using-the-mcp-server). TrustDesk does not call a chat model itself yet | Whatever model your client uses (Claude Code uses Claude, Codex uses OpenAI models, and so on). Other clients let you pick a provider |

So to "use Claude, Kimi or GLM" for answers, connect a client that runs that model. To use a hosted model for search quality, configure embeddings as follows.

### Configure embeddings

```bash
LLM_BASE_URL=https://api.openai.com/v1      # the provider's base URL; TrustDesk appends /embeddings
LLM_API_KEY=<provider key>                  # or mount it as a file (see below)
EMBEDDING_MODEL=text-embedding-3-small      # the provider's embedding model name
EMBEDDING_DIM=768                           # vector size to store; see the notes below
```

| Provider | `LLM_BASE_URL` | Example `EMBEDDING_MODEL` | Notes |
|---|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `text-embedding-3-small` | Supports choosing the output size, so `EMBEDDING_DIM=768` works. |
| OpenRouter | `https://openrouter.ai/api/v1` | `openai/text-embedding-3-small`, `qwen/qwen3-embedding-0.6b` | Has an OpenAI-style embeddings endpoint (`POST /embeddings`) with an optional `dimensions` field. Model list: `GET https://openrouter.ai/api/v1/embeddings/models`. |
| Zhipu GLM | `https://open.bigmodel.cn/api/paas/v4` | `embedding-3` | Documented as OpenAI-compatible. `dimensions` is supported on `embedding-3` and later only. Note the `/v4` path, not `/v1`. |
| Moonshot Kimi | `https://api.moonshot.ai/v1` | none | Its OpenAI-compatible API is documented for **chat**. We could not confirm an embeddings endpoint, so use another provider above for embeddings and Kimi (via a client that supports it) for answering. |
| Anthropic Claude | none | none | Anthropic does not offer an embedding model and points to Voyage AI, whose API is **not** OpenAI-compatible. Use another provider for embeddings; use Claude through an MCP client such as Claude Code for answering. |
| Self-hosted (Ollama, vLLM, LiteLLM proxy) | for example `http://localhost:11434/v1` | the model you serve | Anything exposing `POST {base}/embeddings` in the OpenAI format works. |

> [!NOTE]
> These providers were checked against their public documentation only. TrustDesk's request format is covered by automated tests against a mock server, but it has **not** been run against each live provider. Test with a few documents first.

**Choosing `EMBEDDING_DIM`:**
- It must match what the model returns. If it doesn't, TrustDesk stops with the number the model actually returned.
- The default is 768. The index supports at most 2000 dimensions, so models that default to larger vectors (for example 3072) must be asked for a smaller size.
- If the provider rejects the `dimensions` field, set `EMBEDDING_SEND_DIMENSIONS=false` and set `EMBEDDING_DIM` to the model's native size.

**Switching models later.** Vectors from different models are not comparable, so after changing `LLM_BASE_URL`, `EMBEDDING_MODEL` or `EMBEDDING_DIM`, re-embed everything once:

```bash
trustdesk reembed      # changes the vector size if needed, re-embeds every chunk, rebuilds the index
```

Search is unavailable while it runs, and it is all-or-nothing: if the provider fails midway, nothing changes. On start, TrustDesk refuses to run if `EMBEDDING_DIM` doesn't match the stored vectors, and tells you to match it or run `trustdesk reembed`.

**Keeping the key out of the environment.** If your platform can only mount files, put the key in `/etc/trustdesk/llm_api_key` (or point `LLM_API_KEY_FILE` at another path). The environment variable wins if both are set.

**Privacy.** With an external provider, the text of every document chunk and every search query is sent to that provider. Don't enable it for documents you aren't allowed to share with them, and check the provider's data-retention terms.

**On AgentPG sandboxes.** The documented ways to give a sandbox settings are `--file` (mount a file) and `--command`. AgentPG can also store provider keys on your account, but its docs don't say how they reach a sandbox, so don't rely on that. A pattern that fits the documented options, **not yet verified end to end**: mount the key as a file and pass the non-secret settings through the command:

```bash
agentpg sandbox create trustdesk --image ghcr.io/<owner>/trustdesk:latest --instance <instance-id> --port 8000 \
  --file /etc/trustdesk/llm_api_key=$HOME/llm_api_key \
  --command "env LLM_BASE_URL=https://api.openai.com/v1 EMBEDDING_MODEL=text-embedding-3-small EMBEDDING_DIM=768 trustdesk start"
```

Do not put the key itself in `--command`: command lines are easy to leak. After the first start, check `agentpg sandbox logs <sandbox-id>` for errors, and use `agentpg sandbox console <sandbox-id>` to run `trustdesk reembed` if you changed models on an existing database.

## Using the web app

Open the server URL in a browser and enter your API key (kept in that tab only).

- **Search:** ask a question in plain language. Each result shows its document, heading, version and how it ranked in the vector and full-text searches. "Copy citation" copies a ready-to-paste reference.
- **Documents:** drag files in (Markdown, text, PDF; 10 MB max each) or click to browse. Re-uploading a changed file replaces the old version. **Load demo policies** adds a fictional policy pack so you can try it immediately. **View** shows how a document was split into chunks; **Delete** removes it.
- **Connect:** pick your AI client and copy a ready-made configuration with this server's URL filled in.

Uploading from a terminal works too:

```bash
for f in src/trustdesk/seed/acme/*.md; do
  curl -H "Authorization: Bearer $TRUSTDESK_API_KEY" -F file=@"$f" https://<your-host>/api/documents
done
```

## Using the MCP server

TrustDesk serves [MCP](https://modelcontextprotocol.io) over streamable HTTP at `https://<your-host>/mcp`, authenticated with `Authorization: Bearer <TRUSTDESK_API_KEY>`. Tools:

| Tool | Purpose |
|---|---|
| `search_knowledge(query, limit?, doc_type?)` | Hybrid search; returns passages with document, version and heading to cite |
| `get_policy_passage(chunk_id)` | Fetch one passage by id |
| `list_documents()` | List what is in the knowledge base |

Set your key in the environment where the client runs, then add the server to your client. The same snippets, with your URL filled in, are on the app's **Connect** tab. Replace `<your-host>` below; for a local server use `http://localhost:8000/mcp`.

```bash
export TRUSTDESK_API_KEY=<your key>
```

<details>
<summary><b>Claude Code</b></summary>

Run in a terminal:

```bash
claude mcp add --transport http trustdesk https://<your-host>/mcp \
  --header "Authorization: Bearer $TRUSTDESK_API_KEY"
```

</details>

<details>
<summary><b>VS Code (GitHub Copilot)</b></summary>

Save as .vscode/mcp.json in your workspace (VS Code asks for the key once and stores it securely):

```json
{
  "servers": {
    "trustdesk": {
      "type": "http",
      "url": "https://<your-host>/mcp",
      "headers": {
        "Authorization": "Bearer ${input:trustdesk-key}"
      }
    }
  },
  "inputs": [
    {
      "type": "promptString",
      "id": "trustdesk-key",
      "description": "TrustDesk API key",
      "password": true
    }
  ]
}
```

</details>

<details>
<summary><b>OpenCode</b></summary>

Add to opencode.json (project) or ~/.config/opencode/opencode.json:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "trustdesk": {
      "type": "remote",
      "url": "https://<your-host>/mcp",
      "enabled": true,
      "headers": {
        "Authorization": "Bearer {env:TRUSTDESK_API_KEY}"
      }
    }
  }
}
```

</details>

<details>
<summary><b>Codex CLI</b></summary>

Add to ~/.codex/config.toml:

```toml
[mcp_servers.trustdesk]
url = "https://<your-host>/mcp"
bearer_token_env_var = "TRUSTDESK_API_KEY"
```

</details>

<details>
<summary><b>Cursor</b></summary>

Add to ~/.cursor/mcp.json (or .cursor/mcp.json in a project):

```json
{
  "mcpServers": {
    "trustdesk": {
      "url": "https://<your-host>/mcp",
      "headers": {
        "Authorization": "Bearer ${env:TRUSTDESK_API_KEY}"
      }
    }
  }
}
```

</details>

<details>
<summary><b>Windsurf</b></summary>

Add to ~/.codeium/windsurf/mcp_config.json:

```json
{
  "mcpServers": {
    "trustdesk": {
      "serverUrl": "https://<your-host>/mcp",
      "headers": {
        "Authorization": "Bearer ${env:TRUSTDESK_API_KEY}"
      }
    }
  }
}
```

</details>

<details>
<summary><b>Gemini CLI</b></summary>

Add to ~/.gemini/settings.json:

```json
{
  "mcpServers": {
    "trustdesk": {
      "httpUrl": "https://<your-host>/mcp",
      "headers": {
        "Authorization": "Bearer $TRUSTDESK_API_KEY"
      }
    }
  }
}
```

</details>

<details>
<summary><b>Cline</b></summary>

Add to cline_mcp_settings.json (Cline: MCP Servers, then Configure):

```json
{
  "mcpServers": {
    "trustdesk": {
      "type": "streamableHttp",
      "url": "https://<your-host>/mcp",
      "headers": {
        "Authorization": "Bearer <your key>"
      }
    }
  }
}
```

</details>

<details>
<summary><b>Zed</b></summary>

Add to Zed settings.json:

```json
{
  "context_servers": {
    "trustdesk": {
      "url": "https://<your-host>/mcp",
      "headers": {
        "Authorization": "Bearer <your key>"
      }
    }
  }
}
```

</details>

<details>
<summary><b>Claude Desktop</b></summary>

Claude Desktop needs a local bridge for bearer-key servers. Add to claude_desktop_config.json (requires Node.js):

```json
{
  "mcpServers": {
    "trustdesk": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://<your-host>/mcp",
        "--header",
        "Authorization:${AUTH}"
      ],
      "env": {
        "AUTH": "Bearer <your key>"
      }
    }
  }
}
```

</details>

Then ask your assistant something like: *"Using trustdesk, how long do we retain customer data after termination?"*

Notes:
- The server authenticates with a static bearer key, not OAuth, so web-based connector UIs that require OAuth (such as claude.ai custom connectors) are not supported. Use a client above.
- Client configuration formats change between releases. If a snippet is rejected, check that client's MCP documentation for the current field names (`url` vs `serverUrl` vs `httpUrl`, how headers are declared, and whether it expands environment variables).
- Treat search results as untrusted text: they come from your documents and may contain instructions. Don't give an assistant that reads them more authority than you would give the document authors.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` / `DB_EXTERNAL_DSN` / `PSQL_CONNECTION_STRING` | `PG*` variables | Postgres URI; first one set wins. If none is set, libpq uses `PGHOST`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`. The `vector` and `pg_trgm` extensions must exist (or the role must be allowed to create them). |
| `TRUSTDESK_API_KEY` | generated at startup | Bearer key for `/api` and `/mcp`. Alternatively the first line of the file at `TRUSTDESK_API_KEY_FILE` (default `/etc/trustdesk/api_key`). |
| `TRUSTDESK_ALLOW_ANONYMOUS` | `false` | `true` disables authentication. Throwaway local demos only. |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Bind address. |
| `AUTO_SEED` | `false` | With `trustdesk start`, load the demo pack if the knowledge base is empty. |
| `LLM_BASE_URL`, `LLM_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_DIM` | empty / empty / `text-embedding-3-small` / `768` | Embeddings endpoint; see [Using external LLMs](#using-external-llms-openai-openrouter-glm-kimi-claude-and-others). `LLM_API_KEY` can come from a file (`LLM_API_KEY_FILE`, default `/etc/trustdesk/llm_api_key`). |
| `EMBEDDING_SEND_DIMENSIONS` | `true` | Set `false` if the provider rejects the `dimensions` request field. |

## Container image

`.github/workflows/build-image.yml` lints, tests, then publishes `ghcr.io/<owner>/<repo>` (`latest`, `sha-*`, version tags) from `main`, with a nightly rebuild for base-image updates.

The image runs `trustdesk start`: connect to the database (retrying while a scaled-to-zero database wakes), apply migrations, optionally seed, then serve. It runs as a non-root user and sets `SSL_CERT_FILE` so `sslmode=verify-full` works with the bundled libpq.

## Deploy on AgentPG

> [!IMPORTANT]
> **Free-tier sandboxes auto-pause when idle and cannot be kept awake.** `agentpg sandbox status` shows `Auto-pause: true`, and the AgentPG docs state it can't be disabled on the Free tier (plan changes were also unavailable during the private preview). A paused sandbox answers **503** and does not wake on a request. Before you rely on a deployment, read [Troubleshooting](#troubleshooting).

AgentPG sandboxes run any public image and can be linked to a Postgres instance, which injects the connection details (`PSQL_CONNECTION_STRING` and the `PG*` variables). TrustDesk reads those automatically.

```bash
agentpg login
agentpg create <instance-name>                              # or reuse an instance; note its id
agentpg extensions create <instance-id> vector              # may need elevated rights, so enable them here
agentpg extensions create <instance-id> pg_trgm

python3 -c "import secrets; print(secrets.token_urlsafe(32))" > ~/trustdesk_api_key   # keep it OUTSIDE the repository
agentpg sandbox create trustdesk \
  --image ghcr.io/<owner>/trustdesk:latest \
  --instance <instance-id> --port 8000 \
  --file /etc/trustdesk/api_key=$HOME/trustdesk_api_key
# store the key in your password manager, then delete the file: rm -P ~/trustdesk_api_key

agentpg sandbox status <sandbox-id>                         # prints the public HTTPS URL
agentpg sandbox logs <sandbox-id> -f
```

Notes:
- `--port 8000` is required: without it the platform replaces the image's command with an idle process.
- The image must be **public** on GHCR (package settings → change visibility).
- With no key file, TrustDesk generates a key and prints it once in the sandbox logs.
- Environment variables cannot be set on a sandbox from the CLI, so `AUTO_SEED` is unavailable; to load the demo pack, open the app and click **Load demo policies** on the Documents tab.
- Free-tier sandboxes auto-pause when idle and do **not** wake on a request: a paused sandbox returns 503 until you run `agentpg sandbox resume`. See [Troubleshooting](#troubleshooting).

## Troubleshooting

### I get a 503 on every page (or the app "logged me out" with an error)

**Cause:** the sandbox is paused, not broken. Auto-pause is on by default and can't be turned off on the Free tier. While paused, the platform's proxy returns an empty 503 for *every* path, including `/healthz` and the static files, before any request reaches TrustDesk. Nothing expires on the app side: the API key is static, and the page only returns you to the sign-in screen on a 401.

**How to confirm:**

```bash
agentpg sandbox status <sandbox-id>     # look for "Status: paused"
curl -i https://<your-host>/healthz     # a 503 with no security headers (no content-security-policy) came from the platform, not the app
```

**Fix:**

```bash
agentpg sandbox resume <sandbox-id>
agentpg sandbox logs <sandbox-id>       # copy the new generated key, if you did not set one
```

Then reload the page and sign in again with the current key.

### My API key stopped working after a restart

Without a configured key, TrustDesk generates a random one at every start and prints it once in the logs. Resuming a paused sandbox is a restart, so the key changes. Read the new key from the logs, or configure a fixed one with `TRUSTDESK_API_KEY`, or by mounting a file at `/etc/trustdesk/api_key` (see [Configuration](#configuration)). If the file exists but can't be read by the container's non-root user, the logs print `WARNING: cannot read ... file` and the app falls back to a generated key. On an AgentPG sandbox the mounted file appeared as `-rwxr----- app root` (owned by the container's `app` user), so it is readable. If the logs nevertheless show `Generated a key`, the file was probably not in place when the process started; resume the sandbox once and check the logs again. To check without printing the key, run inside `agentpg sandbox console <sandbox-id>`:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $(cat /etc/trustdesk/api_key)" http://localhost:8000/api/stats   # 200 = the file key is accepted
```

### What survives a pause

Your documents and search index live in the Postgres instance and are unaffected. Only the app process restarts. Pausing is not data loss.

### Ways to avoid surprises

- Resume the sandbox shortly before a demo or a test session and check `/readyz` first.
- Ask your platform provider whether the idle timeout can be lengthened or auto-pause turned off for your account.
- If you need an always-on service, run the container on infrastructure without idle auto-pause, such as a small VM or a platform tier that doesn't pause.
- The web app explains a 502/503/504 itself ("The server is unavailable. It may be paused or waking up") and keeps your key in the browser tab, so retry after resuming.

### `/readyz` returns 503 with `waking-database`

This one comes from TrustDesk itself and is different from the platform 503 above: the app is running but cannot reach the database yet, for example while a scaled-to-zero database wakes. Wait a few seconds and retry; the app retries its connection with backoff.

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
