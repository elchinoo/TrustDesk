"""Typo-tolerant search (pg_trgm, migration 007) against a throwaway Postgres: policy search, requests, employees."""
import os
from datetime import date, timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.db
if not os.environ.get("TEST_DATABASE_URL"):
    pytest.skip("set TEST_DATABASE_URL to run DB tests", allow_module_level=True)

os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

from trustdesk import ingest, migrate, people, search, textsearch, workflow  # noqa: E402
from trustdesk.db import connect, connect_vector  # noqa: E402

SEED = Path(__file__).resolve().parents[1] / "src" / "trustdesk" / "seed" / "acme"


def _available(name):
    with connect() as c:
        return c.execute("SELECT 1 FROM pg_available_extensions WHERE name=%s", (name,)).fetchone() is not None


needs_ext = pytest.mark.skipif(not (_available("pg_trgm") and _available("unaccent")), reason="pg_trgm and unaccent contrib extensions are needed")


@pytest.fixture(scope="module")
def conn():
    with connect() as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        c.commit()
    migrate.run()
    c = connect_vector()
    for f in sorted(SEED.glob("*.md")):
        ingest.ingest_file(f, conn=c)
    yield c
    c.close()


def employee(c, no, name, email=None, **kw):
    return people.create(c, {"employee_no": no, "name": name, "email": email or f"{no.lower()}@example.com", **kw})


def make_request(c, no, title, **kw):
    body = {"employee_no": no, "type": "vacation", "title": title, "description": "time off",
            "start_date": (date.today() + timedelta(days=30)).isoformat(), "end_date": (date.today() + timedelta(days=34)).isoformat(), **kw}
    return workflow.create(c, body, actor="test")[0]


def sources(rows):
    return [r["source"] for r in rows]


# ---- migration ------------------------------------------------------------------------------------------------
@needs_ext
def test_migration_007_creates_the_indexes_and_is_idempotent(conn):
    names = {"kb_chunk_fold_trgm_idx", "kb_document_title_trgm_idx", "request_title_trgm_idx", "employee_name_trgm_idx"}
    have = {r["indexname"]: r["indexdef"] for r in conn.execute("SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY(%s)", (list(names),))}
    assert set(have) == names
    assert all("gin_trgm_ops" in d and "td_fold" in d for d in have.values())     # the immutable wrapper, not a second copy of unaccent
    sql = (migrate.MIGRATIONS / "007_trigram_search.sql").read_text()
    conn.execute(sql)           # again: IF NOT EXISTS / CREATE OR REPLACE, no error
    conn.commit()
    assert textsearch.trigram_ready(conn)
    assert conn.execute("SELECT public.td_fold('JOSÉ Álvarez') AS f").fetchone()["f"] == "jose alvarez"
    assert conn.execute("SELECT provolatile FROM pg_proc WHERE proname='td_fold'").fetchone()["provolatile"] == "i"


# ---- policy search --------------------------------------------------------------------------------------------
@needs_ext
@pytest.mark.parametrize("query,expected", [
    ("vaction polcy", "hr-vacation-policy.md"),
    ("incidnt respnse", "incident-response-policy.md"),
    ("encyption", "encryption-policy.md"),
])
def test_policy_typo_is_found_by_the_trigram_signal(conn, query, expected):
    # Ten results: the demo embedder is noise for a misspelt query (vector ranks are arbitrary) and can outrank a trigram-only hit.
    rows = search.hybrid_search(query, limit=10, conn=conn)
    hit = [r for r in rows if r["source"] == expected]
    assert hit, f"{query!r} did not find {expected}: {sources(rows)}"
    assert hit[0]["trgm_rank"] is not None and hit[0]["trgm_score"] >= textsearch.TRGM_POLICY_THRESHOLD
    assert hit[0]["lex_rank"] is None         # the exact words are not there: the full-text signal cannot be the reason
    best = min(rows, key=lambda r: r["trgm_rank"] if r["trgm_rank"] is not None else 10**6)
    assert best["source"] == expected         # the best trigram match is the right document


@needs_ext
def test_exact_query_still_has_text_and_vector_ranks_and_the_new_fields(conn):
    rows = search.hybrid_search("how is customer data encrypted at rest", limit=3, conn=conn)
    assert "encryption-policy.md" in sources(rows)
    for r in rows:
        assert {"vec_rank", "lex_rank", "trgm_rank", "trgm_score"} <= set(r)
    top = next(r for r in rows if r["source"] == "encryption-policy.md")
    assert top["vec_rank"] is not None and top["lex_rank"] is not None


@needs_ext
def test_a_trigram_signal_adds_to_the_fused_score(conn):
    rows = search.hybrid_search("vaction polcy", limit=5, conn=conn)
    typo = next(r for r in rows if r["trgm_rank"] is not None)
    expected = sum(1.0 / (search.RRF_K + x) for x in (typo["vec_rank"], typo["lex_rank"], typo["trgm_rank"]) if x is not None)
    assert typo["score"] == pytest.approx(expected)


@needs_ext
def test_accent_and_typo_together(conn):
    ingest.ingest_text(title="Viaje a São Paulo", source="sao-paulo.md", conn=conn,
                       text="# Viaje\n\nLos empleados que viajan a São Paulo reservan en el Café Müller.\n")
    for q in ("Sao Paolo", "cafe mueller", "Viage a Sao Paulo"):
        rows = search.hybrid_search(q, limit=3, conn=conn)
        assert "sao-paulo.md" in sources(rows), q
    conn.execute("DELETE FROM kb_document WHERE source='sao-paulo.md'")
    conn.commit()


@needs_ext
@pytest.mark.parametrize("gibberish", ["qzxjvkw", "zzzzzzzz", "xqpwmv bnrtlk", "%%%", "___", "ab"])
def test_gibberish_matches_nothing_by_trigram(conn, gibberish):
    rows = search.hybrid_search(gibberish, limit=5, conn=conn)
    assert all(r["trgm_rank"] is None for r in rows), [(r["source"], r["trgm_score"]) for r in rows]


@needs_ext
def test_thresholds_and_candidates_are_bounded(conn):
    rows = search.hybrid_search("vaction polcy", limit=20, candidates=3, conn=conn)
    ranked = [r for r in rows if r["trgm_rank"] is not None]
    assert 0 < len(ranked) <= 3
    assert all(r["trgm_score"] >= textsearch.TRGM_POLICY_THRESHOLD for r in ranked)
    assert sorted(r["trgm_rank"] for r in ranked) == list(range(1, len(ranked) + 1))      # dense ranks, best similarity first
    by_rank = sorted(ranked, key=lambda r: r["trgm_rank"])
    assert [r["trgm_score"] for r in by_rank] == sorted((r["trgm_score"] for r in by_rank), reverse=True)


@needs_ext
def test_hostile_query_is_data(conn):
    for q in ("100% _ \\ ' \"; DROP TABLE kb_chunk; --", "%vacation%", "vac_tion", "a" * 500, "💥 ünïcode ✓"):
        search.hybrid_search(q, limit=3, conn=conn)
    assert conn.execute("SELECT count(*) AS n FROM kb_chunk").fetchone()["n"] > 0
    rows = search.hybrid_search("%", limit=5, conn=conn)
    assert all(r["trgm_rank"] is None for r in rows)       # a wildcard is not a pattern here


@needs_ext
def test_doc_type_filter_applies_to_the_trigram_signal(conn):
    ingest.ingest_text(title="Zebra handbook", source="zebra.md", conn=conn, doc_type="hr", text="# Zebras\n\nStripes are mandatory.\n")
    ingest.ingest_text(title="Zebra rules", source="zebra2.md", conn=conn, doc_type="travel", text="# Zebras\n\nStripes are optional.\n")
    rows = search.hybrid_search("zebr handbok", limit=10, doc_type="travel", conn=conn)
    assert "zebra.md" not in sources(rows)
    assert all(r["doc_type"] == "travel" for r in rows)
    conn.execute("DELETE FROM kb_document WHERE source IN ('zebra.md','zebra2.md')")
    conn.commit()


def test_search_without_the_trigram_function_falls_back_to_vector_and_text(conn, monkeypatch):
    monkeypatch.setattr(textsearch, "trigram_ready", lambda c: False)
    rows = search.hybrid_search("vacation policy", limit=3, conn=conn)
    assert rows and all(r["trgm_rank"] is None and r["trgm_score"] is None for r in rows)


def test_search_sql_is_parameterised():
    src = Path(search.__file__).read_text() + Path(textsearch.__file__).read_text()
    assert "%(q)s" in src and "%(trgm)s" in src
    for text in (search._TRG, search._SQL):     # the SQL templates hold placeholders, never a query value
        assert "{" not in text.replace("{{", "")


# ---- requests and employees -----------------------------------------------------------------------------------
@pytest.fixture
def staff(conn):
    with connect() as c:
        c.execute("DELETE FROM request_event; DELETE FROM validation; DELETE FROM request; DELETE FROM employee")
        c.commit()
        employee(c, "E1", "José Álvarez")
        employee(c, "E2", "Maria Silva")
        employee(c, "E3", "Ana Alvarz")
        employee(c, "E4", "Peter Smith")
        yield c
        c.rollback()


def names(result):
    return [i["name"] for i in result["items"]]


def find_people(c, q, **kw):
    return people.listing(c, query=q, status=None, limit=kw.get("limit", 20), offset=0)


@needs_ext
def test_substring_and_accent_still_work_exactly(staff):
    assert names(find_people(staff, "jose"))[0] == "José Álvarez"
    assert "José Álvarez" in names(find_people(staff, "JOSÉ"))
    assert names(find_people(staff, "peter")) == ["Peter Smith"]
    assert find_people(staff, "E2")["items"][0]["name"] == "Maria Silva"       # employee number is an exact column, unchanged


@needs_ext
@pytest.mark.parametrize("q", ["alvares", "jse alvarez", "Jose Alvarz", "Álvarés", "joze alvarez"])
def test_employee_typos_find_the_person(staff, q):
    assert "José Álvarez" in names(find_people(staff, q)), q


@needs_ext
def test_employee_exact_hits_come_before_fuzzy_ones(staff):
    got = names(find_people(staff, "jose"))
    assert got[0] == "José Álvarez"         # substring (accent-insensitive) match first, 
    got = names(find_people(staff, "alvarez"))
    assert got[0] == "José Álvarez" and "Ana Alvarz" in got[1:]          # exact first, then the similar name, most similar first
    got = names(find_people(staff, "Maria Silba"))
    assert got[0] == "Maria Silva"


@needs_ext
def test_employee_gibberish_and_short_queries_match_nothing(staff):
    for q in ("qzxjvkw", "xxxxxx", "zq"):
        assert find_people(staff, q)["total"] == 0, q


@needs_ext
def test_employee_fuzzy_total_and_paging_agree(staff):
    r = people.listing(staff, query="alvares", status=None, limit=1, offset=0)
    assert r["total"] >= 1 and len(r["items"]) == 1
    assert r["items"][0]["name"] == "José Álvarez"
    assert people.listing(staff, query="alvares", status=None, limit=1, offset=r["total"])["items"] == []


@needs_ext
def test_employee_status_filter_still_applies_with_fuzzy(staff):
    people.update(staff, "E1", {"status": "inactive"})
    assert names(people.listing(staff, query="alvares", status="inactive", limit=10, offset=0)) == ["José Álvarez"]
    assert "José Álvarez" not in names(people.listing(staff, query="alvares", status="active", limit=10, offset=0))


@needs_ext
def test_request_search_typos_and_ordering(staff):
    make_request(staff, "E4", "Vacation in Lisbon")
    make_request(staff, "E1", "Dentist appointment")
    make_request(staff, "E2", "Vacatin at the lake")
    titles = lambda q: [i["title"] for i in workflow.listing(staff, search=q)["items"]]       # noqa: E731
    assert titles("lisbon") == ["Vacation in Lisbon"]
    got = titles("Vacation")
    assert got[0] == "Vacation in Lisbon" and "Vacatin at the lake" in got     # exact first, the typo after it
    assert "Vacation in Lisbon" in titles("vaction")
    assert "Dentist appointment" in titles("alvares")                            # by the employee's name, typo and accent
    assert "Dentist appointment" in titles("jose")
    assert workflow.listing(staff, search="qzxjvkw")["total"] == 0


@needs_ext
def test_request_search_wildcards_are_data(staff):
    make_request(staff, "E4", "Vacation in Lisbon")
    for q in ("%", "_", "%%", "\\", "'; DROP TABLE request; --"):
        assert workflow.listing(staff, search=q)["total"] == 0, q       # not a pattern: nothing to find in these titles
    make_request(staff, "E4", "100% remote week")
    make_request(staff, "E4", "under_score trip")
    assert [i["title"] for i in workflow.listing(staff, search="100%")["items"]] == ["100% remote week"]
    assert [i["title"] for i in workflow.listing(staff, search="under_score")["items"]] == ["under_score trip"]


@needs_ext
def test_fuzzy_candidates_are_capped(staff):
    for i in range(textsearch.FUZZY_LIMIT + 20):
        employee(staff, f"X{i}", f"Alvarez Clone {i}")
    hits = textsearch.fuzzy_hits(staff, "employee e", "e.id", ["e.name"], "alvarez")
    assert len(hits) == textsearch.FUZZY_LIMIT
    assert all(sim >= textsearch.TRGM_THRESHOLD for _, sim in hits)
    assert [s for _, s in hits] == sorted((s for _, s in hits), reverse=True)
    assert textsearch.fuzzy_hits(staff, "employee e", "e.id", ["e.name"], "al") == []      # too short to say anything


@needs_ext
def test_the_trigram_index_can_be_used(staff):
    """Optional check: with sequential scans off the planner may use the GIN index. Not asserted: small tables are free to scan."""
    staff.execute("SET LOCAL enable_seqscan = off")
    plan = "\n".join(r["QUERY PLAN"] for r in staff.execute(
        "EXPLAIN SELECT id FROM employee WHERE public.td_fold('alvares') <% public.td_fold(name)"))
    assert "employee" in plan
    staff.rollback()


def test_without_trigram_support_lists_keep_their_exact_behaviour(staff, monkeypatch):
    monkeypatch.setattr(textsearch, "trigram_ready", lambda c: False)
    assert find_people(staff, "alvares")["total"] == 0
    assert names(find_people(staff, "jose"))[0] == "José Álvarez"
    assert workflow.listing(staff, search="zzz")["total"] == 0


@needs_ext
def test_migration_007_without_unaccent_only_lowercases_and_the_original_restores_it(conn):
    sql = (migrate.MIGRATIONS / "007_trigram_search.sql").read_text()
    conn.execute(sql.replace("extname = 'unaccent'", "extname = 'no_such_ext'"))
    # Without unaccent the function only lowercases; whether it lowercases an accented capital depends on the database locale (not the C locale),
    # so compare with the database's own lower() and require that the accent is still there.
    plain = conn.execute("SELECT public.td_fold('JOSÉ') AS f, lower('JOSÉ') AS l").fetchone()
    assert plain["f"] == plain["l"] and "jose" != plain["f"]
    conn.execute(sql)
    assert conn.execute("SELECT public.td_fold('JOSÉ') AS f").fetchone()["f"] == "jose"
    conn.commit()
