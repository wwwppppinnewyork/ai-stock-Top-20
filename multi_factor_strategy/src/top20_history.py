import csv
import math
from datetime import date, datetime
from html import escape
from pathlib import Path

from WindPy import w

ROOT = Path(__file__).resolve().parent.parent
TOP_PICKS = ROOT / "output" / "ranked_stocks.csv"
HISTORY = ROOT / "data" / "top20_history.csv"
FREQUENCY_CSV = ROOT / "output" / "top20_frequency_6m.csv"
FREQUENCY_HTML = ROOT / "output" / "top20_frequency.html"
TRACK_TOP_N = 30
DISPLAY_TOP_N = 10
WINDOW_WEEKS = 26
SELECTION_DATES = ((4, 30), (8, 31), (10, 30))


def read_csv(path):
    if not path.exists(): return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle: return list(csv.DictReader(handle))


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def parse_date(value): return datetime.strptime(value, "%Y-%m-%d").date()


def week_key(value):
    day = parse_date(value) if isinstance(value, str) else value
    iso = day.isocalendar()
    return iso.year, iso.week


def latest_week_snapshots(history, end):
    return sorted({row.get("snapshot_date", "") for row in history if row.get("snapshot_date", "") and row["snapshot_date"] <= end})[-WINDOW_WEEKS:]


def is_selection_signal(snap):
    signal = parse_date(snap)
    started = w.start()
    if started.ErrorCode or not w.isconnected():
        raise RuntimeError("Wind未连接，无法核验固定选股日")
    for month, day in SELECTION_DATES:
        target = date(signal.year, month, day)
        result = w.tdaysoffset(0, target.isoformat(), "")
        if result.ErrorCode == 0:
            actual = result.Data[0][0].date() if hasattr(result.Data[0][0], "date") else result.Data[0][0]
            if actual == signal:
                return True
    return False


def record_snapshot(top):
    snap = max((row.get("trade_date", "") for row in top if row.get("trade_date")), default=date.today().isoformat())
    history = read_csv(HISTORY)
    current_week = week_key(snap)
    old = [row for row in history if week_key(row["snapshot_date"]) != current_week]
    new = [{"snapshot_date": snap, "ts_code": row["ts_code"], "name": row["name"], "rank": row["rank"], "total_score": row["total_score"]} for row in top[:TRACK_TOP_N]]
    rows = sorted(old + new, key=lambda row: (row["snapshot_date"], int(float(row["rank"]))))
    write_csv(HISTORY, rows, ["snapshot_date", "ts_code", "name", "rank", "total_score"])
    return snap, rows


def calculate(snap, history):
    snapshots = latest_week_snapshots(history, snap)
    selected = set(snapshots)
    recent = [row for row in history if row.get("snapshot_date") in selected and int(float(row.get("rank", 999))) <= TRACK_TOP_N]
    grouped = {}
    for row in recent: grouped.setdefault(row["ts_code"], []).append(row)
    result = []
    for code, rows in grouped.items():
        ranks = [int(float(row["rank"])) for row in rows]
        last = max(rows, key=lambda row: row["snapshot_date"])
        count = len({week_key(row["snapshot_date"]) for row in rows})
        result.append({"frequency_rank": 0, "ts_code": code, "name": last["name"], "appearances": count, "observed_weeks": len(snapshots), "appearance_rate": round(count / max(len(snapshots), 1) * 100, 2), "best_rank": min(ranks), "average_rank": round(sum(ranks) / len(ranks), 2), "last_rank": int(float(last["rank"])), "last_seen": last["snapshot_date"]})
    result.sort(key=lambda row: (-row["appearances"], row["average_rank"], row["last_rank"], row["ts_code"]))
    for index, row in enumerate(result, 1): row["frequency_rank"] = index
    return snapshots[0] if snapshots else snap, snap, len(snapshots), result


def valid(value): return value is not None and not (isinstance(value, float) and math.isnan(value))


def add_market_data(rows, end):
    codes = [row["ts_code"] for row in rows]
    if not codes: return rows
    end_date = date.fromisoformat(end)
    started = w.start()
    if started.ErrorCode or not w.isconnected(): raise RuntimeError("Wind未连接，无法更新高频榜行情")
    returns = w.wss(",".join(codes), "pct_chg_per", f"startDate={end_date.year}0101;endDate={end_date:%Y%m%d};tradeDate={end_date:%Y%m%d}")
    base = w.tdaysoffset(0, date(end_date.year, 1, 1), "")
    if base.ErrorCode: raise RuntimeError(f"Wind年初基准日失败：{base.ErrorCode}")
    prices = w.wsd(",".join(codes), "close", base.Data[0][0], end_date, "PriceAdj=F")
    if returns.ErrorCode or prices.ErrorCode: raise RuntimeError(f"Wind高频榜行情失败：{returns.ErrorCode}/{prices.ErrorCode}")
    return_map, price_map = dict(zip(returns.Codes, returns.Data[0])), dict(zip(prices.Codes, prices.Data))
    for row in rows:
        value = return_map.get(row["ts_code"]); row["ytd_return"] = "" if not valid(value) else round(float(value), 6)
        row["close_ytd"] = "|".join(str(round(float(item), 4)) for item in price_map.get(row["ts_code"], []) if valid(item))
    return rows


def sparkline(raw, wind_ytd=None):
    values = [float(item) for item in str(raw).split("|") if item]
    if len(values) < 2: return "<span class='muted'>暂无数据</span>"
    low, high = min(values), max(values); span = high - low or 1
    points = " ".join(f"{i*138/(len(values)-1):.1f},{35-(value-low)*31/span:.1f}" for i, value in enumerate(values))
    try: change = float(wind_ytd)
    except (TypeError, ValueError): change = (values[-1] / values[0] - 1) * 100 if values[0] else 0
    color = "#c5162e" if change >= 0 else "#08783a"
    return f"<div class='spark'><svg viewBox='0 0 140 39'><polyline points='{points}' fill='none' stroke='{color}' stroke-width='2.8' stroke-linecap='round' stroke-linejoin='round'/></svg><b style='color:{color}'>{change:.2f}%</b></div>"


def render(cutoff, end, observed, rows, history_start, total_weeks):
    body = "".join(f"<tr><td>{row['frequency_rank']}</td><td><a href='dashboard.html#stock-{escape(row['ts_code']).replace('.', '-')}'>{escape(row['ts_code'])}</a></td><td>{escape(row['name'])}</td><td>{sparkline(row.get('close_ytd',''),row.get('ytd_return'))}</td><td class='strong'>{row['appearances']}</td><td>{row['observed_weeks']}</td><td>{row['appearance_rate']:.2f}%</td><td>{row['best_rank']}</td><td>{row['average_rank']:.2f}</td><td>{row['last_rank']}</td><td>{row['last_seen']}</td></tr>" for row in rows[:DISPLAY_TOP_N])
    html = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>近6个月Top30高频股票前10名</title><style>
:root{{--ink:#172033;--muted:#687386;--line:#d8ddd9;--red:#c51c2d}}*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(145deg,#edf1ef,#f8f4ed);font-family:'Microsoft YaHei','微软雅黑',sans-serif;font-size:12px;font-weight:700;color:var(--ink)}}main{{width:min(1380px,calc(100% - 32px));margin:30px auto}}.hero,.panel{{background:rgba(255,255,255,.9);border:1px solid var(--line);border-radius:26px;padding:28px;box-shadow:0 15px 40px rgba(31,41,55,.08)}}.panel{{margin-top:20px;overflow:auto}}h1{{margin:8px 0;font-size:34px;font-weight:900}}p{{color:var(--muted);line-height:1.8;font-weight:700}}a{{color:#135c66;text-decoration:none;font-weight:800}}.tag{{display:inline-block;padding:7px 12px;border-radius:20px;background:#e7efed;color:#185b62;font-weight:800}}table{{width:100%;border-collapse:collapse;min-width:1080px;font-size:12px}}th,td{{padding:8px 11px;border-bottom:1px solid #e5e7eb;text-align:center;font-size:12px;font-weight:700}}th{{background:#eef2f0;position:sticky;top:0;font-weight:900}}td:nth-child(2),td:nth-child(3){{text-align:left;white-space:nowrap}}.strong{{font-size:12px;font-weight:900;color:var(--red)}}.spark{{width:140px;display:flex;flex-direction:column;align-items:center;gap:0}}.spark svg{{width:140px;height:29px;display:block}}.spark b{{font-size:12px;font-weight:900}}.muted{{color:var(--muted)}}
</style></head><body><main><section class='hero'><a href='dashboard.html'>← 返回当前排名看板</a><div><span class='tag'>Top30 历史跟踪</span></div><h1>近6个月高频入选股票 · 前10名</h1><p>完整历史从 <b>{history_start}</b> 开始，共保存 <b>{total_weeks}</b> 个自然周快照；本榜严格采用最近 <b>{WINDOW_WEEKS}</b> 个有效自然周（{cutoff} 至 {end}）。</p><p>同一股票每个自然周进入Top30累计1次；同一周重复更新只保留最新交易日，不会重复增加周数。</p><p>本窗口实际包含 <b>{observed}</b> 周。先按出现次数降序，次数相同时按平均名次排序，展示频率榜前10名。</p></section><section class='panel'><table><thead><tr><th>频率排名</th><th>证券代码</th><th>证券简称</th><th>{end[:4]}年初至今走势图</th><th>进入Top30周数</th><th>已观察周数</th><th>出现率</th><th>最好名次</th><th>平均名次</th><th>最近名次</th><th>最近入选日</th></tr></thead><tbody>{body or "<tr><td colspan='11'>暂无历史记录</td></tr>"}</tbody></table></section></main></body></html>"""
    FREQUENCY_HTML.write_text(html, encoding="utf-8")


def main():
    top = read_csv(TOP_PICKS)
    if not top: raise RuntimeError(f"完整排名文件为空：{TOP_PICKS}")
    snap, history = record_snapshot(top); cutoff, end, observed, result = calculate(snap, history); result = add_market_data(result[:DISPLAY_TOP_N], end)
    fields = ["frequency_rank", "ts_code", "name", "ytd_return", "close_ytd", "appearances", "observed_weeks", "appearance_rate", "best_rank", "average_rank", "last_rank", "last_seen"]
    write_csv(FREQUENCY_CSV, result, fields)
    all_weeks = {week_key(row["snapshot_date"]) for row in history}; dates = sorted({row["snapshot_date"] for row in history})
    render(cutoff, end, observed, result, dates[0], len(all_weeks))
    print(f"自然周Top30快照 {snap}；最近26周频率榜输出前10名；有效周数 {observed}；{FREQUENCY_HTML}")


if __name__ == "__main__": main()
