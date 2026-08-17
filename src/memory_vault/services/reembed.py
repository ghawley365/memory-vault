"""
Re-embedding backfill — populate chunk embeddings with the current model.

Used after an embedding-model/dimension migration (which NULLs stored
vectors) or to refresh embeddings after changing EMBEDDING_MODEL.
Processes in batches, committing per batch, so an interrupted run
resumes where it left off. Inference runs off the event loop.
"""

from __future__ import annotations

import asyncio
import logging

from memory_vault.config import settings
from memory_vault.models.db import execute_query, fetch_all
from memory_vault.services.embedding import embed_batch

logger = logging.getLogger(__name__)


async def reembed_missing(
    space_id: int | None = None,
    batch_size: int | None = None,
    all_chunks: bool = False,
) -> int:
    """
    Embed chunks whose embedding is NULL (or every chunk with all_chunks=True).

    Returns the number of chunks updated.
    """
    bs = batch_size or settings.embedding_batch_size
    where = "TRUE" if all_chunks else "embedding IS NULL"
    if space_id is not None:
        where += " AND space_id = %s"
        params: tuple = (space_id,)
    else:
        params = ()

    total_updated = 0
    while True:
        # nosec B608 — `where` is composed from closed literal templates above.
        rows = await fetch_all(
            f"""SELECT id, content FROM chunks
                WHERE {where}
                ORDER BY created_at
                LIMIT {int(bs)}
                OFFSET {total_updated if all_chunks else 0}""",  # nosec B608
            params,
        )
        if not rows:
            break

        vectors = await asyncio.to_thread(embed_batch, [r["content"] for r in rows], bs, "document")
        for row, vec in zip(rows, vectors, strict=True):
            await execute_query(
                "UPDATE chunks SET embedding = %s::vector, updated_at = now() WHERE id = %s",
                (str(vec), row["id"]),
            )
        total_updated += len(rows)
        logger.info("Re-embedded %d chunks (total %d)", len(rows), total_updated)

        if all_chunks and len(rows) < bs:
            break

    return total_updated
