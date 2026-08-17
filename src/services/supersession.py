"""
Supersession service — mark one chunk as replaced by another.

A superseded chunk stays in the database (history) but is excluded from
search results and active counts. One contradiction mechanism: newer
facts supersede older ones explicitly, rather than deleting them.
"""

from __future__ import annotations

import logging

from src.models.db import execute_query, fetch_one

logger = logging.getLogger(__name__)


async def supersede(old_chunk_id: str, new_chunk_id: str) -> None:
    """
    Mark `old_chunk_id` as superseded by `new_chunk_id`.

    Raises ValueError when the operation is invalid:
      - a chunk superseding itself
      - either chunk missing
      - the old chunk already superseded
    """
    if old_chunk_id == new_chunk_id:
        raise ValueError("A chunk cannot supersede itself.")

    old = await fetch_one(
        "SELECT id, superseded_by FROM chunks WHERE id = %s", (old_chunk_id,)
    )
    if not old:
        raise ValueError(f"Chunk to supersede not found: {old_chunk_id}")
    if old["superseded_by"] is not None:
        raise ValueError(
            f"Chunk {old_chunk_id} is already superseded by {old['superseded_by']}."
        )

    new = await fetch_one("SELECT id FROM chunks WHERE id = %s", (new_chunk_id,))
    if not new:
        raise ValueError(f"Superseding chunk not found: {new_chunk_id}")

    await execute_query(
        """UPDATE chunks
           SET superseded_by = %s,
               superseded_at = now(),
               updated_at = now()
           WHERE id = %s""",
        (new_chunk_id, old_chunk_id),
    )
    logger.info("Chunk %s superseded by %s", old_chunk_id, new_chunk_id)
