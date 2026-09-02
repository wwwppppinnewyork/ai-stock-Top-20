"""Add annual comparison tables/charts to existing long-horizon backtest pages."""
import csv
from pathlib import Path

from backtest_weekly_curve import annual_comparison_section


ROOT = Path(__file__).resolve().parent.parent


def main():
    for top_n in (5, 10):
        stem = f"top{top_n}_backtest_2016_2026"
        csv_path = ROOT / "output" / f"{stem}_weekly.csv"
        html_path = ROOT / "output" / f"{stem}.html"
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        html = html_path.read_text(encoding="utf-8")
        if "分年度收益率与沪深300对比" in html:
            continue
        annual_section = annual_comparison_section(rows, 2026)
        html = html.replace(
            "</section><section><table>",
            f"</section>{annual_section}<section><h2>逐周收益与持仓明细</h2><table>",
            1,
        )
        html = html.replace(
            ".stocks{text-align:left}",
            ".stocks{text-align:left}.annual-chart{overflow-x:auto}.annual-chart svg{width:100%;min-width:900px}.annual-legend{display:flex;gap:24px;font-weight:800;margin:8px 0}.annual-legend span:before{content:'';display:inline-block;width:20px;height:10px;margin-right:7px;border-radius:2px}.annual-strategy:before{background:#c5162e}.annual-benchmark:before{background:#16727a}.annual-table{overflow-x:auto}.ret.pos{color:#c5162e;font-weight:800}.ret.neg{color:#08783a;font-weight:800}",
        )
        html_path.write_text(html, encoding="utf-8")
        print(html_path)


if __name__ == "__main__":
    main()
