-- Bounded automatic retry of failed AI validations: how many times in a row it failed, when, and whether the failure
-- may be retried at all (a permanent client error of the request itself may not).
ALTER TABLE request ADD COLUMN validation_attempts integer NOT NULL DEFAULT 0 CHECK (validation_attempts >= 0);
ALTER TABLE request ADD COLUMN validation_failed_at timestamptz;
ALTER TABLE request ADD COLUMN validation_retryable boolean NOT NULL DEFAULT true;

-- Requests that already failed before this migration count as one failed attempt, so they are retried (bounded) as
-- soon as the cause is fixed instead of staying failed for good.
UPDATE request r SET validation_attempts = 1,
    validation_failed_at = COALESCE((SELECT max(v.created_at) FROM validation v WHERE v.request_id = r.id AND v.state = 'failed'),
                                    r.updated_at)
 WHERE r.validation_state = 'failed';

CREATE INDEX request_validation_retry_idx ON request (validation_failed_at) WHERE validation_state = 'failed';
