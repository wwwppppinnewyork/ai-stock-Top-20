#!/bin/zsh
set -euo pipefail

ROOT="/Users/ping/Desktop/选股框架搭建"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"

cd "$ROOT"

"$PYTHON_BIN" "$ROOT/src/stock_workflow.py" \
  --ai-universe "$ROOT/data/sample_ai_theme.csv" \
  --etf-holdings "$ROOT/data/sample_etf_top_holdings.csv" \
  --factor-data "$ROOT/data/sample_factor_data.csv" \
  --config "$ROOT/config/weights.json"

"$PYTHON_BIN" "$ROOT/src/render_dashboard.py"
"$PYTHON_BIN" "$ROOT/src/build_site.py"
"$PYTHON_BIN" "$ROOT/src/sync_webapp_public.py"

echo "Updated website files under $ROOT/site"
