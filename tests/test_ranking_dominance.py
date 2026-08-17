"""
Ranking: retrieval rank must dominate importance/recency boosts.

The importance (max +0.15) and recency (max +0.05) boosts were additive
on top of RRF scores whose rank-1 contribution is only ~0.016, so any
recent high-importance chunk outscored an exact match with default
importance — burying it beyond the top results. Boosts must scale the
RRF score, not swamp it: a chunk that is rank 1 in BOTH arms cannot be
pushed out of the top results by boosts alone.
"""

from __future__ import annotations

import json

import pytest

from memory_vault.mcp import server as mcp_server


@pytest.mark.asyncio
async def test_dual_rank1_match_beats_recent_high_importance_chunks():
    # A field of fresh, high-importance chunks (classified as decisions),
    # semantically unrelated to the probe query.
    for i in range(12):
        await mcp_server.remember(
            text=f"We decided to adopt build pipeline number {i} for the web deployment."
        )

    # The probe: default importance ("fact"), exact match for the query.
    probe = json.loads(
        await mcp_server.remember(text="The vault's mascot is a stuffed capybara named Ada.")
    )

    res = json.loads(await mcp_server.recall(query="vault mascot capybara", limit=3))
    top_ids = [r["chunk_id"] for r in res["results"]]
    assert probe["chunk_id"] in top_ids, (
        "exact match (rank 1 in both arms) must not be buried by importance/recency boosts"
    )


def test_fts_only_rank1_lands_inside_default_window():
    """Scoring invariant (no model involved): a chunk found ONLY by the keyword
    arm at rank 1 must place inside the default result window against a full
    field of vector-only candidates, even when every vector candidate carries
    the maximum boost and the keyword hit the minimum. With _FTS_WEIGHT=0.5 the
    FTS-only ceiling 0.5/(k+1) sat below vector rank ~62, so an exact identifier
    match could never surface at any allowed limit."""
    from memory_vault.config import settings
    from memory_vault.services import search as s

    k = s._RRF_K
    max_boost = 1.0 + s._IMPORTANCE_WEIGHT * 1.0 + s._RECENCY_MAX_BOOST  # fresh, importance 1
    min_boost = 1.0 + s._IMPORTANCE_WEIGHT * 0.5  # default importance, very old
    fts_only_score = (s._FTS_WEIGHT / (k + 1)) * min_boost

    beaten_by = sum(
        1
        for r in range(1, 50 * 3 + 1)  # max limit is 50; vec_limit = limit * 3
        if (1.0 / (k + r)) * max_boost > fts_only_score
    )
    position = beaten_by + 1
    assert position <= settings.search_default_limit, (
        f"FTS-only rank-1 candidate lands at position {position}, outside the "
        f"default window of {settings.search_default_limit}"
    )
