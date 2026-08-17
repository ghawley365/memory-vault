"""
Embedding upgrade — long-context model with asymmetric query/document prefixes.

The default model is nomic-embed-text-v1.5 (768-d, 8192-token context),
replacing all-MiniLM-L6-v2 (384-d, 256-token truncation). Queries and
documents are embedded with different task prefixes; stored content never
includes the prefix. A reembed service backfills chunks whose embedding
is NULL (as after the dimension migration).
"""

from __future__ import annotations

import json
import uuid

import pytest

from src.config import settings


def test_default_model_is_long_context_768d():
    assert "nomic-embed-text-v1.5" in settings.embedding_model
    assert settings.embedding_dimensions == 768


def test_query_and_document_prefixes_configured():
    assert settings.embedding_query_prefix == "search_query: "
    assert settings.embedding_document_prefix == "search_document: "


def test_query_and_document_embeddings_differ():
    from src.services.embedding import embed

    text = "PostgreSQL vector indexing with HNSW"
    q = embed(text, kind="query")
    d = embed(text, kind="document")

    assert len(q) == settings.embedding_dimensions
    assert len(d) == settings.embedding_dimensions
    assert q != d  # prefixes make the task-specific vectors distinct


def test_embed_batch_matches_single_embed():
    from src.services.embedding import embed, embed_batch

    text = "reciprocal rank fusion merges ranked lists"
    single = embed(text, kind="document")
    batched = embed_batch([text], kind="document")[0]

    assert len(batched) == settings.embedding_dimensions
    # Same text, same kind → effectively identical vectors
    assert all(abs(a - b) < 1e-5 for a, b in zip(single, batched, strict=True))


@pytest.mark.asyncio
async def test_reembed_backfills_null_embeddings():
    from src.models.db import execute_query, fetch_one
    from src.services.reembed import reembed_missing

    chunk_id = str(uuid.uuid4())
    await execute_query(
        """INSERT INTO chunks (id, space_id, chunk_index, content, embedding, metadata)
           VALUES (%s, 1, 0, %s, NULL, %s::jsonb)""",
        (chunk_id, "A chunk awaiting re-embedding after the migration.", json.dumps({})),
    )

    updated = await reembed_missing(batch_size=8)
    assert updated >= 1

    row = await fetch_one("SELECT embedding FROM chunks WHERE id = %s", (chunk_id,))
    assert row["embedding"] is not None


@pytest.mark.asyncio
async def test_search_survives_null_embedding_chunks():
    """During backfill, chunks with NULL embeddings must not break search."""
    from src.mcp import server as mcp_server
    from src.models.db import execute_query

    await execute_query(
        """INSERT INTO chunks (id, space_id, chunk_index, content, embedding, metadata)
           VALUES (%s, 1, 0, %s, NULL, %s::jsonb)""",
        (str(uuid.uuid4()), "Null-embedding chunk about zebras.", json.dumps({})),
    )
    stored = json.loads(
        await mcp_server.remember(text="A properly embedded fact about zebras.")
    )
    assert stored["stored"] is True

    res = json.loads(await mcp_server.recall(query="tell me about zebras"))
    assert res["status"] == "ok"
    contents = [r["content"] for r in res["results"]]
    assert any("properly embedded" in c for c in contents)
