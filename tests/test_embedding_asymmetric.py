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


@pytest.mark.asyncio
async def test_reembed_all_touches_every_chunk_exactly_once_across_batches(monkeypatch):
    """`--all` must re-embed every chunk once even when the batch size is
    smaller than the row count and created_at ties exist. (OFFSET pagination
    on a non-unique sort key skipped rows once updates re-ordered the scan.)"""
    from memory_vault.models.db import execute_query, fetch_all
    from memory_vault.services import reembed as reembed_mod
    from memory_vault.services.reembed import reembed_missing

    monkeypatch.setattr(reembed_mod, "_WINDOW", 3)  # force several DB windows
    ids = [str(uuid.uuid4()) for _ in range(7)]
    for i, cid in enumerate(ids):
        # identical created_at → a tie group larger than the batch size
        await execute_query(
            """INSERT INTO chunks (id, space_id, chunk_index, content, embedding, metadata, created_at)
               VALUES (%s, 1, 0, %s, NULL, %s::jsonb, '2020-01-01T00:00:00Z')""",
            (cid, f"Tie-group chunk number {i} for the reembed-all test.", json.dumps({})),
        )
    # backfill first so every row has an embedding, then --all with a tiny batch
    await reembed_missing(batch_size=8)
    before = {
        str(r["id"]): r["updated_at"]
        for r in await fetch_all("SELECT id, updated_at FROM chunks WHERE id = ANY(%s)", (ids,))
    }

    updated = await reembed_missing(batch_size=3, all_chunks=True)
    assert updated >= len(ids)

    after = {
        str(r["id"]): r["updated_at"]
        for r in await fetch_all("SELECT id, updated_at FROM chunks WHERE id = ANY(%s)", (ids,))
    }
    assert all(after[i] > before[i] for i in ids), "some tie-group rows were skipped"


@pytest.mark.asyncio
async def test_reembed_space_scope_leaves_other_spaces_untouched():
    from memory_vault.models.db import execute_query, fetch_one
    from memory_vault.services.reembed import reembed_missing

    await execute_query(
        "INSERT INTO memory_spaces (name, description) VALUES ('reembed-scope', 'x') ON CONFLICT DO NOTHING"
    )
    other_space = (await fetch_one("SELECT id FROM memory_spaces WHERE name='reembed-scope'"))["id"]
    in_scope, out_scope = str(uuid.uuid4()), str(uuid.uuid4())
    for cid, sid in ((in_scope, other_space), (out_scope, 1)):
        await execute_query(
            """INSERT INTO chunks (id, space_id, chunk_index, content, embedding, metadata)
               VALUES (%s, %s, 0, %s, NULL, %s::jsonb)""",
            (cid, sid, "scope probe chunk", json.dumps({})),
        )
    await reembed_missing(space_id=other_space, batch_size=8)
    assert (await fetch_one("SELECT embedding FROM chunks WHERE id=%s", (in_scope,)))[
        "embedding"
    ] is not None
    assert (await fetch_one("SELECT embedding FROM chunks WHERE id=%s", (out_scope,)))[
        "embedding"
    ] is None


def test_model_revision_setting_defaults_unpinned_and_is_passed_through(monkeypatch):
    """EMBEDDING_MODEL_REVISION pins the Hub revision loaded under
    trust_remote_code (unpinned 'main' would run whatever code lands upstream).
    Default is None (upstream-safe); when set it must reach SentenceTransformer."""
    from memory_vault.services import embedding as emb

    assert settings.embedding_model_revision is None

    seen: dict = {}

    class FakeST:
        max_seq_length = 512

        def __init__(self, name, **kw):
            seen["name"] = name
            seen.update(kw)

    # Settings is a frozen dataclass — swap the whole object for the probe.
    import dataclasses

    monkeypatch.setattr(emb, "_model", None)
    monkeypatch.setattr(
        emb, "settings", dataclasses.replace(settings, embedding_model_revision="abc123")
    )
    monkeypatch.setattr(emb, "_load_sentence_transformer", lambda: FakeST)
    try:
        emb._get_model()
    finally:
        monkeypatch.setattr(emb, "_model", None)  # never leave the fake installed
    assert seen["name"] == settings.embedding_model
    assert seen["revision"] == "abc123"


def test_sentence_transformers_is_not_imported_at_module_import():
    """The MCP server is one process per Claude session; importing torch and
    sentence-transformers eagerly costs ~500 MB per process before any tool
    call. The import must be deferred to first model load."""
    import subprocess
    import sys

    code = (
        "import sys; import memory_vault.services.embedding; "
        "print('sentence_transformers' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
