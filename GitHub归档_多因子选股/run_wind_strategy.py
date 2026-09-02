import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATUS_PATH = ROOT / "data" / "update_status.json"


def run(*args):
    subprocess.run([sys.executable, *map(str, args)], cwd=ROOT, check=True)


def read_status():
    if not STATUS_PATH.exists():
        return {}
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_status(status, error=""):
    previous = read_status()
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    ranked = ROOT / "output" / "ranked_stocks.csv"
    data_date = ""
    if ranked.exists():
        import csv
        with ranked.open(encoding="utf-8-sig", newline="") as handle:
            first = next(csv.DictReader(handle), None)
            data_date = first.get("trade_date", "") if first else ""
    fallback_success = datetime.fromtimestamp(ranked.stat().st_mtime).astimezone().isoformat(timespec="seconds") if ranked.exists() else ""
    payload = {
        "status": status,
        "last_attempt_at": now,
        "last_success_at": now if status == "success" else previous.get("last_success_at", "") or fallback_success,
        "data_date": data_date,
        "data_source": "Wind API（WindPy）",
        "error": error,
    }
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


try:
    run(ROOT / "src" / "wind_universe.py")
    run(ROOT / "src" / "wind_data.py", "--ai-universe", "data/wind/ai_theme.csv", "--etf-seed", "data/wind/etf_top_holdings.csv", "--sectors", "")
    run(ROOT / "src" / "stock_workflow.py", "--ai-universe", "data/wind/ai_theme.csv", "--etf-holdings", "data/wind/etf_top_holdings.csv", "--factor-data", "data/wind/factor_data.csv", "--config", "config/weights.json", "--top-n", "10", "--output-dir", "output")
    run(ROOT / "src" / "top20_history.py")
    run(ROOT / "src" / "wind_web_table.py")
    write_status("success")
    run(ROOT / "src" / "render_dashboard.py")
    run(ROOT / "src" / "publish_results.py")
except Exception as exc:
    write_status("failed", str(exc))
    try:
        run(ROOT / "src" / "render_dashboard.py")
        run(ROOT / "src" / "publish_results.py")
    finally:
        raise
