"""
Consolidation — near-duplicate detection and supersession-based dedup.

find_duplicate_pairs() uses per-chunk nearest-neighbor search (HNSW) to
find pairs above a cosine-similarity threshold within the same space.
consolidate() is a dry-run by default; with apply=True the OLDER chunk of
each pair is marked superseded by the newer one (history preserved).
"""

from __future__ import annotations

import json

import pytest

from src.mcp import server as mcp_server

TEXT_V1 = "The staging database password rotates every 30 days via the ops cronjob."
TEXT_V2 = "The staging database password rotates every 30 days via the ops cron job."
TEXT_OTHER = "Zebras have distinctive black and white striped coats for camouflage."


async def _remember(text: str, **kw) -> dict:
    return json.loads(await mcp_server.remember(text=text, **kw))


@pytest.mark.asyncio
async def test_find_duplicate_pairs_detects_near_identical():
    from src.services.consolidation import find_duplicate_pairs

    a = await _remember(TEXT_V1)
    b = await _remember(TEXT_V2)
    c = await _remember(TEXT_OTHER)

    pairs = await find_duplicate_pairs(threshold=0.9)

    pair_ids = {(p["older_id"], p["newer_id"]) for p in pairs}
    assert (a["chunk_id"], b["chunk_id"]) in pair_ids

    flat = {i for pair in pair_ids for i in pair}
    assert c["chunk_id"] not in flat


@pytest.mark.asyncio
async def test_consolidate_dry_run_does_not_modify():
    from src.services.consolidation import consolidate

    a = await _remember(TEXT_V1)
    await _remember(TEXT_V2)

    report = await consolidate(threshold=0.9, apply=False)
    assert report["pairs_found"] >= 1
    assert report["applied"] == 0

    from src.models.db import fetch_one

    row = await fetch_one(
        "SELECT superseded_by FROM chunks WHERE id = %s", (a["chunk_id"],)
    )
    assert row["superseded_by"] is None


@pytest.mark.asyncio
async def test_consolidate_apply_marks_older_superseded():
    from src.services.consolidation import consolidate

    a = await _remember(TEXT_V1)
    b = await _remember(TEXT_V2)

    report = await consolidate(threshold=0.9, apply=True)
    assert report["applied"] >= 1

    from src.models.db import fetch_one

    row = await fetch_one(
        "SELECT superseded_by FROM chunks WHERE id = %s", (a["chunk_id"],)
    )
    assert str(row["superseded_by"]) == b["chunk_id"]

    res = json.loads(await mcp_server.recall(query="staging database password rotation"))
    ids = [r["chunk_id"] for r in res["results"]]
    assert a["chunk_id"] not in ids
    assert b["chunk_id"] in ids


@pytest.mark.asyncio
async def test_no_pairs_across_spaces():
    from src.models.db import execute_query
    from src.services.consolidation import find_duplicate_pairs

    await execute_query(
        "INSERT INTO memory_spaces (name) VALUES ('otherspace') ON CONFLICT DO NOTHING"
    )

    a = await _remember(TEXT_V1)
    b = await _remember(TEXT_V2, space="otherspace")

    pairs = await find_duplicate_pairs(threshold=0.9)
    flat = {i for p in pairs for i in (p["older_id"], p["newer_id"])}
    assert not ({a["chunk_id"], b["chunk_id"]} <= flat)
