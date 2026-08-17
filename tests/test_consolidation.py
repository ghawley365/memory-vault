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

from memory_vault.mcp import server as mcp_server

TEXT_V1 = "The staging database password rotates every 30 days via the ops cronjob."
TEXT_V2 = "The staging database password rotates every 30 days via the ops cron job."
TEXT_OTHER = "Zebras have distinctive black and white striped coats for camouflage."


async def _remember(text: str, **kw) -> dict:
    return json.loads(await mcp_server.remember(text=text, **kw))


@pytest.mark.asyncio
async def test_find_duplicate_pairs_detects_near_identical():
    from memory_vault.services.consolidation import find_duplicate_pairs

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
    from memory_vault.services.consolidation import consolidate

    a = await _remember(TEXT_V1)
    await _remember(TEXT_V2)

    report = await consolidate(threshold=0.9, apply=False)
    assert report["pairs_found"] >= 1
    assert report["applied"] == 0

    from memory_vault.models.db import fetch_one

    row = await fetch_one("SELECT superseded_by FROM chunks WHERE id = %s", (a["chunk_id"],))
    assert row["superseded_by"] is None


@pytest.mark.asyncio
async def test_consolidate_apply_marks_older_superseded():
    from memory_vault.services.consolidation import consolidate

    a = await _remember(TEXT_V1)
    b = await _remember(TEXT_V2)

    # near_duplicates=True: these two differ in wording, and merging text that
    # differs is opt-in (see test_apply_supersedes_only_identical_text_by_default).
    report = await consolidate(threshold=0.9, apply=True, near_duplicates=True)
    assert report["applied"] >= 1

    from memory_vault.models.db import fetch_one

    row = await fetch_one("SELECT superseded_by FROM chunks WHERE id = %s", (a["chunk_id"],))
    assert str(row["superseded_by"]) == b["chunk_id"]

    res = json.loads(await mcp_server.recall(query="staging database password rotation"))
    ids = [r["chunk_id"] for r in res["results"]]
    assert a["chunk_id"] not in ids
    assert b["chunk_id"] in ids


@pytest.mark.asyncio
async def test_no_pairs_across_spaces():
    from memory_vault.models.db import execute_query
    from memory_vault.services.consolidation import find_duplicate_pairs

    await execute_query(
        "INSERT INTO memory_spaces (name) VALUES ('otherspace') ON CONFLICT DO NOTHING"
    )

    a = await _remember(TEXT_V1)
    b = await _remember(TEXT_V2, space="otherspace")

    pairs = await find_duplicate_pairs(threshold=0.9)
    flat = {i for p in pairs for i in (p["older_id"], p["newer_id"])}
    assert not ({a["chunk_id"], b["chunk_id"]} <= flat)


# --- hardening surfaced by the 2026-08-17 audit ---------------------------------


def test_default_threshold_is_calibrated_high():
    """0.95 was a MiniLM-era number; document-prefixed nomic embeddings compress
    similarities upward (measured p90 NN-cosine ~0.92, 18% of chunks with an NN
    >= 0.95) and 0.95 collapsed distinct sequential milestones on the live vault."""
    from memory_vault.services.consolidation import DEFAULT_THRESHOLD

    assert DEFAULT_THRESHOLD >= 0.99


@pytest.mark.asyncio
async def test_threshold_out_of_range_is_rejected():
    from memory_vault.services.consolidation import consolidate

    with pytest.raises(ValueError, match="threshold"):
        await consolidate(threshold=0.5)
    with pytest.raises(ValueError, match="threshold"):
        await consolidate(threshold=1.5)
    with pytest.raises(ValueError, match="limit"):
        await consolidate(limit=0)


@pytest.mark.asyncio
async def test_report_signals_truncation():
    """When more pairs exist than --limit, the report must say so instead of
    presenting the truncated count as the total."""
    from memory_vault.mcp import server as mcp_server
    from memory_vault.services.consolidation import consolidate

    for i in range(3):
        await mcp_server.remember(text=f"Duplicate family {i}: the release train leaves at 09:00.")
        await mcp_server.remember(text=f"Duplicate family {i}: the release train leaves at 09:00!")

    report = await consolidate(threshold=0.9, limit=1)
    assert report["pairs_returned"] == 1
    assert report["truncated"] is True
    assert report["pairs_found"] > 1


@pytest.mark.asyncio
async def test_distinct_sequential_milestones_are_not_paired():
    """Near-identical wording that differs in the identifying token (Task 8 vs
    Task 9, STEP 2 vs STEP 3) is a different fact, not a duplicate. A content
    guard must keep such pairs out even when cosine clears the threshold."""
    from memory_vault.mcp import server as mcp_server
    from memory_vault.services.consolidation import find_duplicate_pairs

    a = json.loads(
        await mcp_server.remember(
            text="Sub-project B1 Task 8 SHIPPED 2026-07-07 on branch feature/wizard, commit 844b3a3b."
        )
    )
    b = json.loads(
        await mcp_server.remember(
            text="Sub-project B1 Task 9 SHIPPED 2026-07-07 on branch feature/wizard, commit 93b388b1."
        )
    )
    from memory_vault.services.consolidation import MIN_THRESHOLD, same_identifiers

    # The guard itself, independent of what cosine says:
    assert not same_identifiers(
        "Sub-project B1 Task 8 SHIPPED 2026-07-07, commit 844b3a3b.",
        "Sub-project B1 Task 9 SHIPPED 2026-07-07, commit 93b388b1.",
    )
    assert not same_identifiers(
        "OpenObserve cutover STEP 2 DONE", "OpenObserve cutover STEP 3 DONE"
    )
    assert same_identifiers("release train leaves at 09:00.", "the release train leaves at 09:00!")

    pairs = await find_duplicate_pairs(threshold=MIN_THRESHOLD)  # most permissive allowed
    paired = {(p["older_id"], p["newer_id"]) for p in pairs}
    assert (a["chunk_id"], b["chunk_id"]) not in paired
    assert (b["chunk_id"], a["chunk_id"]) not in paired


@pytest.mark.asyncio
async def test_apply_supersedes_only_identical_text_by_default():
    """Measured on a real 22.9k-chunk corpus: of the near-duplicate pairs that
    cleared BOTH cosine >= 0.99 and the identifier guard but were not
    byte-identical, 62% (41/66) turned out to carry different information when
    adjudicated. Cosine is therefore not sufficient evidence to merge
    non-identical text automatically — `apply` supersedes only byte-identical
    pairs unless the caller explicitly opts in."""
    from memory_vault.mcp import server as mcp_server
    from memory_vault.models.db import fetch_one
    from memory_vault.services.consolidation import consolidate

    a = json.loads(await mcp_server.remember(text="The nightly build runs at 02:00 UTC."))
    b = json.loads(await mcp_server.remember(text="The nightly build runs at 02:00 UTC!"))
    assert b["stored"] is True and a["stored"] is True

    report = await consolidate(threshold=0.9, apply=True)
    assert report["skipped_not_identical"] >= 1
    row = await fetch_one("SELECT superseded_by FROM chunks WHERE id = %s", (a["chunk_id"],))
    assert row["superseded_by"] is None, "near-duplicate merged without opt-in"

    report2 = await consolidate(threshold=0.9, apply=True, near_duplicates=True)
    assert report2["applied"] >= 1
    row2 = await fetch_one("SELECT superseded_by FROM chunks WHERE id = %s", (a["chunk_id"],))
    assert row2["superseded_by"] is not None
