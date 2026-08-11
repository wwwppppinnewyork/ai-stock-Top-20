import csv
from pathlib import Path


WORKDIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = WORKDIR / "output"
DATA_DIR = WORKDIR / "data"
TOP_PICKS_CSV = OUTPUT_DIR / "top_picks.csv"
RANKED_CSV = OUTPUT_DIR / "ranked_stocks.csv"
QUARTERLY_NET_PROFIT_CSV = DATA_DIR / "sample_quarterly_net_profit.csv"
DASHBOARD_HTML = OUTPUT_DIR / "dashboard.html"


def read_rows(path: Path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def format_pct(value: str) -> str:
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "-"


def format_num(value: str) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "-"


def format_profit(value) -> str:
    try:
        return f"{float(value):.1f}亿"
    except (TypeError, ValueError):
        return "-"


def render_top_cards(rows):
    cards = []
    for row in rows[:5]:
        cards.append(
            f"""
            <article class="pick-card">
              <div class="pick-rank">#{row['rank']}</div>
              <div class="pick-name">{row['name']}</div>
              <div class="pick-code">{row['ts_code']}</div>
              <div class="pick-score">{format_num(row['total_score'])}</div>
              <div class="pick-meta">净利润环比 {format_pct(row['net_profit_qoq_growth'])}</div>
              <div class="pick-meta">净利润上修 {format_pct(row['analyst_net_profit_revision_change'])}</div>
            </article>
            """
        )
    return "\n".join(cards)


def render_table(rows):
    trs = []
    for row in rows[:20]:
        trs.append(
            f"""
            <tr>
              <td>{row['rank']}</td>
              <td>{row['name']}</td>
              <td>{row['ts_code']}</td>
              <td>{format_num(row['total_score'])}</td>
              <td>{format_pct(row['analyst_profit_yoy'])}</td>
              <td>{format_num(row['peg'])}</td>
              <td>{format_pct(row['net_profit_qoq_growth'])}</td>
              <td>{format_pct(row['analyst_net_profit_revision_change'])}</td>
              <td>{format_pct(row['net_profit_yoy'])}</td>
              <td>{row['source']}</td>
            </tr>
            """
        )
    return "\n".join(trs)


def group_quarterly_rows(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(row["ts_code"], []).append(row)
    for items in grouped.values():
        items.sort(key=lambda x: x["quarter"])
    return grouped


def render_quarterly_charts(ranked_rows, quarterly_map):
    cards = []
    for row in ranked_rows[:20]:
        series = quarterly_map.get(row["ts_code"], [])[-20:]
        if not series:
            continue
        values = [float(item["net_profit"]) for item in series]
        max_abs = max(max(abs(v) for v in values), 1.0)
        year_marks = {}
        for idx, item in enumerate(series):
            year = item["quarter"][:4]
            year_marks.setdefault(year, []).append(idx)

        bars = []
        for idx, item in enumerate(series):
            value = float(item["net_profit"])
            height = max(abs(value) / max_abs * 120, 6)
            negative_class = " neg" if value < 0 else ""
            year = item["quarter"][:4]
            is_year_start = year_marks[year][0] == idx
            divider = '<span class="year-divider"></span>' if is_year_start and idx != 0 else ""
            bars.append(
                f"""
                <div class="bar-slot">
                  {divider}
                  <div class="bar-value">{format_profit(value)}</div>
                  <div class="bar{negative_class}" style="height:{height:.1f}px"></div>
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
            <article class="chart-card">
              <div class="chart-head">
                <div>
                  <div class="chart-title">{row['name']}</div>
                  <div class="chart-sub">{row['ts_code']} · 最近20个季度净利润</div>
                </div>
                <div class="chart-score">总分 {format_num(row['total_score'])}</div>
              </div>
              <div class="year-row">
                {''.join(year_labels)}
              </div>
              <div class="bar-grid">
                {''.join(bars)}
              </div>
            </article>
            """
        )
    return "\n".join(cards)


def build_html(top_rows, ranked_rows, quarterly_rows):
    quarterly_map = group_quarterly_rows(quarterly_rows)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI选股周频看板</title>
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
      font-family: "PingFang SC", "Noto Sans SC", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(200,92,56,0.18), transparent 32%),
        radial-gradient(circle at top right, rgba(29,92,99,0.14), transparent 24%),
        linear-gradient(180deg, #f6f1e7 0%, #efe6d7 100%);
    }}
    .shell {{
      width: min(1180px, calc(100% - 32px));
      margin: 24px auto 48px;
    }}
    .hero {{
      background: linear-gradient(135deg, rgba(29,36,51,0.96), rgba(29,92,99,0.9));
      color: white;
      border-radius: 28px;
      padding: 28px;
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
      font-size: 13px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: rgba(255,255,255,0.72);
    }}
    h1 {{
      margin: 10px 0 8px;
      font-size: clamp(30px, 6vw, 52px);
      line-height: 1.04;
    }}
    .sub {{
      max-width: 820px;
      color: rgba(255,255,255,0.82);
      font-size: 15px;
      line-height: 1.75;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin-top: 22px;
    }}
    .stat {{
      background: rgba(255,255,255,0.08);
      border: 1px solid rgba(255,255,255,0.09);
      border-radius: 18px;
      padding: 14px 16px;
      backdrop-filter: blur(12px);
    }}
    .stat-label {{ font-size: 12px; color: rgba(255,255,255,0.7); }}
    .stat-value {{ font-size: 28px; font-weight: 700; margin-top: 6px; }}
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
      font-size: 16px;
      font-weight: 700;
      color: white;
      background: linear-gradient(135deg, var(--accent), #da8d4c);
    }}
    .pick-name {{ margin-top: 14px; font-size: 22px; font-weight: 700; }}
    .pick-code {{ margin-top: 4px; color: var(--muted); font-size: 13px; }}
    .pick-score {{ margin-top: 14px; font-size: 34px; font-weight: 800; color: var(--accent-2); }}
    .pick-meta {{ margin-top: 6px; font-size: 13px; color: var(--muted); }}
    .table-wrap {{
      margin-top: 18px;
      overflow-x: auto;
      border-radius: 18px;
      border: 1px solid var(--line);
    }}
    table {{
      width: 100%;
      min-width: 940px;
      border-collapse: collapse;
      background: rgba(255,255,255,0.78);
    }}
    th, td {{
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      font-size: 14px;
    }}
    th {{
      background: rgba(29,36,51,0.04);
      color: var(--muted);
      font-weight: 600;
    }}
    tr:hover td {{
      background: rgba(200,92,56,0.04);
    }}
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
      align-items: end;
      min-height: 180px;
      padding-top: 8px;
    }}
    .bar-slot {{
      position: relative;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: flex-end;
      min-height: 168px;
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
      font-size: 11px;
      color: var(--muted);
      margin-bottom: 6px;
      white-space: nowrap;
    }}
    .bar {{
      width: 100%;
      max-width: 24px;
      border-radius: 8px 8px 0 0;
      background: linear-gradient(180deg, var(--accent), #de9b57);
      box-shadow: inset 0 -10px 18px rgba(0,0,0,0.08);
    }}
    .bar.neg {{
      background: linear-gradient(180deg, #7a8ea1, #a8b9c9);
    }}
    .bar-label {{
      margin-top: 8px;
      font-size: 11px;
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
    @media (max-width: 980px) {{
      .stats, .cards, .note-grid {{ grid-template-columns: 1fr 1fr; }}
    }}
    @media (max-width: 720px) {{
      .shell {{ width: min(100% - 20px, 1180px); }}
      .stats, .cards, .note-grid {{ grid-template-columns: 1fr; }}
      .hero, .section {{ border-radius: 22px; padding: 18px; }}
      .chart-head {{ flex-direction: column; }}
      .bar-grid, .year-row {{
        min-width: 920px;
      }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="eyebrow">AI Selection Dashboard</div>
      <h1>AI选股周频看板</h1>
      <p class="sub">候选池由 AI 相关赛道股票与 A 股 ETF 年内涨幅前 50 名的前十大持仓合并去重，再按五因子周频重排。当前样例页展示的是前 20 名与前 5 名重点观察标的。</p>
      <div class="stats">
        <div class="stat"><div class="stat-label">候选池</div><div class="stat-value">{len(ranked_rows)}</div></div>
        <div class="stat"><div class="stat-label">周频更新</div><div class="stat-value">1次</div></div>
        <div class="stat"><div class="stat-label">重点跟踪</div><div class="stat-value">20只</div></div>
        <div class="stat"><div class="stat-label">核心因子</div><div class="stat-value">5项</div></div>
      </div>
    </section>

    <section class="section">
      <h2>本周前五</h2>
      <p>重点看总分、净利润环比增速和分析师净利润预测上修。后续接入 Wind 后，这一页可以直接自动刷新。</p>
      <div class="cards">
        {render_top_cards(top_rows)}
      </div>
    </section>

    <section class="section">
      <h2>前二十排名</h2>
      <p>当前展示的是样例数据。实际运行时会用你每周刷新后的真实候选池与五因子数据重算。</p>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>排名</th>
              <th>股票</th>
              <th>代码</th>
              <th>总分</th>
              <th>盈利同比</th>
              <th>PEG</th>
              <th>净利润环比</th>
              <th>净利润上修</th>
              <th>净利润同比</th>
              <th>来源</th>
            </tr>
          </thead>
          <tbody>
            {render_table(ranked_rows)}
          </tbody>
        </table>
      </div>
    </section>

    <section class="section">
      <h2>最近20个季度净利润柱状图</h2>
      <p>每只股票按年份分段展示最近 20 个季度净利润。当前是样例数据，接入 Wind 后可以替换成真实季度利润序列。</p>
      <div class="table-wrap">
        <div class="chart-grid">
          {render_quarterly_charts(ranked_rows, quarterly_map)}
        </div>
      </div>
    </section>

    <section class="section">
      <h2>当前规则摘要</h2>
      <div class="note-grid">
        <article class="note">
          <div class="note-title">候选池</div>
          <ul>
            <li>AI 相关赛道股票：半导体、计算机、通信。</li>
            <li>A股 ETF 年内涨幅前 50 名对应前十大持仓。</li>
            <li>两部分合并后去重，形成初选池。</li>
          </ul>
        </article>
        <article class="note">
          <div class="note-title">打分规则</div>
          <ul>
            <li>盈利同比 15%，PEG 20%，净利润环比 50%。</li>
            <li>分析师净利润预测上修 10%。</li>
            <li>ROE、毛利率、净利润同比合计 5%。</li>
            <li>1/2/3/4/1 五季度净利润环比都大于 0 时，环比因子满分。</li>
          </ul>
        </article>
      </div>
    </section>
  </main>
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
