-- Financial account to "90" statistical account map, per Accounting's requirement.
-- The JE files carry financial internal IDs. Stat number = financial number + 900000.
-- stat_internal_id stays NULL until the stat account exists in NetSuite.

CREATE TABLE account_map (
  financial_internal_id TEXT PRIMARY KEY,
  financial_number      TEXT NOT NULL,
  financial_name        TEXT NOT NULL,
  financial_type        TEXT,
  stat_number           TEXT NOT NULL UNIQUE,
  stat_internal_id      TEXT UNIQUE,
  stat_name             TEXT,
  source                TEXT NOT NULL,           -- file the row was imported from
  updated_at            TEXT NOT NULL
);
