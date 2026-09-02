import csv
import json
from datetime import datetime
from html import escape
from pathlib import Path


WORKDIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = WORKDIR / "output"
DATA_DIR = WORKDIR / "data"
TOP_PICKS_CSV = OUTPUT_DIR / "top_picks.csv"
RANKED_CSV = OUTPUT_DIR / "ranked_stocks.csv"
QUARTERLY_NET_PROFIT_CSV = DATA_DIR / "wind" / "quarterly_parent_net_profit.csv"
ANNUAL_NET_PROFIT_CSV = DATA_DIR / "wind" / "annual_net_profit_forecast.csv"
WEB_TABLE_CSV = DATA_DIR / "wind" / "web_table_data.csv"
INTERIM_DISCLOSURE_CSV = DATA_DIR / "wind" / "interim_disclosure_dates.csv"
DASHBOARD_HTML = OUTPUT_DIR / "dashboard.html"
HISTORY_CSV = DATA_DIR / "top20_history.csv"
UPDATE_STATUS_JSON = DATA_DIR / "update_status.json"
WEIGHTS_JSON = WORKDIR / "config" / "weights.json"


def stock_anchor(code):
    return "stock-" + str(code).replace(".", "-").replace("/", "-")


def stock_link(code, label=None, prefix=""):
    shown = escape(str(label if label is not None else code))
    return f'<a class="stock-code-link" href="{prefix}#{stock_anchor(code)}" title="查看该股票详细图表与六因子小分">{shown}</a>'


def read_rows(path: Path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def update_status(trade_date):
    if UPDATE_STATUS_JSON.exists():
        try:
            return json.loads(UPDATE_STATUS_JSON.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    timestamp = datetime.fromtimestamp(RANKED_CSV.stat().st_mtime).astimezone().isoformat(timespec="seconds") if RANKED_CSV.exists() else "-"
    return {"status": "success" if RANKED_CSV.exists() else "failed", "last_attempt_at": timestamp, "last_success_at": timestamp if RANKED_CSV.exists() else "-", "data_date": trade_date, "data_source": "Wind API（WindPy）", "error": ""}


def compact_timestamp(value):
    try:
        return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(value)


def previous_week_snapshot(trade_date):
    if not HISTORY_CSV.exists():
        return "-", []
    rows = read_rows(HISTORY_CSV)
    previous_dates = sorted({row.get("snapshot_date", "") for row in rows if row.get("snapshot_date", "") < trade_date})
    if not previous_dates:
        return "-", []
    snapshot_date = previous_dates[-1]
    snapshot_rows = [row for row in rows if row.get("snapshot_date") == snapshot_date and int(float(row.get("rank", 999))) <= 30]
    snapshot_rows.sort(key=lambda row: int(float(row.get("rank", 999))))
    return snapshot_date, snapshot_rows[:10]


def render_previous_snapshot(rows):
    return "".join(
        f'<tr><td>{escape(str(row.get("rank", "-")))}</td><td>{stock_link(row.get("ts_code", "-"))}</td><td>{escape(row.get("name", "-"))}</td><td>{format_num(row.get("total_score"))}</td></tr>'
        for row in rows
    ) or '<tr><td colspan="4">暂无上周历史快照</td></tr>'


def format_pct(value: str) -> str:
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "-"


def format_report_pct(value: str) -> str:
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "尚未披露"


def revision_diff_note(value):
    try:
        difference = float(value)
    except (TypeError, ValueError):
        return "无预测｜因子记0分"
    if abs(difference) < 0.5:
        return "| 差值 | < 0.5个百分点｜因子记0分"
    return "进入有符号标准化"


def format_num(value: str) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "-"


def format_integer(value: str) -> str:
    try:
        return f"{float(value):.0f}"
    except (TypeError, ValueError):
        return "-"


def compact_number(value,digits=2):
    try:
        return f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "-"


def weight_pct(value):
    return f"{compact_number(float(value) * 100)}%"


def factor_points(config, key):
    return float(config["weights"][key]) * 100


def format_profit(value) -> str:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "-"


def render_sparkline(raw, wind_ytd=None):
    try:
        values = [float(x) for x in str(raw).split("|") if x]
    except ValueError:
        values = []
    if len(values) < 2:
        return '<span class="spark-empty">暂无数据</span>'
    low, high = min(values), max(values)
    span = high - low or 1
    points = " ".join(f"{i*118/(len(values)-1):.1f},{34-(v-low)*30/span:.1f}" for i,v in enumerate(values))
    color = "#c5162e" if values[-1] >= values[0] else "#08783a"
    try:
        change = float(wind_ytd)
    except (TypeError, ValueError):
        change = (values[-1] / values[0] - 1) * 100 if values[0] else 0
    return f'<div class="spark"><svg viewBox="0 0 120 38"><polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"/></svg><span style="color:{color}">{change:.2f}%</span></div>'


def render_top_cards(rows):
    cards = []
    for row in rows[:5]:
        cards.append(
            f"""
            <article class="pick-card">
              <div class="pick-rank">#{row['rank']}</div>
              <div class="pick-name">{row['name']}</div>
              <div class="pick-code">{stock_link(row['ts_code'])}</div>
              <div class="pick-score">{format_num(row['total_score'])}</div>
              <div class="pick-meta">单季度净利润环比：{format_pct(row['net_profit_qoq_growth'])}<br>{row.get('qoq_current_period', '-')} 较 {row.get('qoq_previous_period', '-')}；最近五个连续季度均严格增长时本因子满分</div>
              <div class="pick-meta">{row.get('consensus_year', '-')}E净利润增长率预测差值：{format_pct(row['analyst_net_profit_revision_change'])}<br>当前预测增长率减2026-08-22预测增长率；绝对差值小于0.5个百分点记0</div>
            </article>
            """
        )
    return "\n".join(cards)


def quarter_label(period):
    try:
        year, month, _ = period.split("-")
        return f"{year}Q{(int(month) - 1) // 3 + 1}"
    except (AttributeError, ValueError):
        return "-"


def previous_quarter(period):
    q = quarter_label(period)
    if q == "-": return "-"
    year, quarter = int(q[:4]), int(q[-1])
    return f"{year - 1}Q4" if quarter == 1 else f"{year}Q{quarter - 1}"


def prior_year_quarter(period):
    q = quarter_label(period)
    return "-" if q == "-" else f"{int(q[:4]) - 1}{q[4:]}"


def cumulative_period_label(period):
    try:
        year, month, _ = period.split("-")
        label = {"03": "一季报", "06": "中报", "09": "前三季度", "12": "年报"}[month]
        return f"{year}年{label}累计"
    except (AttributeError, KeyError, ValueError):
        return "当年累计"


def annual_value(annual_map, code, year):
    for item in annual_map.get(code, []):
        if str(item.get("year")) == str(year):
            return format_num(item.get("net_profit"))
    return "-"


def annual_actual_value(annual_map, code):
    for item in annual_map.get(code, []):
        if "实际" in str(item.get("data_type", "")):
            return format_num(item.get("net_profit"))
    return "-"


def render_table(rows, annual_map=None):
    trs = []
    for sequence, row in enumerate(rows[:10], 1):
        trs.append(
            f"""
            <tr>
              <td class="ranking-sequence">{sequence}</td>
              <td>{stock_link(row['ts_code'])}</td>
              <td class="stock-name">{row['name']}</td><td>{render_sparkline(row.get('close_ytd',''), row.get('ytd_return'))}</td><td>{format_integer(row['market_cap'])}</td>
              <td>{format_pct(row['g26'])}<br><small>2026E vs 2025A</small></td><td>{format_pct(row['g26_old'])}<br><small>截至2026-08-22</small></td><td>{format_pct(row['g26_diff'])}<br><small>最新－2026-08-22｜{revision_diff_note(row['g26_diff'])}</small></td>
              <td>{format_pct(row['g27'])}<br><small>2027E vs 2026E</small></td><td>{format_pct(row['g27_old'])}<br><small>截至2026-08-22</small></td><td>{format_pct(row['g27_diff'])}<br><small>最新－2026-08-22｜{revision_diff_note(row['g27_diff'])}</small></td>
              <td>{format_pct(row['g28'])}<br><small>2028E vs 2027E</small></td>
              <td>{annual_actual_value(annual_map or {{}}, row['ts_code'])}</td><td>{format_num(row['p26'])}</td><td>{format_num(row['p26_old'])}</td><td>{format_num(row['p26_diff'])}</td>
              <td>{format_num(row['p27'])}</td><td>{format_num(row['p27_old'])}</td><td>{format_num(row['p27_diff'])}</td><td>{format_num(row['p28'])}</td>
              <td>{format_num(row['pe26'])}</td><td>{format_num(row['pe27'])}</td><td>{format_num(row['pe28'])}</td>
              <td>{format_num(row['peg26'])}</td><td>{format_num(row['peg27'])}</td><td>{format_num(row['peg28'])}</td>
              <td class="industry-cell">{row['sw_industry']}</td>
              <td>{format_pct(row['gm_prior_ytd'])}<br><small>{row['gm_prior_label']}</small></td><td>{format_pct(row['gm_current_ytd'])}<br><small>{row['gm_current_label']}</small></td>
              <td>{format_report_pct(row.get('net_margin_mid_prior'))}<br><small>{row.get('net_margin_mid_prior_label', '-')}</small></td>
              <td>{format_report_pct(row.get('net_margin_mid_current'))}<br><small>{row.get('net_margin_mid_current_label', '-')}</small></td>
              <td>{format_report_pct(row.get('roe_mid_prior'))}<br><small>{row.get('roe_mid_prior_label', '-')}</small></td>
              <td>{format_report_pct(row.get('roe_mid_current'))}<br><small>{row.get('roe_mid_current_label', '-')}</small></td>
            </tr>
            """
        )
    return "\n".join(trs)


FACTOR_RANKINGS = [
    ("分析师净利润同比", "score_analyst_profit_yoy", "weighted_analyst_profit_yoy"),
    ("PEG", "score_peg", "weighted_peg"),
    ("单季度净利润环比", "score_qoq_growth", "weighted_qoq_growth"),
    ("连续增长不中断季度数", "score_consecutive_profit_growth_quarters", "weighted_consecutive_profit_growth_quarters"),
    ("分析师预测变化率", "score_analyst_revision", "weighted_analyst_revision"),
    ("质量", "score_quality", "weighted_quality"),
]


def numeric(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def render_factor_rankings(rows):
    cards = []
    for label, score_field, weighted_field in FACTOR_RANKINGS:
        ordered = sorted(rows, key=lambda row: (-numeric(row.get(score_field)), row.get("ts_code", "")))
        body = "".join(
            f"<tr><td>{factor_rank}</td><td>{stock_link(row.get('ts_code', '-'))}</td><td>{escape(row.get('name', '-'))}</td><td>{numeric(row.get(score_field)):.4f}</td><td>{numeric(row.get(weighted_field)):.2f}</td><td>{escape(str(row.get('rank', '-')))}</td><td>{numeric(row.get('total_score')):.2f}</td></tr>"
            for factor_rank, row in enumerate(ordered, 1)
        )
        cards.append(f"""
        <article class="factor-ranking-card">
          <h3>{label}因子排名</h3>
          <p>按该因子的未加权标准分从高到低排列；覆盖全部通过资格筛选的候选股票。</p>
          <div class="factor-table-wrap"><table class="factor-table"><thead><tr><th>因子排名</th><th>证券代码</th><th>证券简称</th><th>因子标准分</th><th>加权得分</th><th>综合排名</th><th>综合总分</th></tr></thead><tbody>{body}</tbody></table></div>
        </article>
        """)
    return "".join(cards)


def group_quarterly_rows(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(row["ts_code"], []).append(row)
    for items in grouped.values():
        items.sort(key=lambda x: x["quarter"])
    return grouped


def group_annual_rows(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(row["ts_code"], []).append(row)
    for items in grouped.values():
        items.sort(key=lambda x: int(x["year"]))
    return grouped


def metric_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def comparison_bar(value, max_abs, year):
    number = metric_number(value)
    if number is None:
        return f'<div class="compare-slot"><div class="compare-plot"><div class="compare-positive"><span class="compare-missing">-</span></div><div class="compare-negative"></div></div><div class="compare-year">{year}年中报</div></div>'
    height = max(5.0, abs(number) / max_abs * 54.0)
    shown = f"{number:.2f}%"
    if number >= 0:
        plot = f'<div class="compare-positive"><span class="compare-value">{shown}</span><div class="compare-bar positive" style="height:{height:.1f}px"></div></div><div class="compare-negative"></div>'
    else:
        plot = f'<div class="compare-positive"></div><div class="compare-negative"><div class="compare-bar negative" style="height:{height:.1f}px"></div><span class="compare-value negative-text">{shown}</span></div>'
    return f'<div class="compare-slot"><div class="compare-plot">{plot}</div><div class="compare-year">{year}年中报</div></div>'


def comparison_metric_card(label, prior_value, current_value, prior_year, current_year, wind_field):
    values = [number for number in (metric_number(prior_value), metric_number(current_value)) if number is not None]
    max_abs = max([abs(number) for number in values] + [1.0])
    missing_note = " · Wind暂无数据" if len(values) < 2 else ""
    return (
        '<div class="quality-metric">'
        f'<div class="quality-label">中报{label}</div>'
        '<div class="quality-comparison">'
        f'{comparison_bar(prior_value, max_abs, prior_year)}'
        f'{comparison_bar(current_value, max_abs, current_year)}'
        '</div>'
        f'<div class="quality-period">Wind {wind_field}｜{prior_year}-06-30 与 {current_year}-06-30{missing_note}</div>'
        '</div>'
    )


def factor_score_item(label, value, maximum):
    """Render the points actually contributed by one factor to total_score."""
    return (
        '<div class="factor-score-item">'
        f'<div class="factor-score-label">{label}</div>'
        f'<div class="factor-score-value">{format_num(value)}<span> / {maximum}</span></div>'
        '</div>'
    )


def render_quarterly_charts(ranked_rows, quarterly_map, annual_map, web_map, disclosure_map, config):
    profit_year = config["profit_yoy_year_weights"]
    peg_year = config["peg_year_weights"]
    revision_year = config["revision_year_weights"]
    quality_year = config["quality_year_weights"]
    maxima = {key: compact_number(factor_points(config, key)) for key in config["weights"]}
    cards = []
    for stock_index, row in enumerate(ranked_rows, start=1):
        series = quarterly_map.get(row["ts_code"], [])[-20:]
        annual = annual_map.get(row["ts_code"], [])
        web = web_map.get(row["ts_code"], {})
        try:
            current_mid_year = int(str(row.get("report_period", ""))[:4])
        except ValueError:
            current_mid_year = datetime.now().year
        prior_mid_year = current_mid_year - 1
        if not series and not annual:
            continue
        disclosure = disclosure_map.get(row["ts_code"], {})
        scheduled_date = disclosure.get("scheduled_date", "")
        actual_date = disclosure.get("actual_date", "")
        disclosure_note = (
            f'<span class="chart-disclosure">（2026年中报发布时间：{escape(scheduled_date)}）</span>'
            if not actual_date and scheduled_date else ""
        )
        values = [float(item["net_profit"]) for item in series]
        max_abs = max(max(abs(v) for v in values), 1.0)
        year_marks = {}
        for idx, item in enumerate(series):
            year = item["quarter"][:4]
            year_marks.setdefault(year, []).append(idx)

        bars = []
        for idx, item in enumerate(series):
            value = float(item["net_profit"])
            # Each half is 115px high. Reserve room for the bold value label so
            # the tallest bar cannot expand the grid and cross the zero axis.
            height = max(abs(value) / max_abs * 84, 5) if value else 0
            year = item["quarter"][:4]
            is_year_start = year_marks[year][0] == idx
            divider = '<span class="year-divider"></span>' if is_year_start and idx != 0 else ""
            bars.append(
                f"""
                <div class="bar-slot">
                  {divider}
                  <div class="bar-plot">
                    <div class="positive-half">{f'<div class="bar-value">{format_profit(value)}</div><div class="bar positive" style="height:{height:.1f}px"></div>' if value > 0 else ''}</div>
                    <div class="negative-half">{f'<div class="bar negative" style="height:{height:.1f}px"></div><div class="bar-value negative-value">{format_profit(value)}</div>' if value < 0 else ''}</div>
                  </div>
                  <div class="bar-label">{item['quarter'][5:]}</div>
                </div>
                """
            )

        year_labels = []
        for year, indices in year_marks.items():
            year_labels.append(
                f'<div class="year-band" style="grid-column:{indices[0] + 1} / {indices[-1] + 2};">{year}</div>'
            )

        cards.append(
            f"""
            <article class="chart-card" id="{stock_anchor(row['ts_code'])}">
              <div class="chart-head">
                <div>
                  <div class="chart-title">{stock_index}. {row['name']}{disclosure_note}</div>
                  <div class="chart-sub">{row['ts_code']} · 每柱标注实际报告期</div>
                </div>
                <div class="chart-score">总分 {format_num(row['total_score'])}</div>
              </div>
              <div class="factor-score-block">
                <div class="factor-score-heading">六因子计入总分的小分（得分 / 满分）</div>
                <div class="factor-score-strip">
                  {factor_score_item(f'三年预测净利润同比（今年{weight_pct(profit_year["fy1"])}／明年{weight_pct(profit_year["fy2"])}／后年{weight_pct(profit_year["fy3"])}）', row.get('weighted_analyst_profit_yoy'), maxima['analyst_profit_yoy'])}
                  {factor_score_item(f'三年PEG（今年{weight_pct(peg_year["fy1"])}／明年{weight_pct(peg_year["fy2"])}／后年{weight_pct(peg_year["fy3"])}）', row.get('weighted_peg'), maxima['peg'])}
                  {factor_score_item('净利润环比', row.get('weighted_qoq_growth'), maxima['qoq_growth'])}
                  {factor_score_item(f'连续增长不中断季度数<span class="quarter-count-note">（当前{row.get("consecutive_profit_growth_quarters", "-")}季）</span>', row.get('weighted_consecutive_profit_growth_quarters'), maxima['consecutive_profit_growth_quarters'])}
                  {factor_score_item(f'预测变化率（今年{weight_pct(revision_year["fy1"])}／明年{weight_pct(revision_year["fy2"])}）', row.get('weighted_analyst_revision'), maxima['analyst_net_profit_revision_change'])}
                  {factor_score_item(f'ROE／毛利率／净利率（上年{weight_pct(quality_year["prior"])}／当年{weight_pct(quality_year["current"])}）', row.get('weighted_quality'), maxima['quality'])}
                </div>
              </div>
              <div class="profit-split">
                <section class="annual-panel">
                  <h3>单位：亿元｜年度归母净利润（A实际／E一致预期）</h3>
                  <div class="annual-bars">{render_annual_bars(annual)}</div>
                </section>
                <section class="quarter-panel">
                  <h3>单位：亿元｜单季度归母净利润（实际值）</h3>
                  <div class="year-row">{''.join(year_labels)}</div>
                  <div class="bar-grid">{''.join(bars)}</div>
                </section>
              </div>
              <div class="quality-strip">
                {comparison_metric_card('毛利率', web.get('gross_margin_mid_prior'), web.get('gross_margin_mid_current'), prior_mid_year, current_mid_year, 'GROSSPROFITMARGIN')}
                {comparison_metric_card('净利率', web.get('net_margin_mid_prior'), web.get('net_margin_mid_current'), prior_mid_year, current_mid_year, 'NETPROFITMARGIN')}
                {comparison_metric_card('ROE', web.get('roe_mid_prior'), web.get('roe_mid_current'), prior_mid_year, current_mid_year, 'ROE')}
              </div>
            </article>
            """
        )
    return "\n".join(cards)


def render_annual_bars(rows):
    clean = [x for x in rows if x.get("net_profit") not in (None, "")]
    max_abs = max([abs(float(x["net_profit"])) for x in clean] + [1.0])
    result = []
    for i, item in enumerate(rows):
        value = float(item["net_profit"]) if item.get("net_profit") not in (None, "") else 0
        height = max(abs(value) / max_abs * 84, 5) if value else 0
        is_actual = "实际" in str(item.get("data_type", ""))
        label = "A实际归母" if is_actual else "E一致预期"
        positive_class = "actual" if is_actual else "forecast"
        result.append(f'<div class="annual-slot"><div class="bar-plot"><div class="positive-half">{f"<div class=\"bar-value\">{format_profit(value)}</div><div class=\"bar positive {positive_class}\" style=\"height:{height:.1f}px\"></div>" if value > 0 else ""}</div><div class="negative-half">{f"<div class=\"bar negative\" style=\"height:{height:.1f}px\"></div><div class=\"bar-value negative-value\">{format_profit(value)}</div>" if value < 0 else ""}</div></div><div class="bar-label">{item["year"]}<br>{label}</div></div>')
    return "".join(result)


def build_html(top_rows, ranked_rows, quarterly_rows):
    config = json.loads(WEIGHTS_JSON.read_text(encoding="utf-8"))
    weights = config["weights"]
    profit_year = config["profit_yoy_year_weights"]
    peg_year = config["peg_year_weights"]
    revision_year = config["revision_year_weights"]
    quality_year = config["quality_year_weights"]
    quality_sub = config["quality_subweights"]
    qoq_rules = config["qoq_rules"]
    analyst_points = factor_points(config, "analyst_profit_yoy")
    peg_points = factor_points(config, "peg")
    qoq_points = factor_points(config, "qoq_growth")
    consecutive_growth_points = factor_points(config, "consecutive_profit_growth_quarters")
    revision_points = factor_points(config, "analyst_net_profit_revision_change")
    quality_points = factor_points(config, "quality")
    total_points = sum(float(value) for value in weights.values()) * 100
    quarterly_map = group_quarterly_rows(quarterly_rows)
    annual_map = group_annual_rows(read_rows(ANNUAL_NET_PROFIT_CSV))
    web_rows = read_rows(WEB_TABLE_CSV)
    web_map = {row["ts_code"]: row for row in web_rows}
    disclosure_rows = read_rows(INTERIM_DISCLOSURE_CSV) if INTERIM_DISCLOSURE_CSV.exists() else []
    disclosure_map = {row["ts_code"]: row for row in disclosure_rows}
    trade_date = ranked_rows[0].get("trade_date", "-") if ranked_rows else "-"
    status = update_status(trade_date)
    status_ok = status.get("status") == "success"
    status_class = "status-success" if status_ok else "status-failed"
    status_text = "更新成功" if status_ok else "更新失败"
    last_success_at = compact_timestamp(status.get("last_success_at") or "尚无成功记录")
    last_attempt_at = compact_timestamp(status.get("last_attempt_at") or "-")
    data_source = status.get("data_source") or "Wind API（WindPy）"
    status_error = status.get("error") or ""
    previous_date, previous_rows = previous_week_snapshot(trade_date)
    consensus_year = ranked_rows[0].get("consensus_year", "-") if ranked_rows else "-"
    report_period = ranked_rows[0].get("report_period", "-") if ranked_rows else "-"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>仙童多因子选股看板</title>
  <style>
    :root {{
      --bg: #f4efe6;
      --panel: rgba(255,255,255,0.82);
      --ink: #1d2433;
      --muted: #6a7283;
      --accent: #c85c38;
      --accent-2: #1d5c63;
      --line: rgba(29,36,51,0.12);
      --shadow: 0 20px 50px rgba(65, 45, 28, 0.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Microsoft YaHei", "微软雅黑", sans-serif;
      font-size: 12px;
      color: #000;
      font-weight: 700;
      background:
        radial-gradient(circle at top left, rgba(200,92,56,0.18), transparent 32%),
        radial-gradient(circle at top right, rgba(29,92,99,0.14), transparent 24%),
        linear-gradient(180deg, #f6f1e7 0%, #efe6d7 100%);
    }}
    .shell {{
      width: min(1180px, calc(100% - 32px));
      margin: 14px auto 40px;
    }}
    .hero {{
      background: linear-gradient(135deg, rgba(29,36,51,0.96), rgba(29,92,99,0.9));
      color: white;
      border-radius: 22px;
      padding: 17px 20px;
      box-shadow: var(--shadow);
      position: relative;
      overflow: hidden;
    }}
    .hero::after {{
      content: "";
      position: absolute;
      inset: auto -60px -80px auto;
      width: 240px;
      height: 240px;
      border-radius: 50%;
      background: rgba(255,255,255,0.08);
    }}
    .eyebrow {{
      font-size: 11px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: rgba(255,255,255,0.72);
    }}
    .hero-header {{ display:flex; align-items:center; justify-content:space-between; gap:16px; position:relative; z-index:1; }}
    .return-home {{ display:inline-flex; align-items:center; justify-content:center; flex:0 0 auto; padding:7px 13px; border:1px solid rgba(255,255,255,.42); border-radius:999px; background:rgba(255,255,255,.1); color:#fff; text-decoration:none; font-size:12px; font-weight:900; }}
    .return-home:hover {{ background:#fff; color:#173f43; }}
    .return-home:focus-visible {{ outline:3px solid #f0b35b; outline-offset:3px; }}
    h1 {{
      margin: 4px 0 5px;
      font-size: clamp(27px, 4vw, 38px);
      line-height: 1.04;
    }}
    .sub {{
      max-width: none;
      color: rgba(255,255,255,0.82);
      font-size: 12px;
      line-height: 1.45;
      margin:0;
    }}
    .audit-banner {{ margin:9px 0 0; padding:8px 11px; border-radius:11px; background:#fff3cd; color:#6b4f00; border:1px solid #e6c65c; font-size:11px; line-height:1.45; font-weight:750; position:relative; z-index:1; }}
    .audit-banner summary {{ cursor:pointer; font-weight:900; }}
    .audit-detail {{ margin-top:6px; }}
    .factor-diff-note {{ margin-top:12px !important; padding:12px 14px; border-left:4px solid var(--accent-2); background:rgba(29,92,99,.07); color:#000 !important; font-size:12px; font-weight:800; }}
    .update-strip {{ margin-top:10px; display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:7px; position:relative; z-index:1; }}
    .update-item {{ min-height:58px; padding:7px 9px; border:1px solid rgba(255,255,255,.18); background:rgba(255,255,255,.09); }}
    .update-label {{ color:rgba(255,255,255,.72); font-size:10px; font-weight:900; }}
    .update-value {{ margin-top:3px; color:#fff; font-size:12px; font-weight:900; line-height:1.25; }}
    .update-value small {{ font-size:9px; }}
    .status-pill {{ display:inline-flex; align-items:center; gap:5px; padding:3px 7px; color:#fff; font-size:11px; font-weight:900; }}
    .status-pill::before {{ content:""; width:7px; height:7px; border-radius:50%; background:currentColor; }}
    .status-success {{ background:#08783a; }}
    .status-failed {{ background:#c5162e; }}
    .snapshot-link {{ color:#fff; text-decoration:underline; text-underline-offset:3px; }}
    .status-error {{ margin-top:10px; padding:10px 12px; background:rgba(197,22,46,.18); border:1px solid rgba(255,120,130,.45); color:#fff; font-size:12px; font-weight:800; position:relative; z-index:1; }}
    .snapshot-table {{ min-width:560px; }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      margin-top: 9px;
    }}
    .stat {{
      background: rgba(255,255,255,0.08);
      border: 1px solid rgba(255,255,255,0.09);
      border-radius: 12px;
      padding: 8px 11px;
      backdrop-filter: blur(12px);
    }}
    .stat-label {{ font-size: 10px; color: rgba(255,255,255,0.7); }}
    .stat-value {{ font-size: 21px; font-weight: 800; margin-top: 2px; }}
    .section {{
      margin-top: 22px;
      background: var(--panel);
      border: 1px solid rgba(255,255,255,0.55);
      border-radius: 26px;
      padding: 22px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(14px);
    }}
    .section h2 {{
      margin: 0 0 6px;
      font-size: 24px;
    }}
    .section p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.7;
    }}
    .dashboard-tabs {{
      position: sticky;
      top: 0;
      z-index: 30;
      display: flex;
      gap: 8px;
      margin-top: 16px;
      padding: 10px;
      overflow-x: auto;
      background: rgba(246,241,231,.96);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 10px 28px rgba(65,45,28,.12);
      backdrop-filter: blur(12px);
    }}
    .dashboard-tab {{
      flex: 0 0 auto;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 10px 16px;
      background: #fff;
      color: #000;
      font-family: "Microsoft YaHei", "微软雅黑", sans-serif;
      font-size: 14px;
      font-weight: 900;
      cursor: pointer;
    }}
    .dashboard-tab[aria-selected="true"] {{
      color: #fff;
      background: #155e63;
      border-color: #155e63;
    }}
    .tab-panel[hidden] {{ display: none !important; }}
    .tab-panel {{ scroll-margin-top: 78px; }}
    .cards {{
      margin-top: 18px;
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 14px;
    }}
    .pick-card {{
      background: linear-gradient(180deg, rgba(255,255,255,0.96), rgba(247,241,233,0.92));
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 18px;
    }}
    .pick-rank {{
      width: 44px;
      height: 44px;
      border-radius: 50%;
      display: grid;
      place-items: center;
      font-size: 12px;
      font-weight: 700;
      color: white;
      background: linear-gradient(135deg, var(--accent), #da8d4c);
    }}
    .pick-name {{ margin-top: 14px; font-size: 22px; font-weight: 700; }}
    .pick-code {{ margin-top: 4px; color: var(--muted); font-size: 13px; }}
    .stock-code-link {{ color:#155e63; text-decoration:underline; text-decoration-thickness:1.5px; text-underline-offset:3px; font-weight:900; }}
    .stock-code-link:hover, .stock-code-link:focus {{ color:#c5162e; }}
    .chart-card {{ scroll-margin-top:18px; }}
    .pick-score {{ margin-top: 14px; font-size: 34px; font-weight: 800; color: var(--accent-2); }}
    .pick-meta {{ margin-top: 6px; font-size: 13px; color: var(--muted); }}
    .table-wrap {{
      margin-top: 18px;
      overflow-x: auto;
      border-radius: 18px;
      border: 1px solid var(--line);
    }}
    .ranking-table-wrap {{
      max-height: 72vh;
      overflow: auto;
    }}
    .ranking-table-wrap thead th {{
      position: sticky;
      top: 0;
      z-index: 5;
      background: #f3f3f1;
      box-shadow: 0 1px 0 var(--line);
    }}
    table {{
      width: 100%;
      min-width: 940px;
      table-layout: auto;
      border-collapse: collapse;
      background: rgba(255,255,255,0.78);
    }}
    th, td {{
      padding: 4px 14px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      font-size: 16px;
      line-height: 1.08;
      color: #000;
      font-weight: 700;
      writing-mode: horizontal-tb;
      text-orientation: mixed;
      white-space: nowrap;
      word-break: keep-all;
      overflow-wrap: normal;
    }}
    td small {{ color: var(--muted); font-size: 12px; line-height:1.05; white-space: nowrap; }}
    th {{
      background: rgba(29,36,51,0.04);
      color: #000;
      font-family: "Microsoft YaHei", "微软雅黑", sans-serif;
      font-weight: 900;
      font-size: 12pt;
      line-height: 1.2;
    }}
    tr:hover td {{
      background: rgba(200,92,56,0.04);
    }}
    .stock-name {{ white-space: nowrap; writing-mode: horizontal-tb; min-width: 92px; font-weight: 900; }}
    .industry-cell {{ min-width:300px; white-space:nowrap !important; word-break:keep-all !important; writing-mode:horizontal-tb !important; }}
    .spark {{ width:120px; min-width:120px; height:38px; display:flex; flex-direction:column; align-items:center; gap:0; font-weight:900; }}
    .spark svg {{ width:120px; height:24px; display:block; }}
    .spark span {{ font-size:12px; line-height:1; font-weight:900; }}
    .spark-empty {{ white-space:nowrap; color:#000; }}
    .section h2, .section p, .pick-name, .pick-code, .pick-meta,
    .stat-label, .note, .quality-label, .quality-period, .bar-label,
    td small {{ color:#000; font-weight:800; }}
    .note-grid {{
      margin-top: 18px;
      display: grid;
      grid-template-columns: 1.3fr 1fr;
      gap: 14px;
    }}
    .chart-grid {{
      margin-top: 18px;
      display: grid;
      grid-template-columns: 1fr;
      gap: 16px;
    }}
    .profit-split {{ display:grid; grid-template-columns:minmax(280px,.72fr) minmax(0,2fr); gap:18px; }}
    .factor-score-block {{ margin:0 0 14px; }}
    .factor-score-heading {{ margin-bottom:7px; color:#000; font-size:12px; font-weight:900; }}
    .factor-score-strip {{ display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:8px; }}
    .factor-score-item {{ min-width:0; padding:8px 10px; border:1px solid var(--line); background:rgba(29,92,99,.055); }}
    .factor-score-label {{ color:#000; font-family:"Microsoft YaHei","微软雅黑",sans-serif; font-size:12px; font-weight:900; line-height:1.25; }}
    .factor-score-label .quarter-count-note {{ color:#d71920; }}
    .factor-score-value {{ margin-top:4px; color:var(--accent-2); font-size:18px; font-weight:900; line-height:1; }}
    .factor-score-value span {{ color:var(--muted); font-size:12px; font-weight:900; }}
    .annual-panel,.quarter-panel {{ border:1px solid var(--line); border-radius:16px; padding:14px; overflow-x:auto; }}
    .annual-panel h3,.quarter-panel h3 {{ margin:0 0 12px; font-size:14px; color:var(--muted); }}
    .annual-bars {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; align-items:end; min-height:210px; }}
    .annual-slot {{ display:flex; flex-direction:column; align-items:stretch; justify-content:flex-end; min-height:280px; }}
    .bar.forecast {{ background:linear-gradient(180deg,#1d5c63,#63a6aa); }}
    .bar.actual {{ background:#c5162e; }}
    .quality-strip {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin-top:14px; }}
    .quality-metric {{ border:1px solid var(--line); border-radius:14px; padding:12px 14px; background:rgba(29,92,99,.05); min-width:0; }}
    .quality-label {{ color:#000; font-size:12px; font-weight:900; }}
    .quality-comparison {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin-top:8px; }}
    .compare-slot {{ min-width:0; text-align:center; }}
    .compare-plot {{ height:132px; display:grid; grid-template-rows:66px 66px; position:relative; }}
    .compare-positive,.compare-negative {{ display:flex; flex-direction:column; align-items:center; min-width:0; }}
    .compare-positive {{ justify-content:flex-end; }}
    .compare-negative {{ justify-content:flex-start; border-top:2px solid #455468; }}
    .compare-bar {{ width:min(44px,62%); border-radius:0; flex:0 0 auto; }}
    .compare-bar.positive {{ background:#d71920; }}
    .compare-bar.negative {{ background:#168447; }}
    .compare-value,.compare-missing {{ color:#000; font-size:12px; font-weight:900; line-height:1.25; white-space:nowrap; }}
    .negative-text {{ color:#087b39; }}
    .compare-missing {{ margin-bottom:8px; color:var(--muted); }}
    .compare-year {{ margin-top:6px; color:#000; font-size:12px; font-weight:900; white-space:nowrap; }}
    .quality-period {{ margin-top:9px; color:var(--muted); font-size:11px; font-weight:800; line-height:1.45; }}
    .chart-card {{
      border-radius: 22px;
      padding: 18px;
      background: linear-gradient(180deg, rgba(255,255,255,0.96), rgba(248,244,238,0.88));
      border: 1px solid var(--line);
    }}
    .chart-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
      margin-bottom: 14px;
    }}
    .chart-title {{
      font-size: 20px;
      font-weight: 700;
    }}
    .chart-disclosure {{ margin-left:8px; color:#8a5a28; font-size:14px; font-weight:700; }}
    .chart-sub, .chart-score {{
      color: var(--muted);
      font-size: 13px;
      margin-top: 4px;
    }}
    .year-row {{
      display: grid;
      grid-template-columns: repeat(20, minmax(0, 1fr));
      gap: 6px;
      margin-bottom: 8px;
    }}
    .year-band {{
      text-align: center;
      font-size: 12px;
      color: var(--accent-2);
      background: rgba(29,92,99,0.08);
      border-radius: 999px;
      padding: 4px 0;
    }}
    .bar-grid {{
      display: grid;
      grid-template-columns: repeat(20, minmax(0, 1fr));
      gap: 6px;
      align-items: stretch;
      min-height: 280px;
      padding-top: 8px;
    }}
    .bar-slot {{
      position: relative;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: flex-start;
      min-height: 270px;
    }}
    .year-divider {{
      position: absolute;
      left: -4px;
      top: 6px;
      bottom: 18px;
      width: 1px;
      background: rgba(29,36,51,0.12);
    }}
    .bar-value {{
      font-size: 12px;
      font-weight: 800;
      color: var(--muted);
      margin-bottom: 6px;
      white-space: nowrap;
    }}
    .bar-plot {{
      height: 230px;
      display: grid;
      grid-template-rows: 1fr 1fr;
      position: relative;
    }}
    .bar-plot::after {{
      content: "";
      position: absolute;
      left: 0;
      right: 0;
      top: 50%;
      height: 2px;
      background: #475569;
      z-index: 2;
    }}
    .positive-half, .negative-half {{
      display: flex;
      flex-direction: column;
      align-items: center;
      min-height: 0;
      overflow: hidden;
    }}
    .positive-half {{ justify-content: flex-end; }}
    .negative-half {{ justify-content: flex-start; }}
    .bar {{
      width: 100%;
      max-width: 24px;
      box-shadow: inset 0 -10px 18px rgba(0,0,0,0.08);
    }}
    .bar.positive {{ border-radius: 0; background: #d7191c; }}
    .bar.negative {{ border-radius: 0; background: #159447; }}
    .negative-value {{ color: #08783a; margin-top: 6px; margin-bottom: 0; }}
    .bar-label {{
      margin-top: 8px;
      font-size: 12px;
      color: var(--muted);
    }}
    .note {{
      border-radius: 20px;
      padding: 18px;
      background: linear-gradient(180deg, rgba(255,255,255,0.94), rgba(248,244,238,0.86));
      border: 1px solid var(--line);
    }}
    .note-title {{ font-size: 16px; font-weight: 700; margin-bottom: 8px; }}
    .note ul {{
      margin: 0;
      padding-left: 18px;
      color: var(--muted);
      line-height: 1.8;
    }}
    .score-rule-card {{ grid-column:1 / -1; }}
    .rule-table-wrap {{ margin-top:12px; overflow-x:auto; border:1px solid var(--line); }}
    .rule-table {{ min-width:1050px; background:rgba(255,255,255,.68); }}
    .rule-table th,.rule-table td {{ padding:10px 12px; font-size:12px; line-height:1.55; vertical-align:top; white-space:normal; word-break:normal; }}
    .rule-table th {{ position:static; background:#e9eeec; color:#000; font-size:12px; font-weight:900; }}
    .rule-table td:first-child,.rule-table td:nth-child(2) {{ white-space:nowrap; font-weight:900; }}
    .factor-ranking-grid {{ display:grid; grid-template-columns:1fr; gap:18px; }}
    .factor-ranking-card {{ border:1px solid var(--line); border-radius:18px; padding:16px; background:rgba(255,255,255,.74); }}
    .factor-ranking-card h3 {{ margin:0 0 4px; font-size:18px; }}
    .factor-ranking-card p {{ margin:0 0 10px; color:var(--muted); }}
    .factor-table-wrap {{ max-height:560px; overflow:auto; border:1px solid var(--line); border-radius:12px; }}
    .factor-table {{ min-width:760px; }}
    .factor-table thead th {{ position:sticky; top:0; z-index:2; }}
    .formula-box {{ margin-top:14px; padding:14px 16px; border-left:4px solid var(--accent-2); background:rgba(29,92,99,.07); color:#000; font-size:12px; font-weight:800; line-height:1.8; }}
    .quick-links {{ display:flex; gap:7px; flex-wrap:wrap; margin:8px 0 0; }}
    .quick-link {{ display:inline-block; padding:6px 11px; border-radius:999px; color:#fff; text-decoration:none; font-size:10px; font-weight:900; }}
    .quick-link.etf {{ background:#7d3f19; }}
    .quick-link.frequency {{ background:#155e63; }}
    .quick-link:hover {{ filter:brightness(1.08); }}
    .quick-link:focus-visible {{ outline:3px solid #f0b35b; outline-offset:3px; }}
    @media (max-width: 980px) {{
      .stats, .cards, .note-grid {{ grid-template-columns: 1fr 1fr; }}
      .update-strip {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
    }}
    @media (max-width: 720px) {{
      .shell {{ width: min(100% - 20px, 1180px); }}
      .stats, .cards, .note-grid {{ grid-template-columns: 1fr; }}
      .update-strip {{ grid-template-columns:1fr; }}
      .hero, .section {{ border-radius: 22px; padding: 18px; }}
      .chart-head {{ flex-direction: column; }}
      .profit-split,.quality-strip,.factor-score-strip {{ grid-template-columns:1fr; }}
      .bar-grid, .year-row {{
        min-width: 920px;
      }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="hero-header"><div class="eyebrow">AI Selection Dashboard</div><a class="return-home" href="/" aria-label="返回统一网页主页">← 返回网页</a></div>
      <h1>仙童多因子选股看板</h1>
      <p class="sub">候选池按 README 生成：AI相关赛道（半导体、通信设备、电子元件及其设备）股票，与A股股票型ETF年内涨幅前50的每日篮子前10合并去重；剔除ST、*ST、退市及非标准A股后，再按六因子排序。行情日：{trade_date}；一致预期年度：{consensus_year}；财务报告期：{report_period}。</p>
      <div class="quick-links" aria-label="看板明细入口">
        <a class="quick-link etf" href="etf_holdings_pool.html">查看ETF前50名的前十大重仓去重股票 →</a>
        <a class="quick-link frequency" href="top20_frequency.html">查看近6个月高频入选股票前10名 →</a>
        <a class="quick-link frequency" href="top10_backtest.html">查看Top10周频回测 →</a>
        <a class="quick-link frequency" href="top5_backtest.html">查看Top5周频回测 →</a>
        <a class="quick-link frequency" href="top5_backtest_2024.html">查看2024年Top5周频回测 →</a>
      </div>
      <div class="update-strip">
        <div class="update-item"><div class="update-label">数据日期</div><div class="update-value">{escape(str(status.get('data_date') or trade_date))}</div></div>
        <div class="update-item"><div class="update-label">最近更新成功</div><div class="update-value">{escape(str(last_success_at))}</div></div>
        <div class="update-item"><div class="update-label">数据来源</div><div class="update-value">{escape(str(data_source))}</div></div>
        <div class="update-item"><div class="update-label">更新状态</div><div class="update-value"><span class="status-pill {status_class}">{status_text}</span><br><small>最近尝试：{escape(str(last_attempt_at))}</small></div></div>
        <div class="update-item"><div class="update-label">上一更新日快照</div><div class="update-value"><a class="snapshot-link" href="#previous-week-snapshot">{escape(previous_date)}</a></div></div>
      </div>
      {f'<div class="status-error">失败原因：{escape(status_error)}</div>' if not status_ok and status_error else ''}
      <details class="audit-banner"><summary>审计口径：Wind实际单季度净利润；负值或缺失剔除（点击展开）</summary><div class="audit-detail">排名因子使用Wind QFA_NET_PROFIT_IS（合并利润表实际单季度净利润）；柱状图独立使用Wind QFA_NP_BELONGTO_PARCOMSH（实际单季度归母净利润）。最新已披露的排名口径单季度净利润必须严格大于0，且今年、明年、后年至少有一个分析师预测增长率有效；三年预测全部缺失的公司不予关注、不参与排名。最近五个连续自然季度必须逐季严格增长，才触发利润环比因子满分。</div></details>
      <div class="stats">
        <div class="stat"><div class="stat-label">候选池</div><div class="stat-value">{len(ranked_rows)}</div></div>
        <div class="stat"><div class="stat-label">自动更新</div><div class="stat-value">每日21:00</div></div>
        <div class="stat"><div class="stat-label">重点跟踪</div><div class="stat-value">10只</div></div>
        <div class="stat"><div class="stat-label">核心因子</div><div class="stat-value">6项</div></div>
      </div>
    </section>

    <nav class="dashboard-tabs" role="tablist" aria-label="看板内容切换">
      <button class="dashboard-tab" type="button" role="tab" aria-selected="true" aria-controls="panel-overview" data-tab="overview">当前概览</button>
      <button class="dashboard-tab" type="button" role="tab" aria-selected="false" aria-controls="panel-ranking" data-tab="ranking">Top10表格</button>
      <button class="dashboard-tab" type="button" role="tab" aria-selected="false" aria-controls="panel-factor-rankings" data-tab="factor-rankings">六因子排名</button>
      <button class="dashboard-tab" type="button" role="tab" aria-selected="false" aria-controls="panel-charts" data-tab="charts">个股图表</button>
      <button class="dashboard-tab" type="button" role="tab" aria-selected="false" aria-controls="panel-rules" data-tab="rules">因子说明</button>
      <button class="dashboard-tab" type="button" role="tab" aria-selected="false" aria-controls="previous-week-snapshot" data-tab="history">历史快照</button>
      <a class="dashboard-tab" id="weights-page-link" href="http://127.0.0.1:8765/weights" title="打开本机因子权重设置页">权重设置</a>
      <button class="dashboard-tab" type="button" role="tab" aria-selected="false" aria-controls="panel-ml-forecast" data-tab="ml-forecast">机器学习盈利预测</button>
    </nav>

    <section class="section tab-panel" id="previous-week-snapshot" data-panel="history" role="tabpanel" hidden>
      <h2>上一更新日历史快照</h2>
      <p>快照日期：{escape(previous_date)}。这是当前数据日期之前最近一个已保存的有效Top10，不会被当日排名覆盖。</p>
      <div class="table-wrap"><table class="snapshot-table"><thead><tr><th>上一更新日排名</th><th>证券代码</th><th>证券简称</th><th>当日总分</th></tr></thead><tbody>{render_previous_snapshot(previous_rows)}</tbody></table></div>
    </section>

    <section class="section tab-panel" id="panel-overview" data-panel="overview" role="tabpanel">
      <h2>当前前五</h2>
      <p>总分为策略综合评分（当前满分{compact_number(total_points)}分），不是收益率。利润环比因子使用Wind合并利润表实际单季度净利润：本季度严格大于上一季度才算环比增长；最近五个连续自然季度均逐季增长时，该因子取得当前权重对应的满分{compact_number(qoq_points)}分。</p>
      <div class="cards">
        {render_top_cards(top_rows)}
      </div>
    </section>

    <section class="section tab-panel" id="panel-ranking" data-panel="ranking" role="tabpanel" hidden>
      <h2>Top10排名</h2>
      <p>Wind 数据日期：行情及一致预期截至 {trade_date}；FY1 对应 {consensus_year} 年度；财务指标报告期为 {report_period}。</p>
      <p class="factor-diff-note"><strong>预测差值与因子权重：</strong>表格四列历史预测值及相邻差值使用2026-08-22快照。实际进入模型的是{weight_pct(weights['analyst_net_profit_revision_change'])}、范围-{compact_number(revision_points)}至+{compact_number(revision_points)}分的“分析师预测变化率”因子，同样以2026-08-22为基准；绝对差值小于0.5个百分点统一按0处理。今年占{weight_pct(revision_year['fy1'])}、明年占{weight_pct(revision_year['fy2'])}。表格中的净利率和ROE为展示列，不直接替代质量因子的Wind单季度字段。</p>
      <div class="table-wrap ranking-table-wrap">
        <table>
          <thead>
            <tr>
              <th>序号</th><th>证券代码</th><th>证券简称</th><th>股价今年年初至今走势<br>前复权收盘价｜图内标注年度涨幅</th><th>总市值<br>{trade_date}｜亿元</th>
              <th>2026E预测净利润增长率</th><th>2026-08-22预测值</th><th>差值</th>
              <th>2027E预测净利润增长率</th><th>2026-08-22预测值</th><th>差值</th><th>2028E预测净利润增长率</th>
              <th>{int(str(consensus_year)) - 1}A归母净利润<br>Wind年报实际｜亿元</th><th>2026E预测净利润<br>亿元</th><th>2026-08-22预测值<br>亿元</th><th>差值<br>亿元</th>
              <th>2027E预测净利润<br>亿元</th><th>2026-08-22预测值<br>亿元</th><th>差值<br>亿元</th><th>2028E预测净利润<br>亿元</th>
              <th>2026E PE</th><th>2027E PE</th><th>2028E PE</th><th>2026E PEG</th><th>2027E PEG</th><th>2028E PEG</th>
              <th>申万行业分类2021版<br>一级 → 二级 → 三级｜{trade_date}</th><th>{int(str(report_period)[:4]) - 1}中报毛利率</th><th>{str(report_period)[:4]}中报毛利率</th><th>{int(str(report_period)[:4]) - 1}年中报净利率</th><th>{str(report_period)[:4]}年中报净利率</th><th>{int(str(report_period)[:4]) - 1}年中报ROE</th><th>{str(report_period)[:4]}年中报ROE</th>
            </tr>
          </thead>
          <tbody>
            {render_table(web_rows, annual_map)}
          </tbody>
        </table>
      </div>
    </section>

    <section class="section tab-panel" id="panel-factor-rankings" data-panel="factor-rankings" role="tabpanel" hidden>
      <h2>候选池六因子独立排名</h2>
      <p>每张表只按一个因子的标准分排序，不受其他四个因子权重影响；综合排名和综合总分用于对照。</p>
      <div class="factor-ranking-grid">{render_factor_rankings(ranked_rows)}</div>
    </section>

    <section class="section tab-panel" id="panel-charts" data-panel="charts" role="tabpanel" hidden>
      <h2>全部候选股票最近20期单季度归母净利润</h2>
      <p>单位：亿元。右侧每根柱仅代表柱下方对应季度的实际单季度归属于母公司股东的净利润（Wind QFA_NP_BELONGTO_PARCOMSH，rptType=1），不是扣非归母净利润、不是年初至今累计值，也不是滚动十二个月值；正数红色向上，负数绿色向下。左侧年度柱包含上一年Wind年报实际归母净利润（A，字段NP_BELONGTO_PARCOMSH），以及今年、明年、后年的Wind一致预期归母净利润（E）。柱状图展示口径与当前{weight_pct(weights['qoq_growth'])}的环比排名因子口径分开，不会因本图切换而改变排名。</p>
      <div class="table-wrap">
        <div class="chart-grid">
          {render_quarterly_charts(ranked_rows, quarterly_map, annual_map, web_map, disclosure_map, config)}
        </div>
      </div>
    </section>

    <section class="section tab-panel" id="panel-rules" data-panel="rules" role="tabpanel" hidden>
      <h2>当前规则摘要</h2>
      <div class="note-grid">
        <article class="note">
          <div class="note-title">候选池</div>
          <ul>
            <li>AI相关赛道：半导体、通信设备、电子元件及其设备。</li>
            <li>A股 ETF 年内涨幅前 50 名对应前十大持仓。</li>
            <li>两部分合并后去重，形成初选池。</li>
            <li>Wind今年、明年、后年分析师预测增长率全部缺失的公司不予关注，直接剔除排名。</li>
          </ul>
        </article>
        <article class="note score-rule-card">
          <div class="note-title">多因子构成详细说明：六个一级因子、子因子与系数占比</div>
          <div class="rule-table-wrap">
            <table class="rule-table">
              <thead><tr><th>一级因子</th><th>总分占比</th><th>子因子及内部系数</th><th>原始指标计算</th><th>标准化与计分方向</th></tr></thead>
              <tbody>
                <tr><td>分析师预测净利润同比</td><td>{weight_pct(weights['analyst_profit_yoy'])}（满分{compact_number(analyst_points)}分）</td><td>今年{weight_pct(profit_year['fy1'])}、明年{weight_pct(profit_year['fy2'])}、后年{weight_pct(profit_year['fy3'])}<br>对应总分贡献上限：{compact_number(analyst_points * profit_year['fy1'])}、{compact_number(analyst_points * profit_year['fy2'])}、{compact_number(analyst_points * profit_year['fy3'])}分</td><td>今年使用Wind一致预期同比；明年＝（明年预测净利润－今年预测净利润）÷|今年预测净利润|；后年同理。</td><td>三个年度分别先做3倍MAD去极值，再在当周合格候选池内做正向min-max标准化，增长率越高得分越高，最后按年度子权重合成。</td></tr>
                <tr><td>PEG</td><td>{weight_pct(weights['peg'])}（满分{compact_number(peg_points)}分）</td><td>今年{weight_pct(peg_year['fy1'])}、明年{weight_pct(peg_year['fy2'])}、后年{weight_pct(peg_year['fy3'])}<br>对应总分贡献上限：{compact_number(peg_points * peg_year['fy1'])}、{compact_number(peg_points * peg_year['fy2'])}、{compact_number(peg_points * peg_year['fy3'])}分</td><td>各年度PE＝总市值÷该年度一致预期净利润；各年度PEG＝该年度PE÷该年度预测净利润增长率。明年或后年PEG任意一个小于0时，整个PEG因子直接记0分。</td><td>有效正PEG分别先做3倍MAD去极值，再反向min-max标准化；PEG越小得分越高，最后按年度子权重合成。负PEG不进入横截面分布，并触发整项0分规则。</td></tr>
                <tr><td>实际单季度净利润环比</td><td>{weight_pct(weights['qoq_growth'])}（满分{compact_number(qoq_points)}分）</td><td>基础环比标准分；连续五个季度环比增长触发规则；未满分系数{weight_pct(qoq_rules['non_full_positive_ratio'])}</td><td>Wind QFA_NET_PROFIT_IS、rptType=1，合并利润表实际单季度净利润；环比＝（本季度利润－上季度利润）÷|上季度利润|。上季度为0时该环比为空值。</td><td>基础环比增长率先做3倍MAD去极值，再正向min-max标准化。最近连续5个季度环比均严格增长（需要6个连续自然季度数据）时直接得{compact_number(qoq_points * qoq_rules['quarter_cycle_positive_full_ratio'])}分；否则基础标准分×{compact_number(qoq_rules['non_full_positive_ratio'])}×{compact_number(qoq_points)}，最高{compact_number(qoq_points * qoq_rules['non_full_positive_ratio'])}分。</td></tr>
                <tr><td>最近一期连续增长不中断季度数</td><td>{weight_pct(weights['consecutive_profit_growth_quarters'])}（满分{compact_number(consecutive_growth_points)}分）</td><td>从最新一期向前统计利润严格连续增长的次数</td><td>若最新季度利润未高于上一季度记0；连续1次增长记1，连续4次增长（5个季度逐季增长）记4，依此类推。</td><td>原始季度数不做MAD去极值，直接在当期合格候选池内做正向min-max标准化；连续增长季度数越多，得分越高。</td></tr>
                <tr><td>分析师预测变化率</td><td>{weight_pct(weights['analyst_net_profit_revision_change'])}（范围-{compact_number(revision_points)}至+{compact_number(revision_points)}分）</td><td>今年{weight_pct(revision_year['fy1'])}、明年{weight_pct(revision_year['fy2'])}<br>各年贡献范围：-{compact_number(revision_points * revision_year['fy1'])}至+{compact_number(revision_points * revision_year['fy1'])}分；-{compact_number(revision_points * revision_year['fy2'])}至+{compact_number(revision_points * revision_year['fy2'])}分</td><td>每个年度分别计算：当前Wind一致预期净利润增长率－2026-08-22快照中的同年度预测净利润增长率。差值单位为百分点。</td><td>先设置零分区间：| 差值 |&lt;0.5个百分点时记0；再做3倍MAD去极值。其余上修与下修分开定向标准化：最强上修为+1，最强下修为-1。正值得正分，负值得负分，零值或没有预测得0分，最后按年度子权重合成。</td></tr>
                <tr><td>质量综合</td><td>{weight_pct(weights['quality'])}（满分{compact_number(quality_points)}分）</td><td>上年同期{weight_pct(quality_year['prior'])}、当年同期{weight_pct(quality_year['current'])}；每年内部：单季度ROE {weight_pct(quality_sub['roe'])}、单季度毛利率{weight_pct(quality_sub['gross_margin'])}、单季度净利率{weight_pct(quality_sub['net_margin'])}<br>两年贡献上限：{compact_number(quality_points * quality_year['prior'])}、{compact_number(quality_points * quality_year['current'])}分</td><td>代码实际字段：QFA_ROE、QFA_GROSSPROFITMARGIN、QFA_NETPROFITMARGIN；取上年与当年同一报告期，rptType=1。</td><td>六个“年度×指标”分别先做3倍MAD去极值，再正向min-max标准化；之后先按质量指标子权重、后按年度子权重合成。任何指标缺失时，该指标标准分直接记0，不使用中性分。</td></tr>
              </tbody>
            </table>
          </div>
          <div class="formula-box">
            <div>总权重结构：预测净利润同比{weight_pct(weights['analyst_profit_yoy'])}＋PEG {weight_pct(weights['peg'])}＋实际单季度净利润环比{weight_pct(weights['qoq_growth'])}＋连续增长季度数{weight_pct(weights['consecutive_profit_growth_quarters'])}＋预测变化率{weight_pct(weights['analyst_net_profit_revision_change'])}＋质量改善{weight_pct(weights['quality'])}＝{compact_number(total_points)}分。</div>
            <div>统一去极值：所有连续型原始因子先按候选池横截面做3倍MAD缩尾，边界＝中位数±3×1.4826×MAD；超出边界的值截到边界，缺失值保持缺失。MAD为0时不截断。</div>
            <div>去极值后标准化：正向S＝(x*－候选池最小值*)÷(候选池最大值*－候选池最小值*)；反向S＝1－正向标准化结果。</div>
            <div>同比小分＝{compact_number(analyst_points)}×(今年标准分×{compact_number(profit_year['fy1'])}＋明年标准分×{compact_number(profit_year['fy2'])}＋后年标准分×{compact_number(profit_year['fy3'])})。</div>
            <div>PEG小分：若明年PEG&lt;0或后年PEG&lt;0，直接记0分；否则＝{compact_number(peg_points)}×(今年反向标准分×{compact_number(peg_year['fy1'])}＋明年反向标准分×{compact_number(peg_year['fy2'])}＋后年反向标准分×{compact_number(peg_year['fy3'])})。</div>
            <div>环比小分＝连续5个季度环比均增长（6个连续季度数据）时{compact_number(qoq_points * qoq_rules['quarter_cycle_positive_full_ratio'])}分；否则＝{compact_number(qoq_points)}×基础环比标准分×{compact_number(qoq_rules['non_full_positive_ratio'])}。</div>
            <div>连续增长季度数小分＝{compact_number(consecutive_growth_points)}×未经去极值的连续增长季度数正向min-max标准分。</div>
            <div>变化率小分＝{compact_number(revision_points)}×(今年有符号标准分×{compact_number(revision_year['fy1'])}＋明年有符号标准分×{compact_number(revision_year['fy2'])})；上修为正、下修为负、无预测为0。</div>
            <div>上年质量S＝上年同期ROE标准分×{compact_number(quality_sub['roe'])}＋上年同期毛利率标准分×{compact_number(quality_sub['gross_margin'])}＋上年同期净利率标准分×{compact_number(quality_sub['net_margin'])}；当年质量S同理。</div>
            <div>质量小分＝{compact_number(quality_points)}×(上年质量S×{compact_number(quality_year['prior'])}＋当年质量S×{compact_number(quality_year['current'])})；任一原始指标缺失时，该指标标准分记0。</div>
            <div>最终总分＝同比小分＋PEG小分＋环比小分＋连续增长季度数小分＋变化率小分＋质量综合小分，当前满分{compact_number(total_points)}分。</div>
            <div>缺失处理：分析师预测变化率缺失时，对应年度子项为0分；质量因子的六个“年度×指标”缺失时对应指标为0分；其他连续指标缺失时仍按0.5中性值处理，PEG反向处理后缺失仍为0.5。</div>
            <div>硬性资格：最新已披露实际单季度净利润必须有数据且严格大于0；同时今年、明年、后年至少有一个Wind分析师预测增长率有效。单季度净利润不合格，或三年预测全部缺失，均不参与排名。所有标准化均使用通过资格筛选后的候选池横截面，每天更新时重新计算。</div>
          </div>
        </article>
      </div>
    </section>

    <section class="section tab-panel" id="panel-ml-forecast" data-panel="ml-forecast" role="tabpanel" hidden>
      <h2>基于机器学习的盈利预测模型</h2>
      <p>内容正在研发中</p>
    </section>
  </main>
  <script>
    (() => {{
      const weightsLink = document.getElementById('weights-page-link');
      if (weightsLink && /^https?:$/.test(location.protocol)) {{
        weightsLink.href = location.origin + '/weights';
      }}
      const tabs = [...document.querySelectorAll('.dashboard-tab')];
      const panels = [...document.querySelectorAll('.tab-panel')];

      function activate(name, updateHash = false) {{
        const panel = panels.find(item => item.dataset.panel === name);
        if (!panel) return;
        tabs.forEach(tab => tab.setAttribute('aria-selected', String(tab.dataset.tab === name)));
        panels.forEach(item => {{ item.hidden = item !== panel; }});
        if (updateHash) history.replaceState(null, '', '#' + panel.id);
      }}

      function revealHashTarget() {{
        if (!location.hash) return false;
        const target = document.querySelector(location.hash);
        if (!target) return false;
        const panel = target.closest('.tab-panel');
        if (panel) activate(panel.dataset.panel);
        requestAnimationFrame(() => target.scrollIntoView({{ behavior: 'smooth', block: 'start' }}));
        return true;
      }}

      tabs.forEach((tab, index) => {{
        tab.addEventListener('click', () => activate(tab.dataset.tab, true));
        tab.addEventListener('keydown', event => {{
          if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length : (index - 1 + tabs.length) % tabs.length;
          tabs[next].focus();
          tabs[next].click();
        }});
      }});

      document.addEventListener('click', event => {{
        const link = event.target.closest('a[href^="#"]');
        if (!link) return;
        const target = document.querySelector(link.getAttribute('href'));
        const panel = target && target.closest('.tab-panel');
        if (panel) activate(panel.dataset.panel);
      }});
      window.addEventListener('hashchange', revealHashTarget);
      if (!revealHashTarget()) activate('overview');
    }})();
  </script>
</body>
</html>"""


def main():
    top_rows = read_rows(TOP_PICKS_CSV)
    ranked_rows = read_rows(RANKED_CSV)
    quarterly_rows = read_rows(QUARTERLY_NET_PROFIT_CSV)
    html = build_html(top_rows, ranked_rows, quarterly_rows)
    DASHBOARD_HTML.write_text(html, encoding="utf-8")
    print(DASHBOARD_HTML)


if __name__ == "__main__":
    main()
