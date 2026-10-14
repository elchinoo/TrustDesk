-- Knowledge base: documents and chunks with hybrid (vector + lexical) retrieval.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE kb_document (
    id          bigserial PRIMARY KEY,
    org_id      text NOT NULL DEFAULT 'default',
    title       text NOT NULL,
    source      text NOT NULL,                -- file name, URL or connector id
    doc_type    text NOT NULL DEFAULT 'policy',
    version     integer NOT NULL DEFAULT 1,
    sha256      text NOT NULL,
    meta        jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, source, sha256)
);

CREATE TABLE kb_chunk (
    id          bigserial PRIMARY KEY,
    doc_id      bigint NOT NULL REFERENCES kb_document(id) ON DELETE CASCADE,
    org_id      text NOT NULL DEFAULT 'default',
    ord         integer NOT NULL,
    heading     text NOT NULL DEFAULT '',
    body        text NOT NULL,
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('english', heading || ' ' || body)) STORED,
    embedding   vector({{EMBEDDING_DIM}}) NOT NULL,
    meta        jsonb NOT NULL DEFAULT '{}'
);

CREATE INDEX kb_chunk_embedding_idx ON kb_chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX kb_chunk_tsv_idx ON kb_chunk USING gin (tsv);
CREATE INDEX kb_chunk_body_trgm_idx ON kb_chunk USING gin (body gin_trgm_ops);
CREATE INDEX kb_chunk_doc_idx ON kb_chunk (doc_id, ord);
CREATE INDEX kb_chunk_org_idx ON kb_chunk (org_id);

CREATE TABLE kb_embedding_model (
    id          integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    name        text NOT NULL,
    dim         integer NOT NULL
);
