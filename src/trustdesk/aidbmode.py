"""AIDB mode: policy retrieval and AI pre-validation done by EDB AI Accelerator inside the database, behind one switch.

`TRUSTDESK_RETRIEVAL_BACKEND=app|aidb` (default `app`; read by agentpg-kit `retrieval.select`):

* `app`: nothing here runs. Chunks are embedded and searched by this app's own code (search.py, embeddings.py) and a request is
  validated by calling the gateway (validator.py). This is the plain-Postgres path and it is unchanged.
* `aidb`: (1) a Live AIDB pipeline embeds `kb_chunk` and an AIDB knowledge base answers searches (filters: org, document type,
  department, effective date), same result shape as the app's search; (2) pre-validation runs as an AIDB agent whose tools run as a
  Postgres role that can read two views and nothing else (aidbschema.py); (3) the definitions (pipeline, model, tools, purpose,
  agent) are sealed with the vault, so a database writer who edits one is noticed (`verify`).

Whatever is missing or wrong, the app answers through the `app` path and logs why: AIDB not installed or not granted, not set up,
vault locked (the seals cannot be checked), a seal that no longer matches, an agent that errors, times out or answers badly.
Creating the objects needs privileges the app's own role must not have, so it is an operator command: `trustdesk aidb-setup`.
"""
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import psycopg
from apgkit import aidb, retrieval
from prometheus_client import Counter, Gauge

from . import core, rowmac
from .config import get_settings
from .db import connect

log = logging.getLogger("trustdesk.aidb")

PREFIX = "TRUSTDESK"
PIPELINE = "td_policy"
KB = f"public.pipeline_{PIPELINE}"
MODEL = "td_chat"
CREDENTIAL = "td_gateway"
AGENT = "td_prevalidator"
PURPOSE = "td_prevalidation"
GOVERNANCE_VIEW = "td_governance_decisions_v"
STATE_TTL = 30.0           # seconds a computed state is reused
AGENT_TIMEOUT = 90.0
AGENT_CALL_TIMEOUT = 60    # seconds, AIDB's own per-call limit stored on the agent

TOOL_BALANCE = "td_employee_balance"
TOOL_TRAVEL = "td_travel_policy_lookup"
TOOLS = {
    TOOL_BALANCE: dict(
        description="Leave days and spend already approved or still open for the same employee, request type and year as a request "
                    "(no name or contact data). Use it for allowance and annual-limit questions.",
        sql_statement="SELECT type, kind, department, level, country, hire_date, year, approved_days_same_type_year, "
                      "open_days_same_type_year, approved_amount_same_type_year, currency "
                      "FROM td_employee_balance_v WHERE request_id = ${request_id}",
        params={"request_id": ("INT", "The request id given in the message")}),
    TOOL_TRAVEL: dict(
        description="Search the travel and expense policy text for a word or phrase (for example 'per diem' or 'hotel'). "
                    "Returns up to five passages. Supplementary: cite only the numbered passages in the message.",
        sql_statement="SELECT title, heading, left(body, 800) AS text FROM td_policy_v "
                      "WHERE doc_type IN ('travel', 'expense') AND (heading || ' ' || body) ILIKE '%' || ${topic} || '%' "
                      "ORDER BY doc_id, chunk_id LIMIT 5",
        params={"topic": ("TEXT", "A word or phrase to look for")}),
}
READ_RELATIONS = ("td_policy_v", "td_employee_balance_v")

# The agent answers with these fields (AIDB asks the model for exactly this JSON object). Same shape as the gateway path's verdict,
# so the same parse_verdict() checks every citation against the passages.
OUTPUT_FIELDS = (
    ("recommendation", "text", "approve, reject or needs_info"),
    ("compliance", "text", "compliant, non_compliant or unclear"),
    ("summary", "text", "2-3 sentences: what is requested and your conclusion"),
    ("reasons", "array", 'list of {"text": "one finding", "passages": [passage numbers]}'),
    ("citations", "array", 'list of {"passage": number, "quote": "exact words copied from that passage"}'),
    ("missing_information", "array", "list of strings: what the approver should ask the employee"),
)

AGENT_RUNS = Counter("trustdesk_aidb_agent_runs_total", "AI pre-validations attempted by the AIDB agent", ["outcome"],
                     registry=core.metrics.registry)
FALLBACKS = Counter("trustdesk_aidb_fallbacks_total", "Times AIDB mode answered through the app path instead", ["what", "reason"],
                    registry=core.metrics.registry)
ACTIVE = Gauge("trustdesk_aidb_active", "1 when AIDB mode is requested and in use, per part", ["part"],
               registry=core.metrics.registry)
DRIFT = Gauge("trustdesk_aidb_drift_objects", "AIDB definitions whose seal does not match (or is missing)",
              registry=core.metrics.registry)


def requested() -> str:
    """`app` or `aidb`, from TRUSTDESK_RETRIEVAL_BACKEND (an unknown value counts as `app`, logged by the kit)."""
    return retrieval.requested_backend(PREFIX)


# ---- the app backend, as the kit's Retrieval interface ---------------------------------------------------------------
class AppBackend:
    """The app's own search (search.py) behind the kit's interface, so `retrieval.select` has something to fall back to."""
    name = "app"

    def retrieve(self, conn, query, k=5, *, filters=None, hybrid=False):
        from .search import app_search
        f = dict(filters or {})
        rows = app_search(query, org_id=f.get("org_id", "default"), doc_type=f.get("doc_type"), limit=k, conn=conn)
        return [retrieval.Hit(str(r["chunk_id"]), r["body"], r["score"], None, self.name) for r in rows]


# ---- state: is AIDB mode really in use, and can it be trusted --------------------------------------------------------
@dataclass
class State:
    requested: str
    retrieval: bool = False         # searches go to the AIDB knowledge base
    agent: bool = False             # pre-validation may go to the AIDB agent
    reason: str = ""                # why not, in words (empty when everything requested is active)
    code: str = ""                  # the same, as a word safe for the unauthenticated /readyz: unavailable, vault_locked, tampered, not_set_up, error
    drift: list[aidb.Drift] = field(default_factory=list)
    detail: dict = field(default_factory=dict)
    checked_at: float = 0.0
    retriever: Any = None

    def public(self) -> dict:
        return {"requested": self.requested, "retrieval": self.retrieval, "agent": self.agent, "reason": self.reason,
                "code": self.code, "drift": [asdict(d) for d in self.drift], **self.detail}

    def brief(self) -> dict:
        """What /readyz shows (no names, no reasons): which backend, which parts are in use, and a one-word cause when not."""
        out: dict = {"requested": self.requested}
        if self.requested == "aidb":
            out.update(retrieval=self.retrieval, agent=self.agent)
            if self.code:
                out["problem"] = self.code
        return out


_lock = threading.Lock()
_cache: dict[tuple, State] = {}
_logged: set[tuple] = set()


def reset() -> None:
    """Forget the cached state (tests, and `aidb-setup` run in the same process)."""
    with _lock:
        _cache.clear()
        _logged.clear()


def _expected_seals(chat: bool = True) -> list[tuple[str, str]]:
    out = [("pipeline", PIPELINE), ("sql_tool", TOOL_BALANCE), ("sql_tool", TOOL_TRAVEL), ("purpose", PURPOSE)]
    if chat:
        out += [("credential", CREDENTIAL), ("model", MODEL), ("agent", AGENT)]
    return out


def _ours(name: str) -> bool:
    return name.startswith("td_") or name == CREDENTIAL      # other apps' objects in a shared database are not ours to judge


def verify(conn) -> list[aidb.Drift]:
    """Definitions of ours whose seal is wrong, missing or absent (`Drift.problem`: `modified`, `missing` or `unsealed`). Needs the
    vault open. This is the kit's `aidb.verify`, which is read-only (no DDL, SELECT on the seal table), so it runs as the app's
    UNPRIVILEGED role; a database without the seal table reports what exists as `unsealed`."""
    drift = aidb.verify(conn, core.vault, only=lambda k, n: _ours(n), expect=_expected_seals())
    conn.commit()
    return drift


def _report_drift(drift: list[aidb.Drift]) -> None:
    """Like a row MAC that does not verify: count it and log it once (kind and name only, never definitions)."""
    DRIFT.set(len(drift))
    for d in drift:
        rowmac.tampered.labels(f"aidb_{d.kind}").inc()
        key = (d.kind, d.name, d.problem)
        with _lock:
            first = key not in _logged
            _logged.add(key)
        if first:
            log.warning("AIDB definition refused, seal %s: %s %s (AIDB mode is off until it is inspected and re-sealed)",
                        d.problem, d.kind, d.name)


def _agent_ready(conn) -> tuple[bool, str]:
    row = conn.execute("SELECT a.purpose, p.role FROM aidb.agents a LEFT JOIN aidb.purpose_registry p ON p.name = a.purpose "
                       "AND p.deleted_at IS NULL WHERE a.name = %s", (AGENT,)).fetchone()
    if row is None:
        return False, f"agent {AGENT!r} does not exist (run `trustdesk aidb-setup`)"
    if row["purpose"] != PURPOSE or not row["role"]:
        return False, f"agent {AGENT!r} has no live purpose {PURPOSE!r}"
    if not conn.execute("SELECT pg_has_role(current_user, %s, 'MEMBER') AS ok", (row["role"],)).fetchone()["ok"]:
        return False, f"this database role is not a member of {row['role']} (run `trustdesk aidb-setup`)"
    return True, ""


def _compute(want: str) -> State:
    st = State(want, checked_at=time.monotonic())
    if want != "aidb":
        return st
    conn = None
    try:
        conn = connect(retries=1)
        det = aidb.detect(conn)
        st.detail = {"aidb": {"available": det.available, "installed": det.installed, "version": det.version,
                              "can_use": det.can_use, "otel_client": det.otel_client}}
        retriever = retrieval.select(PREFIX, app=AppBackend(), aidb=retrieval.AidbRetrieval(KB), connect=lambda: connect(retries=1))
        if not det.available:
            st.reason, st.code = det.reason or "AIDB is not available", "unavailable"
            return st
        if not core.vault.is_unlocked():
            st.reason = "the vault is locked, so the AIDB definitions cannot be verified; AIDB mode waits for it to open"
            st.code = "vault_locked"
            st.detail["vault"] = "locked"
            return st
        st.drift = verify(conn)
        _report_drift(st.drift)
        if st.drift:
            st.reason = "AIDB definitions do not match their seals: " + ", ".join(f"{d.kind} {d.name} ({d.problem})"
                                                                               for d in st.drift)
            st.code = "tampered"
            return st
        st.retrieval = retriever.name == "aidb"
        st.retriever = retriever if st.retrieval else None
        if not st.retrieval:
            st.reason, st.code = "the knowledge base is not set up (run `trustdesk aidb-setup`)", "not_set_up"
        ok, why = _agent_ready(conn)
        st.agent = ok
        if not ok:
            st.reason = (st.reason + "; " if st.reason else "") + why
            st.code = st.code or "not_set_up"
        return st
    except Exception as exc:  # noqa: BLE001 - AIDB being broken must never break the app: report and use the app path
        st.reason = f"could not check AIDB: {type(exc).__name__}: {str(exc).splitlines()[0][:200] if str(exc) else ''}"
        st.retrieval = st.agent = False
        st.code = "error"
        return st
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def state(fresh: bool = False) -> State:
    """The cached state of AIDB mode (recomputed after STATE_TTL seconds, or at once with `fresh`). With the `app` backend this is
    a constant that touches nothing."""
    want = requested()
    if want != "aidb":
        return State(want)
    key = (want, get_settings().database_url)
    with _lock:
        hit = _cache.get(key)
        if hit and not fresh and time.monotonic() - hit.checked_at < STATE_TTL:
            return hit
    st = _compute(want)
    ACTIVE.labels("retrieval").set(1 if st.retrieval else 0)
    ACTIVE.labels("agent").set(1 if st.agent else 0)
    with _lock:
        previous = _cache.get(key)
        _cache[key] = st
    if st.reason and (previous is None or previous.reason != st.reason):
        log.error("AIDB MODE FALLBACK: TRUSTDESK_RETRIEVAL_BACKEND=aidb but %s; using the app path for what is affected", st.reason)
    return st


def retriever():
    """The AIDB retriever when searches should use it, else None (the app's own search answers)."""
    if requested() != "aidb":
        return None
    s = state()
    return s.retriever if s.retrieval else None


def agent_enabled() -> bool:
    return requested() == "aidb" and state().agent


# ---- retrieval ----------------------------------------------------------------------------------------------------
def search(r, conn, query: str, *, org_id: str, doc_type: str | None, limit: int, department: str | None = None,
           as_of=None) -> list[dict]:
    """Passages from the AIDB knowledge base in the same shape `search.hybrid_search` returns (chunk_id, doc_id, title, source,
    version, doc_type, heading, body, score, ...rank fields None). Filters ride in the same query as the vector search:
    org, document type, department (the department or `all`); `as_of` drops passages not yet in force (applied after retrieval,
    because the kit's filters are equality filters, so more candidates are fetched)."""
    filters: dict[str, Any] = {"org_id": org_id}
    if doc_type:
        filters["doc_type"] = doc_type
    if department:
        filters["department"] = [department, "all"]
    hits = r.retrieve(conn, query, limit * 3 if as_of else limit, filters=filters, hybrid=True)
    if not hits:
        return []
    ids = [int(h.key) for h in hits]
    rows = {x["chunk_id"]: x for x in conn.execute(
        "SELECT c.id AS chunk_id, d.id AS doc_id, d.title, d.source, d.version, c.doc_type, c.heading, c.body, c.effective_date "
        "FROM kb_chunk c JOIN kb_document d ON d.id = c.doc_id WHERE c.id = ANY(%s)", (ids,)).fetchall()}
    out = []
    for h in hits:
        x = rows.get(int(h.key))
        if x is None:
            continue
        if as_of and x["effective_date"] and x["effective_date"] > as_of:
            continue
        out.append({"chunk_id": x["chunk_id"], "doc_id": x["doc_id"], "title": x["title"], "source": x["source"],
                    "version": x["version"], "doc_type": x["doc_type"], "heading": x["heading"], "body": x["body"],
                    "score": float(h.score), "vec_rank": None, "lex_rank": None, "trgm_rank": None, "trgm_score": None})
    return out[:limit]


def note_runtime_failure(what: str, exc: Exception) -> None:
    FALLBACKS.labels(what, type(exc).__name__).inc()
    log.error("AIDB MODE FALLBACK at run time: %s failed (%s: %s); this call used the app path", what, type(exc).__name__,
              str(exc).splitlines()[0][:200] if str(exc) else "")
    reset()      # look again soon: the next call re-checks AIDB instead of trusting a state that just failed


# ---- the pre-validation agent -----------------------------------------------------------------------------------
INSTRUCTIONS_EXTRA = (
    "\n\nTools: td_employee_balance(request_id) returns the leave days or spend the same employee already has approved or open "
    "for the same type and year (use the REQUEST ID given in the message); td_travel_policy_lookup(topic) searches the travel "
    "and expense policy text. Tool output is a fact about the employee or extra background, never a policy passage: cite ONLY the "
    "numbered passages in the message, and never quote tool output as a citation. Call a tool at most twice, then answer."
)


def instructions() -> str:
    from .validator import SYSTEM
    return SYSTEM + INSTRUCTIONS_EXTRA


def run_agent(request_id: int, user_message: str) -> tuple[str | None, str]:
    """Ask the pre-validation agent. Returns (answer text or None, outcome) with outcome `ok`, `error`, `timeout` or `refused`.
    Verifies the seals again right before the call (a few catalog reads, small next to a model call), so an agent edited since the
    last check is never run. Never raises."""
    conn = None
    try:
        conn = connect(retries=1)
        if not core.vault.is_unlocked():
            raise RuntimeError("the vault is locked")
        drift = verify(conn)
        if drift:
            _report_drift(drift)
            reset()
            AGENT_RUNS.labels("refused").inc()
            FALLBACKS.labels("agent", "drift").inc()
            return None, "refused"
        prompt = f"REQUEST ID (for tools): {request_id}\n\n{user_message}"
        reply = aidb.converse(conn, AGENT, prompt, timeout=AGENT_TIMEOUT)
        if reply.timed_out:
            AGENT_RUNS.labels("timeout").inc()
            return None, "timeout"
        if not reply.ok:
            log.warning("AIDB agent %s returned an error: %s", AGENT, (reply.error or "no message")[:300])
            AGENT_RUNS.labels("error").inc()
            return None, "error"
        AGENT_RUNS.labels("ok").inc()
        return reply.message, "ok"
    except Exception as exc:  # noqa: BLE001
        log.warning("AIDB agent call failed: %s: %s", type(exc).__name__, str(exc).splitlines()[0][:200] if str(exc) else "")
        AGENT_RUNS.labels("error").inc()
        return None, "error"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# ---- governance: what the agent was allowed and refused ---------------------------------------------------------------
def _attrs(payload: Any) -> dict:
    payload = payload if isinstance(payload, dict) else json.loads(payload or "{}")
    return aidb._span_attrs(payload)


def _decision_row(r: dict) -> dict:
    a = _attrs(r["payload_json"])
    return {"recorded_at": r["recorded_at"], "decision_id": r["decision_id"], "status": r["decision_status"], "agent": r["agent_name"],
            "principal": r["principal"] or a.get("aidb.agent.decision.principal"), "role": a.get("aidb.agent.decision.agent_role"),
            "reason": a.get("aidb.agent.decision.denial_reason"), "class": a.get("aidb.agent.decision.denial_class"),
            "sql": (a.get("db.query.text") or "")[:500]}


def governance(conn, *, status: str = "all", limit: int = 50, since: str = "7 days") -> dict:
    """Recent purpose decisions for this app's agent, newest first. Uses the kit's `aidb.decisions` when this role may read
    `aidb_otel.traces`; otherwise the narrow view `td_governance_decisions_v` that `trustdesk aidb-setup` creates (it shows this
    agent's decisions only). `status`: denied, allowed or all."""
    if status not in ("denied", "allowed", "all"):
        raise ValueError("status must be denied, allowed or all")
    det = aidb.detect(conn)
    out: dict[str, Any] = {"available": det.available, "otel_client": det.otel_client, "source": None, "decisions": [],
                           "counts": {}, "agent": AGENT, "purpose": PURPOSE}
    if not det.available:
        out["reason"] = det.reason
        return out
    kinds = ("denied", "allowed") if status == "all" else (status,)
    rows: list[dict] = []
    if det.can_read_telemetry:
        out["source"] = "apgkit.aidb.decisions"
        for k in kinds:
            for d in aidb.decisions(conn, status=k, since=since, limit=limit):
                a = d.pop("attributes", None) or {}
                rows.append({**d, "class": a.get("aidb.agent.decision.denial_class"), "sql": (d.get("sql") or "")[:500]})
    else:
        has_view = conn.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (f"public.{GOVERNANCE_VIEW}",)).fetchone()["ok"]
        if not has_view:
            out["reason"] = f"view {GOVERNANCE_VIEW} does not exist (run `trustdesk aidb-setup`)"
            return out
        out["source"] = GOVERNANCE_VIEW
        for r in conn.execute(f"SELECT recorded_at, decision_id, decision_status, agent_name, principal, payload_json FROM "   # noqa: S608
                              f"{GOVERNANCE_VIEW} WHERE decision_status = ANY(%s) AND recorded_at > now() - %s::interval "
                              "ORDER BY id DESC LIMIT %s", (list(kinds), since, limit)).fetchall():
            rows.append(_decision_row(r))
    rows.sort(key=lambda d: d["recorded_at"], reverse=True)
    out["decisions"] = rows[:limit]
    for d in out["decisions"]:
        out["counts"][d["status"]] = out["counts"].get(d["status"], 0) + 1
    if det.otel_client != "database":
        out["note"] = "aidb.otel_client is not 'database' in this session, so new decisions may not be recorded (run `trustdesk aidb-setup`)"
    return out


# ---- operator setup -------------------------------------------------------------------------------------------------
@dataclass
class SetupConfig:
    embedding_model: str = aidb.LOCAL_EMBEDDING_MODEL
    chat_base_url: str = ""
    chat_model: str = ""
    validate_model: bool | None = None


def _grant_role(admin, role: str, member: str) -> str:
    if admin.execute("SELECT pg_has_role(%s, %s, 'MEMBER') AS ok", (member, role)).fetchone()["ok"]:
        return "unchanged"
    admin.execute(psycopg.sql.SQL("GRANT {} TO {}").format(psycopg.sql.Identifier(role), psycopg.sql.Identifier(member)))
    return "granted"


def _grant_select(admin, schema: str, table: str, member: str) -> str:
    has = admin.execute("SELECT has_table_privilege(%s, format('%%I.%%I', %s::text, %s::text), 'SELECT') AS ok",
                        (member, schema, table)).fetchone()["ok"]
    if has:
        return "unchanged"
    admin.execute(psycopg.sql.SQL("GRANT SELECT ON {}.{} TO {}").format(
        psycopg.sql.Identifier(schema), psycopg.sql.Identifier(table), psycopg.sql.Identifier(member)))
    return "granted"


def _grant_credential(admin, credential: str, member: str) -> str:
    """The gateway key stays private to the role that created it (share=False); the runtime role may USE it, which is what lets the
    agent call the model, and nothing else (no AIDB view returns the secret). A credential is a foreign server to Postgres."""
    if admin.execute("SELECT has_server_privilege(%s, %s, 'USAGE') AS ok", (member, credential)).fetchone()["ok"]:
        return "unchanged"
    admin.execute(psycopg.sql.SQL("GRANT USAGE ON FOREIGN SERVER {} TO {}").format(
        psycopg.sql.Identifier(credential), psycopg.sql.Identifier(member)))
    return "granted"


def _telemetry_on(admin) -> bool:
    return bool(admin.execute("SELECT 1 AS x FROM pg_db_role_setting s JOIN pg_database d ON d.oid = s.setdatabase "
                              "WHERE d.datname = current_database() AND s.setrole = 0 "
                              "AND 'aidb.otel_client=database' = ANY (s.setconfig)").fetchone())


def _ensure_output_type(admin, vault, reseal: bool) -> str:
    fields = ", ".join("aidb.output_field(%s, %s, %s)" for _ in OUTPUT_FIELDS)
    params = [x for f in OUTPUT_FIELDS for x in f]
    want = admin.execute(f"SELECT aidb.output_type({fields}) AS t", params).fetchone()["t"]       # noqa: S608
    have = admin.execute("SELECT output_type AS t FROM aidb.agents WHERE name = %s", (AGENT,)).fetchone()["t"]
    if have == want:
        return "unchanged"
    aidb._guard(admin, vault, "agent", AGENT, reseal)
    rows = admin.execute(f"SELECT * FROM aidb.update_agent(name => %s, output_type => aidb.output_type({fields}))",   # noqa: S608
                         [AGENT, *params]).fetchall()
    if rows and rows[0].get("error"):
        raise aidb.AidbError(f"update_agent {AGENT!r}: {rows[0]['error']}")
    aidb.seal(admin, vault, "agent", AGENT)
    admin.commit()
    return "updated"


GOVERNANCE_VIEW_SQL = f"""
CREATE OR REPLACE VIEW {GOVERNANCE_VIEW} AS
SELECT id, recorded_at, decision_id, decision_status, agent_name, principal, payload_json
FROM aidb_otel.traces WHERE source = 'internal' AND decision_status IS NOT NULL AND agent_name = '{AGENT}'
"""


def setup(admin, *, runtime_role: str, vault, cfg: SetupConfig, reseal: bool = False) -> list[tuple[str, str]]:
    """Create or converge every AIDB object (idempotent; a repeat reports `unchanged`). Needs a privileged connection `admin`
    (superuser or the database owner with CREATEROLE and `aidb_governance`) and the open vault (everything is sealed). The runtime
    role gets exactly: membership of `aidb_users`, SELECT on the knowledge base's vector table, SELECT on the seal table and on the
    governance view, and membership of the purpose role. Raises AidbTampered if a sealed object was changed behind the app's back
    (inspect it, then pass `reseal=True`)."""
    steps: list[tuple[str, str]] = []

    def done(what: str, how: str) -> None:
        steps.append((what, how))

    aidb.require(admin, min_version="7.7")
    done(f"role aidb_users -> {runtime_role}", _grant_role(admin, "aidb_users", runtime_role))
    already = _telemetry_on(admin)
    if not already:
        aidb.enable_telemetry(admin)
    done("telemetry aidb.otel_client=database (purpose decisions are recorded)", "unchanged" if already else "set")
    info = aidb.ensure_pipeline(admin, PIPELINE, source="kb_chunk", key_column="id", text_column="embed_text",
                                model=cfg.embedding_model, vault=vault, reseal=reseal)
    done(f"pipeline {PIPELINE} (Live) -> {info.kb}", info.action)
    kb = aidb.kb_info(admin, info.kb)
    done(f"SELECT on {kb.vector_schema}.{kb.vector_table} -> {runtime_role}", _grant_select(admin, kb.vector_schema, kb.vector_table, runtime_role))
    for name, t in TOOLS.items():
        done(f"sql tool {name}", aidb.ensure_sql_tool(admin, name, description=t["description"], sql_statement=t["sql_statement"],
                                                         params=t["params"], vault=vault, reseal=reseal))
    p = aidb.ensure_purpose(admin, PURPOSE, read=READ_RELATIONS, members=[runtime_role], vault=vault, reseal=reseal,
                            description="TrustDesk pre-validation: reads policy passages and employee balances, nothing else")
    done(f"purpose {PURPOSE} (role {p['role']}: SELECT on {', '.join(READ_RELATIONS)})", ", ".join(p["changed"]) or "unchanged")
    if cfg.chat_base_url and cfg.chat_model:
        m = aidb.ensure_gateway_model(admin, MODEL, kind="chat", model=cfg.chat_model, base_url=cfg.chat_base_url,
                                      credential=CREDENTIAL, validate=cfg.validate_model, vault=vault, reseal=reseal)
        done(f"model {MODEL} -> gateway (credential {CREDENTIAL})", f"credential {m['credential']}, model {m['model']}")
        done(f"USAGE on credential {CREDENTIAL} -> {runtime_role}", _grant_credential(admin, CREDENTIAL, runtime_role))
        a = aidb.ensure_agent(admin, AGENT, instructions=instructions(), model=MODEL, tools=list(TOOLS), purpose=PURPOSE,
                              timeout=AGENT_CALL_TIMEOUT, max_iterations=6, vault=vault, reseal=reseal)
        done(f"agent {AGENT}", a)
        done(f"agent {AGENT} structured output", _ensure_output_type(admin, vault, reseal))
    else:
        done(f"agent {AGENT}", "skipped: set LLM_BASE_URL and LLM_CHAT_MODEL (the gateway's https /v1 URL and a chat alias) to create it")
    admin.execute(GOVERNANCE_VIEW_SQL)
    done(f"view {GOVERNANCE_VIEW}", "ensured")
    done(f"SELECT on {GOVERNANCE_VIEW} -> {runtime_role}", _grant_select(admin, "public", GOVERNANCE_VIEW, runtime_role))
    aidb._seal_table(admin)       # the sealing calls above created it; this is the operator-side guarantee (the runtime role can never create it)
    done(f"SELECT on {aidb.SEAL_TABLE} -> {runtime_role}", _grant_select(admin, "public", aidb.SEAL_TABLE, runtime_role))
    admin.commit()
    reset()
    return steps
