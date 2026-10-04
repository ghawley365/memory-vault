#!/bin/bash
# Nightly backup of the memory vault (the owner's decision 10, 2026-10-04: "nightly vault backup").
# Run by ~/Library/LaunchAgents/com.memory-vault.backup.plist at 03:00.
#
# WHY: the vault's only live copy is the Docker volume memory-vault_pgdata inside Docker.raw, which Time Machine
# EXCLUDES. This dump lands in ~/memory-vault/backups/nightly/, which Time Machine INCLUDES (destination: the external
# "G DRIVE"), so every night's dump also reaches a second disk.
# CHECKS: pg_dump must exit 0, the file must be at least MIN_BYTES, and pg_restore --list must show the chunks table
# data. Anything else is a failure: a macOS notification plus exit 1 (launchd logs it). Never silent.
# RETENTION: the newest KEEP nightly dumps in nightly/; manual dumps in the parent folder are never touched.
set -u
umask 077
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"

BACKUP_DIR="${BACKUP_DIR:-$HOME/memory-vault/backups/nightly}"
KEEP="${KEEP:-14}"
CONTAINER="${CONTAINER:-memory-vault-db-1}"
MIN_BYTES="${MIN_BYTES:-100000000}"   # today's dump is ~200 MB; far less means a broken or empty dump

fail() {
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) FAILED: $1" >&2
  [ "${NOTIFY:-1}" = "1" ] && /usr/bin/osascript -e "display notification \"$1\" with title \"memory-vault backup FAILED\"" >/dev/null 2>&1
  exit 1
}

mkdir -p "$BACKUP_DIR" || fail "cannot create $BACKUP_DIR"
[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" = "true" ] || fail "container $CONTAINER is not running"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_DIR/memory_vault-$TS.dump"
TMP="$OUT.partial"
docker exec "$CONTAINER" pg_dump -U memory_vault -d memory_vault -Fc > "$TMP" || { rm -f "$TMP"; fail "pg_dump exited non-zero"; }
SIZE="$(stat -f %z "$TMP")"
[ "$SIZE" -ge "$MIN_BYTES" ] || { rm -f "$TMP"; fail "dump is only $SIZE bytes (minimum $MIN_BYTES)"; }
docker exec -i "$CONTAINER" pg_restore --list < "$TMP" | grep -q 'TABLE DATA public chunks' || { rm -f "$TMP"; fail "the dump has no chunks table data"; }
mv "$TMP" "$OUT" || fail "cannot move the dump into place"

# Retention: keep the newest $KEEP verified nightly dumps.
ls -1t "$BACKUP_DIR"/memory_vault-*.dump 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do rm -f "$old"; done

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ok $OUT $SIZE bytes"
