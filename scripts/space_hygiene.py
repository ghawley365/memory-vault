#!/usr/bin/env python3
"""
LOCAL FORK: a read-only hygiene report for the memory spaces, checked against SPACES.md.

Why: spaces drifted (2026-10-03) — four ad-hoc project spaces holding 12 memories between them, two empty spaces,
and "default" grown into a catch-all of several projects' notes, so recall missed context that sat in a space nobody
searched. The registry (SPACES.md, the table under "## The registry") names every space that should exist; this
report lists what disagrees with it.

Reports (never prints memory content — ids, sources and dates only):
  - spaces in the database that are not in the registry (an ad-hoc space someone created);
  - registry spaces missing from the database;
  - empty spaces (0 chunks AND 0 entities) — candidates to delete, never deleted here;
  - live chunks written to "default" after the registry's "Default reviewed through:" timestamp (or, with no
    marker, in the last N days) — each one may belong to a project space (a session that did not name its space,
    or a project with no MEMORY_VAULT_DEFAULT_SPACE). After reviewing them, move the misfiles and bump the marker.

Exit 0 when clean, 1 when anything above is found, 2 on a usage or connection error.

Usage (from the repo, with the vault DB env set as for the MCP server — DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD):
    .venv/bin/python scripts/space_hygiene.py [--registry SPACES.md] [--default-days 30]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parents[1]
_ROW = re.compile(r"^\|\s*`?([a-z0-9][a-z0-9_-]*)`?\s*\|")
_REVIEWED = re.compile(r"^Default reviewed through:\s*(\S+)", re.M)


def registry_spaces(path: Path) -> list[str]:
    """Space names from the first markdown table under '## The registry' in SPACES.md."""
    names: list[str] = []
    in_section = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            in_section = line.strip().lower() == "## the registry"
            continue
        if in_section:
            m = _ROW.match(line)
            if m and m.group(1).lower() not in {"space", "name"}:
                names.append(m.group(1))
    return names


def default_reviewed_through(path: Path) -> str | None:
    """The ISO timestamp after which new chunks in "default" need a look (SPACES.md marker), or None."""
    m = _REVIEWED.search(path.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def _conninfo() -> str:
    def env(name: str, default: str) -> str:
        return (os.environ.get(name) or "").strip() or default

    return psycopg.conninfo.make_conninfo(
        host=env("DB_HOST", "localhost"),
        port=env("DB_PORT", "5432"),
        dbname=env("DB_NAME", "memory_vault"),
        user=env("DB_USER", "memory_vault"),
        password=os.environ.get("DB_PASSWORD", "memory_vault"),
    )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--registry", type=Path, default=REPO / "SPACES.md")
    ap.add_argument("--default-days", type=int, default=30)
    args = ap.parse_args(argv)

    if not args.registry.is_file():
        print(f"registry not found: {args.registry}", file=sys.stderr)
        return 2
    registered = registry_spaces(args.registry)
    reviewed = default_reviewed_through(args.registry)
    if not registered:
        print(
            f"no spaces parsed from the '## The registry' table in {args.registry}", file=sys.stderr
        )
        return 2

    try:
        with psycopg.connect(_conninfo()) as conn, conn.cursor() as cur:
            cur.execute("SET default_transaction_read_only = on")
            cur.execute(
                """
                SELECT ms.name,
                       count(c.id) AS chunks,
                       count(c.id) FILTER (WHERE c.superseded_by IS NULL AND (c.metadata->>'forgotten')::boolean IS NOT TRUE) AS live,
                       max(c.created_at)::date AS last_write,
                       (SELECT count(*) FROM entities e WHERE e.space_id = ms.id) AS entities
                FROM memory_spaces ms LEFT JOIN chunks c ON c.space_id = ms.id
                GROUP BY ms.id ORDER BY ms.name
                """
            )
            rows = cur.fetchall()
            since_sql = "%s::timestamptz" if reviewed else "now() - make_interval(days => %s)"
            cur.execute(
                f"""
                SELECT c.id::text, coalesce(c.source, ''), c.created_at::date
                FROM chunks c JOIN memory_spaces ms ON ms.id = c.space_id
                WHERE ms.name = 'default' AND c.superseded_by IS NULL
                  AND (c.metadata->>'forgotten')::boolean IS NOT TRUE
                  AND c.created_at > {since_sql}
                ORDER BY c.created_at DESC
                """,
                (reviewed if reviewed else args.default_days,),
            )
            recent_default = cur.fetchall()
    except psycopg.Error as e:
        print(f"cannot read the vault database: {e.__class__.__name__}", file=sys.stderr)
        return 2

    in_db = {r[0] for r in rows}
    issues = 0
    print(f"{'space':24} {'chunks':>7} {'live':>7} {'entities':>8}  last write   registered")
    for name, chunks, live, last_write, entities in rows:
        print(
            f"{name:24} {chunks:7d} {live:7d} {entities:8d}  {str(last_write) if last_write else '-':12} {'yes' if name in registered else 'NO'}"
        )

    unregistered = sorted(in_db - set(registered))
    missing = [n for n in registered if n not in in_db]
    empty = [r[0] for r in rows if r[1] == 0 and r[4] == 0]
    if unregistered:
        issues += 1
        print(f"\nNOT IN THE REGISTRY ({len(unregistered)}): {', '.join(unregistered)}")
    if missing:
        issues += 1
        print(f"\nIN THE REGISTRY BUT NOT IN THE DATABASE ({len(missing)}): {', '.join(missing)}")
    if empty:
        issues += 1
        print(
            f"\nEMPTY (0 chunks, 0 entities) — delete candidates, never deleted here: {', '.join(empty)}"
        )
    if recent_default:
        issues += 1
        window = f"after {reviewed}" if reviewed else f"in the last {args.default_days} days"
        print(
            f"\n{len(recent_default)} live chunk(s) written to 'default' {window}"
            " — check whether each belongs to a project space, then bump 'Default reviewed through:':"
        )
        for cid, source, created in recent_default[:50]:
            print(f"  {cid}  {created}  {source[:60]}")
        if len(recent_default) > 50:
            print(f"  … and {len(recent_default) - 50} more")
    print("\nclean" if issues == 0 else f"\n{issues} kind(s) of issue found")
    return 0 if issues == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
