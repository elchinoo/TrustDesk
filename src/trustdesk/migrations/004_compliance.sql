-- Compliance workflow: employees, requests, AI pre-validation results and an audit trail.
-- A client-application token scope ('client') lets other systems create employees and requests.
ALTER TABLE api_token DROP CONSTRAINT api_token_scope_check;
ALTER TABLE api_token ADD CONSTRAINT api_token_scope_check CHECK (scope IN ('read', 'metrics', 'client', 'full'));

CREATE TABLE employee (
    id          bigserial PRIMARY KEY,
    employee_no text NOT NULL UNIQUE CHECK (employee_no ~ '^[A-Za-z0-9._-]{1,40}$'),
    name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    email       text NOT NULL UNIQUE CHECK (length(email) BETWEEN 3 AND 254),
    department  text NOT NULL DEFAULT '' CHECK (length(department) <= 80),
    title       text NOT NULL DEFAULT '' CHECK (length(title) <= 80),
    level       text NOT NULL DEFAULT '' CHECK (length(level) <= 40),
    country     text CHECK (country IS NULL OR country ~ '^[A-Z]{2}$'),
    manager_id  bigint REFERENCES employee(id) ON DELETE SET NULL,
    hire_date   date,
    status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX employee_manager_idx ON employee (manager_id);

CREATE TABLE request (
    id               bigserial PRIMARY KEY,
    employee_id      bigint NOT NULL REFERENCES employee(id),
    type             text NOT NULL CHECK (type ~ '^[a-z_]{1,40}$'),
    kind             text NOT NULL CHECK (kind IN ('leave', 'travel', 'expense', 'other')),
    title            text NOT NULL CHECK (length(title) BETWEEN 1 AND 160),
    description      text NOT NULL CHECK (length(description) BETWEEN 1 AND 4000),
    start_date       date,
    end_date         date,
    amount           numeric(12, 2) CHECK (amount IS NULL OR amount >= 0),
    currency         text CHECK (currency IS NULL OR currency ~ '^[A-Z]{3}$'),
    details          jsonb NOT NULL DEFAULT '{}',
    status           text NOT NULL DEFAULT 'submitted'
                     CHECK (status IN ('submitted', 'pending_review', 'needs_info', 'approved', 'rejected', 'withdrawn')),
    validation_state text NOT NULL DEFAULT 'pending'
                     CHECK (validation_state IN ('pending', 'running', 'done', 'failed', 'skipped')),
    validation_started_at timestamptz,
    idempotency_key  text CHECK (idempotency_key IS NULL OR length(idempotency_key) BETWEEN 1 AND 80),
    source           text NOT NULL DEFAULT 'api' CHECK (source IN ('api', 'ui', 'demo')),
    submitted_at     timestamptz NOT NULL DEFAULT now(),
    decided_at       timestamptz,
    decided_by       text,
    decision_note    text,
    ai_at_decision   text,                 -- the AI recommendation the approver saw when deciding
    override         boolean NOT NULL DEFAULT false,  -- decision went against the AI recommendation
    updated_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
);
CREATE UNIQUE INDEX request_idem_idx ON request (employee_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX request_status_idx ON request (status, submitted_at);
CREATE INDEX request_employee_idx ON request (employee_id, start_date);
CREATE INDEX request_type_idx ON request (type);

-- Every validation attempt is kept, with a snapshot of the passages it saw, so the evidence for a
-- recommendation survives later edits or deletion of the policy documents.
CREATE TABLE validation (
    id             bigserial PRIMARY KEY,
    request_id     bigint NOT NULL REFERENCES request(id) ON DELETE CASCADE,
    created_at     timestamptz NOT NULL DEFAULT now(),
    state          text NOT NULL CHECK (state IN ('ok', 'failed', 'skipped')),
    recommendation text NOT NULL DEFAULT 'manual' CHECK (recommendation IN ('approve', 'reject', 'needs_info', 'manual')),
    compliance     text NOT NULL DEFAULT 'unclear' CHECK (compliance IN ('compliant', 'non_compliant', 'unclear')),
    summary        text NOT NULL DEFAULT '',
    reasons        jsonb NOT NULL DEFAULT '[]',
    citations      jsonb NOT NULL DEFAULT '[]',
    missing        jsonb NOT NULL DEFAULT '[]',
    warnings       jsonb NOT NULL DEFAULT '[]',
    checks         jsonb NOT NULL DEFAULT '[]',   -- deterministic checks computed by the system
    passages       jsonb NOT NULL DEFAULT '[]',   -- snapshot of the retrieved policy passages
    model          text,
    account        text,
    usage          jsonb,
    error          text,
    duration_ms    integer
);
CREATE INDEX validation_request_idx ON validation (request_id, id DESC);

CREATE TABLE request_event (
    id          bigserial PRIMARY KEY,
    request_id  bigint NOT NULL REFERENCES request(id) ON DELETE CASCADE,
    at          timestamptz NOT NULL DEFAULT now(),
    actor       text NOT NULL,
    action      text NOT NULL,
    note        text,
    data        jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX request_event_idx ON request_event (request_id, id);
