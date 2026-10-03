"""
LOCAL FORK: MCP `remember` falls back to the session's PROJECT space, not to "default".

Why: `remember` hard-coded space="default", so every session that forgot to name a space filed its memory under
"default" — which became a catch-all of several projects' notes (measured 2026-10-03: 281 chunks from CueSignal,
codeapps, FleetIQ and tooling sessions). Each project already launches this MCP server from its own .mcp.json, so
the project's space is configured there as MEMORY_VAULT_DEFAULT_SPACE and read at CALL time (a changed .mcp.json
takes effect at the next server start; tests can monkeypatch the environment).

Contract pinned here:
  - no space given + MEMORY_VAULT_DEFAULT_SPACE set   -> that space
  - no space given + unset or blank                   -> "default" (the old behaviour)
  - an explicit space always wins
  - a configured space that does not exist is REFUSED (stored: false, the unknown-space error) — never silently
    rerouted to "default", which would recreate the catch-all this change removes
  - memory_status reports the configured default, so a session can verify its wiring
"""

from __future__ import annotations

import json
import uuid

import pytest

pytestmark = pytest.mark.asyncio

_SPACE = "fork_default_space_test"


async def _ensure_space(name: str) -> None:
    from memory_vault.models.db import execute_query

    await execute_query(
        "INSERT INTO memory_spaces (name, description) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING",
        (name, "test space for MEMORY_VAULT_DEFAULT_SPACE"),
    )


async def _space_of(chunk_id: str) -> str:
    from memory_vault.models.db import fetch_one

    row = await fetch_one(
        "SELECT ms.name FROM chunks c JOIN memory_spaces ms ON ms.id = c.space_id WHERE c.id = %s",
        (chunk_id,),
    )
    assert row is not None, "the stored chunk must exist"
    return row["name"]


def _unique(label: str) -> str:
    # remember() deduplicates on content, so every test stores text no other test (or run) has stored.
    return f"default-space contract {label} {uuid.uuid4()}"


class TestRememberDefaultSpace:
    async def test_no_space_uses_the_configured_project_space(self, monkeypatch):
        from memory_vault.mcp.server import remember

        await _ensure_space(_SPACE)
        monkeypatch.setenv("MEMORY_VAULT_DEFAULT_SPACE", _SPACE)
        result = json.loads(await remember(_unique("configured")))
        assert result.get("stored") is True, result
        assert await _space_of(result["chunk_id"]) == _SPACE

    async def test_no_space_and_no_configuration_keeps_the_old_default(self, monkeypatch):
        from memory_vault.mcp.server import remember

        monkeypatch.delenv("MEMORY_VAULT_DEFAULT_SPACE", raising=False)
        result = json.loads(await remember(_unique("unset")))
        assert result.get("stored") is True, result
        assert await _space_of(result["chunk_id"]) == "default"

    async def test_a_blank_configuration_counts_as_unset(self, monkeypatch):
        from memory_vault.mcp.server import remember

        monkeypatch.setenv("MEMORY_VAULT_DEFAULT_SPACE", "   ")
        result = json.loads(await remember(_unique("blank")))
        assert result.get("stored") is True, result
        assert await _space_of(result["chunk_id"]) == "default"

    async def test_an_explicit_space_wins_over_the_configuration(self, monkeypatch):
        from memory_vault.mcp.server import remember

        await _ensure_space(_SPACE)
        monkeypatch.setenv("MEMORY_VAULT_DEFAULT_SPACE", _SPACE)
        result = json.loads(await remember(_unique("explicit"), space="default"))
        assert result.get("stored") is True, result
        assert await _space_of(result["chunk_id"]) == "default"

    async def test_a_misconfigured_space_is_refused_not_rerouted(self, monkeypatch):
        from memory_vault.mcp.server import remember

        monkeypatch.setenv("MEMORY_VAULT_DEFAULT_SPACE", "no_such_space_" + uuid.uuid4().hex[:8])
        result = json.loads(await remember(_unique("misconfigured")))
        assert result.get("stored") is False, result
        assert "Unknown space" in result.get("error", ""), result


class TestMemoryStatusReportsTheDefault:
    async def test_status_names_the_configured_default(self, monkeypatch):
        from memory_vault.mcp.server import memory_status

        monkeypatch.setenv("MEMORY_VAULT_DEFAULT_SPACE", _SPACE)
        status = json.loads(await memory_status())
        assert status.get("default_space") == _SPACE, status

    async def test_status_names_default_when_unset(self, monkeypatch):
        from memory_vault.mcp.server import memory_status

        monkeypatch.delenv("MEMORY_VAULT_DEFAULT_SPACE", raising=False)
        status = json.loads(await memory_status())
        assert status.get("default_space") == "default", status
