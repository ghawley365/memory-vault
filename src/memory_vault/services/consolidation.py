"""
Consolidation — near-duplicate detection and supersession-based dedup.

For every live chunk, find its nearest neighbor in the same space (HNSW
KNN). Pairs above the cosine-similarity threshold are near-duplicates:
the OLDER chunk is marked superseded by the newer one, keeping history
while removing the duplicate from recall.

Dry-run by default — apply=True performs the supersessions.
"""

from __future__ import annotations

import logging
from typing import Any

from memory_vault.models.db import fetch_all
from memory_vault.services.supersession import supersede

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.95

_LIVE = (
    "{alias}.embedding IS NOT NULL "
    "AND {alias}.superseded_by IS NULL "
    "AND ({alias}.metadata->>'forgotten')::boolean IS NOT TRUE"
)


async def find_duplicate_pairs(
    space_id: int | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """
    Find near-duplicate chunk pairs (cosine similarity >= threshold).

    Each pair is reported once, oriented older -> newer. Only live chunks
    (embedded, not forgotten, not already superseded) are considered, and
    pairs never cross spaces.
    """
    space_filter = "AND a.space_id = %(space_id)s" if space_id is not None else ""
    a_live = _LIVE.format(alias="a")
    b_live = _LIVE.format(alias="b")

    # nosec B608 — SQL is composed from closed literal templates above;
    # user values are bound via named parameters.
    sql = f"""
        SELECT a.id            AS older_id,
               a.content       AS older_content,
               a.created_at    AS older_created_at,
               nn.id           AS newer_id,
               nn.content      AS newer_content,
               nn.created_at   AS newer_created_at,
               1 - nn.dist     AS similarity
        FROM chunks a
        CROSS JOIN LATERAL (
            SELECT b.id, b.content, b.created_at,
                   a.embedding <=> b.embedding AS dist
            FROM chunks b
            WHERE b.space_id = a.space_id
              AND b.id <> a.id
              AND {b_live}
            ORDER BY a.embedding <=> b.embedding
            LIMIT 1
        ) nn
        WHERE {a_live}
          {space_filter}
          AND nn.dist <= %(max_dist)s
          AND (a.created_at < nn.created_at
               OR (a.created_at = nn.created_at AND a.id < nn.id))
        ORDER BY nn.dist
        LIMIT %(limit)s
    """  # nosec B608

    params: dict[str, Any] = {"max_dist": 1.0 - threshold, "limit": limit}
    if space_id is not None:
        params["space_id"] = space_id

    rows = await fetch_all(sql, params)
    return [
        {
            "older_id": str(r["older_id"]),
            "older_content": r["older_content"],
            "older_created_at": r["older_created_at"],
            "newer_id": str(r["newer_id"]),
            "newer_content": r["newer_content"],
            "newer_created_at": r["newer_created_at"],
            "similarity": float(r["similarity"]),
        }
        for r in rows
    ]


async def consolidate(
    space_id: int | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    apply: bool = False,
    limit: int = 500,
) -> dict[str, Any]:
    """
    Detect near-duplicate pairs; with apply=True supersede the older of
    each pair. Returns a report of what was found and (optionally) done.
    """
    pairs = await find_duplicate_pairs(space_id=space_id, threshold=threshold, limit=limit)

    applied = 0
    errors: list[str] = []
    if apply:
        superseded_ids: set[str] = set()
        for pair in pairs:
            old_id = pair["older_id"]
            if old_id in superseded_ids:
                continue  # already handled in this run
            try:
                await supersede(old_id, pair["newer_id"])
                superseded_ids.add(old_id)
                applied += 1
            except ValueError as e:
                errors.append(str(e))

    return {
        "pairs_found": len(pairs),
        "applied": applied,
        "errors": errors,
        "pairs": pairs,
    }
