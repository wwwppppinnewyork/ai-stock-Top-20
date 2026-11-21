"""Fetch the strategy inputs from WindPy on a Windows machine with Wind installed."""

import argparse
import csv
import math
from datetime import date
from pathlib import Path

from WindPy import w


CONSENSUS_FIELDS = "sec_name,west_netprofit_yoy,west_netprofit_fy1,west_netprofit_fy2,west_netprofit_fy3,mkt_cap_ard"
FINANCIAL_FIELDS = "qfa_roe,qfa_grossprofitmargin,qfa_netprofitmargin,qfa_deductedprofit,qfa_net_profit_is"
QUALITY_FIELDS = "qfa_roe,qfa_grossprofitmargin,qfa_netprofitmargin"
REVISION_BASELINE_DATE = date(2026, 8, 22)
REVISION_NEUTRAL_BAND_PCT_POINTS = 0.5


def finite(value):
    return value is not None and not (isinstance(value, float) and math.isnan(value))


def fmt(value):
    return "" if not finite(value) else round(float(value), 6)


def fmt_date(value):
    if not finite(value):
        return ""
    parsed = value.date() if hasattr(value, "date") else value
    return "" if getattr(parsed, "year", 0) < 2000 else parsed.isoformat()


def read_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_rows(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def require_ok(result, label):
    if result.ErrorCode != 0:
        raise RuntimeError(f"Wind {label} failed: {result.ErrorCode} {result.Data}")
    return result


def latest_trade_day(as_of):
    # The automatic job runs at 21:00, after the A-share close.  Offset 0 uses
    # that day's completed data, while weekends/holidays resolve to the latest
    # prior trading day.
    result = require_ok(w.tdaysoffset(0, as_of.strftime("%Y-%m-%d"), ""), "trade day")
    return result.Data[0][0].date() if hasattr(result.Data[0][0], "date") else result.Data[0][0]


def latest_complete_consensus_snapshot(codes, as_of, min_coverage=0.30, max_lookback=10):
    """Use the latest trade date whose FY1 consensus coverage is not abnormally sparse."""
    checked = set()
    for offset in range(max_lookback + 1):
        result_day = require_ok(w.tdaysoffset(-offset, as_of.strftime("%Y-%m-%d"), ""), "consensus trade day")
        trade_day = result_day.Data[0][0].date() if hasattr(result_day.Data[0][0], "date") else result_day.Data[0][0]
        if trade_day in checked:
            continue
        checked.add(trade_day)
        snapshot = require_ok(
            w.wss(",".join(codes), CONSENSUS_FIELDS + ",close,pct_chg", f"tradeDate={trade_day:%Y%m%d};unit=1"),
            f"consensus snapshot {trade_day}",
        )
        fields = dict(zip([x.lower() for x in snapshot.Fields], snapshot.Data))
        covered = sum(1 for value in fields["west_netprofit_fy1"] if finite(value))
        coverage = covered / max(len(codes), 1)
        if coverage >= min_coverage:
            return trade_day, snapshot
    raise RuntimeError(f"No sufficiently complete Wind consensus snapshot within {max_lookback} trading days")


def wind_sector_rows(sector_names, trade_day):
    merged = {}
    for sector in sector_names:
        result = require_ok(
            w.wset("sectorconstituent", f"date={trade_day:%Y-%m-%d};sector={sector}"),
            f"sector {sector}",
        )
        if not result.Data:
            continue
        for code, name in zip(result.Data[1], result.Data[2]):
            if not str(code).endswith((".SH", ".SZ", ".BJ")):
                continue
            item = merged.setdefault(code, {"ts_code": code, "name": name, "sources": set()})
            item["sources"].add(sector)
    return list(merged.values())


def candidate_rows(ai_path, etf_seed_path):
    # AI theme membership is intentionally controlled by the user; Wind supplies names/factors.
    ai = read_rows(ai_path)
    etf = read_rows(etf_seed_path) if etf_seed_path and Path(etf_seed_path).exists() else []
    merged = {}
    for row, source in [(x, "ai_theme") for x in ai] + [(x, "etf_holdings_seed") for x in etf]:
        code = row["ts_code"].strip().upper()
        item = merged.setdefault(code, {"ts_code": code, "name": row.get("name", ""), "sources": set()})
        item["sources"].add(source)
    return list(merged.values())


def signed_growth(previous, current):
    return ((current - previous) / abs(previous)) * 100 if previous else None


def trailing_consecutive_profits(series):
    """Return the latest uninterrupted run of disclosed single-quarter profits."""
    end = next((i for i in range(len(series) - 1, -1, -1) if series[i][1] is not None), None)
    if end is None:
        return []
    start = end
    while start > 0 and series[start - 1][1] is not None:
        start -= 1
    return series[start:end + 1]


def fill_missing_fourth_quarters(series, annual_values):
    """Rebuild missing Q4 as annual cumulative profit minus Q1-Q3.

    Wind occasionally returns an empty QFA_NET_PROFIT_IS for the year-end
    period (notably for recently listed companies), even though the annual
    cumulative NET_PROFIT_IS is available.  Leaving that hole breaks an
    otherwise continuous quarterly growth streak.
    """
    by_period = dict(series)
    rebuilt = []
    for period, value in series:
        if period.month == 12 and value is None and finite(annual_values.get(period.year)):
            first_three = [
                by_period.get(date(period.year, month, 31 if month == 3 else 30))
                for month in (3, 6, 9)
            ]
            if all(finite(item) for item in first_three):
                value = float(annual_values[period.year]) - sum(first_three)
        rebuilt.append((period, value))
    return rebuilt

def quarterly_profit(code, report_dates):
    values=[]
    for rpt in report_dates:
        result=require_ok(w.wss(code,"qfa_deductedprofit",f"rptDate={rpt:%Y%m%d};unit=1"),f"{code} {rpt}")
        value=result.Data[0][0]
        values.append((rpt,float(value)) if finite(value) else (rpt,None))
    return values


def main():
    parser = argparse.ArgumentParser(description="用 WindPy 生成策略输入数据")
    parser.add_argument("--ai-universe", default="data/ai_theme.csv")
    parser.add_argument("--etf-seed", default="data/etf_top_holdings_seed.csv")
    parser.add_argument("--output-dir", default="data/wind")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--sectors", default="人工智能,ChatGPT,AIGC,机器视觉", help="Wind板块名称，逗号分隔；留空时使用CSV种子池")
    args = parser.parse_args()

    started = w.start()
    if started.ErrorCode != 0 or not w.isconnected():
        raise RuntimeError(f"WindPy connection failed: {started}")

    as_of = date.fromisoformat(args.as_of)
    trade_day = latest_trade_day(as_of)
    sector_names = [x.strip() for x in args.sectors.split(",") if x.strip()]
    candidates = wind_sector_rows(sector_names, trade_day) if sector_names else candidate_rows(args.ai_universe, args.etf_seed)
    codes = [x["ts_code"] for x in candidates]
    if not codes:
        raise ValueError("候选池为空")

    trade_day, snapshot = latest_complete_consensus_snapshot(codes, as_of)
    by_field = dict(zip([x.lower() for x in snapshot.Fields], snapshot.Data))
    baseline_rows = read_rows(
        Path(args.output_dir) / f"forecast_snapshot_{REVISION_BASELINE_DATE:%Y%m%d}.csv"
    )
    baseline_by_code = {row["ts_code"]: row for row in baseline_rows}
    quarter_month = ((as_of.month - 1) // 3) * 3
    report_year = as_of.year
    if quarter_month == 0:
        quarter_month, report_year = 12, report_year - 1
    # Allow roughly one month for quarterly reports; step back if necessary.
    report_end = date(report_year, quarter_month, 31 if quarter_month in (3, 12) else 30)
    if (as_of - report_end).days < 35:
        report_end = date(report_year - (1 if quarter_month == 3 else 0), 12 if quarter_month == 3 else quarter_month - 3, 31 if quarter_month in (3, 6) else 30)
    financial = require_ok(w.wss(",".join(codes), FINANCIAL_FIELDS, f"rptDate={report_end:%Y%m%d};rptType=1;unit=1"), "financial snapshot")
    by_field.update(dict(zip([x.lower() for x in financial.Fields], financial.Data)))
    prior_quality_end = date(report_end.year - 1, report_end.month, report_end.day)
    prior_quality = require_ok(
        w.wss(",".join(codes), QUALITY_FIELDS, f"rptDate={prior_quality_end:%Y%m%d};rptType=1;unit=1"),
        f"prior quality snapshot {prior_quality_end}",
    )
    prior_quality_by_field = dict(zip([x.lower() for x in prior_quality.Fields], prior_quality.Data))
    prior_annual_date = date(as_of.year - 1, 12, 31)
    prior_annual_parent_profit = require_ok(
        w.wss(",".join(codes), "np_belongto_parcomsh", f"rptDate={prior_annual_date:%Y%m%d};rptType=1;unit=1"),
        f"annual parent net profit {prior_annual_date}",
    )
    interim_period = date(as_of.year, 6, 30)
    interim_disclosure = require_ok(
        w.wss(",".join(codes), "stm_predict_issuingdate,stm_issuingdate", f"rptDate={interim_period:%Y%m%d}"),
        f"interim disclosure dates {interim_period}",
    )
    interim_rows = [
        {"ts_code": code, "report_period": interim_period.isoformat(),
         "scheduled_date": fmt_date(interim_disclosure.Data[0][idx]),
         "actual_date": fmt_date(interim_disclosure.Data[1][idx])}
        for idx, code in enumerate(codes)
    ]
    report_dates=[]
    y,q=report_end.year,(report_end.month-1)//3+1
    for back in range(19,-1,-1):
        total=y*4+(q-1)-back; yy,qq=divmod(total,4); month=(qq+1)*3
        report_dates.append(date(yy,month,31 if month in (3,12) else 30))
    profit_by_code={code:[] for code in codes}
    parent_profit_by_code={code:[] for code in codes}
    for rpt in report_dates:
        period_values=[]
        parent_period_values=[]
        for start in range(0,len(codes),300):
            batch=codes[start:start+300]
            result=require_ok(w.wss(",".join(batch),"qfa_net_profit_is,qfa_np_belongto_parcomsh",f"rptDate={rpt:%Y%m%d};rptType=1;unit=1"),f"quarter net profit {rpt}")
            period_values.extend(result.Data[0])
            parent_period_values.extend(result.Data[1])
        for code,value,parent_value in zip(codes,period_values,parent_period_values):
            profit_by_code[code].append((rpt,float(value)) if finite(value) else (rpt,None))
            parent_profit_by_code[code].append((rpt,float(parent_value)) if finite(parent_value) else (rpt,None))

    annual_profit_by_code = {code: {} for code in codes}
    for year in sorted({rpt.year for rpt in report_dates if rpt.month == 12}):
        annual_values = []
        for start in range(0, len(codes), 300):
            batch = codes[start:start + 300]
            result = require_ok(
                w.wss(",".join(batch), "net_profit_is", f"rptDate={year}1231;rptType=1;unit=1"),
                f"annual cumulative net profit {year}",
            )
            annual_values.extend(result.Data[0])
        for code, value in zip(codes, annual_values):
            annual_profit_by_code[code][year] = float(value) if finite(value) else None

    factor_rows, quarter_rows, annual_rows, missing = [], [], [], []
    for idx, item in enumerate(candidates):
        code = item["ts_code"]
        name = by_field["sec_name"][idx] or item["name"] or code
        profit_series = fill_missing_fourth_quarters(
            profit_by_code[code], annual_profit_by_code[code]
        )
        # Full marks require five consecutive quarter-on-quarter increases,
        # which needs six consecutive natural-quarter profit observations.
        series = trailing_consecutive_profits(profit_series)
        values=[v for _,v in series]
        qoq=signed_growth(values[-2], values[-1]) if len(values)>=2 else None
        qoq_previous_period=series[-2][0].isoformat() if len(series)>=2 else ""
        qoq_current_period=series[-1][0].isoformat() if len(series)>=2 else ""
        changes=[signed_growth(a,b) for a,b in zip(values[:-1],values[1:])]
        q1_flag = int(len(changes) >= 2 and changes[-1] > changes[-2])
        cycle_flag = int(len(values) >= 6 and all(b > a for a,b in zip(values[-6:-1],values[-5:])))
        consecutive_growth_quarters = 0
        for previous_profit, current_profit in reversed(list(zip(values[:-1], values[1:]))):
            if current_profit > previous_profit:
                consecutive_growth_quarters += 1
            else:
                break
        latest_quarter_profit = values[-1] if values else None
        fy1, fy2, fy3 = (by_field[field][idx] for field in ("west_netprofit_fy1", "west_netprofit_fy2", "west_netprofit_fy3"))
        market_cap = by_field["mkt_cap_ard"][idx]
        growth1 = by_field["west_netprofit_yoy"][idx]
        growth2 = signed_growth(fy1, fy2) if finite(fy1) and finite(fy2) else None
        growth3 = signed_growth(fy2, fy3) if finite(fy2) and finite(fy3) else None
        pe1 = market_cap / fy1 if finite(market_cap) and finite(fy1) and fy1 > 0 else None
        pe2 = market_cap / fy2 if finite(market_cap) and finite(fy2) and fy2 > 0 else None
        pe3 = market_cap / fy3 if finite(market_cap) and finite(fy3) and fy3 > 0 else None
        peg1 = pe1 / growth1 if finite(pe1) and finite(growth1) and growth1 > 0 else None
        # Preserve negative FY2/FY3 PEG values for the explicit scoring veto:
        # if either next-year or following-year PEG is below zero, the entire
        # PEG factor receives zero points. Zero growth remains undefined.
        peg2 = pe2 / growth2 if finite(pe2) and finite(growth2) and growth2 != 0 else None
        peg3 = pe3 / growth3 if finite(pe3) and finite(growth3) and growth3 != 0 else None
        baseline_row = baseline_by_code.get(code, {})
        baseline_growth1 = baseline_row.get("growth_2026", "")
        baseline_growth2 = baseline_row.get("growth_2027", "")
        baseline_growth_by_year = {
            REVISION_BASELINE_DATE.year: float(baseline_growth1) if baseline_growth1 != "" else None,
            REVISION_BASELINE_DATE.year + 1: float(baseline_growth2) if baseline_growth2 != "" else None,
        }
        revision_scores = []
        for target_year, current_growth in ((as_of.year, growth1), (as_of.year + 1, growth2)):
            baseline_growth = baseline_growth_by_year.get(target_year)
            difference = float(current_growth) - float(baseline_growth) if finite(current_growth) and finite(baseline_growth) else None
            if finite(difference) and abs(difference) < REVISION_NEUTRAL_BAND_PCT_POINTS:
                difference = 0.0
            revision_scores.append(difference)
        row = {
            "ts_code": code, "name": name,
            "trade_date": trade_day.isoformat(),
            "consensus_year": as_of.year,
            "report_period": report_end.isoformat(),
            "close": fmt(by_field["close"][idx]),
            "mkt_cap": fmt(market_cap),
            "pct_chg": fmt(by_field["pct_chg"][idx]),
            "analyst_profit_yoy": fmt(growth1),
            "analyst_profit_yoy_fy1": fmt(growth1),
            "analyst_profit_yoy_fy2": fmt(growth2),
            "analyst_profit_yoy_fy3": fmt(growth3),
            "peg": fmt(peg1), "peg_fy1": fmt(peg1), "peg_fy2": fmt(peg2), "peg_fy3": fmt(peg3),
            "net_profit_qoq_growth": fmt(qoq), "q1_acceleration_flag": q1_flag,
            "qoq_previous_period": qoq_previous_period,
            "qoq_current_period": qoq_current_period,
            "quarter_cycle_positive_flag": cycle_flag,
            "consecutive_profit_growth_quarters": consecutive_growth_quarters,
            "analyst_net_profit_revision_change": fmt(revision_scores[0]),
            "analyst_revision_fy1": fmt(revision_scores[0]),
            "analyst_revision_fy2": fmt(revision_scores[1]),
            "quality_prior_period": prior_quality_end.isoformat(),
            "quality_current_period": report_end.isoformat(),
            "roe_prior": fmt(prior_quality_by_field["qfa_roe"][idx]),
            "gross_margin_prior": fmt(prior_quality_by_field["qfa_grossprofitmargin"][idx]),
            "net_margin_prior": fmt(prior_quality_by_field["qfa_netprofitmargin"][idx]),
            "roe_current": fmt(by_field["qfa_roe"][idx]),
            "gross_margin_current": fmt(by_field["qfa_grossprofitmargin"][idx]),
            "net_margin_current": fmt(by_field["qfa_netprofitmargin"][idx]),
            "current_quarter_deducted_profit": fmt(by_field["qfa_deductedprofit"][idx]),
            "current_quarter_net_profit": fmt(latest_quarter_profit),
        }
        absent = [key for key, value in row.items() if key not in {"ts_code", "name"} and value == ""]
        if absent:
            missing.append({"ts_code": code, "missing_fields": ",".join(absent)})
        factor_rows.append(row)
        actual_value = prior_annual_parent_profit.Data[0][idx]
        annual_rows.append({
            "ts_code": code, "name": name, "year": prior_annual_date.year,
            "net_profit": "" if not finite(actual_value) else round(float(actual_value) / 1e8, 4),
            "data_type": "Wind年报实际归母净利润", "wind_field": "NP_BELONGTO_PARCOMSH", "as_of": prior_annual_date,
        })
        for offset, field in enumerate(("west_netprofit_fy1", "west_netprofit_fy2", "west_netprofit_fy3")):
            value = by_field[field][idx]
            annual_rows.append({
                "ts_code": code, "name": name, "year": as_of.year + offset,
                "net_profit": "" if not finite(value) else round(float(value) / 1e8, 4),
                "data_type": "Wind一致预期", "wind_field": field.upper(), "as_of": trade_day,
            })
        for t, value in parent_profit_by_code[code]:
            if value is not None:
                quarter_rows.append({"ts_code": code, "name": name, "quarter": t.isoformat(), "net_profit": round(value / 1e8, 4), "data_type": "单季度归母净利润", "wind_field": "QFA_NP_BELONGTO_PARCOMSH"})

    out = Path(args.output_dir)
    # wind_universe.py owns ai_theme.csv and etf_top_holdings.csv. Do not overwrite
    # those auditable source tables after they have been consumed.
    write_rows(out / "factor_data.csv", factor_rows, list(factor_rows[0]))
    write_rows(out / "quarterly_parent_net_profit.csv", quarter_rows, ["ts_code", "name", "quarter", "net_profit", "data_type", "wind_field"])
    write_rows(out / "annual_net_profit_forecast.csv", annual_rows, ["ts_code", "name", "year", "net_profit", "data_type", "wind_field", "as_of"])
    write_rows(out / "interim_disclosure_dates.csv", interim_rows, ["ts_code", "report_period", "scheduled_date", "actual_date"])
    write_rows(out / "missing_data.csv", missing, ["ts_code", "missing_fields"])
    print(f"Wind connected: {w.isconnected()}; trade day: {trade_day}; stocks: {len(codes)}; incomplete: {len(missing)}")


if __name__ == "__main__":
    main()
