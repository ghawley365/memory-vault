"""
Supersession — bi-temporal fact replacement.

When a new memory replaces an earlier one, the old chunk is marked
superseded (superseded_by / superseded_at) and disappears from recall,
while remaining in the database as history.
"""

from __future__ import annotations

import json
import uuid

import pytest

from memory_vault.mcp import server as mcp_server


async def _remember(text: str, **kw) -> dict:
    return json.loads(await mcp_server.remember(text=text, **kw))


async def _recall(query: str, **kw) -> dict:
    return json.loads(await mcp_server.recall(query=query, **kw))


@pytest.mark.asyncio
async def test_remember_with_supersedes_marks_old_chunk():
    old = await _remember("The deploy target is server alpha-1.")
    new = await _remember("The deploy target is server beta-9.", supersedes=old["chunk_id"])

    assert new["stored"] is True
    assert new["superseded_chunk_id"] == old["chunk_id"]

    from memory_vault.models.db import fetch_one

    row = await fetch_one(
        "SELECT superseded_by, superseded_at FROM chunks WHERE id = %s",
        (old["chunk_id"],),
    )
    assert str(row["superseded_by"]) == new["chunk_id"]
    assert row["superseded_at"] is not None


@pytest.mark.asyncio
async def test_superseded_chunk_excluded_from_recall():
    old = await _remember("The project codename is BLUEBIRD.")
    await _remember("The project codename is REDHAWK.", supersedes=old["chunk_id"])

    res = await _recall("what is the project codename?")
    ids = [r["chunk_id"] for r in res["results"]]
    assert old["chunk_id"] not in ids

    contents = " ".join(r["content"] for r in res["results"])
    assert "REDHAWK" in contents


@pytest.mark.asyncio
async def test_supersedes_missing_target_still_stores_new_memory():
    bogus = str(uuid.uuid4())
    new = await _remember("A fact whose supersede target does not exist.", supersedes=bogus)

    assert new["stored"] is True
    assert "supersede_error" in new


@pytest.mark.asyncio
async def test_supersede_self_is_rejected():
    from memory_vault.services.supersession import supersede

    chunk = await _remember("A self-referential fact.")
    with pytest.raises(ValueError):
        await supersede(chunk["chunk_id"], chunk["chunk_id"])


@pytest.mark.asyncio
async def test_already_superseded_target_is_rejected():
    from memory_vault.services.supersession import supersede

    a = await _remember("Version 1 of the fact.")
    await _remember("Version 2 of the fact.", supersedes=a["chunk_id"])
    c = await _remember("Version 3 of the fact, stored independently.")

    with pytest.raises(ValueError):
        await supersede(a["chunk_id"], c["chunk_id"])


@pytest.mark.asyncio
async def test_memory_status_active_excludes_superseded():
    a = await _remember("Status count fact, version 1.")
    await _remember("Status count fact, version 2.", supersedes=a["chunk_id"])

    status = json.loads(await mcp_server.memory_status())
    default_space = status["chunks_per_space"]["default"]
    assert default_space["total"] == 2
    assert default_space["active"] == 1


# --- edge cases surfaced by the 2026-08-17 audit -------------------------------


@pytest.mark.asyncio
async def test_supersedes_invalid_uuid_reports_error_but_memory_is_stored():
    """A malformed `supersedes` must not turn a committed store into stored:false."""
    from memory_vault.mcp import server as mcp_server

    res = json.loads(
        await mcp_server.remember(
            text="Fact stored despite bad supersedes id.", supersedes="not-a-uuid"
        )
    )
    assert res["stored"] is True
    assert "supersede_error" in res
    assert "superseded_chunk_id" not in res


@pytest.mark.asyncio
async def test_supersede_rejects_cross_space():
    """A chunk in one space must not be superseded by a chunk stored in another."""
    from memory_vault.mcp import server as mcp_server
    from memory_vault.models.db import execute_query
    from memory_vault.services.supersession import supersede

    await execute_query(
        "INSERT INTO memory_spaces (name, description) VALUES ('probe-space', 'x') ON CONFLICT DO NOTHING"
    )
    old = json.loads(
        await mcp_server.remember(text="Old fact lives in probe-space.", space="probe-space")
    )
    new = json.loads(await mcp_server.remember(text="Replacement stored in default."))

    with pytest.raises(ValueError, match="space"):
        await supersede(old["chunk_id"], new["chunk_id"])


@pytest.mark.asyncio
async def test_supersedes_honoured_when_new_text_is_exact_duplicate():
    """remember(text=T, supersedes=O) with T already stored must still supersede O
    (using the existing chunk as the replacement), not silently drop the intent."""
    from memory_vault.mcp import server as mcp_server
    from memory_vault.models.db import fetch_one

    old = json.loads(await mcp_server.remember(text="Old value: cache TTL is 5 minutes."))
    first = json.loads(await mcp_server.remember(text="New value: cache TTL is 1 hour."))
    dup = json.loads(
        await mcp_server.remember(
            text="New value: cache TTL is 1 hour.", supersedes=old["chunk_id"]
        )
    )
    assert dup["duplicate"] is True
    assert dup["existing_chunk_id"] == first["chunk_id"]
    assert dup.get("superseded_chunk_id") == old["chunk_id"]

    row = await fetch_one("SELECT superseded_by FROM chunks WHERE id = %s", (old["chunk_id"],))
    assert str(row["superseded_by"]) == first["chunk_id"]


@pytest.mark.asyncio
async def test_superseded_chunk_leaves_live_graph_views():
    """Migration 008 extends the live_* views: a superseded chunk's mentions and
    relationships disappear from the graph the same way a forgotten chunk's do."""
    from memory_vault.mcp import server as mcp_server
    from memory_vault.models.db import fetch_one

    text = "Ada Lovelace worked with Charles Babbage in London on the Analytical Engine."
    old = json.loads(await mcp_server.remember(text=text))
    before = await fetch_one(
        "SELECT count(*) AS n FROM live_entity_mentions WHERE chunk_id = %s", (old["chunk_id"],)
    )
    assert before["n"] > 0, "extraction should have produced mentions for this text"

    new = json.loads(
        await mcp_server.remember(
            text="Ada Lovelace collaborated with Charles Babbage in London (revised).",
            supersedes=old["chunk_id"],
        )
    )
    assert new.get("superseded_chunk_id") == old["chunk_id"]

    after = await fetch_one(
        "SELECT count(*) AS n FROM live_entity_mentions WHERE chunk_id = %s", (old["chunk_id"],)
    )
    assert after["n"] == 0
    rel = await fetch_one(
        "SELECT count(*) AS n FROM live_relationships WHERE chunk_id = %s", (old["chunk_id"],)
    )
    assert rel["n"] == 0


@pytest.mark.asyncio
async def test_superseded_content_can_be_stored_again():
    """A superseded chunk must not keep occupying the (space, content_hash)
    dedup slot forever — deliberately re-storing that content is a new, live
    memory (migration 010 narrows the unique index to live rows)."""
    from memory_vault.mcp import server as mcp_server

    old = json.loads(await mcp_server.remember(text="Retention is 30 days for raw logs."))
    new = json.loads(
        await mcp_server.remember(
            text="Retention is 90 days for raw logs.", supersedes=old["chunk_id"]
        )
    )
    assert new.get("superseded_chunk_id") == old["chunk_id"]

    again = json.loads(await mcp_server.remember(text="Retention is 30 days for raw logs."))
    assert again["stored"] is True, again
    assert again["chunk_id"] != old["chunk_id"]

    res = json.loads(await mcp_server.recall(query="raw log retention days", limit=10))
    ids = {r["chunk_id"] for r in res["results"]}
    assert again["chunk_id"] in ids
