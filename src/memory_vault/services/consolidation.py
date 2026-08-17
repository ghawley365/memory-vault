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
import re
from typing import Any

from memory_vault.models.db import fetch_all, fetch_one
from memory_vault.services.supersession import supersede

logger = logging.getLogger(__name__)

# Cosine on document-prefixed retrieval embeddings sits in a high, narrow
# band (measured on a 26k-chunk vault under nomic-embed-text: p50 NN-cosine
# ~0.89, p90 ~0.92, 18% of chunks with a neighbour >= 0.95). 0.95 collapsed
# distinct sequential facts; >= 0.99 is where pairs are byte-level rewordings.
DEFAULT_THRESHOLD = 0.99
MIN_THRESHOLD = 0.8

# Tokens carrying a digit are the parts of a memory that name WHICH fact it is
# (task/step numbers, dates, hashes, versions, ids). Two chunks whose
# digit-bearing tokens differ are different facts however alike the prose.
_IDENT_TOKEN = re.compile(r"[a-z0-9][a-z0-9._:/-]*\d[a-z0-9._:/-]*|\d+", re.IGNORECASE)


def _identifier_tokens(text: str) -> frozenset[str]:
    return frozenset(t.lower().strip(".:/-") for t in _IDENT_TOKEN.findall(text))


def same_identifiers(a: str, b: str) -> bool:
    """True when both texts name the same digit-bearing identifiers."""
    return _identifier_tokens(a) == _identifier_tokens(b)


def _validate(threshold: float, limit: int) -> None:
    if not (MIN_THRESHOLD <= threshold <= 1.0):
        raise ValueError(
            f"threshold must be between {MIN_THRESHOLD} and 1.0 (got {threshold}); "
            "lower values pair merely related memories, and supersession hides them"
        )
    if limit < 1:
        raise ValueError(f"limit must be >= 1 (got {limit})")


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
    pairs whose digit-bearing identifier tokens differ are dropped (they are
    different facts, not duplicates — see `same_identifiers`), and
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
        ORDER BY nn.dist, a.id
        LIMIT %(page)s OFFSET %(offset)s
    """  # nosec B608

    _validate(threshold, limit)
    params: dict[str, Any] = {"max_dist": 1.0 - threshold}
    if space_id is not None:
        params["space_id"] = space_id

    # The identifier guard runs in Python, so page through the SQL candidates
    # until limit + 1 guard-passing pairs are in hand (or the candidates run
    # out). The extra pair lets consolidate() tell "limit pairs" from "at
    # least limit pairs" without a second query in the common case.
    want = limit + 1
    page = max(want, 200)
    rows: list[Any] = []
    offset = 0
    while len(rows) < want:
        batch = await fetch_all(sql, {**params, "page": page, "offset": offset})
        rows.extend(r for r in batch if same_identifiers(r["older_content"], r["newer_content"]))
        if len(batch) < page:
            break
        offset += page
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
    _validate(threshold, limit)
    fetched = await find_duplicate_pairs(space_id=space_id, threshold=threshold, limit=limit)
    truncated = len(fetched) > limit
    pairs = fetched[:limit]
    pairs_found = len(pairs)
    if truncated:
        pairs_found = await _count_pairs(space_id, threshold)

    applied = 0
    skipped_chained = 0
    errors: list[str] = []
    if apply:
        superseded_ids: set[str] = set()
        for pair in pairs:
            old_id, new_id = pair["older_id"], pair["newer_id"]
            if old_id in superseded_ids:
                continue
            if new_id in superseded_ids:
                # The keeper was itself superseded earlier in this run; pointing
                # at it would create a chain to a dead chunk. Leave the pair for
                # the next run, when the keeper's replacement is the neighbour.
                skipped_chained += 1
                continue
            try:
                await supersede(old_id, new_id)
                superseded_ids.add(old_id)
                applied += 1
            except ValueError as e:
                errors.append(str(e))

    return {
        "pairs_found": pairs_found,
        "pairs_returned": len(pairs),
        "truncated": truncated,
        "applied": applied,
        "skipped_chained": skipped_chained,
        "errors": errors,
        "pairs": pairs,
    }


async def _count_pairs(space_id: int | None, threshold: float) -> int:
    """Total near-duplicate pairs (cosine only — the identifier guard is applied
    in Python, so this is an upper bound). Run only when a report is truncated."""
    space_filter = "AND a.space_id = %(space_id)s" if space_id is not None else ""
    a_live, b_live = _LIVE.format(alias="a"), _LIVE.format(alias="b")
    sql = f"""
        SELECT count(*) AS n
        FROM chunks a
        CROSS JOIN LATERAL (
            SELECT b.id, b.created_at, a.embedding <=> b.embedding AS dist
            FROM chunks b
            WHERE b.space_id = a.space_id AND b.id <> a.id AND {b_live}
            ORDER BY a.embedding <=> b.embedding
            LIMIT 1
        ) nn
        WHERE {a_live} {space_filter}
          AND nn.dist <= %(max_dist)s
          AND (a.created_at < nn.created_at
               OR (a.created_at = nn.created_at AND a.id < nn.id))
    """  # nosec B608
    params: dict[str, Any] = {"max_dist": 1.0 - threshold}
    if space_id is not None:
        params["space_id"] = space_id
    row = await fetch_one(sql, params)
    return int(row["n"]) if row else 0
