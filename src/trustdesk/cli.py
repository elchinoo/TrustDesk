import argparse
import sys
from pathlib import Path

SEED_DIR = Path(__file__).resolve().parent / "seed"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="trustdesk")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply SQL migrations")
    seed = sub.add_parser("seed", help="load a seed pack (default: acme)")
    seed.add_argument("pack", nargs="?", default="acme")
    ing = sub.add_parser("ingest", help="ingest files or directories")
    ing.add_argument("paths", nargs="+")
    q = sub.add_parser("search", help="hybrid search from the terminal")
    q.add_argument("query")
    q.add_argument("-n", type=int, default=5)
    sub.add_parser("reembed", help="re-embed all chunks after changing the embedding model or EMBEDDING_DIM")
    sub.add_parser("serve", help="run the API + MCP server")
    sub.add_parser("reset-admin", help="forget the admin password and delete stored provider keys (recovery)")
    sub.add_parser("start", help="container entrypoint: migrate, optionally seed (AUTO_SEED=true), serve")
    args = p.parse_args(argv)

    if args.cmd == "migrate":
        from .migrate import run

        applied = run()
        print("applied:", ", ".join(applied) if applied else "nothing (up to date)")
    elif args.cmd in ("seed", "ingest"):
        from .ingest import ingest_file

        if args.cmd == "seed" and args.pack == "acme":
            from .seedpack import load

            for r in load():
                print(f"{r['source']}: {r['status']} ({r['chunks']} chunks)")
            return 0
        paths = [SEED_DIR / args.pack] if args.cmd == "seed" else [Path(x) for x in args.paths]
        files = [f for p_ in paths for f in ([p_] if p_.is_file() else sorted(p_.rglob("*"))) if f.is_file()
                 and f.suffix.lower() in {".md", ".txt", ".pdf"}]
        if not files:
            print("no ingestible files found", file=sys.stderr)
            return 1
        for f in files:
            r = ingest_file(f)
            print(f"{f.name}: {r['status']} ({r['chunks']} chunks)")
    elif args.cmd == "search":
        from .search import hybrid_search

        for r in hybrid_search(args.query, limit=args.n):
            print(f"[{r['score']:.4f}] {r['title']} › {r['heading']} (v{r['version']})\n    {r['body'][:160]!r}")
    elif args.cmd == "reembed":
        from .embeddings import EmbeddingError
        from .migrate import reembed

        try:
            r = reembed()
        except (EmbeddingError, ValueError, RuntimeError) as exc:
            print(f"reembed failed (no changes were made): {exc}", file=sys.stderr)
            return 1
        print(f"done: {r['chunks']} chunks, vector size {r['dimension']}")
    elif args.cmd == "reset-admin":
        from . import auth
        from .db import connect

        with connect() as conn:
            auth.reset_admin(conn)
        print("admin password, vault and stored provider keys were cleared; the setup code flow is active again")
    elif args.cmd == "serve":
        _serve()
    elif args.cmd == "start":
        return _start()
    return 0


def _serve() -> None:
    import os

    import uvicorn

    from .core import BIND_HOST

    # Forwarded headers are honoured only from the reverse proxy (default: the local one), never from any caller (never "*").
    uvicorn.run("trustdesk.api:app", host=BIND_HOST, port=int(os.environ.get("PORT", "8000")), proxy_headers=True,
                forwarded_allow_ips=os.environ.get("TRUSTDESK_FORWARDED_ALLOW_IPS", "127.0.0.1"))


def _start() -> int:
    """Zero-touch startup for sandboxes where the user cannot run separate migrate/seed jobs."""
    import os

    from .db import connect
    from .migrate import run

    try:
        applied = run()  # db.connect retries with backoff, so a waking database is tolerated
    except Exception as exc:  # noqa: BLE001 - surface a readable cause, then fail the container
        print(f"startup failed while migrating: {exc}\n"
              "Check that the database is reachable. If the error mentions an extension, enable it first "
              "(it may need elevated rights), e.g. on AgentPG:\n"
              "  agentpg extensions create <instance-id> vector\n"
              "  agentpg extensions create <instance-id> pg_trgm", file=sys.stderr)
        return 1
    print("migrations applied:", ", ".join(applied) if applied else "none (up to date)")
    from . import embedcfg, seedpack, vault
    from .migrate import reconcile_on_startup

    reconcile_on_startup(progress=lambda m: print(m, file=sys.stderr, flush=True))   # never raises, never exits
    with connect() as conn:
        embedcfg.ensure_loaded(conn)
    try:    # seeding embeds through the configured account, whose key is in the vault: open it first, as the app does
        with connect(retries=1) as conn:
            vault.auto_unlock(conn)
    except Exception as exc:  # noqa: BLE001 - the app tries again at startup, and a locked vault only defers the seed
        print(f"WARNING: could not unlock the vault before seeding: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
    if os.environ.get("AUTO_SEED", "").lower() in {"1", "true", "yes"}:
        seedpack.auto_seed(progress=lambda m: print(m, file=sys.stderr if m.startswith("WARNING") else sys.stdout, flush=True))
    _serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
