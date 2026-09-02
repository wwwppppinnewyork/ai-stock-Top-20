import argparse
import csv
import math
from datetime import date
from html import escape
from pathlib import Path

from WindPy import w
from backtest_chart import nav_chart


ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "data" / "top20_history.csv"
START_DATE = date(2026, 1, 1)
ROUND_TRIP_COST = 0.002
BENCHMARK = "000300.SH"


def read_history(path=HISTORY):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def week_key(value):
    iso = date.fromisoformat(value).isocalendar()
    return iso.year, iso.week


def latest_week_dates(rows, start_date=START_DATE, end_date=date.max):
    return sorted({row["snapshot_date"] for row in rows if start_date <= date.fromisoformat(row["snapshot_date"]) <= end_date})


def finite(value):
    return value is not None and not (isinstance(value, float) and math.isnan(value))


def pct_return(codes, start_date, end_date):
    result = w.wss(",".join(codes), "pct_chg_per", f"startDate={start_date:%Y%m%d};endDate={end_date:%Y%m%d};tradeDate={end_date:%Y%m%d}")
    if result.ErrorCode != 0: raise RuntimeError(f"Wind区间收益失败：{result.ErrorCode}")
    values = [float(value) / 100 for value in result.Data[0] if finite(value)]
    return sum(values) / len(values) if values else 0.0, len(values)


def benchmark_return(start_date, end_date):
    result = w.wss(BENCHMARK, "pct_chg_per", f"startDate={start_date:%Y%m%d};endDate={end_date:%Y%m%d};tradeDate={end_date:%Y%m%d}")
    if result.ErrorCode != 0 or not result.Data or not finite(result.Data[0][0]): return 0.0
    return float(result.Data[0][0]) / 100


def max_drawdown(values):
    peak, worst = values[0], 0.0
    for value in values:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst


def main():
    parser = argparse.ArgumentParser(description="每年4月30日、8月31日、10月31日三次调仓回测")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--start", default=START_DATE.isoformat())
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--history", default=str(HISTORY))
    parser.add_argument("--output-name", default="")
    args = parser.parse_args()
    if args.top_n <= 0:
        raise ValueError("--top-n 必须大于0")
    start_date, end_date = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if start_date > end_date: raise ValueError("--start 不能晚于 --end")
    stem = args.output_name or f"top{args.top_n}_backtest"
    output_csv = ROOT / "output" / f"{stem}_weekly.csv"
    output_html = ROOT / "output" / f"{stem}.html"
    started = w.start()
    if started.ErrorCode or not w.isconnected(): raise RuntimeError("Wind未连接")
    history = read_history(args.history); snapshots = latest_week_dates(history, start_date, end_date)
    by_date = {snapshot: sorted([row for row in history if row["snapshot_date"] == snapshot and int(float(row["rank"])) <= args.top_n], key=lambda row: int(float(row["rank"]))) for snapshot in snapshots}
    rows, gross_nav, net_nav, benchmark_nav = [], 1.0, 1.0, 1.0
    gross_curve, net_curve = [1.0], [1.0]
    for index, signal_date in enumerate(snapshots):
        next_signal = snapshots[index + 1] if index + 1 < len(snapshots) else end_date.isoformat()
        if next_signal <= signal_date: continue
        buy_result = w.tdaysoffset(1, signal_date, "")
        if buy_result.ErrorCode != 0: raise RuntimeError(f"Wind下一交易日失败：{buy_result.ErrorCode}")
        buy_date = buy_result.Data[0][0]
        buy_date = buy_date.date() if hasattr(buy_date, "date") else buy_date
        sell_date = date.fromisoformat(next_signal)
        # 截止日当天才买入的最新信号尚未形成可回测持有期，也不应误扣交易成本。
        if buy_date >= sell_date:
            continue
        codes = [row["ts_code"] for row in by_date[signal_date]]
        gross_return, valid_count = pct_return(codes, buy_date, sell_date)
        net_return = gross_return - ROUND_TRIP_COST
        bench_return = benchmark_return(buy_date, sell_date)
        gross_nav *= 1 + gross_return; net_nav *= 1 + net_return; benchmark_nav *= 1 + bench_return
        gross_curve.append(gross_nav); net_curve.append(net_nav)
        rows.append({"signal_date": signal_date, "buy_date": buy_date.isoformat(), "sell_date": sell_date.isoformat(), "stock_count": valid_count, "stocks": "、".join(row["name"] for row in by_date[signal_date]), "codes": ",".join(codes), "gross_return": round(gross_return * 100, 4), "net_return_after_cost": round(net_return * 100, 4), "benchmark_return": round(bench_return * 100, 4), "gross_nav": round(gross_nav, 6), "net_nav": round(net_nav, 6), "benchmark_nav": round(benchmark_nav, 6)})
    with output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    periods = len(rows); years = periods / 52
    total_net = net_nav - 1; benchmark_total = benchmark_nav - 1
    annualized = net_nav ** (1 / years) - 1 if years else 0
    win_rate = sum(1 for row in rows if row["net_return_after_cost"] > 0) / periods if periods else 0
    body = "".join(f"<tr><td>{escape(row['signal_date'])}</td><td>{escape(row['buy_date'])}</td><td>{escape(row['sell_date'])}</td><td>{row['stock_count']}</td><td class='stocks'>{escape(row['stocks'])}</td><td>{row['gross_return']:.2f}%</td><td>{row['net_return_after_cost']:.2f}%</td><td>{row['benchmark_return']:.2f}%</td><td>{row['net_nav']:.4f}</td><td>{row['benchmark_nav']:.4f}</td></tr>" for row in rows)
    label = f"Top{args.top_n}"
    chart = nav_chart(rows)
    chart_css = ".chart-heading{display:flex;justify-content:space-between;align-items:center;gap:16px}.chart-heading h2{margin:0}.legend{display:flex;gap:22px;font-weight:700}.legend span::before{content:'';display:inline-block;width:24px;height:4px;margin:0 8px 3px 0;border-radius:3px}.strategy-key::before{background:#c5162e}.benchmark-key::before{background:#16727a}.nav-chart{overflow-x:auto}.nav-chart svg{display:block;width:100%;min-width:760px;height:auto;margin-top:12px}"
    html = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{start_date.year}年{label}三时点调仓回测</title><style>body{{font-family:'Microsoft YaHei',sans-serif;background:#f4efe6;color:#18202e;margin:0}}main{{width:min(1420px,calc(100% - 32px));margin:28px auto}}section{{background:#fff;border-radius:20px;padding:24px;margin-bottom:18px;box-shadow:0 12px 32px #0001}}.kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}}.kpi{{background:#eef3f1;border-radius:14px;padding:16px}}.kpi b{{display:block;font-size:24px;margin-top:6px}}{chart_css}table{{width:100%;border-collapse:collapse;min-width:1180px}}th,td{{padding:9px;border-bottom:1px solid #ddd;text-align:center}}th{{background:#e9eeec;position:sticky;top:0}}.wrap{{max-height:650px;overflow:auto}}.stocks{{min-width:300px;text-align:left}}a{{color:#17616a;font-weight:800;text-decoration:none}}</style></head><body><main><section><a href='dashboard.html'>← 返回看板</a><h1>{start_date.year}年三时点调仓回测{label}</h1><p>回测区间：<b>{start_date.isoformat()} 至 {end_date.isoformat()}</b>。每年仅在4月30日、8月31日、10月31日（非交易日取此前最近交易日）收盘后使用最新六因子模型生成{label}，下一交易日买入并持有至下一信号日，最后一期于区间结束日平仓。净收益按每期0.20%双边交易成本估算，不含涨跌停、停牌冲击与滑点。基准：沪深300。</p><div class='kpis'><div class='kpi'>区间净收益<b>{total_net:.2%}</b></div><div class='kpi'>年化收益<b>{annualized:.2%}</b></div><div class='kpi'>最大回撤<b>{max_drawdown(net_curve):.2%}</b></div><div class='kpi'>期间胜率<b>{win_rate:.2%}</b></div><div class='kpi'>同期沪深300<b>{benchmark_total:.2%}</b></div></div></section>{chart}<section class='wrap'><table><thead><tr><th>信号日</th><th>买入日</th><th>卖出日</th><th>有效股票数</th><th>当期入选前{args.top_n}名</th><th>毛收益</th><th>成本后收益</th><th>沪深300</th><th>策略净值</th><th>基准净值</th></tr></thead><tbody>{body}</tbody></table></section></main></body></html>"""
    output_html.write_text(html, encoding="utf-8")
    print(f"{label}回测完成：{snapshots[0]} 至 {snapshots[-1]}，{periods}期，净收益{total_net:.2%}，最大回撤{max_drawdown(net_curve):.2%}，沪深300 {benchmark_total:.2%}")


if __name__ == "__main__": main()
