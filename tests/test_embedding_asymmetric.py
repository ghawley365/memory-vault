"""
Asymmetric embedding support + re-embedding backfill.

Model-agnostic: the conftest sets task prefixes and a max_seq_length cap
via environment, so these tests exercise the configuration machinery with
whatever model is configured (the default all-MiniLM-L6-v2 in CI).
Prefixes are applied at encode time only — stored content never includes
them. reembed_missing() backfills chunks whose embedding is NULL (as
after an embedding-model migration); the vector search arm skips NULL
embeddings so search keeps working mid-backfill via the full-text arm.
"""

from __future__ import annotations

import json
import uuid

import pytest

from memory_vault.config import settings


def test_prefixes_and_seq_cap_come_from_config():
    assert settings.embedding_query_prefix == "search_query: "
    assert settings.embedding_document_prefix == "search_document: "
    assert settings.embedding_max_seq_length == 128  # set by conftest


def test_model_honors_seq_cap():
    from memory_vault.services.embedding import _get_model

    assert _get_model().max_seq_length == settings.embedding_max_seq_length


def test_query_and_document_embeddings_differ():
    from memory_vault.services.embedding import embed

    text = "PostgreSQL vector indexing with HNSW"
    q = embed(text, kind="query")
    d = embed(text, kind="document")

    assert len(q) == settings.embedding_dimensions
    assert len(d) == settings.embedding_dimensions
    assert q != d  # prefixes make the task-specific vectors distinct


def test_embed_batch_matches_single_embed():
    from memory_vault.services.embedding import embed, embed_batch

    text = "reciprocal rank fusion merges ranked lists"
    single = embed(text, kind="document")
    batched = embed_batch([text], kind="document")[0]

    assert len(batched) == settings.embedding_dimensions
    assert all(abs(a - b) < 1e-5 for a, b in zip(single, batched, strict=True))


@pytest.mark.asyncio
async def test_reembed_backfills_null_embeddings():
    from memory_vault.models.db import execute_query, fetch_one
    from memory_vault.services.reembed import reembed_missing

    chunk_id = str(uuid.uuid4())
    await execute_query(
        """INSERT INTO chunks (id, space_id, chunk_index, content, embedding, metadata)
           VALUES (%s, 1, 0, %s, NULL, %s::jsonb)""",
        (chunk_id, "A chunk awaiting re-embedding after a model migration.", json.dumps({})),
    )

    updated = await reembed_missing(batch_size=8)
    assert updated >= 1

    row = await fetch_one("SELECT embedding FROM chunks WHERE id = %s", (chunk_id,))
    assert row["embedding"] is not None


@pytest.mark.asyncio
async def test_search_survives_null_embedding_chunks():
    """During a backfill, chunks with NULL embeddings must not break search."""
    from memory_vault.mcp import server as mcp_server
    from memory_vault.models.db import execute_query

    await execute_query(
        """INSERT INTO chunks (id, space_id, chunk_index, content, embedding, metadata)
           VALUES (%s, 1, 0, %s, NULL, %s::jsonb)""",
        (str(uuid.uuid4()), "Null-embedding chunk about zebras.", json.dumps({})),
    )
    stored = json.loads(await mcp_server.remember(text="A properly embedded fact about zebras."))
    assert stored["stored"] is True

    res = json.loads(await mcp_server.recall(query="tell me about zebras"))
    assert res["status"] == "ok"
    contents = [r["content"] for r in res["results"]]
    assert any("properly embedded" in c for c in contents)
