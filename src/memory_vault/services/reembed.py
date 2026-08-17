"""
Re-embedding backfill — populate chunk embeddings with the current model.

Used after an embedding-model/dimension migration (which NULLs stored
vectors) or to refresh embeddings after changing EMBEDDING_MODEL.

Pages by keyset on the primary key (`id > last_id ORDER BY id`) — an
UPDATE that touches the indexed embedding column can never be HOT, so
OFFSET paging over a non-unique sort key re-ordered the scan underneath
itself and skipped rows. Each window is fetched wide and handed to
embed_batch in one call so sentence-transformers can length-sort across
the whole window (fetching exactly one encode batch at a time defeated
that and ran ~5x slower). Commits per window, so an interrupted run
resumes where it left off. Inference runs off the event loop.
"""

from __future__ import annotations

import asyncio
import logging

from memory_vault.config import settings
from memory_vault.models.db import execute_query, fetch_all
from memory_vault.services.embedding import embed_batch

logger = logging.getLogger(__name__)

# Rows fetched per DB round-trip; embed_batch splits this into encode batches
# of settings.embedding_batch_size internally, length-sorted across the window.
_WINDOW = 512


async def reembed_missing(
    space_id: int | None = None,
    batch_size: int | None = None,
    all_chunks: bool = False,
) -> int:
    """
    Embed chunks whose embedding is NULL (or every chunk with all_chunks=True).

    `batch_size` is the encode batch (default settings.embedding_batch_size);
    the DB window is fixed at _WINDOW rows. Returns the number of chunks updated.
    """
    bs = batch_size or settings.embedding_batch_size
    window = max(_WINDOW, bs)
    where = "TRUE" if all_chunks else "embedding IS NULL"
    params: list = []
    if space_id is not None:
        where += " AND space_id = %s"
        params.append(space_id)

    total_updated = 0
    last_id: str | None = None
    while True:
        cursor_sql = " AND id > %s" if last_id is not None else ""
        cursor_params = [*params, last_id] if last_id is not None else params
        # nosec B608 — `where`/`cursor_sql` are composed from closed literal templates above.
        rows = await fetch_all(
            f"""SELECT id, content FROM chunks
                WHERE {where}{cursor_sql}
                ORDER BY id
                LIMIT {int(window)}""",  # nosec B608
            tuple(cursor_params),
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
        last_id = str(rows[-1]["id"])
        logger.info("Re-embedded %d chunks (total %d)", len(rows), total_updated)

        if len(rows) < window:
            break

    return total_updated
