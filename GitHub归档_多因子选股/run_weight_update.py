"""Re-rank the latest local factor data after a weight-only change."""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def run(*args):
    subprocess.run([sys.executable, *map(str, args)], cwd=ROOT, check=True)


def main():
    required = (
        ROOT / "data" / "wind" / "ai_theme.csv",
        ROOT / "data" / "wind" / "etf_top_holdings.csv",
        ROOT / "data" / "wind" / "factor_data.csv",
    )
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("缺少本地排名数据：" + "、".join(missing) + "。请先运行一次完整更新。")

    run(
        ROOT / "src" / "stock_workflow.py",
        "--ai-universe", "data/wind/ai_theme.csv",
        "--etf-holdings", "data/wind/etf_top_holdings.csv",
        "--factor-data", "data/wind/factor_data.csv",
        "--config", "config/weights.json",
        "--top-n", "10",
        "--output-dir", "output",
    )
    run(ROOT / "src" / "top20_history.py")
    run(ROOT / "src" / "wind_web_table.py")
    run(ROOT / "src" / "render_dashboard.py")
    run(ROOT / "src" / "publish_results.py")


if __name__ == "__main__":
    main()
