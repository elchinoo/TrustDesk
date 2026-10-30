-- A client application sees and changes only what it created (unless it holds employees:read / employees:write).
-- Rows that exist before this migration have no owner: only people (requests:read) and unrestricted credentials see them.
ALTER TABLE employee ADD COLUMN IF NOT EXISTS created_by text;
ALTER TABLE request ADD COLUMN IF NOT EXISTS client_id text;
CREATE INDEX IF NOT EXISTS request_client_idx ON request (client_id);
CREATE INDEX IF NOT EXISTS employee_created_by_idx ON employee (created_by);
