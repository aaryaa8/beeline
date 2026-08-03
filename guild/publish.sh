#!/usr/bin/env bash
# Publish the four Beeline agents to Guild.ai.
#
# Each agent in this folder (*.agent.ts) is a real @guildai/agents-sdk agent and
# has been type-checked against the SDK. This script scaffolds a publishable
# directory for each, drops the agent code in, builds it, and pushes a version.
#
# Prereqs (once):
#   - guild CLI on PATH and authenticated (guild auth status)
#   - node/npm available
#
# Usage:
#   bash guild/publish.sh          # build + save + publish all four
#   bash guild/publish.sh --dry    # scaffold + build only, do not push
#
# The build directory (guild/.build) is disposable and gitignored.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BUILD="$HERE/.build"
DRY="${1:-}"

command -v guild >/dev/null || { echo "guild CLI not found on PATH"; exit 1; }
guild auth status >/dev/null 2>&1 || { echo "not authenticated: run 'guild auth login'"; exit 1; }

mkdir -p "$BUILD"
AGENTS=(matchmaker icebreaker empath router)

for a in "${AGENTS[@]}"; do
  echo "=================================================================="
  echo "Agent: beeline-$a"
  dir="$BUILD/beeline-$a"
  if [ ! -f "$dir/guild.json" ]; then
    guild agent init --name "beeline-$a" --agent-type GUILD_TYPESCRIPT \
      --template LLM --category productivity --directory "$dir"
  fi
  # Drop in the real agent code (overwrites the template entry).
  cp "$HERE/$a.agent.ts" "$dir/agent.ts"
  ( cd "$dir"
    npm install >/dev/null 2>&1
    npm run bundle >/dev/null 2>&1
    echo "  built agent.js.gz ($(du -h agent.js.gz | cut -f1))"
    git add -A && git commit -m "beeline $a agent" >/dev/null 2>&1 || true
    if [ "$DRY" = "--dry" ]; then
      echo "  [dry] skipping save/publish"
    else
      guild agent save
      guild agent publish
      echo "  published beeline-$a"
    fi
  )
done

echo "=================================================================="
echo "Done. Then set GUILD_BACKEND=guild in .env once the app is wired to"
echo "invoke the published agents (see guild/README.md for the I/O contract)."
