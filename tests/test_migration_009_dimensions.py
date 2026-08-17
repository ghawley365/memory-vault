"""
Migration 009 converts chunks.embedding to the configured EMBEDDING_DIMENSIONS.

The migration is templated by the runner (`{{EMBEDDING_DIMENSIONS}}`) and is
a no-op when the column already has that width, so it is safe on every
database: fresh installs at the upstream default (384) stay 384; a deployment
that pins a 768-d model in .env gets the column widened (and its now-
meaningless vectors NULLed for `memory-vault reembed`).
"""

from __future__ import annotations

import pytest

from memory_vault.config import settings


@pytest.mark.asyncio
async def test_migration_009_leaves_column_at_configured_dimensions():
    from memory_vault.models.db import fetch_one

    row = await fetch_one(
        """SELECT atttypmod FROM pg_attribute
           WHERE attrelid = 'public.chunks'::regclass AND attname = 'embedding'"""
    )
    assert row["atttypmod"] == settings.embedding_dimensions


@pytest.mark.asyncio
async def test_migration_009_recorded_and_hnsw_index_present():
    from memory_vault.models.db import fetch_one

    m = await fetch_one(
        "SELECT 1 AS ok FROM _migrations WHERE filename = '009_embedding_dimensions.sql'"
    )
    assert m is not None
    idx = await fetch_one(
        """SELECT indexdef FROM pg_indexes
           WHERE tablename = 'chunks' AND indexname = 'chunks_embedding_idx'"""
    )
    assert idx is not None and "hnsw" in idx["indexdef"]


def test_runner_templates_dimensions():
    from memory_vault.models.db import _render_migration

    assert _render_migration("vector({{EMBEDDING_DIMENSIONS}})") == (
        f"vector({settings.embedding_dimensions})"
    )
