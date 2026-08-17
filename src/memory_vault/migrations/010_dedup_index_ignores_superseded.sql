-- The remember() dedup index (006) must only see LIVE rows.
--
-- Supersession (008) keeps a replaced chunk in the table as history. Under
-- the 006 index that history row kept owning its (space, content_hash) slot,
-- so deliberately re-storing that content answered "duplicate" forever and
-- pointed at a chunk recall no longer returns. Narrowing the predicate frees
-- the slot: re-storing superseded content creates a new, live chunk.
--
-- remember()'s ON CONFLICT clause names the same predicate so Postgres can
-- infer this index as the arbiter (mcp/server.py).

DROP INDEX IF EXISTS chunks_space_content_hash_idx;
CREATE UNIQUE INDEX chunks_space_content_hash_idx
    ON chunks (space_id, (metadata->>'content_hash'))
    WHERE metadata->>'content_hash' IS NOT NULL
      AND metadata->>'source_file' IS NULL
      AND superseded_by IS NULL;
