"""Create the dashboard table from Wind, including year-to-date price trends."""
import csv
import math
from datetime import date
from pathlib import Path
from WindPy import w

ROOT = Path(__file__).resolve().parent.parent

def read(path):
    with open(path, encoding="utf-8-sig", newline="") as f: return list(csv.DictReader(f))
def valid(x): return x not in (None, "") and not (isinstance(x, float) and math.isnan(x))
def num(x, scale=1): return "" if not valid(x) else round(float(x) / scale, 6)
def growth(a, b): return "" if not valid(a) or not valid(b) or not b else (float(a) / float(b) - 1) * 100
def ratio(a, b): return "" if not valid(a) or not valid(b) or not b else float(a) / float(b)
def require(result, label):
    if result.ErrorCode: raise RuntimeError((label, result.ErrorCode, result.Data))
    return result

ranked = read(ROOT / "output" / "ranked_stocks.csv")
codes = [x["ts_code"] for x in ranked]
forecast_snapshot_date = "20260822"
forecast_snapshot = {
    row["ts_code"]: row
    for row in read(ROOT / "data" / "wind" / f"forecast_snapshot_{forecast_snapshot_date}.csv")
}
w.start()
trade_date = date.fromisoformat(ranked[0]["trade_date"])
today = trade_date.strftime("%Y%m%d")
report_date = date.fromisoformat(ranked[0]["report_period"])
prior_report_date = date(report_date.year - 1, report_date.month, report_date.day)
period_label = {3: "Q1", 6: "中报", 9: "前三季度", 12: "年报"}[report_date.month]
mid_current_date = date(trade_date.year, 6, 30)
mid_prior_date = date(trade_date.year - 1, 6, 30)

current = require(w.wss(",".join(codes), "pct_chg_per,mkt_cap_ard,west_netprofit_yoy,west_netprofit_fy1,west_netprofit_fy2,west_netprofit_fy3", f"tradeDate={today};startDate={trade_date.year}0101;endDate={today};unit=1"), "current")
gm_prior = require(w.wss(",".join(codes), "grossprofitmargin,netprofitmargin,roe", f"rptDate={prior_report_date:%Y%m%d};rptType=1;unit=1"), "prior cumulative financials")
gm_current = require(w.wss(",".join(codes), "grossprofitmargin,netprofitmargin,roe,yoynetprofit,qfa_yoynetprofit,qfa_deductedprofit", f"rptDate={report_date:%Y%m%d};rptType=1;unit=1"), "current cumulative financials")
mid_fin_prior = require(w.wss(",".join(codes), "grossprofitmargin,netprofitmargin,roe", f"rptDate={mid_prior_date:%Y%m%d};rptType=1;unit=1"), "prior interim margins and roe")
mid_fin_current = require(w.wss(",".join(codes), "grossprofitmargin,netprofitmargin,roe", f"rptDate={mid_current_date:%Y%m%d};rptType=1;unit=1"), "current interim margins and roe")
ded_prior = require(w.wss(",".join(codes), "qfa_deductedprofit", f"rptDate={prior_report_date:%Y%m%d};unit=1"), "prior quarter profit")
sw1 = require(w.wss(",".join(codes), "industry_sw_2021", f"tradeDate={today};industryType=1"), "sw1")
sw2 = require(w.wss(",".join(codes), "industry_sw_2021", f"tradeDate={today};industryType=2"), "sw2")
sw3 = require(w.wss(",".join(codes), "industry_sw_2021", f"tradeDate={today};industryType=3"), "sw3")
year_base = require(w.tdaysoffset(0, date(trade_date.year, 1, 1), ""), "year base day").Data[0][0]
prices = require(w.wsd(",".join(codes), "close", year_base, trade_date, "PriceAdj=F"), "year-to-date prices")
price_map = {code: [v for v in series if valid(v)] for code, series in zip(prices.Codes, prices.Data)}

rows = []
for i, base in enumerate(ranked):
    ytd, mcap, g26, p26, p27, p28 = [x[i] for x in current.Data]
    snapshot = forecast_snapshot.get(base["ts_code"], {})
    og26 = snapshot.get("growth_2026", "")
    og27 = snapshot.get("growth_2027", "")
    op26 = snapshot.get("profit_2026_billion", "")
    op27 = snapshot.get("profit_2027_billion", "")
    g27, g28 = growth(p27, p26), growth(p28, p27)
    pe26, pe27, pe28 = ratio(mcap, p26), ratio(mcap, p27), ratio(mcap, p28)
    rows.append({
        "rank": base["rank"], "ts_code": base["ts_code"], "name": base["name"], "ytd_return": num(ytd), "market_cap": num(mcap, 1e8),
        "close_ytd": "|".join(str(round(float(v), 4)) for v in price_map.get(base["ts_code"], [])),
        "g26": num(g26), "g26_old": num(og26), "g26_diff": num(float(g26)-float(og26)) if valid(g26) and valid(og26) else "",
        "g27": num(g27), "g27_old": num(og27), "g27_diff": num(g27-float(og27)) if valid(g27) and valid(og27) else "", "g28": num(g28),
        "p26": num(p26,1e8), "p26_old": num(op26), "p26_diff": num(p26/1e8-float(op26)) if valid(p26) and valid(op26) else "",
        "p27": num(p27,1e8), "p27_old": num(op27), "p27_diff": num(p27/1e8-float(op27)) if valid(p27) and valid(op27) else "", "p28": num(p28,1e8),
        "pe26": num(pe26), "pe27": num(pe27), "pe28": num(pe28), "peg26": num(ratio(pe26,g26)), "peg27": num(ratio(pe27,g27)), "peg28": num(ratio(pe28,g28)),
        "sw_industry": " → ".join(str(x) for x in (sw1.Data[0][i],sw2.Data[0][i],sw3.Data[0][i]) if x),
        "gm_prior_ytd": num(gm_prior.Data[0][i]), "gm_current_ytd": num(gm_current.Data[0][i]),
        "gm_prior_label": f"{prior_report_date.year}年{period_label}毛利率", "gm_current_label": f"{report_date.year}年{period_label}毛利率",
        "gross_margin_mid_prior": num(mid_fin_prior.Data[0][i]), "gross_margin_mid_current": num(mid_fin_current.Data[0][i]),
        "gross_margin_mid_prior_label": f"{mid_prior_date.year}年中报毛利率", "gross_margin_mid_current_label": f"{mid_current_date.year}年中报毛利率",
        "net_margin_mid_prior": num(mid_fin_prior.Data[1][i]), "net_margin_mid_current": num(mid_fin_current.Data[1][i]),
        "net_margin_mid_prior_label": f"{mid_prior_date.year}年中报净利率", "net_margin_mid_current_label": f"{mid_current_date.year}年中报净利率",
        "roe_mid_prior": num(mid_fin_prior.Data[2][i]), "roe_mid_current": num(mid_fin_current.Data[2][i]),
        "roe_mid_prior_label": f"{mid_prior_date.year}年中报ROE", "roe_mid_current_label": f"{mid_current_date.year}年中报ROE",
        "net_margin_current_ytd": num(gm_current.Data[1][i]), "net_margin_current_label": f"{report_date.year}年{period_label}净利率",
        "roe_current_ytd": num(gm_current.Data[2][i]), "roe_current_label": f"{report_date.year}年{period_label}ROE",
        "net_profit_yoy_ytd": num(gm_current.Data[3][i]),
        "qfa_net_profit_yoy": num(gm_current.Data[4][i]), "qfa_deducted_profit_yoy": num(growth(gm_current.Data[5][i],ded_prior.Data[0][i])),
        "quarter_metric": base.get("net_profit_qoq_growth", ""), "quarter_period": f"{base.get('qoq_current_period','')} 较 {base.get('qoq_previous_period','')}｜实际单季度净利润环比"
    })

out = ROOT / "data" / "wind" / "web_table_data.csv"
with open(out, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
print(out, len(rows), f"累计毛利率：{prior_report_date} vs {report_date}", f"年初至今基准：{year_base}至{trade_date}")
