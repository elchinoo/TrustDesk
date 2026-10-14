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
- API tokens are random, shown once, stored as SHA-256 hashes, revocable, and have a `read` or `full` scope.
  `read` tokens can search and list but cannot change data, manage accounts or spend provider credit.
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
