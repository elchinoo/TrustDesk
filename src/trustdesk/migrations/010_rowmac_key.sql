-- The llm_account MAC now also covers api_key_enc (see rowmac.py). MACs written before this change used a shorter column list, so
-- clear them: `trustdesk adopt-rowmacs` signs the rows again (it trusts the database as it is at that moment).
UPDATE llm_account SET mac = NULL WHERE mac IS NOT NULL;
