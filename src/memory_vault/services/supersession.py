"""
Supersession service — mark one chunk as replaced by another.

A superseded chunk stays in the database (history) but is excluded from
search results and active counts. One contradiction mechanism: newer
facts supersede older ones explicitly, rather than deleting them.
"""

from __future__ import annotations

import logging
import uuid

from memory_vault.models.db import execute_query, fetch_one

logger = logging.getLogger(__name__)


async def supersede(old_chunk_id: str, new_chunk_id: str) -> None:
    """
    Mark `old_chunk_id` as superseded by `new_chunk_id`.

    Raises ValueError when the operation is invalid:
      - either id is not a UUID
      - a chunk superseding itself
      - either chunk missing
      - the chunks live in different spaces
      - the old chunk already superseded
    """
    for label, value in (("supersedes", old_chunk_id), ("new chunk", new_chunk_id)):
        try:
            uuid.UUID(str(value))
        except (ValueError, AttributeError, TypeError):
            raise ValueError(f"{label} id is not a valid UUID: {value!r}") from None

    if old_chunk_id == new_chunk_id:
        raise ValueError("A chunk cannot supersede itself.")

    old = await fetch_one(
        "SELECT id, space_id, superseded_by FROM chunks WHERE id = %s", (old_chunk_id,)
    )
    if not old:
        raise ValueError(f"Chunk to supersede not found: {old_chunk_id}")
    if old["superseded_by"] is not None:
        raise ValueError(f"Chunk {old_chunk_id} is already superseded by {old['superseded_by']}.")

    new = await fetch_one("SELECT id, space_id FROM chunks WHERE id = %s", (new_chunk_id,))
    if not new:
        raise ValueError(f"Superseding chunk not found: {new_chunk_id}")
    if new["space_id"] != old["space_id"]:
        raise ValueError(
            "Cannot supersede across spaces: the replacement must be stored in the "
            "same space as the chunk it replaces."
        )

    # The WHERE re-checks superseded_by so two concurrent supersessions of the
    # same chunk cannot both win — the second finds no row to update.
    await execute_query(
        """UPDATE chunks
           SET superseded_by = %s,
               superseded_at = now(),
               updated_at = now()
           WHERE id = %s
             AND superseded_by IS NULL""",
        (new_chunk_id, old_chunk_id),
    )
    logger.info("Chunk %s superseded by %s", old_chunk_id, new_chunk_id)
