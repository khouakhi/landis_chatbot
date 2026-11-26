-- LandIS assistant: per-record link and title for structured database records.
-- source_url is the record's page in the LandIS Soils Guide. source_title names the record.
-- Both are NULL for chunks that come from documents. Safe to re-run.

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS source_url TEXT;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS source_title TEXT;
