# Security policy

## Reporting a vulnerability
Please **do not open a public issue** for security problems. Use GitHub's private vulnerability reporting
("Security" tab → "Report a vulnerability") on this repository. Include steps to reproduce and the
affected version or commit.

## Security model

**Credentials**
- First run: no credentials exist and every protected route is denied. The server prints a one-time setup code
  to its log; whoever presents it in the browser sets the admin password. The code is held in memory only and
  changes on each restart until setup is complete.
- The admin password is stored only as a salted scrypt hash (N=2^15, r=8, p=1), 12-character minimum.
- Browser sessions use a random cookie (`HttpOnly`, `SameSite=Strict`, `Secure` over HTTPS). Only its SHA-256
  is stored server-side. Sessions last 12 hours and all end when the password changes or you sign out. Browser
  writes also require a custom header, and no credential is kept in page storage.
- API tokens are random, shown once, stored as SHA-256 hashes, revocable, and have a `read`, `metrics`, `client` or `full` scope.
  `read` tokens can search and list but cannot change data, manage accounts or spend provider credit.
  `client` tokens (for applications that send employee requests) can only use the employee and request endpoints of
  `/api/v1`: they cannot read documents, see the AI advice, decide, open reports or use `/mcp`. Scopes are matched
  strictly: a `read` or `metrics` token is refused on the request API.
- `/mcp` accepts bearer tokens only, never browser cookies.
- An optional static `TRUSTDESK_API_KEY` remains for automation. It is a full-access credential held in the
  environment or a file: treat it like a password, or leave it unset and use tokens.
- Failed sign-ins and bad credentials are rate limited (10 per minute per client address, in memory, per
  process). Behind a reverse proxy all clients can share one address, so add proxy-level limits too.
- With no credential configured the app **denies everything**. Anonymous access exists only behind the explicit
  opt-in `TRUSTDESK_ALLOW_ANONYMOUS=true`; never use it on a reachable host.

**Provider API keys**
- Encrypted at rest with Fernet using a random 256-bit data key. The data key is stored only *wrapped*
  (encrypted) under a key derived from the admin password, and optionally under a platform-managed secret
  (`TRUSTDESK_ENCRYPTION_KEY`). A database dump alone reveals no provider key.
- After a restart the data key is not in memory: the vault is **locked** until the admin enters the password
  (or the platform secret unlocks it automatically if configured). While locked, features that need a stored key
  return HTTP 423.
- Keys are never returned by the API (only the last 4 characters), never logged, and removed from provider
  error messages before they are shown.
- Recovery from a lost password is `trustdesk reset-admin`, which deletes the password, the vault and every stored
  provider key. There is no back door.
- An attacker with **both** database access and a running, unlocked server process, or with the admin password,
  can use the keys. Use provider-side spending limits and revoke a key at the provider if you suspect exposure.

**Outbound requests (SSRF)**
- Provider base URLs entered in the UI are validated before every call: only `https` (plain `http` only for
  allowed local servers), no embedded credentials, and no loopback, private, link-local (cloud metadata) or
  carrier-grade-NAT addresses unless the operator sets `TRUSTDESK_ALLOW_PRIVATE_LLM_URLS=true`. Link-local is
  always blocked. Redirects are never followed and responses are size-limited.
- Limitation: the host name is resolved once for the check and again by the HTTP client, so a hostile DNS
  server could answer differently the second time. Redirects are refused, which closes the usual route; run the
  server with network egress rules if that matters to you.

**Compliance data and AI validation**
- Requests can hold sensitive facts (sick leave, medical expenses). Only the approver session, `full` tokens and
  `client` tokens can read them; `read` and `metrics` tokens and `/mcp` cannot. A client token is a service
  credential: anything holding it can read every request, so keep it server-side.
- Employees are identified by the client application; TrustDesk does not authenticate individual employees.
- The AI model sees the request text and details, system-computed facts and retrieved policy passages. It never
  receives names, e-mail addresses or employee numbers. Free text from employees can still contain personal data.
- Request text is untrusted: it is delimited in the prompt, the model is told to ignore instructions inside it and
  has no tools. The model's verdict is parsed strictly and re-checked (citations must be real passages, quotes are
  matched against the passage text, uncited decisions are downgraded, a failed automatic check blocks an
  approval). The AI never decides: only an approver can, and every decision is written to an audit trail. A
  decision against the AI advice requires a written justification and is flagged.
- Every validation attempt stores a snapshot of the passages it saw, so the evidence survives later edits or
  deletion of a policy document.
- Report CSV files neutralise cells beginning with `=`, `+`, `-` (non-numeric) or `@`. Request bodies over 64 KB are
  refused.

**Content**
- Retrieved document text is untrusted data. The Ask prompt delimits passages, tells the model not to follow
  instructions inside them, gives it no tools, and flags answers that cite nothing. A model can still be
  misled, so treat answers as drafts and check the cited sources.
- With an external provider configured, passage text, queries and questions are sent to that provider.
- The knowledge base is single-tenant: anyone with full access can read and change every document.
- Uploads: 10 MB limit, 500-page PDF limit, file names reduced to a safe basename. No OCR, no macro execution.

**Platform**
- The container runs as a non-root user. Secrets are never baked into the image or stored in the repository.
- Interactive API docs are disabled. The UI is served with a strict Content-Security-Policy (no inline script
  or style) and `X-Frame-Options: DENY`, `nosniff` and `no-referrer`.
- Run behind TLS. The app itself does not terminate TLS.
