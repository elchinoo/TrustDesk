# Security policy

## Reporting a vulnerability
Please **do not open a public issue** for security problems. Use GitHub's private vulnerability reporting
("Security" tab → "Report a vulnerability") on this repository. Include steps to reproduce and the
affected version or commit.

## Security model
- `/api/*` and `/mcp` require `Authorization: Bearer <TRUSTDESK_API_KEY>`. With no key configured the app
  **denies everything** (the `trustdesk serve` / `start` commands generate a random key instead). Anonymous
  access exists only behind the explicit opt-in `TRUSTDESK_ALLOW_ANONYMOUS=true`; never use it on a reachable host.
- Repeated failed attempts from one client address are rate limited (10 per minute, in memory, per process).
  Put the app behind a reverse proxy or WAF for stronger protection.
- The API is single-tenant. Anyone holding the key can read and add all documents.
- Retrieved document text is untrusted data. If you feed search results to an LLM, treat them as potential
  prompt-injection input and don't give that model write access you wouldn't give an anonymous user.
- Uploads: 10 MB limit, 500-page PDF limit, file names are reduced to a safe basename. No OCR, no macro execution.
- Secrets come from environment variables only; none are baked into the image or stored in the repository.
- The container runs as a non-root user. Interactive API docs are disabled; the UI is served with a strict
  Content-Security-Policy and no inline script.
- Run behind TLS. The app itself does not terminate TLS.
