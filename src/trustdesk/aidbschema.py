"""The few schema objects AIDB mode needs. They are plain Postgres (no AIDB objects here), idempotent, and applied after every
migrate (`migrate.run`), so a database that never uses AIDB just carries three nullable columns, one trigger and three views.

* `kb_chunk` gets `doc_type`, `department` and `effective_date`, copied from the owning document by a trigger when a chunk is
  inserted, so an AIDB knowledge base over `kb_chunk` can filter on them in the same query (the kit joins the knowledge base's vector
  table to its source table and applies the filter there). `department` and `effective_date` come from the document's `meta`
  (`{"department": "finance", "effective_date": "2026-01-01"}`); without them a chunk is for `all` departments and effective from the
  day its document was created. `embed_text` is the text the pipeline embeds: the heading and the body, as the app embeds them.
* Two views are everything the pre-validation agent's database role may read. Views run with their owner's privileges, so the role
  needs no access to `kb_chunk`, `employee`, `request` or anything else: `td_policy_v` (policy passages) and `td_employee_balance_v`
  (per request: leave days and spend already approved or open for the same employee, type and year, without name, email or employee
  number).
"""
import psycopg

POLICY_VIEW = "td_policy_v"
BALANCE_VIEW = "td_employee_balance_v"

SQL = """
ALTER TABLE kb_chunk ADD COLUMN IF NOT EXISTS doc_type text;
ALTER TABLE kb_chunk ADD COLUMN IF NOT EXISTS department text;
ALTER TABLE kb_chunk ADD COLUMN IF NOT EXISTS effective_date date;
ALTER TABLE kb_chunk ADD COLUMN IF NOT EXISTS embed_text text GENERATED ALWAYS AS
    (CASE WHEN heading = '' THEN body ELSE heading || E'\\n' || body END) STORED;

CREATE OR REPLACE FUNCTION td_kb_chunk_filters() RETURNS trigger LANGUAGE plpgsql AS $f$
BEGIN
    SELECT COALESCE(NEW.doc_type, d.doc_type),
           COALESCE(NEW.department, NULLIF(d.meta->>'department', ''), 'all'),
           COALESCE(NEW.effective_date,
                    CASE WHEN d.meta->>'effective_date' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN (d.meta->>'effective_date')::date END,
                    d.created_at::date)
      INTO NEW.doc_type, NEW.department, NEW.effective_date
      FROM kb_document d WHERE d.id = NEW.doc_id;
    RETURN NEW;
END $f$;
DROP TRIGGER IF EXISTS td_kb_chunk_filters ON kb_chunk;
CREATE TRIGGER td_kb_chunk_filters BEFORE INSERT ON kb_chunk FOR EACH ROW EXECUTE FUNCTION td_kb_chunk_filters();

-- chunks that existed before this (a one-off; the guard keeps repeat runs from rewriting the table)
UPDATE kb_chunk c SET doc_type = d.doc_type,
       department = COALESCE(NULLIF(d.meta->>'department', ''), 'all'),
       effective_date = COALESCE(CASE WHEN d.meta->>'effective_date' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
                                      THEN (d.meta->>'effective_date')::date END, d.created_at::date)
  FROM kb_document d WHERE d.id = c.doc_id AND c.doc_type IS NULL;

CREATE OR REPLACE VIEW td_policy_v AS
SELECT c.id AS chunk_id, d.id AS doc_id, d.org_id, d.title, c.doc_type, d.version, c.heading, c.body, c.department, c.effective_date
FROM kb_chunk c JOIN kb_document d ON d.id = c.doc_id;

CREATE OR REPLACE VIEW td_employee_balance_v AS
SELECT r.id AS request_id, r.type, r.kind, e.department, e.level, e.country, e.hire_date, r.currency,
       extract(year FROM r.start_date)::int AS year,
       (SELECT COALESCE(sum(o.end_date - o.start_date + 1), 0) FROM request o
         WHERE o.employee_id = r.employee_id AND o.type = r.type AND o.id <> r.id AND o.status = 'approved'
           AND extract(year FROM o.start_date) = extract(year FROM r.start_date))::int AS approved_days_same_type_year,
       (SELECT COALESCE(sum(o.end_date - o.start_date + 1), 0) FROM request o
         WHERE o.employee_id = r.employee_id AND o.type = r.type AND o.id <> r.id
           AND o.status IN ('submitted', 'pending_review', 'needs_info')
           AND extract(year FROM o.start_date) = extract(year FROM r.start_date))::int AS open_days_same_type_year,
       (SELECT COALESCE(sum(o.amount), 0) FROM request o
         WHERE o.employee_id = r.employee_id AND o.type = r.type AND o.id <> r.id AND o.status = 'approved'
           AND o.currency = r.currency AND extract(year FROM o.start_date) = extract(year FROM r.start_date)) AS approved_amount_same_type_year
FROM request r JOIN employee e ON e.id = r.employee_id;
"""


def apply(conn: psycopg.Connection) -> None:
    """Create or refresh the AIDB-mode objects. Safe to repeat; the caller commits."""
    conn.execute(SQL)
