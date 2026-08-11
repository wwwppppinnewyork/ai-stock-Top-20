#!/bin/zsh
set -euo pipefail

ROOT="/Users/ping/Desktop/选股框架搭建"
TARGET="$ROOT/github_ready/ai-stock-weekly"

rm -rf "$TARGET"
mkdir -p "$TARGET"

rsync -a \
  --exclude '.DS_Store' \
  --exclude '.git' \
  --exclude 'output' \
  --exclude 'site' \
  --exclude 'github_ready' \
  --exclude 'webapp/node_modules' \
  --exclude 'webapp/.artifacts' \
  --exclude 'webapp/dist' \
  --exclude 'webapp/.wrangler' \
  --exclude 'webapp/.vinext' \
  "$ROOT/" "$TARGET/"

printf '%s\n' "$TARGET"
