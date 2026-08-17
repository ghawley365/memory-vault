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

from src.mcp import server as mcp_server


async def _remember(text: str, **kw) -> dict:
    return json.loads(await mcp_server.remember(text=text, **kw))


async def _recall(query: str, **kw) -> dict:
    return json.loads(await mcp_server.recall(query=query, **kw))


@pytest.mark.asyncio
async def test_remember_with_supersedes_marks_old_chunk():
    old = await _remember("The deploy target is server alpha-1.")
    new = await _remember(
        "The deploy target is server beta-9.", supersedes=old["chunk_id"]
    )

    assert new["stored"] is True
    assert new["superseded_chunk_id"] == old["chunk_id"]

    from src.models.db import fetch_one

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
    from src.services.supersession import supersede

    chunk = await _remember("A self-referential fact.")
    with pytest.raises(ValueError):
        await supersede(chunk["chunk_id"], chunk["chunk_id"])


@pytest.mark.asyncio
async def test_already_superseded_target_is_rejected():
    from src.services.supersession import supersede

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
