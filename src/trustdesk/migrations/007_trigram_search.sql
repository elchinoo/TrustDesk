-- Typo-tolerant search with pg_trgm: trigram indexes on the text that search looks at, plus one immutable folding function.
-- Idempotent. When pg_trgm cannot be created the block is skipped quietly and search falls back (see textsearch.py).

-- public.td_fold(text) = lower-case and, when the `unaccent` extension is there, accent-free. unaccent(regdictionary, text) is only
-- STABLE, so an index cannot use it directly; this wrapper is declared IMMUTABLE (the dictionary does not change in practice).
-- Queries use the same expression, so the indexes can serve them. Without unaccent it only lower-cases.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'unaccent') THEN
        CREATE OR REPLACE FUNCTION public.td_fold(text) RETURNS text
            LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
            AS $f$ SELECT lower(public.unaccent('public.unaccent'::regdictionary, $1)) $f$;
    ELSE
        CREATE OR REPLACE FUNCTION public.td_fold(text) RETURNS text
            LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
            AS $f$ SELECT lower($1) $f$;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_trgm') THEN
        CREATE EXTENSION IF NOT EXISTS pg_trgm;
        -- policy search: the chunk text (heading + body, what the full-text column covers) and the document title
        CREATE INDEX IF NOT EXISTS kb_chunk_fold_trgm_idx ON kb_chunk USING gin (public.td_fold(heading || ' ' || body) gin_trgm_ops);
        CREATE INDEX IF NOT EXISTS kb_document_title_trgm_idx ON kb_document USING gin (public.td_fold(title) gin_trgm_ops);
        -- request and employee search: the title of a request and the name of a person
        CREATE INDEX IF NOT EXISTS request_title_trgm_idx ON request USING gin (public.td_fold(title) gin_trgm_ops);
        CREATE INDEX IF NOT EXISTS employee_name_trgm_idx ON employee USING gin (public.td_fold(name) gin_trgm_ops);
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'trigram search not enabled: %', SQLERRM;
END $$;
