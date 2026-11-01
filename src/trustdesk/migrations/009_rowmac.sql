-- Row MACs: an HMAC under a subkey of the vault data key over the security-relevant columns of a row (see rowmac.py).
-- Null on rows written before this migration, or while the vault was locked: they are ignored once the vault is open until
-- `trustdesk adopt-rowmacs` signs them. web_session and api_token are signed by the kit (LocalAuth).
ALTER TABLE llm_account ADD COLUMN IF NOT EXISTS mac text;
ALTER TABLE app_setting ADD COLUMN IF NOT EXISTS mac text;
ALTER TABLE web_session ADD COLUMN IF NOT EXISTS mac text;
ALTER TABLE api_token ADD COLUMN IF NOT EXISTS mac text;
