# TrustDesk guide

One document for TrustDesk: what it is, how it fits the ecosystem, how to install, configure, run and operate it, and what it does and does not do.

Everything below was checked against the code in this repository (and, for ecosystem behaviour, against `ecosystem/ecosystem.yaml`, `ecosystem/src/ecoctl/plan.py` and the sibling `agentpg-kit` and `CityPulseSimulator` sources) on the date of writing. Where something could not be verified it is listed under [Known limitations](#16-known-limitations). The README stays the short entry point; [API.md](../API.md) is the client API reference and [SECURITY.md](../SECURITY.md) is the security model. Live URLs are never written here: sandbox URLs change when an app is recreated, so this guide uses `<your-host>` and `https://<trustdesk>`.

Contents

1. [Purpose and audience](#1-purpose-and-audience)
2. [How TrustDesk fits the ecosystem](#2-how-trustdesk-fits-the-ecosystem)
3. [Architecture](#3-architecture)
4. [Install](#4-install)
5. [Configuration reference](#5-configuration-reference)
6. [First-run walkthrough](#6-first-run-walkthrough)
7. [Using TrustDesk](#7-using-trustdesk)
8. [Roles and permissions](#8-roles-and-permissions)
9. [Security notes](#9-security-notes)
10. [Operations](#10-operations)
11. [Troubleshooting](#11-troubleshooting)
12. [Testing and development](#12-testing-and-development)
13. [AIDB and AgentPG integration](#13-aidb-and-agentpg-integration)
14. [Data model](#14-data-model)
15. [CLI reference](#15-cli-reference)
16. [Known limitations](#16-known-limitations)

---

## 1. Purpose and audience

TrustDesk is a compliance workspace on **Postgres + pgvector**, with a web app, a REST API and an MCP server. You load your company rules (HR, travel, expenses, governance, security). Your applications send employee requests (vacation, sick leave, trips, expense reimbursements, anything else). Every request is **pre-validated by an AI model against your own documents** before a person decides. The advice comes with reasons and the policy passages it rests on, and each quote is checked word for word against the document. The AI never decides: only an approver does, and going against the advice needs a written justification.

It is also a showcase of what a single Postgres database can carry: the documents, the vector index, the full-text index, the workflow, the audit trail and the reports all live in the same database.

Who it is for:

| Reader | What they do with it |
|---|---|
| **Approver** (HR, finance, a manager) | Works the requests queue, reads the AI advice and the quoted rules, decides, runs reports. |
| **Administrator** | Loads policy documents, connects an AI provider, sets validation behaviour, creates API tokens, manages access. |
| **Application developer** | Sends employees and requests through the client API (`/api/v1`) from an HR, travel or expense application. |
| **Platform / demo owner** | Deploys it on AgentPG (alone or as part of the fleet), watches it through `/metrics`, resets it. |
| **AI assistant users** | Search the policies from Claude Code, Cursor and other MCP clients (read-only). |

## 2. How TrustDesk fits the ecosystem

TrustDesk is one app of a showcase fleet that demonstrates EDB AIDB and AgentPG. Every app runs on its **own AgentPG sandbox with its own Postgres instance** (TrustDesk creates tables such as `app_setting` and `api_token` in `public`, so two apps must not share a database). Apps share a library (`agentpg-kit`), an identity provider and an LLM gateway, and one tool (`ecoctl`) deploys the fleet from `ecosystem/ecosystem.yaml`.

```mermaid
flowchart LR
  subgraph fleet[Fleet deployed by ecoctl]
    ID[identity<br/>OIDC provider]
    GW[llm-gateway<br/>aliases, budgets]
    TD[TrustDesk]
    SIM[CityPulse Simulator<br/>cpsim]
    PL[PagerLens]
  end
  Browser((Person)) -- "sign in: code + PKCE" --> ID
  Browser -- "roles in token" --> TD
  SIM -- "client_credentials<br/>scope trustdesk.requests.write" --> ID
  SIM -- "Bearer token: employees, requests" --> TD
  TD -- "verifies tokens with JWKS" --> ID
  TD -- "OpenAI-compatible<br/>LLM_BASE_URL, LLM_API_KEY" --> GW
  PL -. "scrape /metrics<br/>with a metrics-only token" .-> TD
  MCP((MCP client)) -- "Bearer read token /mcp" --> TD
```

The solid arrows are wired by `ecosystem.yaml` and `ecoctl`. The dotted PagerLens arrow is a documented use that is **not** wired by the manifest (see below).

### The manifest entry

From `ecosystem/ecosystem.yaml`:

| Key | Value | Meaning |
|---|---|---|
| `llm` | `true` | `ecoctl` creates a gateway client named `trustdesk` and injects the `LLM_*` and `EMBEDDING_*` variables. |
| `extensions` | `vector`, `pg_trgm` | Enabled on the instance. |
| `generate` | `TRUSTDESK_ENCRYPTION_KEY` | A random platform secret that lets the vault unlock itself after every restart. |
| `env` | `AUTO_SEED: "true"` | Load the Acme demo policies into an empty knowledge base at start. |
| `roles` | `admin`, `approver`, `viewer` | Registered at the identity provider. |
| `exposes` | `trustdesk.requests.write`, `trustdesk.kb.read`, `trustdesk.metrics` | Scopes other services may request **for** TrustDesk. |
| groups | `ecosystem-admins` gets `admin` on every app; `support` gets `approver` on `trustdesk` | Granting a person to a group gives them the role. |

### Interaction with each project

| Project | Relation | How it works | Variables and scopes |
|---|---|---|---|
| **agentpg-kit** (`apgkit`) | Library | TrustDesk is built on it: database access with cold-start retries, the vault, authentication (local admin, tokens, OIDC), the LLM client, the SSRF guard, the settings store, metrics, the security middleware, migrations and the shared UI (`/kit/*`). The image installs kit tag `v0.2.5`; `pyproject.toml` requires `agentpg-kit>=0.2.5`. | none at runtime |
| **identity** | Sign-in and service tokens | Federation turns on when `TRUSTDESK_OIDC_ISSUER` is set. People sign in at `/auth/login` (authorization code with PKCE, state and nonce) and return to `/auth/callback`. API calls with a bearer JWT are verified locally against the provider's JWKS: signature (RS256, ES256 or EdDSA), issuer, audience (the client id, `trustdesk`), expiry. Roles come as `trustdesk:<role>` and only roles of this app count. Services use the `client_credentials` grant and carry scopes. | `TRUSTDESK_OIDC_ISSUER`, `TRUSTDESK_OIDC_CLIENT_ID` (`trustdesk`), `TRUSTDESK_OIDC_CLIENT_SECRET`, `TRUSTDESK_LOCAL_LOGIN`, optional `TRUSTDESK_PUBLIC_URL`. Scopes: `trustdesk.requests.write` (client application), `trustdesk.kb.read` (search and MCP), `trustdesk.metrics` (scrape). |
| **llm-gateway** | Model access | TrustDesk calls the gateway like any OpenAI-compatible provider, as the **environment account**. The gateway holds the provider keys, the alias-to-model mapping, per-client limits and budgets. TrustDesk uses it for request validation (chat), Ask (chat) and embeddings. | `LLM_BASE_URL` (`<gateway>/v1`), `LLM_API_KEY` (the `lg_` key of the client `trustdesk`), `LLM_CHAT_MODEL` (`chat-default`), `EMBEDDING_MODEL` (`embed-default`), `EMBEDDING_DIM` (`llm.embed_dimensions`, 1536 in the shipped manifest). Defaults per client: 60 requests per minute, 5,000,000 tokens per month, `hard`. Environment settings are trusted, so the gateway's public https URL needs no allow-private switch. |
| **CityPulse Simulator** (`cpsim`) | Calls TrustDesk | A simulator "company" creates employees and requests through the client API, withdraws a share of them and later reads statuses. It mints a short-lived token with `client_credentials` for the scope `trustdesk.requests.write` (or uses a saved client token). Calls it makes: `GET /api/v1/request-types`, `GET` and `POST /api/v1/employees`, `POST /api/v1/requests` with `Idempotency-Key: sim-<seed>-<n>`, `POST /api/v1/requests/{ref}/withdraw`, `GET /api/v1/requests/{ref}`. The manifest has `cpsim.calls.trustdesk: [trustdesk.requests.write]`. Requests then get AI advice in TrustDesk like any other. | In cpsim: `TRUSTDESK_URL` (injected by `ecoctl`, used as the fleet reference `${TRUSTDESK_URL}`), `CPSIM_SERVICE_CLIENT_ID` and `CPSIM_SERVICE_CLIENT_SECRET`, optional `TRUSTDESK_CLIENT_TOKEN`. On the TrustDesk side nothing is configured for it. |
| **PagerLens** | Monitors TrustDesk (documented, not wired) | `/metrics` uses the same metric names every app of the family exposes, so a `pagerlens-agent` can scrape it. The credential is a local **Metrics only** token (Settings > API tokens) or a service token with scope `trustdesk.metrics`. The manifest exposes the scope but the `pagerlens-agent` entry has no `calls` for TrustDesk, so `ecoctl` does not provision this; set it up by hand following the PagerLens README. | Scope `trustdesk.metrics`; token scope `metrics`. |
| **CityPulse** | None | No code or manifest link between TrustDesk and CityPulse. The simulator feeds CityPulse; TrustDesk is a separate target of the same simulator. | none |
| **PayBroker** | None | No link in code or manifest. (Both use the same LLM gateway and identity independently.) | none |
| **ShopFront** | None | No link in code or manifest. | none |
| **ecoctl** | Provisions TrustDesk | `ecoctl` creates the database and sandbox, registers the web client and roles at identity, creates the gateway client, generates `TRUSTDESK_ENCRYPTION_KEY`, sets the environment, creates the groups and grants, and checks health (`ecoctl status`). | See [section 4.3](#43-deploy-with-ecoctl). |

Environment `ecoctl` gives the TrustDesk sandbox (from `ecosystem/src/ecoctl/plan.py`): `TRUSTDESK_OIDC_ISSUER`, `TRUSTDESK_OIDC_CLIENT_ID=trustdesk`, `TRUSTDESK_OIDC_CLIENT_SECRET`, `TRUSTDESK_ENCRYPTION_KEY`, `AUTO_SEED=true`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_CHAT_MODEL`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`, plus the database variables from the linked instance. TrustDesk has no `calls`, so it gets no `TRUSTDESK_SERVICE_CLIENT_*` variables and does not call any other app with an identity token. `ecoctl` does not set `TRUSTDESK_PUBLIC_URL`; the app derives its address from the request (honouring `X-Forwarded-Proto`), and the manifest note says to set it in `env` if the derived URL differs from the registered redirect URI.

## 3. Architecture

```mermaid
flowchart TB
  subgraph clients[Callers]
    B[Browser: web app]
    C[Client application<br/>HR / travel / expense]
    M[MCP client<br/>read token]
    P[Prometheus / PagerLens agent<br/>metrics token]
  end
  subgraph app[TrustDesk process: FastAPI + uvicorn]
    G[SecurityGuard middleware<br/>headers, CSRF header, body cap, metrics]
    MG[McpGuard<br/>bearer only, needs mcp:use]
    A[Authenticator<br/>session, token, static key, JWT]
    API["/api/*, /api/v1/*"]
    MCP["/mcp (3 read tools)"]
    V[Validator<br/>facts, retrieval, model, verification]
    BL[Background loops<br/>backlog + retry, seed retry]
    VT[Vault<br/>provider keys]
  end
  DB[(Postgres + pgvector + pg_trgm<br/>documents, chunks, requests, validations, events, tokens, sessions, settings)]
  LLM[OpenAI-compatible endpoint<br/>gateway or provider]
  B --> G --> A --> API
  C --> G
  M --> MG --> A --> MCP
  P --> G
  API --> DB
  MCP --> DB
  API --> V
  BL --> V
  V -->|hybrid search| DB
  V -->|chat| LLM
  API -->|embeddings, chat| LLM
  VT --- DB
```

Main pieces (all under `src/trustdesk/`):

| Module | Role |
|---|---|
| `api.py` | The FastAPI app: static UI, sign-in and setup, documents, search, Ask, accounts, embeddings, tokens, `/metrics`, `/healthz`, `/readyz`; mounts `/mcp`; runs the background loops. Interactive API docs are disabled. |
| `api_v1.py` | The compliance API (`/api/v1`): request catalogue, employees, requests, decisions, reports, validation settings, demo data. |
| `core.py` | The app's instances of the kit (database, vault, SSRF guard, LLM client, authenticator) and the authorization model (permissions, roles, scopes). |
| `request_types.py` | The 19 request types and the validation of a submission (pure functions). |
| `workflow.py` | Request lifecycle, events (audit trail), decisions, the validation queue and retry rules. |
| `facts.py` | Facts the system computes exactly (working days, notice, overlaps, balances, duplicates) and the automatic checks built on them. |
| `validator.py` | Retrieval, prompt, model call, strict parsing and verification of the verdict, background catch-up. |
| `search.py` | Hybrid retrieval: vector and full-text in one SQL query, fused with reciprocal rank fusion (k = 60). |
| `ingest.py`, `chunking.py` | Parse (Markdown, text, PDF), heading-aware chunking, embed, store; idempotent and versioned. |
| `embeddings.py`, `embedcfg.py`, `migrate.py`, `reembed_job.py` | Embedding providers (with an offline hashing embedder), the active embedding choice, SQL migrations, atomic re-embedding. |
| `accounts.py`, `vault.py`, `netguard.py`, `llm_http.py` | Provider accounts, encrypted keys, the outbound URL guard and HTTP client. |
| `reports.py`, `metrics.py`, `people.py`, `demo.py`, `seedpack.py` | Reports, Prometheus metrics, employees, demo people and requests, the Acme policy pack. |
| `mcp_server.py` | The three read-only MCP tools. |
| `static/` | The browser UI (plain ES modules, no inline script or style). |

### Request flow

1. A client application (or a person) submits a request. TrustDesk checks the fields against the catalogue, stores it with status `submitted` and writes a `submitted` event.
2. A background task starts the validation: it claims the request, computes the **facts and automatic checks**, chooses the provider account, retrieves up to 10 policy passages (several queries per type, 4 results each, ranked by fused score), sends the model the facts, the passages (fenced) and the request (fenced, marked untrusted), and asks for one JSON verdict.
3. The verdict is parsed strictly and verified (section 7.2). The result, including a snapshot of the passages the model saw, is stored. The request moves to `pending_review`.
4. A person approves, rejects or asks for information. The decision, the AI recommendation at that moment and an override flag are stored with an event.

## 4. Install

TrustDesk needs Postgres with the `vector` and `pg_trgm` extensions (the role must be allowed to create them, or an admin enables them first), and Python 3.11 or newer (the image uses 3.14).

### 4.1 Local development with a throwaway Postgres

Requires Docker and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env          # then set POSTGRES_PASSWORD; .env is git-ignored
docker compose up -d db       # pgvector/pgvector:pg17 on 127.0.0.1:5432, volume `pgdata`
uv venv && uv pip install -e ".[dev]"
set -a; . ./.env; set +a      # exports POSTGRES_PASSWORD and DATABASE_URL
.venv/bin/trustdesk migrate
.venv/bin/trustdesk serve     # http://localhost:8000
```

Notes that matter:

- `pyproject.toml` has `[tool.uv.sources] agentpg-kit = { git = "https://github.com/elchinoo/agentpg-kit", tag = "v0.2.5" }`: `uv pip install -e ".[dev]"` takes the kit from the same release tag the Dockerfile installs, so no sibling checkout is needed (it needs network access to GitHub). When you raise the minimum kit version, raise this tag and `KIT_URL` in the Dockerfile together. To develop the kit alongside, install your checkout over the pinned one: `uv pip install -e ../agentpg-kit` (or `make kit-local`). Without `uv`, install the kit first as the Dockerfile does (`pip install "agentpg-kit @ https://github.com/elchinoo/agentpg-kit/archive/refs/tags/v0.2.5.tar.gz"`) and then `pip install -e ".[dev]"`. The UI regression tests under `tests/ui` need a sibling `../agentpg-kit` checkout (they read its static files) and skip themselves without one.
- `.env.example` sets `DATABASE_URL=postgresql://trustdesk:${POSTGRES_PASSWORD}@127.0.0.1:5432/trustdesk`; sourcing it with `set -a` expands the password.
- `make up`, `make migrate`, `make seed`, `make serve`, `make lint`, `make test`, `make test-db` wrap the commands above (see [section 12](#12-testing-and-development)).
- To throw everything away: `docker compose down -v` (removes the `pgdata` volume).
- The first start prints the one-time setup code (see [section 6](#6-first-run-walkthrough)).

### 4.2 Docker Compose (everything in containers)

```bash
cp .env.example .env          # set POSTGRES_PASSWORD
docker compose up --build
docker compose logs api       # the one-time setup code is here
```

`docker-compose.yml` runs the `db` service (healthcheck with `pg_isready`) and the `api` service built from the `Dockerfile`, with `DATABASE_URL` pointing at `db`, `AUTO_SEED: "true"` and the command `trustdesk start`. Both ports bind to `127.0.0.1` only. The build installs agentpg-kit from GitHub, so it needs network access.

`trustdesk start` (the container entrypoint) does, in order: connect (retrying while a scaled-to-zero database wakes) and apply migrations; reconcile the vector size if it differs from `EMBEDDING_DIM`; load the active embedding choice; try to unlock the vault from `TRUSTDESK_ENCRYPTION_KEY`; if `AUTO_SEED` is true load the demo policies into an empty knowledge base; serve. A failed migration prints the cause and exits with code 1.

### 4.3 Deploy with ecoctl

`ecoctl` lives in `ecosystem/`. It needs the `agentpg` CLI, logged in (`agentpg login`). `apply --execute` creates databases and sandboxes on your account, which may use credits.

```bash
cd ecosystem
.venv/bin/ecoctl validate                          # check the manifest
.venv/bin/ecoctl plan --only trustdesk             # every command and API call; changes nothing
.venv/bin/ecoctl apply --only trustdesk --execute  # identity + TrustDesk + the gateway TrustDesk uses
.venv/bin/ecoctl status                            # health, gateway client and one real call, drift
.venv/bin/ecoctl env trustdesk                     # the environment the sandbox is created with (secrets as ***)
.venv/bin/ecoctl llm test trustdesk                # one tiny chat call with TrustDesk's own gateway key
.venv/bin/ecoctl recreate trustdesk                # new image or environment; the URL changes
```

`apply --only trustdesk` creates the identity provider, TrustDesk and the LLM gateway (callees before callers). A sandbox's environment cannot change in place: after changing the manifest, `ecoctl recreate trustdesk` (the database keeps its data). To grant people access, add them to an identity group such as `support` (approver) or `ecosystem-admins` (admin). See `ecosystem/docs/DEPLOY.md` for the full walkthrough.

### 4.4 Deploy on AgentPG by hand

```bash
agentpg login
agentpg create --name <instance-name> --size small           # note the instance id
agentpg extensions create vector <instance-id>
agentpg extensions create pg_trgm <instance-id>
agentpg sandbox create trustdesk \
  --image ghcr.io/<owner>/trustdesk:latest \
  --instance <instance-id> --port 8000
agentpg sandbox status <sandbox-id>     # prints the public HTTPS URL
agentpg sandbox logs <sandbox-id>       # contains the one-time setup code
```

`--port 8000` is required (without it the platform replaces the image's command with an idle process). The image must be public on GHCR. A linked instance injects `PSQL_CONNECTION_STRING` and the `PG*` variables, which TrustDesk reads. `--env KEY=VALUE` is repeatable (for example `--env AUTO_SEED=true`) but is not a secret store. Free-tier sandboxes auto-pause and answer 503 until `agentpg sandbox resume <sandbox-id>` (the shipped manifest sets `auto_pause: false` for the fleet).

The image is built and published by `.github/workflows/build-image.yml`: lint (`ruff`), tests against a pgvector Postgres service, then push to `ghcr.io/<owner>/<repo>` (`latest`, `sha-*`, version tags) from `main`, with a nightly rebuild. It runs as a non-root user, exposes 8000, has a `HEALTHCHECK` on `/healthz`, and sets `SSL_CERT_FILE` so `sslmode=verify-full` works.

## 5. Configuration reference

Every variable below is read in `config.py`, `core.py`, `cli.py`, `api.py`, `embedcfg.py` or the kit modules named in the table. `[_FILE]` means the variable can instead name a file (`<NAME>_FILE=/path`, first line is the value).

### Database and server

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL`, `DB_EXTERNAL_DSN`, `PSQL_CONNECTION_STRING` | empty | Postgres URI. First one set wins. If none is set libpq uses `PGHOST`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`. |
| `HOST` | `0.0.0.0` | Bind address (`serve`, `start`). |
| `PORT` | `8000` | Bind port. |
| `SSL_CERT_FILE` | set in the image | CA bundle for `sslmode=verify-full`. |

### Credentials and security

| Variable | Default | Meaning |
|---|---|---|
| `TRUSTDESK_ENCRYPTION_KEY[_FILE]` | none (file default `/etc/trustdesk/encryption_key`) | Platform secret that wraps the vault's data key a second time so the vault unlocks itself after a restart. With SSO only (no admin password) it also lets TrustDesk create the vault. |
| `TRUSTDESK_API_KEY[_FILE]` | none (file default `/etc/trustdesk/api_key`) | Static full-access credential for automation. Prefer API tokens. When set, the first-run setup flow is not offered (a credential exists). |
| `TRUSTDESK_ALLOW_ANONYMOUS` | `false` | `1`, `true`, `yes` (or `on` in the kit check) disables authentication entirely. Throwaway local demos only. |
| `TRUSTDESK_ALLOW_PRIVATE_LLM_URLS` | `false` | Allow provider URLs on loopback and private networks (for example Ollama). Link-local stays blocked. Applies to accounts entered in the UI; the environment account is trusted and not checked. |

### Single sign-on (federation)

| Variable | Default | Meaning |
|---|---|---|
| `TRUSTDESK_OIDC_ISSUER` | empty | The identity provider's URL. Federation is off when unset. Must be https (http only for localhost). |
| `TRUSTDESK_OIDC_CLIENT_ID` | `trustdesk` | Client id; also the audience tokens must carry. |
| `TRUSTDESK_OIDC_CLIENT_SECRET[_FILE]` | empty | The app's secret. Empty means a public client (PKCE only). |
| `TRUSTDESK_PUBLIC_URL` | derived from the request | The address TrustDesk is reached at, used to build `/auth/callback`. |
| `TRUSTDESK_LOCAL_LOGIN` | `true` | `false` removes the admin-password and API-key login when SSO is on. Ignored (always on) when SSO is off. |

### AI provider (environment account)

| Variable | Default | Meaning |
|---|---|---|
| `LLM_BASE_URL` | empty | OpenAI-compatible base URL. Empty means the offline demo embedder and no environment chat account. Trailing `/` removed. |
| `LLM_API_KEY[_FILE]` | empty | Provider key (file default `/etc/trustdesk/llm_api_key`). Prefer entering keys in the browser. |
| `LLM_CHAT_MODEL` | empty | Chat model. With `LLM_BASE_URL` set, the environment account is the default for validation, Ask and embeddings. |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model name. |
| `EMBEDDING_DIM` | `768` | Vector size. Fixed in the table when the database is first migrated; changing it later triggers re-embedding. Maximum 2000 for the index. |
| `EMBEDDING_SEND_DIMENSIONS` | `true` | `false` (`0`, `no`) if the provider rejects the `dimensions` field. |

### Validation behaviour

| Variable | Default | Meaning |
|---|---|---|
| `VALIDATE_BACKLOG_PER_MINUTE` | `10` | How many waiting requests the background pass validates per minute. `0` is off, capped at 60. Settings (stored in the database) override it. A non-numeric value falls back to 10. |
| `VALIDATE_MAX_ATTEMPTS` | `3` | How many times in all a failed validation is attempted automatically (after 5, 15, then 60 minutes). `0` never retries, capped at 10. Settings override it. |
| `AUTO_SEED` | `false` | With `trustdesk start`, load the demo pack if the knowledge base is empty (`1`, `true`, `yes`). If it has to be skipped (vault locked, provider down) it is retried in the background after the app is serving: first after 5 seconds, then every 60 seconds, at most 5 real attempts (waiting for a locked vault does not count). |

### Values stored in the database (set in the UI or API, not the environment)

`app_setting` keys used by the app: `validation_account` (an account id, `env` or `none`), `validate_backlog_per_minute`, `validate_max_attempts`, `embedding_account_id`. The kit adds the admin password hash and the vault's wrapped keys. Saving "forget the choice" deletes the key and the environment default applies again.

### Hard limits in code

| Limit | Value |
|---|---|
| Upload size | 10 MB per file; PDFs up to 500 pages; text extraction only (no OCR) |
| Search query | 1 to 500 characters; `limit` 1 to 20 (REST), 1 to 20 (MCP) |
| Ask question | 3 to 1000 characters; 1 to 10 passages |
| API request body | 64 KB (413 above) |
| Request fields | title 160, description 5 to 4,000, up to 20 `details` fields, text values up to 500 characters, date range up to 366 days, dates 5 years back to 3 years ahead, amount 0 to 10,000,000 with 2 decimals |
| Passages per validation | up to 10, each cut to 1,500 characters in the prompt; model reply capped at 1,200 tokens |
| Sessions | 12 hours |
| Failed-credential rate limit | 10 per minute per client address, in memory per process |
| Embedding index | at most 2000 dimensions (HNSW limit for `vector`) |
| Automatic re-embed at startup | up to 5,000 chunks |

## 6. First-run walkthrough

This is the standalone path (no identity provider). With SSO, skip to step 4.

1. **Start the server.** On the first start with no credential configured it prints, to its console (stderr), a one-time setup code:

   ```
   === TrustDesk first-run setup ===
   Open the app in your browser and enter this one-time code to create the admin password:
       XXXX-XXXX-XXXX
   ```

   The code is held in memory, changes on every restart until setup is complete, and is also announced the first time a browser asks for the session state. Locally it is in the terminal running `serve`; in Compose use `docker compose logs api`; on AgentPG use `agentpg sandbox logs <sandbox-id>`.
2. **Create the admin password.** Open http://localhost:8000 (the app shows "Set up TrustDesk"). Enter the setup code, then a password of 12 to 256 characters twice. You are signed in. Setup attempts are rate limited.
3. **Load the demo data.** The Requests page says "Nothing here yet" and offers **Load demo policies, people and requests**. It embeds 16 policy documents, creates 11 fictional employees and 22 requests (7 already decided as history, 15 new ones that get AI pre-validation). Alternatively **Documents > Load demo policies** loads only the policies.
4. **Connect an AI account** (otherwise requests arrive without advice). Either set the environment account (`LLM_BASE_URL`, `LLM_CHAT_MODEL`, `LLM_API_KEY`) before starting, or open **Settings > Provider accounts > Add account**, pick a provider, paste the key, set the chat model, use **Test chat**. Then under **Settings > Request validation** choose the account and **Save**. With the environment account configured nothing needs choosing: it is the default.
5. **Watch the validation.** Demo requests move from "Validating" to "Needs decision" within seconds to a minute, depending on the model. If no chat account was usable they show "No AI advice" and the Settings page counts them as waiting; once an account works the background pass catches up (or press **Validate waiting requests**).
6. **Open a request** from the queue, read the advice, and decide. See [section 7.1](#71-requests-and-the-review-window).
7. **Optional:** create a **Client application** token under **Settings > API tokens** and send a request from a script (the **Connect** tab has copy-paste commands), or a **Read-only** token for MCP.

With SSO (federated): sign in with **Sign in with single sign-on**. A person who has no `trustdesk:*` role is sent back with "your account has no role in TrustDesk; ask an administrator". The local admin login stays as a break-glass unless `TRUSTDESK_LOCAL_LOGIN=false`.

## 7. Using TrustDesk

### 7.1 Requests and the review window

The **Requests** tab (default) shows counts by status and a filterable table: search by employee or title, status (Open, Needs decision, Waiting for employee, Validating, Approved, Rejected, Withdrawn, All), type, and AI advice. While any request is still validating the list refreshes itself every 2.5 seconds (up to 60 times). **New request** adds one on behalf of an active employee (it needs the `requests:client` permission, which `admin` and `approver` have).

Statuses:

| Status | Meaning |
|---|---|
| `submitted` | Being pre-validated. A decision is refused (409) until validation ends. |
| `pending_review` | Waiting for a person. Also the state after a failed or skipped validation, so a person is never blocked. |
| `needs_info` | The approver asked the employee something (`decision_note`). The employee answers with `PUT`, which re-validates and returns it to `submitted`. |
| `approved`, `rejected` | Decided. `decided_at`, `decided_by`, `decision_note` set. |
| `withdrawn` | The employee cancelled it (only while open). |

The **review window** shows the request, the employee, the AI recommendation with the reasons and the foundation (document, heading, version, quote, full passage, and whether the quote was found word for word), the automatic checks, and the history. Actions:

- **Approve**, **Reject**, **Review: ask for information** (only from `pending_review`). Reject and ask need a note the employee will see.
- Going against the AI (approve when it said `reject`, or reject when it said `approve`) needs a note of at least 10 characters. The decision is flagged as an **override** and appears under Reports > Decisions against AI advice.
- **Run again** re-runs the validation (starts the automatic-retry count over).
- A role without `requests:decide` sees "Your role can read requests but not decide them."

### 7.2 AI pre-validation in detail

What the system computes (so the model does not guess), from `facts.py`:

| Fact or check | Detail |
|---|---|
| Employee context | department, job title, level, country, tenure in days at the request start. **Not** name, e-mail or number. |
| Dates | calendar days, Monday-to-Friday working days (public holidays are not subtracted), days of notice, days since an expense. |
| Overlaps | other leave or travel of the same employee in the same period (`overlap`: `fail` if the other request is approved, `warn` otherwise; remote work is ignored). |
| Leave taken | same-type calendar days this year, approved vs pending. |
| Team | team size and colleagues on approved leave on the same dates. |
| Expenses | approved same-type total this year in the same currency; possible duplicates (`duplicate`); missing receipt (`no_receipt`, or `receipt_unknown`). |
| Other checks | `retroactive` (submitted after it started, except sick leave), `future_expense` (`fail`), `no_working_days` (vacation or unpaid leave on a weekend only). |

What the model is asked: judge the request against the numbered passages only, never invent rules, treat the request block as untrusted, and answer with one JSON object: `recommendation` (`approve`, `reject`, `needs_info`), `compliance` (`compliant`, `non_compliant`, `unclear`), a summary, reasons (each with passage numbers), citations (passage plus an exact quote) and missing information. A reply that is not valid JSON gets one correction attempt.

What TrustDesk then enforces (`validator.parse_verdict`):

- Citations must point at real passages (numbers outside the range are dropped).
- Each quote is normalised (whitespace, Markdown marks, curly quotes) and matched against the passage text; it is marked verified or not. A quote must be at least 8 characters to count.
- An `approve` or `reject` with no citable passage becomes `needs_info` with a warning.
- `approve` is downgraded to `needs_info` while any automatic check has severity `fail`.
- If no quote of an `approve` or `reject` can be verified, a warning tells the approver to read the passages. Unverified quotes are marked.
- `approve` with `non_compliant` is treated as `unclear`.
- The passages the model saw are stored with every attempt, so the evidence survives later edits or deletion of a document.

Validation states per request: `pending`, `running`, `done`, `failed`, `skipped`. `skipped` means no chat model was configured. A request whose claim is older than 10 minutes is taken over.

**Catch-up and retries.** Once a minute a background pass validates requests that were waiting (no account when they arrived), oldest first, up to the configured rate, skipping requests younger than 2 minutes (their own task is still working). It also retries failed validations after 5, then 15, then 60 minutes (and every 60 after), up to `max_attempts` in all. A failure that the provider gives for that very request (HTTP 400, 413, 422) is not retried. The pass does nothing while there is no usable account or while a stored account's key is locked. Every attempt is a line in the request history saying whether another try is coming.

Where it surfaces: `GET /api/v1/requests/{ref}` (approver credentials) includes `validation`; client credentials never see it.

### 7.3 Employees

**Employees** lists people (search by name, e-mail, number, department), with open and total request counts and a link to their requests. **Add employee** and edit are available; deactivating (`status: inactive`) blocks new requests and keeps history. Client applications normally create employees through the API. Fields: `employee_no` (1 to 40 letters, digits, `.`, `-`, `_`, your identifier), `name`, `email`, `department`, `title`, `level`, `country` (2-letter code), `manager_no`, `hire_date`, `status`.

### 7.4 Reports

**Reports** (needs `reports:read`): pick a report and dates, **Run**, **Print** or **Download CSV**. All reports have the same table shape. At most 5,000 rows are returned (the response says when truncated). CSV cells that a spreadsheet would read as formulas (starting with `=`, `+`, `-` when non-numeric, or `@`) are neutralised.

| Name | Content |
|---|---|
| `overview` | Volumes by status, approval rate, average hours to decision, how many were pre-validated and flagged non-compliant, how often decisions followed the AI, number of overrides, oldest open request. |
| `requests` | Every request in the period. |
| `compliance` | Compliance by request type. |
| `out_of_compliance` | Requests the AI flagged, including those approved anyway. |
| `overrides` | Decisions against the AI advice, with the justification. |
| `expenses` | Expenses by department and type. |
| `leave` | Approved leave in working days. |
| `aging` | Open requests by age (does not use the period; the SLA constant is 5 days). |
| `absences` | Upcoming absences and trips. |

### 7.5 Documents, search and Ask

- **Documents:** drag Markdown, text or PDF files (10 MB each) into the drop zone after choosing the type (HR and leave, Travel, Expenses, Governance, Security, General policy). Re-uploading a changed file under the same name creates a new version that replaces the old one in search; an identical file is reported unchanged. **View** shows how a document was split. Uploading and deleting need the `kb:write` permission (admin only); everyone with `kb:read` can list and view, and the UI hides the upload area and the delete buttons from them.
- **Search:** plain-language query; each result shows document, heading, version and its rank in the vector and in the full-text search, and **Copy citation**. Hybrid retrieval: the vector candidates and the full-text candidates (OR semantics over an English `tsvector`) are fused with reciprocal rank fusion.
- **Ask:** (needs the `ask` permission: admin and approver) pick an account, ask a question; the top passages (up to 10) go to that model, which must cite them as `[n]`. An answer citing none is flagged. If nothing matches, nothing is sent to the model.
- **Embeddings:** with no provider, a built-in signed feature-hashing embedder works offline; it matches words, not meaning. Under **Settings > Search embeddings** you can switch to an account with an embedding model; the passages are re-embedded in the background with progress, atomically (nothing changes if the provider fails). Search and uploads answer 503 while that runs.

### 7.6 Settings, Database and Connect

**Settings** (admin): provider accounts (add, edit, delete, **Test chat**, **Test embeddings**; the environment account is shown read-only), Request validation (account, rate of catching up, number of automatic attempts, counts of waiting, retrying and gave-up requests, **Validate waiting requests**), Search embeddings, admin password change and API tokens (name, scope; the token is shown once). **Connect** has copy-paste MCP setup for ten clients and a client-API cheat sheet with this server's URL filled in.

**Database** (admin, `settings:manage`, last tab): what EDB's extensions know about the workload, from the kit's insights page: the slowest statements (`edb_stat_monitor`, or `pg_stat_statements`), the `edb_pg_tuner` recommendations and the `query_advisor` index hints. On a database without those extensions (plain Postgres, a laptop) each part says "Not available on this database." with the reason. Statement text is normalised (no bound values) but reveals table names, hence the admin permission.

### 7.7 REST API

[API.md](../API.md) documents `/api/v1`. Summary of what is available and to whom:

| Area | Endpoints | Credentials |
|---|---|---|
| Catalogue | `GET /api/v1/request-types` | client, or anyone with `requests:read` |
| Employees | `POST`, `GET`, `GET /{no}`, `PATCH /{no}` under `/api/v1/employees` | client for writes; client or `requests:read` for reads |
| Requests (client side) | `POST /requests` (with `Idempotency-Key`), `GET /requests`, `GET /requests/{ref}`, `PUT /requests/{ref}` (only while `needs_info`), `POST /requests/{ref}/withdraw` | client for writes; read with client or `requests:read` (clients get the request without `ai` and `override`, and only public events) |
| Approver | `POST /requests/{ref}/decision`, `POST /requests/{ref}/validate`, `POST /requests/validate-pending`, `GET /requests/counts` | `requests:decide` (counts: `requests:read`) |
| Reports | `GET /reports`, `GET /reports/{name}?from=&to=&format=json\|csv` | `reports:read` |
| Validation settings | `GET /validation/settings`, `PUT /validation/settings` | `requests:read` / `settings:manage` |
| Demo | `POST /demo` | `settings:manage` |

Other endpoints (browser and admin): `GET /api/insights?order=total|mean|calls&limit=` (`settings:manage`; the Database tab), `GET /api/requests/events` (`requests:read`; a Server-Sent Events stream, see "Live updates" below), `GET /api/session`, `POST /api/setup`, `/api/login`, `/api/logout`, `/api/vault/unlock`, `/api/admin-password`, `GET/POST/DELETE /api/tokens`, `GET /api/providers`, `/api/accounts` (CRUD and `/test`), `/api/embeddings/status` and `/activate`, `GET /api/search`, `/api/documents` (list, get, upload, delete), `POST /api/seed`, `GET /api/stats`, `GET /api/ask/accounts` and `POST /api/ask` (the `ask` permission), and the SSO routes `/auth/login` and `/auth/callback`. Browser writes (cookie authentication) under `/api/` must carry the header `X-TrustDesk-CSRF`; requests with an `Authorization` header are exempt.

Error statuses: `401` no or bad credentials, `403` credential lacks the permission or missing CSRF header, `404`, `409` wrong state or duplicate, `413` too large, `422` invalid input, `423` vault locked, `429` too many failed attempts, `502` provider error, `503` database waking or re-embedding in progress.

### 7.8 MCP tools

Served at `/mcp` over streamable HTTP, bearer token only (browser sessions are refused). The credential must carry `mcp:use`: a `read` or `full` token, an admin, approver or viewer person, or a service with scope `trustdesk.kb.read`. `client` and `metrics` credentials get 403.

| Tool | Purpose |
|---|---|
| `search_knowledge(query, limit=5, doc_type=None)` | Hybrid search; query cut to 500 characters, limit 1 to 20. Returns `chunk_id`, `document`, `source`, `version`, `heading`, `text`, `score`. |
| `get_policy_passage(chunk_id)` | One passage by id with its document metadata, or `{"error": "not found"}`. |
| `list_documents()` | Documents with id, title, source, type, version and chunk count. |

The tools are read-only and do not expose requests or employees. `search_knowledge` stays an application tool because it embeds the question through our gateway; `get_policy_passage` and `list_documents` are plain reads of `kb_chunk` and `kb_document` that AgentPG's restricted MCP server could serve over the readable knowledge-base tables (see "What a read-only user can and cannot see"); they are kept for now. Client configuration snippets for ten clients are in the README and on the Connect tab. Treat results as untrusted text.

## 8. Roles and permissions

Permissions are what the code checks; roles (people, from the identity provider) and scopes (services and local tokens) are mapped to them in `core.py`.

| Permission | Allows |
|---|---|
| `kb:read` | search and read the knowledge base |
| `kb:write` | upload and delete documents |
| `ask` | ask questions (spends provider credit); also lists the accounts that can chat, by name and model only (`GET /api/ask/accounts`) |
| `requests:client` | create employees and requests, read status (client applications) |
| `requests:read` | see requests, AI advice and history |
| `requests:decide` | approve, reject, ask for information, re-run validation |
| `reports:read` | open and download reports |
| `settings:manage` | provider accounts, validation settings, embeddings, tokens, demo data |
| `mcp:use` | use the MCP server |
| `metrics:scrape` | scrape `/metrics` |

| Role (person) | Permissions |
|---|---|
| `admin` | everything (`*`) |
| `approver` | `kb:read`, `ask`, `requests:client`, `requests:read`, `requests:decide`, `reports:read`, `mcp:use` |
| `viewer` | `kb:read`, `mcp:use`, `requests:read`, `reports:read` |

| Scope (service or token) | Permissions |
|---|---|
| `full` (local token), static API key, local admin session | everything |
| `read` (local token), `trustdesk.kb.read` | `kb:read`, `mcp:use` |
| `client` (local token), `trustdesk.requests.write` | `requests:client` |
| `metrics` (local token), `trustdesk.metrics` | `metrics:scrape` |

Route requirements worth knowing: the local admin password login and the static key are full access. `POST` and `DELETE` on `/api/documents` need `kb:write`; `/api/ask` and `/api/ask/accounts` need `ask`. Several routes check the legacy name `full`, which is mapped to `settings:manage`, so they are admin-only: demo-policy load (`/api/seed`), accounts (`/api/accounts`, including the full listing, which shows endpoints and key details), embeddings activation, tokens, vault unlock and password change. `read` maps to `kb:read`: search, document list and view, stats. `/metrics` needs `metrics:scrape`. Only `admin` and the full-access credentials hold `kb:write`; no other role or scope carries it, so to let another role manage documents, add `kb:write` to its entry in `core.py`. A person with no role in TrustDesk can do nothing. Decisions are recorded under the person's name (`decided_by`, the actor in the history).

## 9. Security notes

[SECURITY.md](../SECURITY.md) is the full model. The points an operator needs:

- **Fail closed.** With no credential configured every protected route is denied. Anonymous access only exists behind `TRUSTDESK_ALLOW_ANONYMOUS=true`; never use it on a reachable host.
- **Admin password:** salted scrypt (N=2^15, r=8, p=1), 12 to 256 characters. **Sessions:** random cookie `td_session` (`HttpOnly`, `SameSite=Strict` for local sign-in and `Lax` for SSO, `Secure` over HTTPS), only its SHA-256 stored, 12 hours; changing the password or signing out ends sessions (a password change deletes every session).
- **Tokens:** `td_` prefix, shown once, stored as SHA-256, revocable. Scopes `read`, `metrics`, `client`, `full`. A `client` token is a service credential that can read every request: keep it server-side.
- **Provider keys** are encrypted (Fernet) with a random data key that is stored only wrapped under a key derived from the admin password and, optionally, under `TRUSTDESK_ENCRYPTION_KEY`. A database dump alone reveals nothing. The vault is locked after a restart until someone signs in with the password or the platform secret unlocks it; locked features return 423.
- **Outbound requests:** provider URLs entered in the UI are checked before every call (https, no embedded credentials, no loopback, private, link-local or carrier-grade-NAT addresses unless `TRUSTDESK_ALLOW_PRIVATE_LLM_URLS`; link-local always blocked; redirects never followed; responses size-limited). Limitation: DNS is resolved once for the check and again by the client.
- **Prompt injection:** employee text and document passages are delimited and flagged as untrusted, the model has no tools, its verdict is strictly parsed and re-checked, and a human decides.
- **Personal data:** the model never receives names, e-mail addresses or employee numbers, but free text written by an employee can contain personal data. Passages, queries and questions go to the provider you configure. Sensitive requests (sick leave, medical expenses) can be read only by approver sessions, `full`, `client` and the `requests:read` roles; `read` and `metrics` tokens and `/mcp` cannot.
- **Web hardening:** strict CSP (no inline script or style), `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, `Cache-Control: no-store`, interactive API docs disabled, 64 KB body cap on `/api/v1`, CSV formula neutralisation. Run behind TLS; the app does not terminate it. Failed-credential rate limits are in memory per process, so add proxy-level limits too.
- **Single tenant:** anyone with full access reads and changes every document and request.
- **Reporting:** use GitHub private vulnerability reporting, not a public issue.

## 10. Operations

### Health and readiness

| Endpoint | Auth | Answer |
|---|---|---|
| `GET /healthz` | none | `200 {"status":"ok","version":"0.1.0"}` while the process runs. The image's `HEALTHCHECK` uses it. |
| `GET /readyz` | none | `200 {"status":"ready"}` if the database answers `SELECT 1`, else `503 {"status":"waking-database"}` (the app retries with backoff). |
| `GET /metrics` | metrics token, service scope `trustdesk.metrics`, or admin | Prometheus text. |
| `GET /api/stats` | `kb:read` | documents, chunks, embedder, dimension, `reembedding`, `needs_reembedding`, `stored_dimension`. |
| `GET /api/embeddings/status` | `kb:read` | re-embedding job state and the active embedding choice. |

A `503` with no security headers (no `content-security-policy`) on every path, including `/healthz`, came from the platform (a paused sandbox), not the app.

### Metrics

Same names as the rest of the family: `http_requests_total`, `http_request_duration_seconds`, `db_up{app}`, `app_info`, `process_*`. TrustDesk adds `trustdesk_documents`, `trustdesk_requests{status}`, `trustdesk_oldest_open_request_seconds`, `trustdesk_validations_total{state,recommendation}`, `trustdesk_validation_duration_seconds` and `trustdesk_searches_total{kind}`. A scrape never fails because the database is down; `db_up` goes to 0 instead. `ecoctl` does not provision a scraper for TrustDesk (see section 2).

The kit's insights collector adds `db_insights_available{app}` (1 when query statistics can be read), `db_insights_top_query_mean_seconds{app,queryid}` (the 5 slowest statements with at least 5 calls), `db_insights_statement_calls{app}` and `db_insights_tuner_recommendations{app}`, cached 30 seconds so scrapes do not hit the database; a scrape never fails because of them (`db_insights_available 0` on plain Postgres).

### Backup and restore

All state is in Postgres: documents and chunks, employees, requests, validations (with passage snapshots), events, tokens, sessions, settings, the vault's wrapped keys, accounts. Back up the database with your usual tooling (`pg_dump`, or the platform's instance backups). Nothing is stored on the app's disk, so a sandbox can be recreated without data loss. After restoring: provider keys stay locked until the admin signs in; if you used `TRUSTDESK_ENCRYPTION_KEY`, restore it from the platform secret store, because without it (and without the password) the keys cannot be unwrapped. A restored database with a different `EMBEDDING_DIM` than the configured one triggers the re-embedding logic below. This repository has no backup scripts; the restore procedure was not exercised here.

### Vault and secret rotation

| What | How |
|---|---|
| Admin password | Settings > Security > Change password (needs the current one). It re-protects the stored provider keys and signs every browser out. |
| Forgotten admin password | `trustdesk reset-admin` where the server can reach the database (on AgentPG `agentpg sandbox console <sandbox-id>`). It clears the password, the vault and **every stored provider key** (accounts remain, keys are nulled), and setup starts again with a new code at next start. No back door. |
| Provider key | Edit the account in Settings and paste a new key; or revoke it at the provider. Use provider-side spending limits. |
| API tokens | Settings > API tokens: create a new one, switch clients, **Revoke** the old one (takes effect at once). |
| `TRUSTDESK_ENCRYPTION_KEY` | Held in the platform secret store. The data key is wrapped under it as well as under the password. This guide does not document a rotation procedure for it; changing it without the admin password present locks the platform-unlock path. Treat rotation as untested. |
| SSO client secret | Rotate at the identity provider (`ecoctl apply --rotate`), then recreate the sandbox so the new `TRUSTDESK_OIDC_CLIENT_SECRET` is injected. |
| Gateway key | Rotated at the gateway; `ecoctl llm sync` compares limits, and a lost key is rotated and reported by `ecoctl apply`. |

### Re-embedding

If the stored vector size differs from `EMBEDDING_DIM`, startup does not fail. With up to 5,000 chunks it re-embeds by itself (atomic; if the provider is down nothing changes and one warning is logged). With more, or for a size above 2000, it logs a warning and starts anyway. Until re-embedded, search uses full-text only, uploads answer 503, and `GET /api/embeddings/status` and `/api/stats` report `needs_reembedding` (the UI shows a banner). Fix with `trustdesk reembed`, or activate the embedding account again in Settings. Change `EMBEDDING_DIM` only for a new fleet: the table's dimension is fixed when first migrated.

### Pausing

A free-tier AgentPG sandbox that auto-pauses answers 503 and does not wake on a request: `agentpg sandbox resume <sandbox-id>`. A resume is a restart, so the vault locks again unless `TRUSTDESK_ENCRYPTION_KEY` is set (the fleet manifest generates it). Resume a few minutes before a demo and check `/readyz`.

## 11. Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Every URL returns an empty `503` | Sandbox paused by the platform | `agentpg sandbox status <id>`; `agentpg sandbox resume <id>`; confirm with `curl -i https://<your-host>/healthz` (the platform's 503 has no CSP header). |
| `/readyz` returns `503 waking-database` | App is up, database not reachable yet (scale to zero) | Wait a few seconds and retry; check the instance. |
| Container exits with "startup failed while migrating" | Database unreachable, or `vector` or `pg_trgm` missing for the role | Fix the DSN; `agentpg extensions create vector <instance-id>` and `pg_trgm`. |
| "Provider keys are locked" banner, or HTTP 423 | Restart locked the vault | Enter the admin password in the banner, or set `TRUSTDESK_ENCRYPTION_KEY` for unattended unlock. |
| Requests show "No AI advice" / "validation skipped" | No usable chat model when they arrived | Add or choose an account (Settings > Request validation); they are caught up within a minute, or press **Validate waiting requests**. Settings shows the counts. |
| Validation "failed" with a provider message | Provider error, revoked key, upstream 402 or 502, invalid verdict | Fix the cause; it retries after 5, 15, 60 minutes up to the attempt limit, or press **Run again**. HTTP 400, 413, 422 from the provider are not retried. |
| Validation never starts after a restart | The background pass is waiting: no account, or the stored account's key is locked | Unlock the vault or use the environment account. |
| Search says re-embedding is in progress | Embedding provider switch running | Wait; progress is in Settings. Failures change nothing. |
| Search returns only full-text results; upload answers 503; banner "needs re-embedding" | Stored vector size differs from `EMBEDDING_DIM` | `trustdesk reembed` or activate the embedding account again. |
| "the embedding model returned N dimensions but M were configured" | Wrong `EMBEDDING_DIM` for the model | Set the number it reports (or a size the model supports, at most 2000). |
| Provider rejects a request mentioning `dimensions` | Provider does not accept the field | `EMBEDDING_SEND_DIMENSIONS=false`. |
| Adding an account for a local Ollama fails with a URL error | SSRF guard blocks private addresses | `TRUSTDESK_ALLOW_PRIVATE_LLM_URLS=true` on the server (or use the environment account). |
| `401` on `/api/v1` | Missing or revoked token, or a JWT for another audience or issuer | Create a token; check the token's `aud` equals `trustdesk` and the issuer matches. |
| `403` "this credential cannot do that" | Scope too narrow (a `client` token cannot read advice, decide, open reports or use `/mcp`; a `read` token cannot use `/api/v1` requests) | Use the right credential. |
| `403 missing CSRF header` | Cookie-authenticated write without `X-TrustDesk-CSRF` | Send the header, or use a bearer token. |
| `429` too many failed attempts | 10 failures per minute from one address | Wait a minute; behind a proxy all clients may share one address. |
| `409` on a decision | Request still `submitted`, already decided, or changed meanwhile | Wait for validation or reload; re-run validation if stuck. |
| `422` "a note is required" / "Explain in the note" | Reject and ask need a note; contradicting the AI needs 10+ characters | Write the note. |
| "your account has no role in TrustDesk" after SSO | The person has no `trustdesk:*` role | Add them to an identity group (for example `support`). |
| SSO redirect error `invalid_request` | Registered redirect URI differs from the URL the app derives | `ecoctl status` shows drift; set `TRUSTDESK_PUBLIC_URL` in the manifest `env` and recreate. |
| `sso_error=the identity provider is unavailable` | Issuer unreachable | Check identity health; the local admin login remains unless disabled. |
| MCP client gets `401` or `403` | Browser cookie (not accepted), wrong scope | Use a read-only token in the `Authorization: Bearer` header. |
| Forgot the admin password | | `trustdesk reset-admin` (erases stored provider keys). |
| Demo policies missing after start with `AUTO_SEED=true` | Skipped (vault locked, provider down, index needs re-embedding) and retried at most 5 times | Check the log for `AUTO_SEED skipped`; use **Documents > Load demo policies**. |

## 12. Testing and development

```bash
make lint       # ruff check src tests
make test       # unit tests, no database needed
make test-db    # integration tests against a throwaway database on the compose Postgres
                # (needs POSTGRES_PASSWORD exported: set -a; . ./.env; set +a)
```

`make test-db` drops and recreates a database `trustdesk_test` on the Compose Postgres and runs `pytest` with `TEST_DATABASE_URL`. Tests marked as database tests skip themselves when `TEST_DATABASE_URL` is unset. In this checkout, `make test` without a database gave **115 passed, 11 skipped** (the skipped ones are the database modules); against a throwaway database all 225 passed. The CI workflow runs `ruff` and the full suite against a `pgvector/pgvector:pg17` service before building the image.

| Test module | Covers |
|---|---|
| `test_compliance_db.py`, `test_compliance_pure.py` | Request types, facts and checks, verdict parsing, decisions, overrides, reports, scopes, demo data |
| `test_validation_retry_db.py`, `test_startup_reconcile_db.py`, `test_autoseed_db.py`, `test_reembed.py` | Backlog and retry rules, startup reconciliation, AUTO_SEED, re-embedding |
| `test_federation_db.py`, `test_auth_db.py`, `test_api_auth.py`, `test_accounts_db.py` | SSO roles and scopes, setup and sessions, token scopes, provider accounts |
| `test_db_search.py`, `test_embeddings.py`, `test_chunking.py`, `test_api_db.py`, `test_metrics_db.py`, `test_passwords_netguard.py`, `test_config.py` | Retrieval, embedders, chunking, API, metrics, password and SSRF guard, configuration |
| `test_edb_features_db.py` | Database insights (permission, 'not available' on plain Postgres, metrics), read-only masking with a real login role, `unaccent` (names, policy search, degradation, migration skipped without the extension), `tcn` live updates (real LISTEN, reconnect, no trigger, stream frames, endpoint guard) |
| `test_kit_version.py` | The installed kit is at least 0.3.0 (insights, masking, `after_migrate`) |
| `test_ui_js.py` and `tests/ui/*.mjs` | UI regression tests on a fake DOM (needs Node); `insights_tab.mjs` and `live_updates.mjs` cover the Database tab and the live stream with its poll fallback |
| `test_fake_llm.py` | The demo stand-in model: every scripted verdict passes TrustDesk's own verdict checks against the real policy pack |

Code style: `ruff` with line length 100, rules `E F I B UP`. Migrations are plain SQL in `src/trustdesk/migrations/` (`001` to `006`), applied in order and recorded by file name; `{{EMBEDDING_DIM}}` is substituted in the first one. No ORM. The static UI is ES modules with no inline script or style (the CSP forbids it).

### Demo stand-in model and the video recorder

`scripts/fake_llm.py` is a **demo stand-in, not a model**: a standard-library, OpenAI-compatible server on loopback that answers `/v1/chat/completions` and `/v1/embeddings` without any AI, so a recording is reproducible. For each of the 15 new demo requests it returns a fixed verdict (rejection for the two weeks in Portugal, the 340 USD hotel night and the gift card; approval for the long weekend and the first auditor lunch; "needs more information" for the rest) whose quote it copies word for word from the retrieved passage that holds the rule, so TrustDesk's quote check passes. Unknown requests get a cautious "needs more information" with no citation, Ask gets the best-matching sentence cited as `[1]`, and embeddings are the same lexical feature-hashing vectors as the built-in embedder. It refuses to listen anywhere but loopback and is not part of the image. Use it with the environment variables `LLM_BASE_URL=http://127.0.0.1:<port>/v1`, `LLM_CHAT_MODEL=demo-standin`, `EMBEDDING_MODEL=demo-standin`, `EMBEDDING_DIM=768` and any `LLM_API_KEY`.

`docs/video/record.spec.ts` is the runnable recorder for [VIDEO-SCRIPT.md](VIDEO-SCRIPT.md): it starts a throwaway Postgres, the stand-in and TrustDesk, drives the scenes in Chrome at 1920x1080 and writes a video to `docs/video/out/` (git-ignored). See the header of the file for the commands.

Not covered by the suite (stated in the README and the ecosystem state report): validation quality against real providers (tests use a scripted stand-in model), behaviour with a large corpus, and the UI in a real browser for some flows.

## 13. AIDB and AgentPG integration

**AgentPG today.** TrustDesk runs as an AgentPG sandbox (any public image, port 8000) linked to an AgentPG Postgres instance. It reads the injected `PSQL_CONNECTION_STRING` and `PG*` variables, needs the `vector` and `pg_trgm` extensions enabled on the instance (and optionally `unaccent` and `tcn`, which add accent-insensitive search and live updates; without them the app falls back), tolerates a scaled-to-zero database (connection retries, `/readyz` reports `waking-database`), and holds all state in Postgres so a sandbox can be recreated.

**What runs in the database today.** (See also the next subsection for the EDB and AgentPG features.) pgvector (`vector(N)` column with an HNSW cosine index), full-text search (`tsvector` generated column with a GIN index), `pg_trgm` (a trigram index on chunk text), and a hybrid query with reciprocal rank fusion, all in plain SQL (`search.py`).

### EDB and AgentPG capabilities used by this app

Verified facts about what exists on an AgentPG instance are in `ecosystem/docs/06-edb-and-agentpg-capabilities.md`. TrustDesk uses these:

| Capability | Where | Without it |
|---|---|---|
| `edb_stat_monitor`, `pg_stat_statements`, `edb_pg_tuner`, `query_advisor` (kit `apgkit.insights`) | Database tab, `GET /api/insights`, `db_insights_*` metrics | each part reports "not available" with a reason; never a 500 |
| `a1p_readonly` group role + masked views (kit `apgkit.masking`), re-applied after every migrate | read-only DB users and AgentPG's restricted MCP | skipped quietly where the role does not exist (plain Postgres) |
| `unaccent` (contrib) | policy search (text search configuration `td_english`, used by the generated `kb_chunk.tsv`), employee and request name search | exact matching (accents matter); the migration skips itself if the extension cannot be created |
| `tcn` (contrib) | live updates of the Requests page | the page polls as before; the stream says `live: false` |
| pgvector, pg_trgm, full-text search | hybrid retrieval | (required: `vector`) |

**Accent-insensitive search.** Migration `006_unaccent_tcn.sql` creates `unaccent` (if the instance offers it), a text search configuration `public.td_english` (the English configuration with `unaccent` first in the word mappings) and rebuilds `kb_chunk.tsv` on it, so searching "Sao Paulo" finds "São Paulo" and "jose" finds "José". Search asks the database each time whether the extension and configuration exist, so a database that gains them later works without a restart. The employee list and the request search use `unaccent(col) ILIKE unaccent(pattern)` on name, e-mail, employee number and department (requests: title, name, number). The rebuild of `tsv` is a one-off table rewrite of `kb_chunk`.

**Live updates.** The same migration puts `triggered_change_notification('request_changes')` (the `tcn` extension) on `request` as `request_tcn`: every insert, update or delete sends `NOTIFY request_changes` with the table, operation and key. The app keeps ONE background connection that `LISTEN`s (`live.py`, started in the app's lifespan, reconnecting with a back-off and telling browsers to refresh after a reconnect) and relays each notification to every connected browser over `GET /api/requests/events` (Server-Sent Events, needs `requests:read`). The first frame is `hello {live}` (`live` is true when the connection is up and the trigger exists), then `changed` per notification (bursts collapse to one pending event per browser) and a `: ping` comment every 15 s. No row data travels through the stream: on `changed` the Requests page re-reads the list and the counts through the normal API (so permission checks and filters are the usual ones). The poll that refreshes the list while requests are still being validated remains as the fallback: it runs whenever the stream is not live (no `tcn`, dropped connection, a browser without `EventSource`). The stream is closed when the user leaves the Requests tab (a browser allows few connections per site); at most 200 browsers are served at once (`busy` beyond that). The tcn payload is not used for anything but waking the browser.

**What a read-only user (and AgentPG's restricted MCP) can and cannot see.** A user made with `agentpg db-users create --permission read` is a member of `a1p_readonly`, and the restricted MCP server runs its queries as that role. After every migrate (`migrate.run(after_migrate=[...])`, also what `trustdesk migrate` and the container start do) `hardening.py` makes the database enforce this, whichever client connects:

| Table | The read-only role sees | Why |
|---|---|---|
| `employee` | no access; `employee_masked`: `id`, `employee_no` as a 12-character hash, `name` as initial plus `***`, `email` as `***@domain`, department, title, level, country, `manager_id`, hire date reduced to the year, status, timestamps | employee identity is personal data; ids still join to `request_masked` |
| `request` | no access; `request_masked`: all the request text (title, description, dates, amount, `details`, status, decision note) but `decided_by` as initial plus `***`, without `idempotency_key` and the validation bookkeeping, and with the `details` keys `relationship`, `medical_certificate` and `parental_type` removed | the text stays readable so an assistant can answer "what is flagged and why"; health and family keys are removed |
| `request_event` | no access; `request_event_masked`: `actor` as initial plus `***`, action, note, data | actors are people or client names |
| `app_setting`, `llm_account`, `api_token`, `web_session`, `oidc_login` | nothing: `SELECT` is revoked and there is no view | the admin password hash, the vault's wrapped data key, LLM key ciphertext, API token and session hashes, SSO state |
| `kb_document`, `kb_chunk`, `kb_embedding_model`, `validation`, `schema_migrations` | everything | policies are the knowledge base itself; `validation` holds the AI advice, which can quote request text |

Limits: only the table owner can revoke, so the app's migrate must run as the role that owns the tables (a revoke that did not take is logged and reported, never assumed); default privileges give `a1p_readonly` SELECT on every NEW table, so a table added by a later migration is readable until the next migrate runs the hook (it runs on every start); the protection covers the `a1p_readonly` path only, not the app's own connection, `a1p_readwrite` members or the platform's owner role; `employee_no` hashing is a short unsalted digest and does not stop a guess-and-compare of known numbers; request text and AI advice are free text and can contain personal data an employee typed into them; the hosted MCP endpoint with an `agentpg token` JWT runs as a read-write role and is **not** covered (see the capabilities document). `hardening.apply(conn)` returns what it did per table; it never raises, so a privilege problem cannot stop the app from starting.

**AIDB today: not used.** No code in this repository references AIDB. The ecosystem design document (`ecosystem/docs/03-aidb-integration.md`) records that AIDB 7.8.0 installs on an AgentPG instance but the instance login role lacks the `aidb_users` grant, so nothing could be run; that is a platform item.

**What is planned** (a design in the ecosystem docs, to be proven in a spike first; none of it is implemented):

| Today (application code) | Planned with AIDB |
|---|---|
| Ingest, heading-aware chunking, embedding and versioning in Python; a reconciler when the vector size changes | An AIDB pipeline over the document table (parse, chunk, knowledge base) in live mode; `aidb.retrieve_text`, optionally `aidb.rerank_text` |
| Own reciprocal-rank-fusion SQL | Knowledge-base hybrid search with relational filters (document type, effective date, department) |
| Pre-validation in application code calling the gateway | An AIDB agent whose tools are a retrieval tool and custom SQL tools over employee and balance tables, run under a purpose bound to a read-only Postgres role. Quote verification stays in the app as a second check. |
| No receipt or scan support | `parse_pdf` and `perform_ocr` in a pipeline so expense requests with receipts could be checked |
| Audit trail written by TrustDesk (events, validation snapshots) | Decision records in `aidb_otel.traces` read by EDB Agent Governance, in addition to the app's own trail |

The planned approach is a retrieval-backend switch in the kit (`app` or `aidb`) so the plain-Postgres path stays supported and tested. The gateway stays: AIDB models would be registered against the gateway's OpenAI-compatible URL. The AIDB MCP endpoint authenticates with Postgres roles, not identity tokens.

## 14. Data model

Tables created by `src/trustdesk/migrations/001` to `006` and the kit (migration 006 adds no table: the optional `unaccent` text search configuration and the `tcn` trigger on `request`; after each migrate the masked views `employee_masked`, `request_masked` and `request_event_masked` are created):

| Table | Holds |
|---|---|
| `kb_document`, `kb_chunk` | Documents (title, source, type, version, SHA-256) and their chunks (`heading`, `body`, generated `tsv`, `embedding vector(N)`); HNSW, GIN and trigram indexes. |
| `kb_embedding_model` | The name and size of the model that produced the stored vectors. |
| `llm_account` | Provider accounts (URL, encrypted key, last 4 characters, chat and embedding models). |
| `employee` | Employees (number, name, e-mail, department, title, level, country, manager, hire date, status). |
| `request` | Requests: type, kind, title, description, dates, amount, currency, `details` (JSON), status, validation state and attempt counters, idempotency key, source (`api`, `ui`, `demo`), decision fields, `override`, `ai_at_decision`. |
| `validation` | One row per attempt: state, recommendation, compliance, summary, reasons, citations, missing, warnings, checks, passages, model, account, usage, error, duration. |
| `request_event` | The audit trail: actor, action, note, data. |
| `app_setting`, `api_token`, `web_session` (+ OIDC state) | From the kit: settings and wrapped vault keys, hashed tokens, hashed sessions. |

## 15. CLI reference

`trustdesk <command>` (entry point `trustdesk.cli:main`):

| Command | Does |
|---|---|
| `migrate` | Apply SQL migrations (and the kit's auth tables). |
| `seed [pack]` | Load a seed pack; default and only packaged pack is `acme`. |
| `ingest <paths...>` | Ingest `.md`, `.txt`, `.pdf` files or directories. |
| `search <query> [-n N]` | Hybrid search from the terminal. |
| `reembed` | Re-embed everything after changing the model or `EMBEDDING_DIM`; atomic. |
| `serve` | Run the API and MCP server with uvicorn on `HOST:PORT`. |
| `start` | Container entrypoint: migrate, reconcile, unlock, optional seed, serve. |
| `reset-admin` | Forget the admin password and delete stored provider keys. |

## 16. Known limitations

Verified in code or documented by the project; each is a behaviour to plan around.

**Behaviour and scope**

- The AI advises and can be wrong. Quote checking proves a quote exists in a passage, not that it supports the claim. Validation quality was exercised with a scripted stand-in model and not benchmarked against real providers; the model providers listed in the README were checked against their documentation, not run live.
- Working days exclude weekends but not public holidays; there is no leave balance beyond what earlier requests in the system show.
- Decisions are recorded under the signed-in person's name, but there is no per-employee sign-in (client applications are trusted to name the employee), no e-mail notifications, and no receipt or file uploads on requests. Documents are text extraction only (no OCR).
- One tenant: there is no per-department or per-employee visibility restriction among people who may read requests.
- `/mcp` only searches the knowledge base; it does not expose requests, employees or decisions.

**Operations**

- A free-tier AgentPG sandbox that auto-pauses answers 503 until resumed; the fleet manifest disables auto-pause.
- The failed-credential rate limit is in memory and per process. Behind a proxy that hides client addresses, clients share one bucket.
- DNS rebinding between the URL check and the HTTP call is a documented limitation of the SSRF guard.
- No backup, restore or `TRUSTDESK_ENCRYPTION_KEY` rotation procedure has been exercised or scripted in this repository.
- `ecoctl` wires the gateway, identity and the simulator's calls to TrustDesk, but it does not provision a metrics scraper for TrustDesk; the PagerLens agent must be configured by hand to monitor it.
- `ecoctl` does not set `TRUSTDESK_PUBLIC_URL`; the redirect URI relies on the request's host and forwarded protocol.
- The embedding dimension is fixed when the table is first created. The shipped manifest sets `llm.embed_dimensions: 1536`; an older sentence in `ecosystem/docs/DEPLOY.md` still says 768. Changing it later means re-embedding (automatic only up to 5,000 chunks).

**Not verified here**

- Nothing in this guide was run against a live fleet or a live provider; live URLs and the state of the deployed fleet were not read. Behaviour that depends on the identity provider, gateway or AgentPG was taken from the repositories' code and the ecosystem manifest and docs.
- The 129-test count in the ecosystem report is a static count of `def test_*`; the executed numbers differ (this checkout: 97 passed and 11 skipped without a database; the database tests were not run here).
- The UI was driven in a real Chrome only by the video recorder's rehearsal (`docs/video/record.spec.ts`, against the demo stand-in model, as admin). The approver and viewer experiences were checked through the API tests and by reading the UI code, not in a browser.
