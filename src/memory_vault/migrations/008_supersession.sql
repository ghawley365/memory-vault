-- Supersession — bi-temporal fact replacement.
--
-- When a new chunk replaces an earlier one (a decision changed, a fact went
-- stale), the old chunk is marked with superseded_by (the replacing chunk)
-- and superseded_at. Superseded chunks are excluded from search, active
-- counts, and the live graph views, but stay in the database as history.
--
-- Written idempotently (IF NOT EXISTS) so it also applies cleanly to
-- databases that received these columns from a pre-port migration.

ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS superseded_by UUID REFERENCES chunks(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ;

-- Partial index: finding what superseded a chunk, without taxing the
-- common case (superseded_by IS NULL) that search filters on.
CREATE INDEX IF NOT EXISTS chunks_superseded_by_idx ON chunks (superseded_by)
    WHERE superseded_by IS NOT NULL;

-- The live graph views (007) state the "not forgotten" rule once for every
-- graph surface; superseded chunks leave the graph the same way. The newer
-- chunk of a superseded pair re-extracts its own mentions, so dropping the
-- old chunk's rows does not lose live knowledge.

CREATE OR REPLACE VIEW live_entity_mentions AS
    SELECT em.*
    FROM entity_mentions em
    JOIN chunks c ON c.id = em.chunk_id
    WHERE (c.metadata->>'forgotten')::boolean IS NOT TRUE
      AND c.superseded_by IS NULL;

CREATE OR REPLACE VIEW live_relationships AS
    SELECT r.*
    FROM relationships r
    WHERE NOT EXISTS (
        SELECT 1
        FROM chunks c
        WHERE c.id = r.chunk_id
          AND ((c.metadata->>'forgotten')::boolean IS TRUE
               OR c.superseded_by IS NOT NULL)
    );
