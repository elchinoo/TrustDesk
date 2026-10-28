-- EDB/AgentPG contrib features, each optional: when an extension is not installable (no privilege, not shipped) the block is skipped
-- quietly and the app falls back (see textsearch.py and live.py).

-- 1. unaccent: a text search configuration that folds accents, so "jose" finds "José" and "cafe" finds "café" in policy search.
--    The generated column and its index are rebuilt on it (a one-off table rewrite).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'unaccent') THEN
        CREATE EXTENSION IF NOT EXISTS unaccent;
        IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'td_english') THEN
            CREATE TEXT SEARCH CONFIGURATION public.td_english (COPY = pg_catalog.english);
            ALTER TEXT SEARCH CONFIGURATION public.td_english
                ALTER MAPPING FOR hword, hword_part, word WITH public.unaccent, english_stem;
        END IF;
        DROP INDEX IF EXISTS kb_chunk_tsv_idx;
        ALTER TABLE kb_chunk DROP COLUMN tsv;
        ALTER TABLE kb_chunk ADD COLUMN tsv tsvector
            GENERATED ALWAYS AS (to_tsvector('public.td_english'::regconfig, heading || ' ' || body)) STORED;
        CREATE INDEX kb_chunk_tsv_idx ON kb_chunk USING gin (tsv);
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'unaccent not enabled: %', SQLERRM;
END $$;

-- 2. tcn: every change of a request row sends NOTIFY request_changes (payload: table, operation, primary key), which the app turns
--    into live updates of the Requests page.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'tcn') THEN
        CREATE EXTENSION IF NOT EXISTS tcn;
        DROP TRIGGER IF EXISTS request_tcn ON request;
        CREATE TRIGGER request_tcn AFTER INSERT OR UPDATE OR DELETE ON request
            FOR EACH ROW EXECUTE FUNCTION triggered_change_notification('request_changes');
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'tcn not enabled: %', SQLERRM;
END $$;
