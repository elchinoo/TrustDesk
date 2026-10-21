<p align="center"><img src="src/trustdesk/static/logo.svg" alt="TrustDesk logo" width="96" height="96"></p>

<h1 align="center">TrustDesk</h1>

A **compliance workspace** built on **Postgres + pgvector**, with a web app, a REST API and an **MCP server**. Load your company rules (HR, travel, expenses, security), let your applications send employee requests (vacation, sick leave, trips, expense reimbursements and anything else), and have every request **pre-validated by an AI model against your own documents** before an approver decides. The advice comes with the reasons and the quoted passages it is based on. You can also search the documents and ask questions that cite their sources.

> [!WARNING]
> **Hosted sandboxes can pause themselves and then answer `503`.**
> If you deploy TrustDesk on a platform whose free tier auto-pauses idle workloads (AgentPG sandboxes do), the app stops after a period of inactivity and the platform returns an empty **HTTP 503** for every URL until you resume it. Your data is safe (it lives in the database), but the app is unreachable.
>
> - **Resume it:** `agentpg sandbox resume <sandbox-id>`.
> - **Then unlock it:** a restart locks the stored provider keys until you sign in again (see [How secrets are handled](#how-secrets-are-handled)). Search with an external embedding provider and Ask need the keys.
> - **Plan for it:** resume a few minutes before any demo.
> - **Details and fixes:** see [Troubleshooting](#troubleshooting) and [Deploy on AgentPG](#deploy-on-agentpg).

## Features

- **Requests with AI pre-validation:** 19 request types (leave, trips, expenses, remote work, overtime, equipment, anything else). Each new request gets a recommendation (approve, reject, needs information), the reasons, and the policy passages it rests on, with quotes checked word for word against the documents. A person always decides; going against the advice needs a written justification. See [Compliance workflow](#compliance-workflow).
- **Client API** (`/api/v1`, see [API.md](API.md)) for applications that create employees and send requests, with a token scope that cannot see the AI advice or decide anything.
- **Reports:** overview, all requests, compliance by type, out of compliance, decisions against AI advice, expenses, leave, ageing and upcoming absences, as tables and CSV.
- **Web app** at `/`: requests queue and review window, employees, reports, documents, search with explainable results, **Ask** (an answer with citations from the model you pick), drag-and-drop upload, document viewer, provider accounts, API tokens, and copy-paste MCP setup for ten clients. Light and dark mode; works on phones.
- **Multiple provider accounts** (OpenAI, OpenRouter, GLM, Kimi, Claude, any OpenAI-compatible server), configured in the browser. Choose one for embeddings and one per question.
- **Hybrid retrieval**: vector and full-text results fused with reciprocal rank fusion (RRF), with provenance on every result.
- **Safe by default**: no secret files, admin password set on first run, hashed API tokens, encrypted provider keys. See [How secrets are handled](#how-secrets-are-handled).
- **MCP server** at `/mcp` (streamable HTTP): `search_knowledge`, `get_policy_passage`, `list_documents`.
- Plain-SQL migrations (no ORM), Markdown/text/PDF ingest with heading-aware chunking, idempotent and versioned.
- Works offline with a built-in demo embedder, and ships a fictional "Acme Cloudworks" policy pack for demos.

## Quick start

Requires Docker and [uv](https://docs.astral.sh/uv/) (or any Python 3.11+ environment).

```bash
cp .env.example .env              # then set POSTGRES_PASSWORD; .env is git-ignored
docker compose up -d db
uv venv && uv pip install -e ".[dev]"
set -a; . ./.env; set +a
.venv/bin/trustdesk migrate
.venv/bin/trustdesk serve         # http://localhost:8000
```

The first time the server starts it prints a **one-time setup code** to its console:

```
=== TrustDesk first-run setup ===
Open the app in your browser and enter this one-time code to create the admin password:
    XXXX-XXXX-XXXX
```

Open http://localhost:8000, enter the code and choose an admin password (12+ characters). Then go to **Documents** and click **Load demo policies**. Everything else is configured in the browser; there are no key files to create.

Everything in containers: `docker compose up --build`, then read the setup code from `docker compose logs api`.

### Embeddings

With no provider configured, a built-in demo embedder is used so everything runs with no credentials. It matches on words, not meaning. For real semantic search, add a provider account and switch embeddings to it (see [Using external LLMs](#using-external-llms-openai-openrouter-glm-kimi-claude-and-others)).

## How secrets are handled

Files full of credentials are the usual way these tools leak, so TrustDesk avoids them:

| Secret | Where it lives | How it is protected |
|---|---|---|
| **Admin password** | Entered once in the browser (first run needs the setup code from the server log) | Stored only as a salted **scrypt** hash. |
| **Browser session** | An `HttpOnly`, `SameSite=Strict` cookie (`Secure` over HTTPS) | Random value, only its SHA-256 stored server-side, 12-hour expiry, signing out or changing the password ends every session. No credential is kept in page storage. State-changing browser requests also need a custom header. |
| **API tokens** (MCP, scripts) | Created in **Settings**, shown **once** | Only a SHA-256 hash is stored. Revocable. `read` tokens can search and list but cannot change anything or spend provider credit. |
| **Provider API keys** (OpenAI etc.) | Entered in **Settings** | Encrypted (AES via Fernet) with a random **data key**. The data key is never stored in the clear: it is wrapped under a key derived from your admin password (scrypt). The database alone reveals nothing. Keys are never returned by the API (only the last 4 characters) and never logged. |

**What this means in practice**

- After a **restart** the stored provider keys are **locked** until you sign in or use the unlock box. The unwrapped key exists only in the server's memory. Until then, external embeddings and Ask return "locked" (searching with the demo embedder keeps working).
- For unattended restarts, you may *additionally* provide a platform-managed secret as `TRUSTDESK_ENCRYPTION_KEY` (or a file at `/etc/trustdesk/encryption_key`). The server then wraps the data key a second time and unlocks itself. This reintroduces one secret outside the app; keep it in your platform's secret store, never in git or an image.
- **Forgot the admin password?** Run `trustdesk reset-admin` on the server (it needs database access). It clears the password, the vault and **every stored provider key**, and the setup flow starts again. There is no back door: that is the point.
- Optionally, a static `TRUSTDESK_API_KEY` (environment variable or file) still works as a full-access credential for automation. Prefer API tokens.
- **Outbound requests:** provider URLs you enter are checked before every call. Loopback, private, link-local (cloud metadata) and carrier-grade-NAT addresses are refused, along with embedded credentials and plain `http` to non-local hosts; redirects are never followed. To allow a local model server such as Ollama, the operator sets `TRUSTDESK_ALLOW_PRIVATE_LLM_URLS=true`.

See [SECURITY.md](SECURITY.md) for the full model and its limits.

## Using external LLMs (OpenAI, OpenRouter, GLM, Kimi, Claude and others)

A language model does two different jobs here, configured in different places:

| Job | Where | Notes |
|---|---|---|
| **Embeddings**: turn documents and questions into vectors for search | **Settings > Search embeddings**: pick one account | Needs an OpenAI-compatible `/embeddings` endpoint. Switching models re-embeds your passages in the background, with progress, and changes nothing if the provider call fails. |
| **Answering**: write the reply from retrieved passages | **Ask**: pick an account for each question | Needs an OpenAI-compatible `/chat/completions` endpoint. The answer cites the numbered passages; an answer that cites none is flagged. |
| **Answering through your own tools** | Your MCP client's model | Claude Code uses Claude, Codex uses OpenAI models, and so on; see [Using the MCP server](#using-the-mcp-server). |

### Add accounts in the browser

Open **Settings > Provider accounts > Add account**, choose a provider (this pre-fills the URL and suggestions), paste the API key, set the chat and/or embedding model names, and use **Test chat** / **Test embeddings** to confirm it works. You can add as many accounts as you like. Model names change often, so use the ones from your provider's documentation.

| Provider | Base URL | Notes |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | Embedding model `text-embedding-3-small` supports a custom size, so 768 works. |
| OpenRouter | `https://openrouter.ai/api/v1` | One key for many models; has an OpenAI-style embeddings endpoint (for example `openai/text-embedding-3-small`). |
| Zhipu GLM | `https://open.bigmodel.cn/api/paas/v4` | Documented as OpenAI-compatible; embedding model `embedding-3`. Note `/v4`, not `/v1`. |
| Moonshot Kimi | `https://api.moonshot.ai/v1` | Documented for chat only; use another account for embeddings. |
| Anthropic Claude | `https://api.anthropic.com/v1` | Anthropic's OpenAI-compatibility endpoint, which they describe as not for production use. Chat only: Anthropic offers no embedding model. |
| Self-hosted (Ollama, vLLM, LiteLLM) | for example `http://localhost:11434/v1` | Needs `TRUSTDESK_ALLOW_PRIVATE_LLM_URLS=true` on the server. |

> [!NOTE]
> These providers were checked against their public documentation only. The request format is covered by automated tests against a mock server and was exercised end to end in the UI against a fake local provider, but it has **not** been run against each live provider. Test with a few documents first.

**Choosing the embedding dimension.** It must match what the model returns; if it doesn't, TrustDesk tells you the number the model actually returned. The index supports at most 2000 dimensions, so models whose default is larger (for example 3072) must be asked for a smaller size.

**Privacy.** With an external provider, the text of document passages, search queries and questions is sent to that provider. Don't use it for documents you aren't allowed to share with them, and check their data-retention terms.

### Environment configuration (optional)

You can also configure a single provider from the environment instead of the browser. It appears read-only in Settings as "Environment".

| Variable | Meaning |
|---|---|
| `LLM_BASE_URL` | Provider base URL. Empty means the demo embedder. |
| `LLM_API_KEY` | Provider key. Prefer entering keys in the browser. |
| `EMBEDDING_MODEL`, `EMBEDDING_DIM` | Embedding model and vector size (default `768`). |
| `LLM_CHAT_MODEL` | Chat model, to use this account in Ask. |
| `EMBEDDING_SEND_DIMENSIONS` | `false` if the provider rejects the `dimensions` field. |

From a terminal, `trustdesk reembed` re-embeds everything after you change these.

## Compliance workflow

1. **Load the rules.** Under **Documents**, upload your policies (Markdown, text or PDF) and tag them (HR, travel, expense, governance, security). **Load demo policies** adds a fictional "Acme" pack covering vacation, sick and parental leave, remote work, overtime, travel, expenses, customer entertainment and who approves what.
2. **Connect an AI account.** Add an account with a chat model under **Settings > Provider accounts**, then choose it under **Request validation**. Without one, requests still arrive but without advice.
3. **Requests arrive** from your applications through the [client API](API.md) or by hand (**New request**). TrustDesk checks the fields, stores the request and starts the pre-validation in the background.
4. **Pre-validation** (a few seconds). The system computes the facts a model gets wrong when it guesses: working days, notice given, overlapping leave, leave already taken this year, expenses already approved, possible duplicates, missing receipts. It retrieves the policy passages that relate to the request type and the request text, and asks the model for one structured verdict. TrustDesk then checks that verdict before anyone sees it: citations must point at real passages, each quote is matched word for word against the passage and marked verified or not, a "yes" or "no" with no citable basis becomes "needs information", and an approval is not suggested while an automatic check fails (for example an overlap with approved leave).
5. **The review window** shows the request, the employee, the recommendation, the reasons, the foundation (document, heading, version, quote, full passage), the automatic checks and the history. You **approve**, **reject**, or **ask for information** (which sends the request back to the employee). Rejecting or asking needs a note the employee will see. Approving against a "reject" recommendation, or rejecting against an "approve", needs a written justification and is listed under **Reports > Decisions against AI advice**.
6. **Reports** cover volumes and outcomes, compliance by request type, anything out of compliance (including requests approved anyway), expenses by department, approved leave, open requests by age and upcoming absences. Each can be downloaded as CSV.

What goes to the AI provider: the request text and details, the system-computed facts (department, job title, level, country, tenure, dates, amounts) and the retrieved policy passages. **Not** names, e-mail addresses or employee numbers. Free text written by an employee can still contain personal data, so use a provider you are allowed to send it to.

Limits worth knowing: the AI only advises and can be wrong, so read the quoted passages. Working days exclude weekends but not public holidays. There is one approver identity (the signed-in admin); there are no per-employee sign-ins, e-mail notifications or receipt uploads, and validation is only as good as the documents you load. Validation quality has been exercised with a scripted stand-in model in tests and for the UI, not benchmarked against real providers.

## Using the web app

- **Requests:** the queue (open by default) with counts, filters and the review window described above. **New request** adds one on behalf of an employee.
- **Employees:** add, edit, deactivate; **Requests** jumps to that person's requests.
- **Reports:** pick a report and dates, run it, print it or download the CSV.
- **Search:** ask in plain language. Each result shows its document, heading, version and how it ranked in the vector and full-text searches. "Copy citation" copies a ready-to-paste reference.
- **Ask:** choose an account and ask a question. You get an answer with `[1]`-style citations that jump to the numbered source passages. Treat it as a draft: check the sources.
- **Documents:** drag files in (Markdown, text, PDF; 10 MB max each), choosing the type first (HR, travel, expense, governance, security or general). Re-uploading a changed file replaces the old version. **Load demo policies** adds a fictional policy pack. **View** shows how a document was split into chunks.
- **Settings:** provider accounts, which account provides embeddings, your admin password and API tokens.
- **Connect:** pick your AI client and copy a ready-made configuration with this server's URL filled in.

Scripting uploads needs an API token with **full** access (Settings > API tokens):

```bash
for f in src/trustdesk/seed/acme/*.md; do
  curl -H "Authorization: Bearer $TRUSTDESK_TOKEN" -F file=@"$f" https://<your-host>/api/documents
done
```

## Using the MCP server

TrustDesk serves [MCP](https://modelcontextprotocol.io) over streamable HTTP at `https://<your-host>/mcp`, authenticated with `Authorization: Bearer <token>`. Create a **read-only** token under **Settings > API tokens**. Browser sessions are not accepted on `/mcp`. Tools:

| Tool | Purpose |
|---|---|
| `search_knowledge(query, limit?, doc_type?)` | Hybrid search; returns passages with document, version and heading to cite |
| `get_policy_passage(chunk_id)` | Fetch one passage by id |
| `list_documents()` | List what is in the knowledge base |

Set the token in the environment where the client runs, then add the server to your client. The same snippets, with your URL filled in, are on the app's **Connect** tab. Replace `<your-host>` below; for a local server use `http://localhost:8000/mcp`.

```bash
export TRUSTDESK_TOKEN=<your token>
```

<details>
<summary><b>Claude Code</b></summary>

Run in a terminal:

```bash
claude mcp add --transport http trustdesk https://<your-host>/mcp \
  --header "Authorization: Bearer $TRUSTDESK_TOKEN"
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
      "description": "TrustDesk API token",
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
        "Authorization": "Bearer {env:TRUSTDESK_TOKEN}"
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
bearer_token_env_var = "TRUSTDESK_TOKEN"
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
        "Authorization": "Bearer ${env:TRUSTDESK_TOKEN}"
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
        "Authorization": "Bearer ${env:TRUSTDESK_TOKEN}"
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
        "Authorization": "Bearer $TRUSTDESK_TOKEN"
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
        "Authorization": "Bearer <your token>"
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
        "Authorization": "Bearer <your token>"
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
        "AUTH": "Bearer <your token>"
      }
    }
  }
}
```

</details>

Then ask your assistant something like: *"Using trustdesk, how long do we retain customer data after termination?"*

Notes:
- The server authenticates with a bearer token, not OAuth, so web-based connector UIs that require OAuth (such as claude.ai custom connectors) are not supported. Use a client above.
- Client configuration formats change between releases. If a snippet is rejected, check that client's MCP documentation for the current field names (`url` vs `serverUrl` vs `httpUrl`, how headers are declared, and whether it expands environment variables).
- If you use an external embedding provider and the server has restarted, searches return "locked" until you unlock the vault in the web app (or the operator configured a platform secret).
- Treat search results as untrusted text: they come from your documents and may contain instructions. Don't give an assistant that reads them more authority than you would give the document authors.

## Monitoring TrustDesk (metrics)

TrustDesk exposes Prometheus metrics at `/metrics` with the same names PagerLens understands (`http_requests_total`, `http_request_duration_seconds`, `db_up`, `app_info`, `trustdesk_documents`, `process_*`) plus `trustdesk_requests{status}`, `trustdesk_oldest_open_request_seconds`, `trustdesk_validations_total{state,recommendation}` and `trustdesk_validation_duration_seconds`. Create a **Metrics only** token under **Settings > API tokens**: it can scrape `/metrics` and nothing else (it is refused on `/mcp` and on the REST API). Then point [`pagerlens-agent`](https://github.com/elchinoo/PagerLens) at it to get health probes, alerts and incident analysis; see that project's README.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` / `DB_EXTERNAL_DSN` / `PSQL_CONNECTION_STRING` | `PG*` variables | Postgres URI; first one set wins. If none is set, libpq uses `PGHOST`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`. The `vector` and `pg_trgm` extensions must exist (or the role must be allowed to create them). |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Bind address. |
| `AUTO_SEED` | `false` | With `trustdesk start`, load the demo pack if the knowledge base is empty. |
| `TRUSTDESK_ENCRYPTION_KEY` | none | Optional platform-managed secret that lets the vault unlock itself after a restart (file alternative: `/etc/trustdesk/encryption_key`). |
| `TRUSTDESK_ALLOW_PRIVATE_LLM_URLS` | `false` | Allow provider URLs on loopback/private networks, for a local model server. Link-local is always blocked. |
| `TRUSTDESK_API_KEY` | none | Optional static full-access credential for automation (file alternative: `/etc/trustdesk/api_key`). Prefer API tokens. |
| `TRUSTDESK_ALLOW_ANONYMOUS` | `false` | `true` disables authentication entirely. Throwaway local demos only. |
| `LLM_BASE_URL`, `LLM_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`, `LLM_CHAT_MODEL`, `EMBEDDING_SEND_DIMENSIONS` | | Optional environment provider; see [Environment configuration](#environment-configuration-optional). |

## Container image

`.github/workflows/build-image.yml` lints, tests, then publishes `ghcr.io/<owner>/<repo>` (`latest`, `sha-*`, version tags) from `main`, with a nightly rebuild for base-image updates.

The image runs `trustdesk start`: connect to the database (retrying while a scaled-to-zero database wakes), apply migrations, optionally seed, then serve. It runs as a non-root user and sets `SSL_CERT_FILE` so `sslmode=verify-full` works with the bundled libpq.

## Deploy on AgentPG

> [!IMPORTANT]
> **Free-tier sandboxes auto-pause when idle and cannot be kept awake.** `agentpg sandbox status` shows `Auto-pause: true`, and the AgentPG docs state it can't be disabled on the Free tier (plan changes were also unavailable during the private preview). A paused sandbox answers **503** and does not wake on a request. Before you rely on a deployment, read [Troubleshooting](#troubleshooting).

AgentPG sandboxes run any public image and can be linked to a Postgres instance, which injects the connection details (`PSQL_CONNECTION_STRING` and the `PG*` variables). TrustDesk reads those automatically. **No key files are needed.**

```bash
agentpg login
agentpg create --name <instance-name> --size small           # or reuse an instance; note its id
agentpg extensions create vector <instance-id>              # may need elevated rights, so enable them here
agentpg extensions create pg_trgm <instance-id>

agentpg sandbox create trustdesk \
  --image ghcr.io/<owner>/trustdesk:latest \
  --instance <instance-id> --port 8000

agentpg sandbox status <sandbox-id>                         # prints the public HTTPS URL
agentpg sandbox logs <sandbox-id>                           # contains the one-time setup code
```

Then open the URL, enter the **setup code from the logs**, choose an admin password, and click **Load demo policies** on the Documents tab. Add provider accounts under Settings.

Notes:
- `--port 8000` is required: without it the platform replaces the image's command with an idle process.
- The image must be **public** on GHCR (package settings > change visibility).
- Setup is protected by the code in the logs, which only someone who can read the sandbox logs has. Complete setup promptly after creating the sandbox: until then, anyone who finds the URL can try to guess the code (attempts are rate limited).
- `agentpg sandbox create` accepts `--env KEY=VALUE` (repeatable), e.g. `--env AUTO_SEED=true` to load the demo policies on first start. Keep secrets out of it: everything sensitive is configured in the browser, and values passed with `--env` are not a secret store.
- Free-tier sandboxes auto-pause when idle and do **not** wake on a request: a paused sandbox returns 503 until you run `agentpg sandbox resume`. After a resume, **unlock the provider keys** with your admin password. See [Troubleshooting](#troubleshooting).
- Optional: the documented way to give a sandbox a file is `--file`. If you want unattended unlock after restarts, mount a platform-managed secret at `/etc/trustdesk/encryption_key`, created outside this repository, and never commit it. The trade-offs are described in [How secrets are handled](#how-secrets-are-handled).

## Troubleshooting

### I get a 503 on every page

**Cause:** the sandbox is paused, not broken. Auto-pause is on by default and can't be turned off on the Free tier. While paused, the platform's proxy returns an empty 503 for *every* path, including `/healthz` and the static files, before any request reaches TrustDesk. Nothing expires on the app side.

**How to confirm:**

```bash
agentpg sandbox status <sandbox-id>     # look for "Status: paused"
curl -i https://<your-host>/healthz     # a 503 with no security headers (no content-security-policy) came from the platform, not the app
```

**Fix:** `agentpg sandbox resume <sandbox-id>`, reload the page and sign in. The web app explains a 502/503/504 itself and offers a Retry button.

### The page says "Provider keys are locked"

The server restarted (a resume is a restart), and the stored provider keys can only be unwrapped with your admin password, which is not stored anywhere. Enter the password in the banner. Until then, external embeddings and Ask are paused; searching with the demo embedder still works. To avoid this on unattended restarts, see the platform secret under [How secrets are handled](#how-secrets-are-handled).

### I forgot the admin password

Run `trustdesk reset-admin` where the server can reach the database (on AgentPG: `agentpg sandbox console <sandbox-id>`). This clears the password, the vault and every stored provider key, then restarts the first-run setup (a new setup code is printed to the log on the next start). Re-add your provider accounts' keys afterwards.

### What survives a pause

Your documents, search index, accounts (without their keys being usable until unlocked), API tokens and sessions live in the Postgres instance and are unaffected. Only the app process restarts. Pausing is not data loss.

### Ways to avoid surprises

- Resume the sandbox shortly before a demo or a test session and check `/readyz` first.
- Ask your platform provider whether the idle timeout can be lengthened or auto-pause turned off for your account.
- If you need an always-on service, run the container on infrastructure without idle auto-pause, such as a small VM or a platform tier that doesn't pause.

### `/readyz` returns 503 with `waking-database`

This one comes from TrustDesk itself and is different from the platform 503 above: the app is running but cannot reach the database yet, for example while a scaled-to-zero database wakes. Wait a few seconds and retry; the app retries its connection with backoff.

### Search says "re-embedding is in progress"

You switched embedding providers. Search and uploads pause while every passage is re-embedded (progress is shown in Settings). If the provider fails midway, nothing is changed.

## Tests

```bash
make test       # unit tests, no database needed
make test-db    # integration tests against a throwaway database on the compose Postgres
                # (needs POSTGRES_PASSWORD exported: set -a; . ./.env; set +a)
```

## Security

Read [SECURITY.md](SECURITY.md) before exposing this to a network. In short: no secret files, a salted-hash admin password, hashed revocable tokens, encrypted provider keys that stay locked after a restart, rate-limited sign-in, strict CSP, SSRF-guarded provider URLs, bounded uploads, non-root container. The knowledge base is single-tenant: anyone with full access can read and change every document.

## Design notes

- **Just Postgres:** SQL lives in `src/trustdesk/migrations/` and `src/trustdesk/search.py`.
- **Cold-start tolerant:** `db.connect` retries with backoff and `/readyz` reports `waking-database` rather than failing hard.
- **Read-only MCP tools:** the MCP server only searches and lists; retrieved text is data, never instructions.
- **Limits:** 10 MB uploads, 500-page PDFs, text extraction only (no OCR).

## License

Apache License 2.0. See [LICENSE](LICENSE).
