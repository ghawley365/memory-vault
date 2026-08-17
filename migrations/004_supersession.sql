-- Supersession — bi-temporal fact replacement.
--
-- When a new chunk replaces an earlier one, the old chunk is marked with
-- superseded_by (the replacing chunk) and superseded_at. Superseded chunks
-- are excluded from search and from "active" counts, but stay in the
-- database as history.

ALTER TABLE chunks
    ADD COLUMN superseded_by UUID REFERENCES chunks(id) ON DELETE SET NULL,
    ADD COLUMN superseded_at TIMESTAMPTZ;

-- Partial index: finding what superseded a chunk, without taxing the
-- common case (superseded_by IS NULL) that search filters on.
CREATE INDEX chunks_superseded_by_idx ON chunks (superseded_by)
    WHERE superseded_by IS NOT NULL;
