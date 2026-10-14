-- Provider accounts, instance settings, API tokens and web sessions.
CREATE TABLE app_setting (
    key   text PRIMARY KEY,
    value text NOT NULL
);

CREATE TABLE llm_account (
    id               bigserial PRIMARY KEY,
    name             text NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 60),
    provider         text NOT NULL DEFAULT 'custom',
    base_url         text NOT NULL,
    api_key_enc      text,                      -- encrypted with the vault data key; never returned by the API
    api_key_last4    text,
    chat_model       text,
    embedding_model  text,
    embedding_dim    integer CHECK (embedding_dim IS NULL OR embedding_dim BETWEEN 8 AND 2000),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE api_token (
    id            bigserial PRIMARY KEY,
    name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
    token_hash    text NOT NULL UNIQUE,         -- sha256 of the token; the token itself is shown once
    prefix        text NOT NULL,                -- first characters, to recognise a token in the list
    scope         text NOT NULL CHECK (scope IN ('read', 'full')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_used_at  timestamptz,
    revoked_at    timestamptz
);

CREATE TABLE web_session (
    token_hash    text PRIMARY KEY,             -- sha256 of the cookie value
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL
);
