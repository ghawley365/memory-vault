-- Embedding upgrade: all-MiniLM-L6-v2 (384-d) → nomic-embed-text-v1.5 (768-d).
--
-- Old 384-d vectors are meaningless in the new space, so this migration
-- NULLs every embedding. Search degrades gracefully (full-text arm still
-- works; the vector arm skips NULL embeddings) until the backfill runs:
--
--     memory-vault reembed
--
-- run it immediately after migrating.

DROP INDEX IF EXISTS chunks_embedding_idx;

ALTER TABLE chunks
    ALTER COLUMN embedding TYPE vector(768) USING NULL::vector(768);

CREATE INDEX chunks_embedding_idx ON chunks
    USING hnsw (embedding vector_cosine_ops);
