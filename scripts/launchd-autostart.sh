#!/bin/bash
# Started at login by ~/Library/LaunchAgents/com.memory-vault.autostart.plist.
# Ensures Docker Desktop is running, waits for the daemon, then brings up the
# memory-vault compose stack (db on host 55432, app on 18080).
set -u

DOCKER="/usr/local/bin/docker"
REPO_DIR="/Users/garyhawley/memory-vault/memory-vault"
LOG="$REPO_DIR/logs/autostart.log"

mkdir -p "$(dirname "$LOG")"
echo "=== $(date) :: memory-vault autostart ===" >> "$LOG"

# 1. Make sure Docker Desktop is launched (no-op if already running).
open -a Docker 2>>"$LOG"

# 2. Wait up to ~3 minutes for the Docker daemon to accept commands.
for i in $(seq 1 90); do
  if "$DOCKER" info >/dev/null 2>&1; then
    echo "[$i] docker daemon ready" >> "$LOG"
    break
  fi
  if [ "$i" -eq 90 ]; then
    echo "ERROR: docker daemon not ready after 180s, giving up" >> "$LOG"
    exit 1
  fi
  sleep 2
done

# 3. Bring the stack up (idempotent — no-op if already running).
#    Run from the repo dir with NO -f: an explicit -f replaces compose's file
#    discovery, which silently drops docker-compose.override.yml — and with it
#    the locally-built image and the 768-d embedding env, recreating the app
#    from the upstream ghcr image at every login.
cd "$REPO_DIR" || { echo "ERROR: $REPO_DIR missing" >> "$LOG"; exit 1; }
"$DOCKER" compose up -d >>"$LOG" 2>&1
echo "compose up exit: $?" >> "$LOG"
