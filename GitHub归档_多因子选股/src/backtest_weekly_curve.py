"""Weekly NAV curve for portfolios reselected only on the three annual dates."""
import argparse
import csv
from datetime import date
from html import escape
from pathlib import Path

from WindPy import w

from backtest_chart import nav_chart
from backtest_top30 import benchmark_return, max_drawdown, pct_return


ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "data" / "top20_history.csv"
ROUND_TRIP_COST = 0.002


def read_history(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def as_date(value):
    return value.date() if hasattr(value, "date") else value


def trade_offset(offset, value):
    result = w.tdaysoffset(offset, value.isoformat(), "")
    if result.ErrorCode != 0:
        raise RuntimeError(f"Wind交易日偏移失败：{result.ErrorCode}")
    return as_date(result.Data[0][0])


def ranked_by_signal(history, top_n):
    signals = sorted({row["snapshot_date"] for row in history})
    return {
        signal: sorted(
            [row for row in history if row["snapshot_date"] == signal and int(float(row["rank"])) <= top_n],
            key=lambda row: int(float(row["rank"])),
        )
        for signal in signals
    }


def annual_comparison_section(rows, end_year):
    """Build calendar-year strategy/benchmark return table and grouped bar chart."""
    annual = []
    prior_net = prior_benchmark = 1.0
    years = sorted({date.fromisoformat(row["sell_date"]).year for row in rows})
    for year in years:
        year_rows = [row for row in rows if date.fromisoformat(row["sell_date"]).year == year]
        last = year_rows[-1]
        net = float(last["net_nav"])
        benchmark = float(last["benchmark_nav"])
        annual.append({
            "year": year,
            "strategy": net / prior_net - 1,
            "benchmark": benchmark / prior_benchmark - 1,
            "strategy_nav": net,
            "benchmark_nav": benchmark,
        })
        prior_net, prior_benchmark = net, benchmark
    if not annual:
        return ""
    max_abs = max(max(abs(item["strategy"]), abs(item["benchmark"])) for item in annual) or 1
    width, height, left, right, top, bottom = 1180, 420, 70, 25, 30, 55
    plot_w, plot_h = width - left - right, height - top - bottom
    zero_y = top + plot_h / 2
    half_h = plot_h / 2 - 12
    group_w = plot_w / len(annual)
    bar_w = min(28, group_w * 0.28)
    bars = []
    labels = []
    for index, item in enumerate(annual):
        center = left + group_w * (index + 0.5)
        for offset, key, color in ((-bar_w / 2, "strategy", "#c5162e"), (bar_w / 2, "benchmark", "#16727a")):
            value = item[key]
            bar_h = abs(value) / max_abs * half_h
            y = zero_y - bar_h if value >= 0 else zero_y
            x = center + offset - bar_w / 2
            bars.append(f"<rect x='{x:.1f}' y='{y:.1f}' width='{bar_w:.1f}' height='{bar_h:.1f}' rx='3' fill='{color}'><title>{item['year']} {key}: {value:.2%}</title></rect>")
        suffix = "*" if item["year"] == end_year else ""
        labels.append(f"<text x='{center:.1f}' y='{height - 22}' text-anchor='middle' font-size='12' fill='#4b5563'>{item['year']}{suffix}</text>")
    ticks = []
    for ratio in (-1, -0.5, 0, 0.5, 1):
        y = zero_y - ratio * half_h
        value = ratio * max_abs
        ticks.append(f"<line x1='{left}' y1='{y:.1f}' x2='{width-right}' y2='{y:.1f}' stroke='#d9dfdc' stroke-width='1'/><text x='{left-9}' y='{y+4:.1f}' text-anchor='end' font-size='11' fill='#697386'>{value:.0%}</text>")
    chart = f"<div class='annual-chart'><div class='annual-legend'><span class='annual-strategy'>Top组合净值收益率</span><span class='annual-benchmark'>沪深300收益率</span></div><svg viewBox='0 0 {width} {height}' role='img' aria-label='各年度Top组合与沪深300收益率柱状对比'>{''.join(ticks)}{''.join(bars)}{''.join(labels)}</svg></div>"
    body = "".join(
        f"<tr><td>{item['year']}{'（截至当前）' if item['year'] == end_year else ''}</td><td class='ret {'pos' if item['strategy'] >= 0 else 'neg'}'>{item['strategy']:.2%}</td><td class='ret {'pos' if item['benchmark'] >= 0 else 'neg'}'>{item['benchmark']:.2%}</td><td>{item['strategy_nav']:.4f}</td><td>{item['benchmark_nav']:.4f}</td><td>{item['strategy']-item['benchmark']:.2%}</td></tr>"
        for item in annual
    )
    table = f"<div class='annual-table'><table><thead><tr><th>年度</th><th>Top组合净值收益率</th><th>沪深300收益率</th><th>策略年末净值</th><th>基准年末净值</th><th>超额收益</th></tr></thead><tbody>{body}</tbody></table></div>"
    return f"<section><h2>分年度收益率与沪深300对比</h2><p>按自然年度划分；末年度为截至回测结束日的阶段收益。红色为Top组合，蓝绿色为沪深300。</p>{chart}{table}</section>"


def main():
    parser = argparse.ArgumentParser(description="三时点调仓、周频净值曲线回测")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--history", default=str(HISTORY))
    parser.add_argument("--output-name", required=True)
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    started = w.start()
    if started.ErrorCode or not w.isconnected():
        raise RuntimeError("Wind未连接")

    history = read_history(args.history)
    portfolios = ranked_by_signal(history, args.top_n)
    buy_dates = {signal: trade_offset(1, date.fromisoformat(signal)) for signal in portfolios}
    range_days = w.tdays(start.isoformat(), end.isoformat(), "")
    if range_days.ErrorCode != 0 or not range_days.Data or not range_days.Data[0]:
        raise RuntimeError(f"Wind回测区间交易日失败：{range_days.ErrorCode}")
    first_trade, last_trade = as_date(range_days.Data[0][0]), as_date(range_days.Data[0][-1])
    weekly_result = w.tdays(first_trade.isoformat(), last_trade.isoformat(), "Period=W")
    if weekly_result.ErrorCode != 0:
        raise RuntimeError(f"Wind周频交易日失败：{weekly_result.ErrorCode}")
    week_ends = sorted({as_date(value) for value in weekly_result.Data[0] if first_trade < as_date(value) <= last_trade})
    if not week_ends or week_ends[-1] != last_trade:
        week_ends.append(last_trade)

    eligible_signals = [signal for signal in portfolios if buy_dates[signal] <= last_trade]
    if not any(buy_dates[signal] <= first_trade for signal in eligible_signals):
        raise RuntimeError("回测起点之前没有可沿用的上一调仓组合")

    rows = []
    net_nav = benchmark_nav = 1.0
    net_curve = [1.0]
    interval_start = first_trade
    for week_end in week_ends:
        switches = sorted(
            [(buy_dates[signal], signal) for signal in eligible_signals if interval_start < buy_dates[signal] <= week_end]
        )
        active = max((signal for signal in eligible_signals if buy_dates[signal] <= interval_start), key=lambda signal: buy_dates[signal])
        segment_start = interval_start
        weekly_growth = 1.0
        costs = 0.0
        for switch_date, new_signal in switches:
            prior_end = trade_offset(-1, switch_date)
            if segment_start <= prior_end:
                codes = [row["ts_code"] for row in portfolios[active]]
                segment_return, _ = pct_return(codes, segment_start, prior_end)
                weekly_growth *= 1 + segment_return
            active = new_signal
            segment_start = switch_date
            costs += ROUND_TRIP_COST
        codes = [row["ts_code"] for row in portfolios[active]]
        if segment_start <= week_end:
            segment_return, valid_count = pct_return(codes, segment_start, week_end)
            weekly_growth *= 1 + segment_return
        else:
            valid_count = len(codes)
        net_return = weekly_growth - 1 - costs
        bench_return = benchmark_return(interval_start, week_end)
        net_nav *= 1 + net_return
        benchmark_nav *= 1 + bench_return
        net_curve.append(net_nav)
        rows.append({
            "signal_date": active, "buy_date": buy_dates[active].isoformat(),
            "sell_date": week_end.isoformat(), "stock_count": valid_count,
            "stocks": "、".join(row["name"] for row in portfolios[active]), "codes": ",".join(codes),
            "gross_return": round((weekly_growth - 1) * 100, 4),
            "net_return_after_cost": round(net_return * 100, 4),
            "benchmark_return": round(bench_return * 100, 4),
            "gross_nav": round(net_nav, 6), "net_nav": round(net_nav, 6),
            "benchmark_nav": round(benchmark_nav, 6),
        })
        interval_start = trade_offset(1, week_end) if week_end < last_trade else week_end

    out_csv = ROOT / "output" / f"{args.output_name}_weekly.csv"
    out_html = ROOT / "output" / f"{args.output_name}.html"
    with out_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    total = net_nav - 1
    years = max((last_trade - first_trade).days / 365.25, 1 / 365.25)
    annualized = net_nav ** (1 / years) - 1
    win_rate = sum(row["net_return_after_cost"] > 0 for row in rows) / len(rows)
    body = "".join(
        f"<tr><td>{escape(row['sell_date'])}</td><td>{escape(row['signal_date'])}</td><td class='stocks'>{escape(row['stocks'])}</td><td>{row['net_return_after_cost']:.2f}%</td><td>{row['benchmark_return']:.2f}%</td><td>{row['net_nav']:.4f}</td><td>{row['benchmark_nav']:.4f}</td></tr>"
        for row in rows
    )
    chart = nav_chart(rows)
    annual_section = annual_comparison_section(rows, end.year) if start.year != end.year else ""
    benchmark_total = benchmark_nav - 1
    html = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{start.year}年Top{args.top_n}周频收益曲线</title><style>body{{font-family:'Microsoft YaHei',sans-serif;background:#f4efe6;color:#18202e;margin:0}}main{{width:min(1420px,calc(100% - 32px));margin:28px auto}}section{{background:#fff;border-radius:20px;padding:24px;margin-bottom:18px}}.kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}}.kpi{{background:#eef3f1;border-radius:14px;padding:16px}}.kpi b{{display:block;font-size:24px}}.chart-heading{{display:flex;justify-content:space-between}}.legend{{display:flex;gap:20px}}.legend span::before{{content:'';display:inline-block;width:24px;height:4px;margin-right:6px}}.strategy-key::before{{background:#c5162e}}.benchmark-key::before{{background:#16727a}}.nav-chart{{overflow-x:auto}}.nav-chart svg{{width:100%;min-width:760px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:9px;border-bottom:1px solid #ddd;text-align:center}}.stocks{{text-align:left}}a{{color:#17616a;font-weight:800;text-decoration:none}}</style></head><body><main><section><a href='dashboard.html'>← 返回看板</a><h1>{start.year}年Top{args.top_n}三时点调仓 · 周频收益曲线</h1><p>组合仅在4月30日、8月31日、10月30日（非交易日取此前最近交易日）按六因子模型调仓，净值按周测算。1月1日至4月30日沿用上一年度10月30日组合。调仓期扣除0.20%双边交易成本，基准为沪深300。</p><div class='kpis'><div class='kpi'>区间净收益<b>{total:.2%}</b></div><div class='kpi'>年化收益<b>{annualized:.2%}</b></div><div class='kpi'>最大回撤<b>{max_drawdown(net_curve):.2%}</b></div><div class='kpi'>周胜率<b>{win_rate:.2%}</b></div><div class='kpi'>同期沪深300<b>{benchmark_total:.2%}</b></div></div></section>{chart}<section><table><thead><tr><th>周末日期</th><th>当前组合信号日</th><th>持仓</th><th>周收益</th><th>沪深300</th><th>策略净值</th><th>基准净值</th></tr></thead><tbody>{body}</tbody></table></section></main></body></html>"""
    if annual_section:
        html = html.replace("</section><section><table>", f"</section>{annual_section}<section><h2>逐周收益与持仓明细</h2><table>", 1)
        html = html.replace(".stocks{text-align:left}", ".stocks{text-align:left}.annual-chart{overflow-x:auto}.annual-chart svg{width:100%;min-width:900px}.annual-legend{display:flex;gap:24px;font-weight:800;margin:8px 0}.annual-legend span:before{content:'';display:inline-block;width:20px;height:10px;margin-right:7px;border-radius:2px}.annual-strategy:before{background:#c5162e}.annual-benchmark:before{background:#16727a}.annual-table{overflow-x:auto}.ret.pos{color:#c5162e;font-weight:800}.ret.neg{color:#08783a;font-weight:800}")
    if start.year != end.year:
        html = html.replace(f"{start.year}年Top", f"{start.year}—{end.year}年Top")
    html = html.replace("三时点调仓", "每周选股调仓")
    html = html.replace("组合仅在4月30日、8月31日、10月30日（非交易日取此前最近交易日）按六因子模型调仓，净值按周测算。1月1日至4月30日沿用上一年度10月30日组合。", "组合每周最后一个交易日收盘后按六因子模型重新选股，下一交易日调仓，净值按周测算。")
    out_html.write_text(html, encoding="utf-8")
    print(f"Top{args.top_n}周频曲线完成：{first_trade}至{last_trade}，{len(rows)}周，净收益{total:.2%}")


if __name__ == "__main__":
    main()
