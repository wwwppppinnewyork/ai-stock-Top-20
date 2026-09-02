import argparse
import csv
from datetime import date
from html import escape
from pathlib import Path

from WindPy import w

from backtest_top30 import benchmark_return, finite, max_drawdown, pct_return, week_key


ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "data" / "top20_history.csv"
FREQUENCY_WEEKS = 6
TRACK_TOP_N = 30
ROUND_TRIP_COST = 0.002


def read_history(path=HISTORY):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def weekly_snapshots(history):
    return sorted({row["snapshot_date"] for row in history})


def frequency_portfolio(history, snapshots, signal_date, portfolio_top_n):
    window = [snapshot for snapshot in snapshots if snapshot <= signal_date][-FREQUENCY_WEEKS:]
    selected_dates = set(window)
    grouped = {}
    for row in history:
        if row["snapshot_date"] in selected_dates and int(float(row["rank"])) <= TRACK_TOP_N:
            grouped.setdefault(row["ts_code"], []).append(row)
    ranked = []
    for code, rows in grouped.items():
        ranks = [int(float(row["rank"])) for row in rows]
        latest = max(rows, key=lambda row: row["snapshot_date"])
        ranked.append({
            "ts_code": code,
            "name": latest["name"],
            "appearances": len({row["snapshot_date"] for row in rows}),
            "average_rank": sum(ranks) / len(ranks),
            "last_rank": int(float(latest["rank"])),
        })
    ranked.sort(key=lambda row: (-row["appearances"], row["average_rank"], row["last_rank"], row["ts_code"]))
    return ranked[:portfolio_top_n], len(window)


def nav_chart(rows):
    width, height = 1180, 390
    left, right, top, bottom = 64, 28, 30, 54
    strategy = [1.0] + [float(row["net_nav"]) for row in rows]
    benchmark = [1.0] + [float(row["benchmark_nav"]) for row in rows]
    dates = [rows[0]["buy_date"]] + [row["sell_date"] for row in rows]
    values = strategy + benchmark
    low, high = min(values), max(values)
    padding = max((high - low) * 0.08, 0.05)
    low, high = max(0, low - padding), high + padding
    plot_w, plot_h = width - left - right, height - top - bottom

    def points(series):
        return " ".join(
            f"{left + i * plot_w / (len(series) - 1):.1f},{top + (high - value) * plot_h / (high - low):.1f}"
            for i, value in enumerate(series)
        )

    grid = []
    for i in range(5):
        value = high - i * (high - low) / 4
        y = top + i * plot_h / 4
        grid.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" stroke="#d9dfdc"/><text x="{left-10}" y="{y+5:.1f}" text-anchor="end" fill="#66706c" font-size="12">{value:.2f}</text>')
    end_y_strategy = top + (high - strategy[-1]) * plot_h / (high - low)
    end_y_benchmark = top + (high - benchmark[-1]) * plot_h / (high - low)
    return f'''<section class="chart-panel"><div class="chart-heading"><h2>累计净值曲线</h2><div class="legend"><span class="strategy-key">策略成本后净值</span><span class="benchmark-key">沪深300净值</span></div></div><div class="nav-chart"><svg viewBox="0 0 {width} {height}" role="img" aria-label="策略与沪深300累计净值对比">{''.join(grid)}<line x1="{left}" y1="{top+plot_h}" x2="{width-right}" y2="{top+plot_h}" stroke="#8d9692"/><polyline points="{points(benchmark)}" fill="none" stroke="#16727a" stroke-width="3" stroke-linejoin="round"/><polyline points="{points(strategy)}" fill="none" stroke="#c5162e" stroke-width="3.5" stroke-linejoin="round"/><circle cx="{width-right}" cy="{end_y_strategy:.1f}" r="4" fill="#c5162e"/><circle cx="{width-right}" cy="{end_y_benchmark:.1f}" r="4" fill="#16727a"/><text x="{width-right-8}" y="{end_y_strategy-9:.1f}" text-anchor="end" fill="#c5162e" font-weight="700">策略 {strategy[-1]:.2f}</text><text x="{width-right-8}" y="{end_y_benchmark+20:.1f}" text-anchor="end" fill="#16727a" font-weight="700">沪深300 {benchmark[-1]:.2f}</text><text x="{left}" y="{height-18}" fill="#66706c" font-size="12">{dates[0]}</text><text x="{width-right}" y="{height-18}" text-anchor="end" fill="#66706c" font-size="12">{dates[-1]}</text></svg></div></section>'''


def main():
    parser = argparse.ArgumentParser(description="最近6个固定选股时点高频入选股票等权回测")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--output-name", default="")
    parser.add_argument("--history", default=str(HISTORY))
    args = parser.parse_args()
    if args.top_n <= 0:
        raise ValueError("--top-n 必须大于0")
    start_date, end_date = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if start_date > end_date:
        raise ValueError("--start 不能晚于 --end")
    stem = args.output_name or f"frequency_top{args.top_n}_backtest"
    output_csv = ROOT / "output" / f"{stem}_weekly.csv"
    output_html = ROOT / "output" / f"{stem}.html"
    started = w.start()
    if started.ErrorCode or not w.isconnected():
        raise RuntimeError("Wind未连接")
    history = read_history(args.history)
    all_snapshots = weekly_snapshots(history)
    signals = [snapshot for snapshot in all_snapshots if start_date <= date.fromisoformat(snapshot) <= end_date]
    rows, net_nav, benchmark_nav = [], 1.0, 1.0
    net_curve = [1.0]
    for index, signal_date in enumerate(signals):
        next_signal = signals[index + 1] if index + 1 < len(signals) else end_date.isoformat()
        if next_signal <= signal_date:
            continue
        portfolio, observed_weeks = frequency_portfolio(history, all_snapshots, signal_date, args.top_n)
        buy_result = w.tdaysoffset(1, signal_date, "")
        if buy_result.ErrorCode != 0:
            raise RuntimeError(f"Wind下一交易日失败：{buy_result.ErrorCode}")
        buy_date = buy_result.Data[0][0]
        buy_date = buy_date.date() if hasattr(buy_date, "date") else buy_date
        sell_date = date.fromisoformat(next_signal)
        # 截止日当天才买入的最新信号尚未形成可回测持有期，也不应误扣交易成本。
        if buy_date >= sell_date:
            continue
        codes = [row["ts_code"] for row in portfolio]
        gross_return, valid_count = pct_return(codes, buy_date, sell_date)
        net_return = gross_return - ROUND_TRIP_COST
        bench_return = benchmark_return(buy_date, sell_date)
        net_nav *= 1 + net_return
        benchmark_nav *= 1 + bench_return
        net_curve.append(net_nav)
        rows.append({
            "signal_date": signal_date,
            "buy_date": buy_date.isoformat(),
            "sell_date": sell_date.isoformat(),
            "observed_weeks": observed_weeks,
            "stock_count": valid_count,
            "stocks": "、".join(row["name"] for row in portfolio),
            "codes": ",".join(codes),
            "gross_return": round(gross_return * 100, 4),
            "net_return_after_cost": round(net_return * 100, 4),
            "benchmark_return": round(bench_return * 100, 4),
            "net_nav": round(net_nav, 6),
            "benchmark_nav": round(benchmark_nav, 6),
        })
    if not rows:
        raise RuntimeError("有效历史周数不足，无法回测")
    with output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    periods = len(rows)
    years = periods / 52
    total_net = net_nav - 1
    annualized = net_nav ** (1 / years) - 1 if years else 0
    benchmark_total = benchmark_nav - 1
    win_rate = sum(row["net_return_after_cost"] > 0 for row in rows) / periods
    body = "".join(
        f"<tr><td>{row['signal_date']}</td><td>{row['buy_date']}</td><td>{row['sell_date']}</td>"
        f"<td>{row['observed_weeks']}</td><td>{row['stock_count']}</td><td class='stocks'>{escape(row['stocks'])}</td>"
        f"<td>{row['gross_return']:.2f}%</td><td>{row['net_return_after_cost']:.2f}%</td>"
        f"<td>{row['benchmark_return']:.2f}%</td><td>{row['net_nav']:.4f}</td><td>{row['benchmark_nav']:.4f}</td></tr>"
        for row in rows
    )
    chart = nav_chart(rows)
    html = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>近6个月高频前{args.top_n}名等权周频回测</title><style>
body{{font-family:'Microsoft YaHei',sans-serif;background:#f4efe6;color:#18202e;margin:0}}main{{width:min(1480px,calc(100% - 32px));margin:28px auto}}section{{background:#fff;border-radius:20px;padding:24px;margin-bottom:18px;box-shadow:0 12px 32px #0001}}.kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}}.kpi{{background:#eef3f1;border-radius:14px;padding:16px}}.kpi b{{display:block;font-size:24px;margin-top:6px}}.chart-heading{{display:flex;justify-content:space-between;align-items:center;gap:16px}}.chart-heading h2{{margin:0}}.legend{{display:flex;gap:22px;font-weight:700}}.legend span::before{{content:'';display:inline-block;width:24px;height:4px;margin:0 8px 3px 0;border-radius:3px}}.strategy-key::before{{background:#c5162e}}.benchmark-key::before{{background:#16727a}}.nav-chart{{overflow-x:auto}}.nav-chart svg{{display:block;width:100%;min-width:760px;height:auto;margin-top:12px}}table{{width:100%;border-collapse:collapse;min-width:1250px}}th,td{{padding:9px;border-bottom:1px solid #ddd;text-align:center}}th{{background:#e9eeec;position:sticky;top:0}}.wrap{{max-height:680px;overflow:auto}}.stocks{{min-width:300px;text-align:left}}a{{color:#17616a;font-weight:800;text-decoration:none}}
</style></head><body><main><section><a href='dashboard.html'>← 返回看板</a><h1>近6个月高频入选股票 · 前{args.top_n}名等权周频回测</h1><p>回测区间：<b>{start_date.isoformat()} 至 {end_date.isoformat()}</b>。每个信号日仅使用当时已知的最近{FREQUENCY_WEEKS}个有效周Top30快照，按入选次数、平均名次、最近名次依次排序选出前{args.top_n}名；下一交易日等权买入并持有至下一周，最后一期于区间结束日平仓。净收益按每期0.20%双边交易成本估算。基准：沪深300。</p><div class='kpis'><div class='kpi'>区间净收益<b>{total_net:.2%}</b></div><div class='kpi'>年化收益<b>{annualized:.2%}</b></div><div class='kpi'>最大回撤<b>{max_drawdown(net_curve):.2%}</b></div><div class='kpi'>周胜率<b>{win_rate:.2%}</b></div><div class='kpi'>同期沪深300<b>{benchmark_total:.2%}</b></div></div></section>{chart}<section class='wrap'><table><thead><tr><th>信号日</th><th>买入日</th><th>卖出日</th><th>统计周数</th><th>有效股票数</th><th>当期高频前{args.top_n}名</th><th>毛收益</th><th>成本后收益</th><th>沪深300</th><th>策略净值</th><th>基准净值</th></tr></thead><tbody>{body}</tbody></table></section></main></body></html>"""
    if args.top_n == 3:
        report_name = f"{start_date.year}年高频前3回测"
        html = html.replace("近6个月高频前3名等权周频回测", report_name)
        html = html.replace("近6个月高频入选股票 · 前3名等权周频回测", report_name)
    html = html.replace(
        "每个信号日仅使用当时已知的最近26个有效周Top30快照",
        "每个信号日仅使用最新六因子模型及当前权重配置生成、且当时已知的最近26个有效周Top30快照",
    )
    output_html.write_text(html, encoding="utf-8")
    print(f"高频Top{args.top_n}回测完成：{signals[0]} 至 {signals[-1]}，{periods}期，净收益{total_net:.2%}，最大回撤{max_drawdown(net_curve):.2%}，沪深300 {benchmark_total:.2%}")


if __name__ == "__main__":
    main()
