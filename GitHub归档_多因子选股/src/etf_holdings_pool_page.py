"""Build a deduplicated audit page for holdings from the selected Top-50 ETFs."""
import csv
import json
import math
from collections import defaultdict
from datetime import date
from html import escape
from pathlib import Path

from WindPy import w


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data" / "wind" / "etf_top_holdings.csv"
ETF_TOP50_SOURCE = ROOT / "data" / "wind" / "etf_top50.csv"
OUTPUT_HTML = ROOT / "output" / "etf_holdings_pool.html"
OUTPUT_CSV = ROOT / "output" / "etf_top_holdings_dedup.csv"


def read_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def as_int(value, default=999):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def valid_number(value):
    return value not in (None, "") and not (isinstance(value, float) and math.isnan(value))


def load_price_data(rows, trade_date):
    """Load forward-adjusted closes from the year's first trade day through the data date."""
    codes = [row["ts_code"] for row in rows]
    if not codes:
        return {}
    w.start()
    end_date = date.fromisoformat(trade_date)
    base = w.tdaysoffset(0, date(end_date.year, 1, 1), "")
    if base.ErrorCode:
        raise RuntimeError(f"Wind年初交易日查询失败：{base.ErrorCode}")
    start_date = base.Data[0][0]
    result = w.wsd(",".join(codes), "close", start_date, end_date, "PriceAdj=F")
    if result.ErrorCode:
        raise RuntimeError(f"Wind ETF持仓股行情查询失败：{result.ErrorCode}")
    dates = [item.strftime("%Y-%m-%d") for item in result.Times]
    payload = {}
    for code, values in zip(result.Codes, result.Data):
        points = [
            {"date": day, "close": round(float(value), 4)}
            for day, value in zip(dates, values)
            if valid_number(value)
        ]
        payload[code] = points
    return payload


def aggregate(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["ts_code"]].append(row)
    result = []
    for code, items in grouped.items():
        items.sort(key=lambda row: (as_int(row.get("etf_rank_50")), as_int(row.get("holding_rank_10")), row.get("etf_code", "")))
        etf_details = []
        for row in items:
            etf_details.append(
                f'{row.get("etf_code", "")} {row.get("etf_name", "")} '
                f'（ETF涨幅第{as_int(row.get("etf_rank_50"), 0)}名｜重仓第{as_int(row.get("holding_rank_10"), 0)}名｜{as_float(row.get("holding_weight_pct")):.2f}%）'
            )
        result.append({
            "ts_code": code,
            "name": items[0].get("name", ""),
            "etf_count": len({row.get("etf_code", "") for row in items}),
            "best_etf_rank": min(as_int(row.get("etf_rank_50")) for row in items),
            "best_holding_rank": min(as_int(row.get("holding_rank_10")) for row in items),
            "max_holding_weight_pct": round(max(as_float(row.get("holding_weight_pct")) for row in items), 4),
            "trade_date": items[0].get("trade_date", ""),
            "holding_report_period": items[0].get("holding_report_period", ""),
            "etf_details": "；".join(etf_details),
        })
    result.sort(key=lambda row: (-row["etf_count"], row["best_etf_rank"], row["best_holding_rank"], row["ts_code"]))
    return result


def write_csv(rows):
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def mini_trend(points):
    """Render a compact YTD price trend beside the stock name."""
    if len(points) < 2:
        return '<span class="mini-trend-empty">暂无走势</span>'
    values = [float(point["close"]) for point in points]
    low, high = min(values), max(values)
    span = high - low or 1.0
    coordinates = " ".join(
        f"{index * 116 / (len(values) - 1):.1f},{4 + (high - value) * 28 / span:.1f}"
        for index, value in enumerate(values)
    )
    ytd = (values[-1] / values[0] - 1) * 100 if values[0] else 0.0
    color = "#168447" if ytd < 0 else "#c5162e"
    return (
        f'<span class="mini-trend" title="年初至今涨幅 {ytd:.2f}%">'
        f'<svg viewBox="0 0 116 36" role="img" aria-label="年初至今走势"><polyline points="{coordinates}" fill="none" stroke="{color}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        f'<b style="color:{color}">{ytd:.1f}%</b></span>'
    )


def build_html(source_rows, rows, price_data, etf_rows):
    trade_date = source_rows[0].get("trade_date", "-") if source_rows else "-"
    holding_period = source_rows[0].get("holding_report_period", "-") if source_rows else "-"
    etf_count = len({row.get("etf_code", "") for row in source_rows})
    body = "".join(
        f'''<tr data-search="{escape((row['ts_code'] + ' ' + row['name'] + ' ' + row['etf_details']).lower())}">
          <td data-sort-value="{index}">{index}</td><td data-sort-value="{escape(row['ts_code'])}"><a class="stock-select" href="#stock-{escape(row['ts_code'].replace('.', '-'))}" data-code="{escape(row['ts_code'])}">{escape(row['ts_code'])}</a></td><td data-sort-value="{escape(row['name'])}"><span class="stock-name-with-trend"><span>{escape(row['name'])}</span>{mini_trend(price_data.get(row['ts_code'], []))}</span></td>
          <td class="strong" data-sort-value="{row['etf_count']}">{row['etf_count']}</td><td data-sort-value="{row['best_etf_rank']}">{row['best_etf_rank']}</td><td data-sort-value="{row['best_holding_rank']}">{row['best_holding_rank']}</td>
          <td data-sort-value="{row['max_holding_weight_pct']}">{row['max_holding_weight_pct']:.2f}%</td>
        </tr>'''
        for index, row in enumerate(rows, 1)
    )
    etf_body = "".join(
        f'''<tr>
          <td data-sort-value="{index}">{index}</td>
          <td data-sort-value="{escape(row.get('etf_code', ''))}">{escape(row.get('etf_code', ''))}</td>
          <td data-sort-value="{escape(row.get('etf_name', ''))}">{escape(row.get('etf_name', ''))}</td>
          <td class="return-value {'negative-return' if as_float(row.get('ytd_return')) < 0 else 'positive-return'}" data-sort-value="{as_float(row.get('ytd_return'))}">{as_float(row.get('ytd_return')):.2f}%</td>
          <td data-sort-value="{escape(row.get('invest_type', ''))}">{escape(row.get('invest_type', ''))}</td>
          <td data-sort-value="{escape(row.get('trade_date', ''))}">{escape(row.get('trade_date', ''))}</td>
        </tr>'''
        for index, row in enumerate(etf_rows, 1)
    )
    chart_payload = {}
    for row in rows:
        points = price_data.get(row["ts_code"], [])
        ytd = None
        if len(points) >= 2 and points[0]["close"]:
            ytd = round((points[-1]["close"] / points[0]["close"] - 1) * 100, 4)
        chart_payload[row["ts_code"]] = {
            "code": row["ts_code"],
            "name": row["name"],
            "etfCount": row["etf_count"],
            "bestEtfRank": row["best_etf_rank"],
            "bestHoldingRank": row["best_holding_rank"],
            "maxWeight": row["max_holding_weight_pct"],
            "details": row["etf_details"],
            "points": points,
            "ytd": ytd,
        }
    payload_json = json.dumps(chart_payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ETF前十大重仓去重股票池</title><style>
:root{{--ink:#172033;--muted:#687386;--line:#d8ddd9;--teal:#155e63;--red:#c5162e;--green:#168447}}*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(145deg,#edf1ef,#f8f4ed);font-family:"Microsoft YaHei","微软雅黑",sans-serif;color:var(--ink);font-weight:700}}main{{width:min(1500px,calc(100% - 32px));margin:28px auto}}.hero,.panel{{background:rgba(255,255,255,.92);border:1px solid var(--line);border-radius:24px;padding:24px;box-shadow:0 15px 40px rgba(31,41,55,.08)}}.panel{{margin-top:18px}}a{{color:var(--teal);font-weight:900;text-decoration:none}}h1{{margin:12px 0 8px;font-size:34px}}p{{margin:6px 0;color:var(--muted);line-height:1.7}}.stats{{display:flex;gap:12px;flex-wrap:wrap;margin-top:16px}}.stat{{min-width:170px;padding:12px 15px;background:#edf3f1;border:1px solid var(--line);border-radius:14px}}.stat b{{display:block;margin-top:4px;font-size:22px;color:var(--teal)}}.pool-tabs{{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0 0}}.pool-tab{{padding:11px 17px;border:1px solid var(--line);border-radius:999px;background:#fff;color:var(--teal);font:inherit;font-weight:900;cursor:pointer}}.pool-tab.active{{background:var(--teal);color:#fff;border-color:var(--teal)}}.pool-tab:focus-visible{{outline:3px solid #e9a23b;outline-offset:3px}}.viewer{{margin-top:18px}}.viewer-head{{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;flex-wrap:wrap}}.viewer-title{{font-size:25px;font-weight:900}}.viewer-code{{color:var(--muted);font-size:14px;margin-top:4px}}.viewer-metrics{{display:flex;gap:10px;flex-wrap:wrap}}.metric{{min-width:140px;padding:10px 13px;border:1px solid var(--line);border-radius:12px;background:#f4f6f4}}.metric small{{display:block;color:var(--muted);margin-bottom:3px}}.metric b{{font-size:19px}}.chart-wrap{{position:relative;height:290px;margin-top:14px;border:1px solid var(--line);border-radius:16px;background:#fff;padding:10px}}#priceChart{{width:100%;height:100%;display:block}}.chart-empty{{position:absolute;inset:0;display:grid;place-items:center;color:var(--muted)}}.source-detail{{margin-top:12px;padding:12px 14px;background:#f5f7f5;border-radius:12px;color:var(--muted);line-height:1.7}}.toolbar{{display:flex;gap:12px;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap}}input{{width:min(420px,100%);padding:11px 14px;border:1px solid var(--line);border-radius:12px;font:inherit}}.table-wrap{{max-height:56vh;overflow:auto;border:1px solid var(--line);border-radius:16px}}table{{width:100%;min-width:900px;border-collapse:collapse}}#etfTable{{min-width:850px}}th,td{{padding:9px 12px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:middle;white-space:nowrap}}th{{position:sticky;top:0;z-index:2;background:#eef2f0;font-weight:900;padding:0}}.sort-button{{display:flex;width:100%;min-height:42px;align-items:center;justify-content:space-between;gap:8px;padding:9px 12px;border:0;background:transparent;color:var(--ink);font:inherit;font-weight:900;text-align:left;cursor:pointer;white-space:nowrap}}.sort-button:hover{{background:#dfe9e6}}.sort-button:focus-visible{{outline:3px solid #e9a23b;outline-offset:-3px}}.sort-indicator{{color:var(--teal);font-size:14px;min-width:14px;text-align:center}}th[aria-sort="ascending"] .sort-button,th[aria-sort="descending"] .sort-button{{background:#dcebea;color:#0c4d51}}.strong{{color:var(--red);font-size:17px;font-weight:900}}.stock-select{{display:inline-block;padding:4px 7px;border-radius:7px}}.stock-select:hover,.stock-select.active{{background:#dcebea;color:#0c4d51}}.stock-select:focus-visible{{outline:3px solid #e9a23b;outline-offset:2px}}.stock-name-with-trend{{display:flex;align-items:center;justify-content:space-between;gap:14px;min-width:245px}}.mini-trend{{display:inline-flex;align-items:center;gap:6px}}.mini-trend svg{{display:block;width:116px;height:36px}}.mini-trend b{{min-width:47px;text-align:right;font-size:11px}}.mini-trend-empty{{color:var(--muted);font-size:11px}}.return-value{{font-weight:900}}.positive-return{{color:var(--red)}}.negative-return{{color:var(--green)}}[hidden]{{display:none!important}}@media(max-width:720px){{main{{width:min(100% - 16px,1500px);margin:10px auto}}.hero,.panel{{padding:16px;border-radius:18px}}.chart-wrap{{height:240px}}}}
</style></head><body><main><section class="hero"><a href="dashboard.html">← 返回仙童多因子选股看板</a><h1>ETF前十大重仓去重股票池</h1><p>规则：A股ETF年初至数据日涨幅前50名，每只ETF取Wind定期报告前十大重仓股，合并后按证券代码去重。一只股票如被多只ETF重复持有，只在主表出现1次，并展开列出所有来源ETF。</p><div class="stats"><div class="stat">数据日<b>{trade_date}</b></div><div class="stat">持仓报告期<b>{holding_period}</b></div><div class="stat">入选ETF<b>{etf_count}只</b></div><div class="stat">原始持仓记录<b>{len(source_rows)}条</b></div><div class="stat">去重后股票<b>{len(rows)}只</b></div></div></section>
<div class="pool-tabs" role="tablist" aria-label="ETF股票池查看切换"><button class="pool-tab active" type="button" role="tab" aria-selected="true" data-pool-tab="stocks">去重重仓股票（{len(rows)}只）</button><button class="pool-tab" type="button" role="tab" aria-selected="false" data-pool-tab="etfs">入选ETF（{len(etf_rows)}只）</button></div>
<div id="pool-stocks" class="pool-panel" role="tabpanel">
<section class="panel viewer" aria-live="polite"><div class="viewer-head"><div><div class="viewer-title" id="stockName">-</div><div class="viewer-code" id="stockCode">-</div></div><div class="viewer-metrics"><div class="metric"><small>年初至今涨幅</small><b id="stockYtd">-</b></div><div class="metric"><small>最新收盘价</small><b id="lastClose">-</b></div><div class="metric"><small>被入选ETF持有</small><b id="etfCount">-</b></div><div class="metric"><small>最高持仓权重</small><b id="maxWeight">-</b></div></div></div><div class="chart-wrap"><canvas id="priceChart" aria-label="股票年初至今收盘价走势图"></canvas><div class="chart-empty" id="chartEmpty" hidden>暂无行情数据</div></div><div class="source-detail" id="sourceDetail">-</div></section>
<section class="panel"><div class="toolbar"><strong>点击证券代码切换图表；点击任一表头切换升序／降序</strong><input id="search" type="search" placeholder="搜索股票代码、简称或ETF名称" aria-label="搜索去重股票池"></div><div class="table-wrap"><table id="stockTable" data-sortable data-sort-column="3" data-sort-direction="descending"><thead><tr>
<th aria-sort="none"><button class="sort-button" type="button" data-column="0" data-type="number">序号<span class="sort-indicator">↕</span></button></th>
<th aria-sort="none"><button class="sort-button" type="button" data-column="1" data-type="text">证券代码（可点击）<span class="sort-indicator">↕</span></button></th>
<th aria-sort="none"><button class="sort-button" type="button" data-column="2" data-type="text">证券简称<span class="sort-indicator">↕</span></button></th>
<th aria-sort="descending"><button class="sort-button" type="button" data-column="3" data-type="number">被多少只入选ETF持有<span class="sort-indicator">▼</span></button></th>
<th aria-sort="none"><button class="sort-button" type="button" data-column="4" data-type="number">最佳ETF涨幅名次<span class="sort-indicator">↕</span></button></th>
<th aria-sort="none"><button class="sort-button" type="button" data-column="5" data-type="number">最高重仓名次<span class="sort-indicator">↕</span></button></th>
<th aria-sort="none"><button class="sort-button" type="button" data-column="6" data-type="number">最高持仓权重<span class="sort-indicator">↕</span></button></th>
</tr></thead><tbody>{body}</tbody></table></div></section></div>
<div id="pool-etfs" class="pool-panel" role="tabpanel" hidden><section class="panel"><div class="toolbar"><strong>当日入选的50只ETF，默认按年初至今涨幅名次排列</strong></div><div class="table-wrap"><table id="etfTable" data-sortable data-sort-column="0" data-sort-direction="ascending"><thead><tr>
<th aria-sort="ascending"><button class="sort-button" type="button" data-column="0" data-type="number">涨幅名次<span class="sort-indicator">▲</span></button></th>
<th aria-sort="none"><button class="sort-button" type="button" data-column="1" data-type="text">ETF代码<span class="sort-indicator">↕</span></button></th>
<th aria-sort="none"><button class="sort-button" type="button" data-column="2" data-type="text">ETF名称<span class="sort-indicator">↕</span></button></th>
<th aria-sort="none"><button class="sort-button" type="button" data-column="3" data-type="number">年初至今涨幅<span class="sort-indicator">↕</span></button></th>
<th aria-sort="none"><button class="sort-button" type="button" data-column="4" data-type="text">投资类型<span class="sort-indicator">↕</span></button></th>
<th aria-sort="none"><button class="sort-button" type="button" data-column="5" data-type="text">数据日<span class="sort-indicator">↕</span></button></th>
</tr></thead><tbody>{etf_body}</tbody></table></div></section></div></main>
<script id="stockData" type="application/json">{payload_json}</script><script>
const data=JSON.parse(document.getElementById('stockData').textContent);const input=document.getElementById('search');const stockTable=document.getElementById('stockTable');const tableRows=[...stockTable.querySelectorAll('tbody tr')];const links=[...document.querySelectorAll('.stock-select')];const canvas=document.getElementById('priceChart');const empty=document.getElementById('chartEmpty');let selectedCode='';
function fmt(value,digits=2){{return value==null||Number.isNaN(Number(value))?'-':Number(value).toFixed(digits)}}
function drawChart(stock){{const points=stock.points||[];const rect=canvas.getBoundingClientRect();const dpr=window.devicePixelRatio||1;canvas.width=Math.max(1,Math.round(rect.width*dpr));canvas.height=Math.max(1,Math.round(rect.height*dpr));const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,rect.width,rect.height);if(points.length<2){{empty.hidden=false;return}}empty.hidden=true;const pad={{l:58,r:18,t:22,b:36}},w=rect.width-pad.l-pad.r,h=rect.height-pad.t-pad.b;const values=points.map(p=>p.close),min=Math.min(...values),max=Math.max(...values),span=max-min||1;ctx.font='700 12px Microsoft YaHei';ctx.fillStyle='#687386';ctx.strokeStyle='#d8ddd9';ctx.lineWidth=1;for(let i=0;i<4;i++){{const y=pad.t+h*i/3;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(pad.l+w,y);ctx.stroke();const value=max-span*i/3;ctx.fillText(value.toFixed(2),5,y+4)}}const color=stock.ytd!=null&&stock.ytd<0?'#168447':'#c5162e';ctx.strokeStyle=color;ctx.lineWidth=3;ctx.lineJoin='round';ctx.lineCap='round';ctx.beginPath();points.forEach((p,i)=>{{const x=pad.l+w*i/(points.length-1),y=pad.t+(max-p.close)/span*h;i?ctx.lineTo(x,y):ctx.moveTo(x,y)}});ctx.stroke();ctx.fillStyle='#687386';ctx.textAlign='left';ctx.fillText(points[0].date,pad.l,rect.height-10);ctx.textAlign='right';ctx.fillText(points[points.length-1].date,pad.l+w,rect.height-10)}}
function selectStock(code,updateHash=true){{const stock=data[code];if(!stock)return;selectedCode=code;document.getElementById('stockName').textContent=stock.name;document.getElementById('stockCode').textContent=stock.code+' · 年初至数据日前复权收盘价';const ytd=document.getElementById('stockYtd');ytd.textContent=stock.ytd==null?'-':fmt(stock.ytd)+'%';ytd.style.color=stock.ytd!=null&&stock.ytd<0?'#168447':'#c5162e';const last=stock.points.length?stock.points[stock.points.length-1].close:null;document.getElementById('lastClose').textContent=last==null?'-':fmt(last);document.getElementById('etfCount').textContent=stock.etfCount+'只';document.getElementById('maxWeight').textContent=fmt(stock.maxWeight)+'%';document.getElementById('sourceDetail').textContent='来源ETF：'+stock.details;links.forEach(link=>link.classList.toggle('active',link.dataset.code===code));drawChart(stock);if(updateHash)history.replaceState(null,'','#stock-'+code.replace('.','-'))}}
function setupSortable(table){{const tbody=table.querySelector('tbody'),rows=[...tbody.querySelectorAll('tr')],buttons=[...table.querySelectorAll('.sort-button')];let sortColumn=Number(table.dataset.sortColumn),sortDirection=table.dataset.sortDirection;function sortRows(column,type,direction){{const multiplier=direction==='ascending'?1:-1;rows.sort((a,b)=>{{const av=a.cells[column].dataset.sortValue||'',bv=b.cells[column].dataset.sortValue||'';if(type==='number'){{const an=Number(av),bn=Number(bv);return ((Number.isNaN(an)?Infinity:an)-(Number.isNaN(bn)?Infinity:bn))*multiplier}}return av.localeCompare(bv,'zh-CN',{{numeric:true,sensitivity:'base'}})*multiplier}}).forEach(row=>tbody.appendChild(row));sortColumn=column;sortDirection=direction;buttons.forEach(button=>{{const th=button.closest('th'),active=Number(button.dataset.column)===column;th.setAttribute('aria-sort',active?direction:'none');button.querySelector('.sort-indicator').textContent=active?(direction==='ascending'?'▲':'▼'):'↕'}})}}buttons.forEach(button=>button.addEventListener('click',()=>{{const column=Number(button.dataset.column),direction=sortColumn===column&&sortDirection==='ascending'?'descending':'ascending';sortRows(column,button.dataset.type,direction)}}))}}
function activatePoolTab(name){{document.querySelectorAll('.pool-tab').forEach(button=>{{const active=button.dataset.poolTab===name;button.classList.toggle('active',active);button.setAttribute('aria-selected',String(active))}});document.querySelectorAll('.pool-panel').forEach(panel=>panel.hidden=panel.id!=='pool-'+name);if(name==='stocks'&&selectedCode)requestAnimationFrame(()=>drawChart(data[selectedCode]))}}
links.forEach(link=>link.addEventListener('click',event=>{{event.preventDefault();selectStock(link.dataset.code)}}));document.querySelectorAll('table[data-sortable]').forEach(setupSortable);document.querySelectorAll('.pool-tab').forEach(button=>button.addEventListener('click',()=>activatePoolTab(button.dataset.poolTab)));input.addEventListener('input',()=>{{const q=input.value.trim().toLowerCase();tableRows.forEach(row=>row.hidden=q&&!row.dataset.search.includes(q));}});window.addEventListener('resize',()=>{{if(selectedCode)drawChart(data[selectedCode])}});const requested=location.hash.startsWith('#stock-')?location.hash.slice(7).replace(/-(SH|SZ)$/i,'.$1').toUpperCase():'';selectStock(data[requested]?requested:Object.keys(data)[0],false);
</script></body></html>'''


def main():
    source_rows = read_rows(SOURCE)
    etf_rows = read_rows(ETF_TOP50_SOURCE)
    if not source_rows:
        raise RuntimeError(f"持仓数据为空：{SOURCE}")
    if not etf_rows:
        raise RuntimeError(f"ETF前50数据为空：{ETF_TOP50_SOURCE}")
    rows = aggregate(source_rows)
    trade_date = source_rows[0].get("trade_date", "")
    price_data = load_price_data(rows, trade_date)
    write_csv(rows)
    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(build_html(source_rows, rows, price_data, etf_rows), encoding="utf-8")
    print(f"{OUTPUT_HTML} ETF={len({row['etf_code'] for row in source_rows})} raw={len(source_rows)} dedup={len(rows)}")


if __name__ == "__main__":
    main()
