-- Embedding upgrade: all-MiniLM-L6-v2 (384-d) → nomic-embed-text-v1.5 (768-d).
--
-- Old 384-d vectors are meaningless in the new space, so this migration
-- NULLs every embedding. Search degrades gracefully (full-text arm still
-- works; the vector arm skips NULL embeddings) until the backfill runs:
--
--     memory-vault reembed
--
-- run it immediately after migrating.

-- Dockerized Postgres defaults to a 64MB /dev/shm; a parallel HNSW build
-- requests a maintenance_work_mem-sized shared segment and fails. The
-- column is all-NULL at this point (nothing to index), so a serial,
-- small-memory build is instant. Backfill inserts populate the index
-- incrementally. Both SETs are transaction-local.
SET LOCAL maintenance_work_mem = '48MB';
SET LOCAL max_parallel_maintenance_workers = 0;

DROP INDEX IF EXISTS chunks_embedding_idx;

ALTER TABLE chunks
    ALTER COLUMN embedding TYPE vector(768) USING NULL::vector(768);

CREATE INDEX chunks_embedding_idx ON chunks
    USING hnsw (embedding vector_cosine_ops);
