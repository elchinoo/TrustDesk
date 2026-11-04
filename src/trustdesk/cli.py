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
    sub.add_parser("adopt-rowmacs", help="sign the security-relevant rows that have no row MAC yet (asks for the admin password)")
    setup = sub.add_parser("aidb-setup", help="operator: create or converge the AIDB pipeline, tools, purpose and agent (needs a privileged "
                                              "database connection; idempotent)")
    setup.add_argument("--runtime-role", default="", help="the database role the app runs as (default: the user of DATABASE_URL)")
    setup.add_argument("--embedding-model", default="", help="AIDB embedding model for the knowledge base (default: the local bge-small)")
    setup.add_argument("--skip-model-check", action="store_true", help="register the chat model without AIDB calling the endpoint first")
    setup.add_argument("--reseal", action="store_true", help="accept the current state of AIDB objects that no longer match their seal "
                                                             "(after you inspected them)")
    sub.add_parser("aidb-status", help="AIDB mode: requested backend, what is in use and why not, pipeline lag, seals, recent decisions")
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
    elif args.cmd == "adopt-rowmacs":
        return _adopt_rowmacs()
    elif args.cmd == "aidb-setup":
        return _aidb_setup(args)
    elif args.cmd == "aidb-status":
        return _aidb_status()
    elif args.cmd == "serve":
        _serve()
    elif args.cmd == "start":
        return _start()
    return 0


def _adopt_rowmacs() -> int:
    """Sign every security-relevant row that has no MAC (a database that existed before row MACs, or rows written while the vault was
    locked). It TRUSTS THE DATABASE AS IT IS AT THIS MOMENT: run it once, after migrating, on a database you believe untampered.
    The admin password is asked for on the terminal (or the platform secret TRUSTDESK_ENCRYPTION_KEY opens the vault); it is never an argument."""
    import getpass

    from . import rowmac
    from .core import vault
    from .db import connect

    with connect() as conn:
        password = None
        if vault.has_password_wrap(conn) and not vault.is_unlocked():
            password = getpass.getpass("Admin password: ")
        vault.try_unlock_with_platform_secret(conn)
        try:
            if not vault.is_unlocked() and password is not None:
                vault.unlock_with_password(conn, password)
        except Exception as exc:  # noqa: BLE001 - VaultError and friends: say it, never echo the password
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        if not vault.is_unlocked():
            print("the vault is locked: the admin password is wrong, or set TRUSTDESK_ENCRYPTION_KEY for this command", file=sys.stderr)
            return 1
        counts = rowmac.adopt(conn)
        conn.commit()
    print("signed rows without a MAC: " + ", ".join(f"{t} {n}" for t, n in counts.items()) +
          ". This trusted the database as it was now; rows written by anything else from here on are ignored.")
    return 0


def _unlock_vault_for_cli(conn) -> str:
    """Open the vault for an operator command: the admin password is asked on the terminal (never an argument), or the platform
    secret opens it. Returns an error message, or '' when the vault is open."""
    import getpass

    from .core import vault

    password = None
    if vault.has_password_wrap(conn) and not vault.is_unlocked():
        password = getpass.getpass("Admin password: ")
    vault.try_unlock_with_platform_secret(conn)
    try:
        if not vault.is_unlocked() and password is not None:
            vault.unlock_with_password(conn, password)
    except Exception as exc:  # noqa: BLE001 - never echo the password
        return f"refused: {exc}"
    if not vault.is_unlocked():
        return ("the vault is locked or not set up: sign in once to create the admin password, then run this again "
                "(or set TRUSTDESK_ENCRYPTION_KEY for this command)")
    return ""


def _aidb_setup(args) -> int:
    """Create or converge the AIDB objects. The privileged connection string comes from the environment variable
    TRUSTDESK_AIDB_ADMIN_DSN (or a file named by TRUSTDESK_AIDB_ADMIN_DSN_FILE) or a prompt. It is used for this command only:
    never stored, never printed, never put in the app's environment."""
    import getpass
    import os

    import psycopg
    from apgkit import aidb
    from apgkit.config import read_secret
    from psycopg.rows import dict_row

    from . import aidbmode
    from .config import get_settings
    from .core import vault
    from .db import connect

    with connect() as app:
        runtime = args.runtime_role or app.execute("SELECT current_user AS u").fetchone()["u"]
        err = _unlock_vault_for_cli(app)
    if err:
        print(err, file=sys.stderr)
        return 1
    dsn = read_secret("TRUSTDESK_AIDB_ADMIN_DSN") or getpass.getpass("Privileged database connection string: ")
    cfg = aidbmode.SetupConfig(embedding_model=args.embedding_model or aidb.LOCAL_EMBEDDING_MODEL,
                               chat_base_url=get_settings().llm_base_url, chat_model=get_settings().llm_chat_model,
                               validate_model=False if args.skip_model_check else None)
    try:
        with psycopg.connect(dsn, row_factory=dict_row) as admin:
            steps = aidbmode.setup(admin, runtime_role=runtime, vault=vault, cfg=cfg, reseal=args.reseal)
    except aidb.AidbTampered as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except (aidb.AidbError, psycopg.Error) as exc:   # the message never contains the connection string
        print(f"aidb-setup failed: {type(exc).__name__}: {str(exc).splitlines()[0][:300] if str(exc) else ''}", file=sys.stderr)
        return 1
    for what, how in steps:
        print(f"{how:>12}  {what}" if len(how) <= 12 else f"  {what}: {how}")
    backend = os.environ.get("TRUSTDESK_RETRIEVAL_BACKEND", "app") or "app"
    print(f"\nDone. Switch the app over with TRUSTDESK_RETRIEVAL_BACKEND=aidb (now: {backend}), then check `trustdesk aidb-status`.")
    return 0


def _aidb_status() -> int:
    import json
    from dataclasses import asdict

    from apgkit import aidb

    from . import aidbmode
    from .core import vault
    from .db import connect

    with connect() as conn:
        report: dict = {"requested_backend": aidbmode.requested()}
        vault.try_unlock_with_platform_secret(conn)
        report["vault"] = "open" if vault.is_unlocked() else "locked (seals cannot be verified; set TRUSTDESK_ENCRYPTION_KEY for this command)"
        report["aidb_mode"] = aidbmode.state(fresh=True).public() if aidbmode.requested() == "aidb" else \
            {"note": "the app backend is requested: nothing from AIDB is used"}
        report["aidb"] = aidb.status(conn)
        if vault.is_unlocked() and report["aidb"]["aidb"].get("available"):
            report["seals"] = [asdict(d) for d in aidbmode.verify(conn)]
        report["governance"] = aidbmode.governance(conn, limit=10)
    print(json.dumps(report, indent=2, default=str))
    problems = report["aidb"].get("problems", [])
    return 1 if (report.get("seals") or any("pipeline" in p for p in problems)) else 0


def _serve() -> None:
    import os

    import uvicorn

    from .core import BIND_HOST

    # Forwarded headers are honoured only from the reverse proxy (default: the local one), never from any caller (never "*").
    uvicorn.run("trustdesk.api:app", host=BIND_HOST, port=int(os.environ.get("PORT", "8000")), proxy_headers=True,
                forwarded_allow_ips=os.environ.get("TRUSTDESK_FORWARDED_ALLOW_IPS", "127.0.0.1"))


def boot_database(progress=None) -> int:
    """Everything that needs the database before serving: migrate, reconcile the embedding size, open the vault, seed.
    Returns a process exit code (0 = ready). Called by `trustdesk start` before serving, or, with sealed boot, by the app
    itself once the operator has unsealed it (the database URL does not exist before that)."""
    import os

    from .db import connect
    from .migrate import run

    say = progress or (lambda m: print(m, file=sys.stderr, flush=True))
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

    reconcile_on_startup(progress=say)   # never raises, never exits
    with connect() as conn:
        embedcfg.ensure_loaded(conn)
    try:    # seeding embeds through the configured account, whose key is in the vault: open it first, as the app does
        with connect(retries=1) as conn:
            vault.auto_unlock(conn)
    except Exception as exc:  # noqa: BLE001 - the app tries again at startup, and a locked vault only defers the seed
        print(f"WARNING: could not unlock the vault before seeding: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
    if os.environ.get("AUTO_SEED", "").lower() in {"1", "true", "yes"}:
        seedpack.auto_seed(progress=lambda m: print(m, file=sys.stderr if m.startswith("WARNING") else sys.stdout, flush=True))
    return 0


def _start() -> int:
    """Zero-touch startup for sandboxes where the user cannot run separate migrate/seed jobs."""
    from apgkit import sealed as sealing

    if sealing.Sealed.from_env().enabled:
        # Sealed boot: no database URL exists until an operator unseals the app, so the database work happens in the app's
        # deferred lifespan (api.py), not here.
        print("sealed boot: waiting for an operator to unseal (ecoctl unseal)", file=sys.stderr, flush=True)
        _serve()
        return 0
    code = boot_database()
    if code:
        return code
    _serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
