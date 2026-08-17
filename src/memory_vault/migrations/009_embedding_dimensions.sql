-- Bring chunks.embedding to the configured EMBEDDING_DIMENSIONS.
--
-- The initial schema (001) hard-codes vector(384) for the default model
-- (all-MiniLM-L6-v2). A deployment that pins a different model — e.g.
-- nomic-embed-text-v1.5 at 768 — needs the column widened, and the runner
-- refuses to start when the schema and EMBEDDING_DIMENSIONS disagree.
--
-- The runner substitutes {{EMBEDDING_DIMENSIONS}} from settings before
-- executing, and the DO block makes this a no-op when the column already
-- has that width. So: fresh installs at the default stay 384; a 768-d
-- deployment gets converted once; re-running is harmless.
--
-- Vectors from a different model are meaningless in the new space, so a
-- real conversion NULLs them. Search degrades gracefully (the vector arm
-- skips NULL embeddings; the keyword arm keeps working) until
--
--     memory-vault reembed
--
-- repopulates them. Run it immediately after migrating.

-- Dockerized Postgres defaults to a 64MB /dev/shm; a parallel HNSW build
-- requests a maintenance_work_mem-sized shared segment and fails there. The
-- column is all-NULL after a conversion, so a serial small-memory build is
-- instant and the backfill grows the index incrementally. Both are
-- transaction-local.
SET LOCAL maintenance_work_mem = '48MB';
SET LOCAL max_parallel_maintenance_workers = 0;

DO $$
DECLARE
    current_dims integer;
BEGIN
    SELECT atttypmod INTO current_dims
    FROM pg_attribute
    WHERE attrelid = 'public.chunks'::regclass AND attname = 'embedding';

    IF current_dims IS DISTINCT FROM {{EMBEDDING_DIMENSIONS}} THEN
        RAISE NOTICE 'chunks.embedding: vector(%) -> vector({{EMBEDDING_DIMENSIONS}}); '
                     'stored vectors NULLed — run `memory-vault reembed`', current_dims;
        DROP INDEX IF EXISTS chunks_embedding_idx;
        ALTER TABLE chunks
            ALTER COLUMN embedding TYPE vector({{EMBEDDING_DIMENSIONS}})
            USING NULL::vector({{EMBEDDING_DIMENSIONS}});
        CREATE INDEX chunks_embedding_idx ON chunks
            USING hnsw (embedding vector_cosine_ops);
    END IF;
END $$;
