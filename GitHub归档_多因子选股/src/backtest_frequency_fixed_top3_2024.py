import argparse
import csv
from datetime import date
from html import escape
from pathlib import Path

from WindPy import w

from backtest_frequency_top10 import frequency_portfolio, read_history, weekly_snapshots
from backtest_top30 import benchmark_return, max_drawdown, pct_return


ROOT = Path(__file__).resolve().parent.parent
START_DATE = date(2024, 1, 1)
END_DATE = date(2024, 12, 31)
TOP_N = 3
ROUND_TRIP_COST = 0.002


def as_date(value):
    return value.date() if hasattr(value, "date") else value


def main():
    parser = argparse.ArgumentParser(description="当前近6个月高频榜前3名固定名单等权周频回测")
    parser.add_argument("--start", default=START_DATE.isoformat())
    parser.add_argument("--end", default=END_DATE.isoformat())
    parser.add_argument("--output-stem", default="frequency_top3_equal_weight_weekly_2024")
    args = parser.parse_args()
    start_date, end_date = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if start_date > end_date:
        raise ValueError("--start 不能晚于 --end")
    started = w.start()
    if started.ErrorCode or not w.isconnected():
        raise RuntimeError("Wind未连接")

    history = read_history()
    snapshots = weekly_snapshots(history)
    if not snapshots:
        raise RuntimeError("没有高频入选历史快照")
    portfolio, observed_weeks = frequency_portfolio(history, snapshots, snapshots[-1], TOP_N)
    if len(portfolio) != TOP_N:
        raise RuntimeError("近6个月高频榜不足3只股票")
    codes = [row["ts_code"] for row in portfolio]

    daily_trades = w.tdays(start_date.isoformat(), end_date.isoformat(), "")
    if daily_trades.ErrorCode or not daily_trades.Data or not daily_trades.Data[0]:
        raise RuntimeError("Wind未返回回测区间交易日")
    first_trade = as_date(daily_trades.Data[0][0])
    weekly = [as_date(value) for value in w.tdays(start_date.isoformat(), end_date.isoformat(), "Period=W").Data[0]]
    boundaries = [first_trade] + [day for day in weekly if first_trade < day < end_date] + [end_date]

    rows, net_nav, benchmark_nav = [], 1.0, 1.0
    net_curve = [1.0]
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        gross_return, valid_count = pct_return(codes, start, end)
        net_return = gross_return - ROUND_TRIP_COST
        bench_return = benchmark_return(start, end)
        net_nav *= 1 + net_return
        benchmark_nav *= 1 + bench_return
        net_curve.append(net_nav)
        rows.append({
            "buy_date": start.isoformat(), "sell_date": end.isoformat(),
            "stock_count": valid_count,
            "stocks": "、".join(row["name"] for row in portfolio),
            "codes": ",".join(codes), "gross_return": round(gross_return * 100, 4),
            "net_return_after_cost": round(net_return * 100, 4),
            "benchmark_return": round(bench_return * 100, 4),
            "net_nav": round(net_nav, 6), "benchmark_nav": round(benchmark_nav, 6),
        })

    output_csv = ROOT / "output" / f"{args.output_stem}.csv"
    with output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)

    periods = len(rows)
    total_net = net_nav - 1
    annualized = net_nav ** (52 / periods) - 1
    benchmark_total = benchmark_nav - 1
    win_rate = sum(row["net_return_after_cost"] > 0 for row in rows) / periods
    names = "、".join(f"{escape(row['name'])}（{escape(row['ts_code'])}）" for row in portfolio)
    body = "".join(
        f"<tr><td>{i}</td><td>{row['buy_date']}</td><td>{row['sell_date']}</td><td>{row['stock_count']}</td><td class='stock-list'>{escape(row['stocks'])}</td>"
        f"<td>{row['gross_return']:.2f}%</td><td>{row['net_return_after_cost']:.2f}%</td>"
        f"<td>{row['benchmark_return']:.2f}%</td><td>{row['net_nav']:.4f}</td><td>{row['benchmark_nav']:.4f}</td></tr>"
        for i, row in enumerate(rows, 1)
    )
    html = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>近6个月高频入选股票 · 前3名等权周频回测（{start_date.year}）</title><style>
body{{font-family:'Microsoft YaHei',sans-serif;background:#f4efe6;color:#18202e;margin:0}}main{{width:min(1480px,calc(100% - 32px));margin:28px auto}}section{{background:#fff;border-radius:20px;padding:24px;margin-bottom:18px;box-shadow:0 12px 32px #0001}}h1{{margin:12px 0}}.sub{{color:#59636f;line-height:1.8}}.stocks{{padding:14px 16px;background:#fff4df;border-left:4px solid #c5162e;border-radius:10px}}.kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-top:18px}}.kpi{{background:#eef3f1;border-radius:14px;padding:16px}}.kpi b{{display:block;font-size:24px;margin-top:6px}}table{{width:100%;border-collapse:collapse;min-width:1250px}}th,td{{padding:9px;border-bottom:1px solid #ddd;text-align:center}}th{{background:#e9eeec;position:sticky;top:0}}.stock-list{{min-width:230px;text-align:left;font-weight:700}}.wrap{{max-height:680px;overflow:auto}}a{{color:#17616a;font-weight:800;text-decoration:none}}@media(max-width:850px){{.kpis{{grid-template-columns:1fr 1fr}}}}
</style></head><body><main><section><a href='dashboard.html'>← 返回仙童多因子选股看板</a><h1>近6个月高频入选股票 · 前3名等权周频回测</h1><p class='sub'>时间跨度：<b>{start_date.isoformat()} 至 {end_date.isoformat()}</b>。固定采用最新近6个月（{observed_weeks}个有效周）高频榜前3名，每周等权再平衡。净收益每期扣除0.20%双边交易成本，基准为沪深300。</p><p class='stocks'><b>回测股票：</b>{names}</p><p class='sub'>口径提示：股票名单来自当前近6个月高频榜，因此本页展示的是该固定名单在{start_date.year}年的历史表现，存在事后选股偏差，不代表当时可实时执行的动态信号。</p><div class='kpis'><div class='kpi'>区间净收益<b>{total_net:.2%}</b></div><div class='kpi'>年化收益<b>{annualized:.2%}</b></div><div class='kpi'>最大回撤<b>{max_drawdown(net_curve):.2%}</b></div><div class='kpi'>周胜率<b>{win_rate:.2%}</b></div><div class='kpi'>同期沪深300<b>{benchmark_total:.2%}</b></div></div></section><section class='wrap'><table><thead><tr><th>期数</th><th>买入日</th><th>卖出日</th><th>有效股票数</th><th>具体股票</th><th>毛收益</th><th>成本后收益</th><th>沪深300</th><th>策略净值</th><th>基准净值</th></tr></thead><tbody>{body}</tbody></table></section></main></body></html>"""
    output_html = ROOT / "output" / f"{args.output_stem}.html"
    output_html.write_text(html, encoding="utf-8")
    print(f"{output_html} | {names} | {periods}期 | 净收益{total_net:.2%}")


if __name__ == "__main__":
    main()
