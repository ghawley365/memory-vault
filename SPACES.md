# Memory spaces — the registry

One space per project, plus a few shared spaces with a fixed purpose. A memory that lands in the wrong space is only
found by a search that happens to name that space, so this list is what every session should follow.

**Find anything:** `recall` with no `spaces` argument searches every space.

## The registry

| Space | What belongs here | Default for |
|---|---|---|
| `km-sales-app` | CueSignal (the Signal repo in the "Konica Minolta Sales APP Strategy" workspace): decisions, outcomes, plans, owner instructions, audits — including the Sentinel fleet notes folded in from the former cuesignal-* spaces and, since 2026-10-03, the LLM-testing ledger notes (ids T/F/D/X/E-nnn; the former `ai-output` space was merged in and deleted on the owner's word — ALL CueSignal memory stays here). The legacy name is kept on purpose (configs, hooks and docs name it). | the CueSignal workspace (`.mcp.json`, `MEMORY_VAULT_DEFAULT_SPACE=km-sales-app`) |
| `codeapps` | codeapps.ai (the product on its own Hostinger VPS: CISO agent, OpenObserve, site, deploys, GAP items). | codeapps.ai (local-scope MCP entry, `MEMORY_VAULT_DEFAULT_SPACE=codeapps`) |
| `insights` | Cross-project engineering lessons and gotchas. Project history belongs in the project's space. | — (named per call) |
| `fleetiq` | FleetIQ (inactive): bulk-ingested code and docs plus its session notes. | — |
| `printsim` | PrintSim printer-fleet simulator (inactive). | — |
| `bizhub-iws-monitor` | bizhub IWS Monitoring Tool (inactive): repo docs plus its session notes. | — |
| `docs` | Reference only: memory-vault v1.0 ARCHITECTURE.md (out of date). Never write here. | — |
| `default` | Fallback for projects without a space; personal notes; retired side projects (SafeQ tooling, MFD-QR-HOST); cross-project owner rules; vault test probes. | anything without a configured space |

Default reviewed through: 2026-10-03T13:06:48Z

## Rules

1. **A project that writes regularly gets its own space.** Create it (`memory-vault space create <name> --description "..."`,
   run in the app container or the repo venv), add a row above, and set `MEMORY_VAULT_DEFAULT_SPACE` for that project:
   in the project's `.mcp.json` only when that file is NOT tracked in git (the vault entry carries the DB password);
   otherwise a local-scope entry — `claude mcp add-json memory-vault '<json>' -s local`, run in the project folder.
2. **No ad-hoc spaces for sub-topics.** One space per project; lessons that apply across projects go to `insights`.
3. **Moving notes:** only the vault's own move (`move_memory` / `POST /api/chunks/{id}/move`), never raw SQL; move a
   supersession chain as a unit (the move does not touch `superseded_by`). An identical copy cannot be moved next to
   its twin (the dedup index) — forget it instead.
4. **Deleting a space:** only through `delete_space` (REST `DELETE /api/spaces/{name}`), which refuses a space that
   still holds memories or graph entities. Never `DELETE FROM memory_spaces`: entities cascade.
5. **Never run `purge_forgotten`** (owner decision): forgotten notes stay recoverable.
6. **Hygiene:** `.venv/bin/python scripts/space_hygiene.py` with the vault DB env (as in `.mcp.json`). It reports
   unregistered or missing spaces, empty spaces, and `default` notes written after the reviewed-through mark. After a
   review, move any misfiles and update the mark.

## History

- 2026-10-03: reorganised 14 spaces into 9 with nothing lost — 42 notes moved (stray CueSignal notes into km-sales-app;
  BIZHUB/FleetIQ/PrintSim session notes into their spaces), 204 codeapps.ai notes moved into the new `codeapps`
  space, 4 exact duplicates in default forgotten, 6 empty spaces deleted (code, planning, cuesignal-codebase,
  cuesignal-findings, cuesignal-ops, cuesignal-security), the dormant sentinel-fleet token revoked. Every move was
  gated per note (text, embedding, metadata, history links, graph keys); the vault record is in `km-sales-app`.
