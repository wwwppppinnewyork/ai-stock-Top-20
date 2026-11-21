import argparse
import csv
import json
import math
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from WindPy import w

from stock_workflow import build_candidate_pool, score_stocks


ROOT = Path(__file__).resolve().parent.parent
SELECTION_DATES = ((4, 30), (8, 31), (10, 30))


def read_csv(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def finite(value):
    return value is not None and not (isinstance(value, float) and math.isnan(value))


def fmt(value):
    return "" if not finite(value) else round(float(value), 6)


def fill_missing_fourth_quarters(series, annual_values):
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


def ok(result, label):
    if result.ErrorCode != 0:
        raise RuntimeError(f"Wind {label} failed: {result.ErrorCode} {result.Data}")
    return result


def report_period(as_of):
    quarter_month = ((as_of.month - 1) // 3) * 3
    year = as_of.year
    if quarter_month == 0:
        quarter_month, year = 12, year - 1
    result = date(year, quarter_month, 31 if quarter_month in (3, 12) else 30)
    if (as_of - result).days < 35:
        result = date(year - (1 if quarter_month == 3 else 0), 12 if quarter_month == 3 else quarter_month - 3, 31 if quarter_month in (3, 6) else 30)
    return result


def wind_universe(as_of, destination):
    ai_path = destination / "ai_theme.csv"
    etf_path = destination / "etf_top_holdings.csv"
    # Universe rules are point-in-time inputs to the signal. Always regenerate
    # so legacy AI/ETF cache files cannot contaminate the new all-A-share test.
    subprocess.run([
        sys.executable, str(ROOT / "src" / "wind_universe.py"),
        "--config", str(ROOT / "config" / "universe.json"),
        "--output-dir", str(destination), "--as-of", as_of.isoformat(), "--allow-partial-etf-history",
    ], cwd=ROOT, check=True)
    return build_candidate_pool(read_csv(ai_path), read_csv(etf_path))


def factor_snapshot(pool, as_of):
    codes = [r["ts_code"] for r in pool]
    trade_day = ok(w.tdaysoffset(-1, as_of.isoformat(), ""), "trade day").Data[0][0]
    if hasattr(trade_day, "date"):
        trade_day = trade_day.date()
    fields = "sec_name,west_netprofit_yoy,west_netprofit_fy1,west_netprofit_fy2,west_netprofit_fy3,mkt_cap_ard,close,pct_chg"
    snap = ok(w.wss(",".join(codes), fields, f"tradeDate={trade_day:%Y%m%d};unit=1"), "historical consensus")
    values = dict(zip([f.lower() for f in snap.Fields], snap.Data))
    revision_snapshots = {}
    for label, days_back in (("1m", 30), ("3m", 90)):
        observation = as_of - timedelta(days=days_back)
        observation_trade = ok(w.tdaysoffset(-1, observation.isoformat(), ""), f"{label} trade day").Data[0][0]
        observation_trade = observation_trade.date() if hasattr(observation_trade, "date") else observation_trade
        fields_by_year = []
        for target_year in (as_of.year, as_of.year + 1):
            horizon = target_year - observation_trade.year + 1
            fields_by_year.append(f"west_netprofit_fy{horizon}")
        old = ok(w.wss(",".join(codes), ",".join(fields_by_year), f"tradeDate={observation_trade:%Y%m%d};unit=1"), f"historical {label} forecasts")
        by_field = dict(zip([f.lower() for f in old.Fields], old.Data))
        revision_snapshots[label] = {
            target_year: by_field[field]
            for target_year, field in zip((as_of.year, as_of.year + 1), fields_by_year)
        }
    rpt = report_period(as_of)
    financial = ok(w.wss(",".join(codes), "qfa_roe,qfa_grossprofitmargin,qfa_netprofitmargin,qfa_deductedprofit,qfa_net_profit_is", f"rptDate={rpt:%Y%m%d};rptType=1;unit=1"), "historical financials")
    values.update(dict(zip([f.lower() for f in financial.Fields], financial.Data)))
    prior_quality_rpt = date(rpt.year - 1, rpt.month, rpt.day)
    prior_quality = ok(w.wss(",".join(codes), "qfa_roe,qfa_grossprofitmargin,qfa_netprofitmargin", f"rptDate={prior_quality_rpt:%Y%m%d};rptType=1;unit=1"), "historical prior-year quality")
    prior_quality_values = dict(zip([f.lower() for f in prior_quality.Fields], prior_quality.Data))
    y, q = rpt.year, (rpt.month - 1) // 3 + 1
    report_dates = []
    # Five consecutive quarter-on-quarter increases require six observations.
    for back in range(5, -1, -1):
        total = y * 4 + (q - 1) - back
        yy, qq = divmod(total, 4)
        month = (qq + 1) * 3
        report_dates.append(date(yy, month, 31 if month in (3, 12) else 30))
    profit_map = {code: [] for code in codes}
    for report_date in report_dates:
        period_values = []
        for start_index in range(0, len(codes), 6000):
            batch = codes[start_index:start_index + 6000]
            result = ok(w.wss(",".join(batch), "qfa_net_profit_is", f"rptDate={report_date:%Y%m%d};rptType=1;unit=1"), f"historical quarter profit {report_date}")
            period_values.extend(result.Data[0])
        for code, value in zip(codes, period_values):
            profit_map[code].append((report_date, float(value) if finite(value) else None))
    annual_profit_map = {code: {} for code in codes}
    for year in sorted({period.year for period in report_dates if period.month == 12}):
        annual_values = []
        for start_index in range(0, len(codes), 6000):
            batch = codes[start_index:start_index + 6000]
            result = ok(
                w.wss(",".join(batch), "net_profit_is", f"rptDate={year}1231;rptType=1;unit=1"),
                f"historical annual cumulative net profit {year}",
            )
            annual_values.extend(result.Data[0])
        for code, value in zip(codes, annual_values):
            annual_profit_map[code][year] = float(value) if finite(value) else None
    rows = []
    for i, stock in enumerate(pool):
        code = stock["ts_code"]
        raw_series = fill_missing_fourth_quarters(
            profit_map.get(code, []), annual_profit_map.get(code, {})
        )
        end_index = next((j for j in range(len(raw_series) - 1, -1, -1) if raw_series[j][1] is not None), None)
        series = []
        if end_index is not None:
            start_index = end_index
            while start_index > 0 and raw_series[start_index - 1][1] is not None:
                start_index -= 1
            series = raw_series[start_index:end_index + 1]
        nums = [x[1] for x in series]
        qoq = ((nums[-1] - nums[-2]) / abs(nums[-2])) * 100 if len(nums) >= 2 and nums[-2] else None
        consecutive_growth_quarters = 0
        for previous_profit, current_profit in reversed(list(zip(nums[:-1], nums[1:]))):
            if current_profit > previous_profit:
                consecutive_growth_quarters += 1
            else:
                break
        changes = [((b - a) / abs(a)) * 100 if a else None for a, b in zip(nums[:-1], nums[1:])]
        yoy = values["west_netprofit_yoy"][i]
        fy1, fy2, fy3 = values["west_netprofit_fy1"][i], values["west_netprofit_fy2"][i], values["west_netprofit_fy3"][i]
        market_cap = values["mkt_cap_ard"][i]
        growth2 = ((fy2 - fy1) / abs(fy1)) * 100 if finite(fy1) and finite(fy2) and fy1 else None
        growth3 = ((fy3 - fy2) / abs(fy2)) * 100 if finite(fy2) and finite(fy3) and fy2 else None
        pe1 = market_cap / fy1 if finite(market_cap) and finite(fy1) and fy1 > 0 else None
        pe2 = market_cap / fy2 if finite(market_cap) and finite(fy2) and fy2 > 0 else None
        pe3 = market_cap / fy3 if finite(market_cap) and finite(fy3) and fy3 > 0 else None
        peg1 = pe1 / yoy if finite(pe1) and finite(yoy) and yoy > 0 else None
        peg2 = pe2 / growth2 if finite(pe2) and finite(growth2) and growth2 != 0 else None
        peg3 = pe3 / growth3 if finite(pe3) and finite(growth3) and growth3 != 0 else None
        revision_scores = []
        for year_offset, current_value in enumerate((fy1, fy2)):
            target_year = as_of.year + year_offset
            old_values = []
            for label, days_back in (("1m", 30), ("3m", 90)):
                old_values.append(revision_snapshots[label][target_year][i])
            change_1m = ((current_value - old_values[0]) / abs(old_values[0])) * 100 if finite(current_value) and finite(old_values[0]) and old_values[0] else None
            change_3m = ((current_value - old_values[1]) / abs(old_values[1])) * 100 if finite(current_value) and finite(old_values[1]) and old_values[1] else None
            revision_scores.append(change_1m - change_3m if finite(change_1m) and finite(change_3m) else None)
        rows.append({
            "ts_code": code, "name": values["sec_name"][i] or stock["name"], "trade_date": trade_day.isoformat(),
            "consensus_year": as_of.year, "report_period": rpt.isoformat(), "close": fmt(values["close"][i]),
            "pct_chg": fmt(values["pct_chg"][i]), "analyst_profit_yoy": fmt(yoy),
            "analyst_profit_yoy_fy1": fmt(yoy), "analyst_profit_yoy_fy2": fmt(growth2), "analyst_profit_yoy_fy3": fmt(growth3),
            "peg": fmt(peg1), "peg_fy1": fmt(peg1), "peg_fy2": fmt(peg2), "peg_fy3": fmt(peg3), "net_profit_qoq_growth": fmt(qoq),
            "qoq_previous_period": series[-2][0].isoformat() if len(series) >= 2 else "",
            "qoq_current_period": series[-1][0].isoformat() if len(series) >= 2 else "",
            "q1_acceleration_flag": int(len(changes) >= 2 and changes[-1] is not None and changes[-2] is not None and changes[-1] > changes[-2]),
            "quarter_cycle_positive_flag": int(len(nums) >= 6 and all(b > a for a, b in zip(nums[-6:-1], nums[-5:]))),
            "consecutive_profit_growth_quarters": consecutive_growth_quarters,
            "analyst_net_profit_revision_change": fmt(revision_scores[0]),
            "analyst_revision_fy1": fmt(revision_scores[0]), "analyst_revision_fy2": fmt(revision_scores[1]),
            "quality_prior_period": prior_quality_rpt.isoformat(), "quality_current_period": rpt.isoformat(),
            "roe_prior": fmt(prior_quality_values["qfa_roe"][i]),
            "gross_margin_prior": fmt(prior_quality_values["qfa_grossprofitmargin"][i]),
            "net_margin_prior": fmt(prior_quality_values["qfa_netprofitmargin"][i]),
            "roe_current": fmt(values["qfa_roe"][i]), "gross_margin_current": fmt(values["qfa_grossprofitmargin"][i]),
            "net_margin_current": fmt(values["qfa_netprofitmargin"][i]),
            "current_quarter_deducted_profit": fmt(values["qfa_deductedprofit"][i]),
            "current_quarter_net_profit": fmt(nums[-1]) if nums else "",
        })
    return trade_day.isoformat(), rows


def main():
    parser = argparse.ArgumentParser(description="从指定日期起按周回填真实历史Top30")
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--only-missing", action="store_true")
    parser.add_argument("--history-output", default="data/top20_history.csv")
    parser.add_argument("--no-publish", action="store_true")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    started = w.start()
    if started.ErrorCode != 0 or not w.isconnected():
        raise RuntimeError("Wind未连接")
    planned = []
    weekly = ok(w.tdays(start.isoformat(), end.isoformat(), "Period=W"), "weekly selection calendar").Data[0]
    calendar = ok(w.tdays(start.isoformat(), (end + timedelta(days=10)).isoformat(), ""), "daily selection calendar").Data[0]
    trading_days = [value.date() if hasattr(value, "date") else value for value in calendar]
    for value in weekly:
        signal_day = value.date() if hasattr(value, "date") else value
        next_trade = next(day for day in trading_days if day > signal_day)
        planned.append((signal_day, next_trade))
    planned = sorted(set(planned))
    config = json.loads((ROOT / "config" / "weights.json").read_text(encoding="utf-8-sig"))
    history_path = ROOT / args.history_output
    existing = read_csv(history_path) if history_path.exists() else []
    if not args.only_missing:
        existing = [r for r in existing if r.get("snapshot_date", "") < start.isoformat() or r.get("snapshot_date", "") > end.isoformat()]
        with history_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["snapshot_date", "ts_code", "name", "rank", "total_score"])
            writer.writeheader(); writer.writerows(existing)
    if args.only_missing:
        saved = {r["snapshot_date"] for r in existing}
        planned = [pair for pair in planned if pair[0].isoformat() not in saved]
    historical = []
    exclusion_audit = []
    cache = ROOT / "data" / "history_universe"
    for number, (week_end, as_of) in enumerate(planned, 1):
        week_dir = cache / as_of.isoformat()
        week_dir.mkdir(parents=True, exist_ok=True)
        # The pool is observed at the signal close, never on the next buy day.
        pool = wind_universe(week_end, week_dir)
        snap, factors = factor_snapshot(pool, as_of)
        for factor in factors:
            value = factor.get("current_quarter_net_profit", "")
            if value != "" and float(value) < 0:
                exclusion_audit.append({"snapshot_date": snap, "report_period": factor.get("report_period", ""), "ts_code": factor["ts_code"], "name": factor["name"], "current_quarter_net_profit": value, "exclusion_reason": "当时最新已披露单季度净利润为负"})
        ranked = score_stocks(pool, factors, config)[:30]
        snapshot_rows = [{"snapshot_date": snap, "ts_code": r["ts_code"], "name": r["name"], "rank": r["rank"], "total_score": r["total_score"]} for r in ranked]
        historical.extend(snapshot_rows)
        existing = [r for r in existing if r.get("snapshot_date") != snap] + snapshot_rows
        existing.sort(key=lambda r: (r["snapshot_date"], int(float(r["rank"]))))
        with history_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["snapshot_date", "ts_code", "name", "rank", "total_score"])
            writer.writeheader(); writer.writerows(existing)
        print(f"[{number}/{len(planned)}] {snap}: 候选{len(pool)}，Top30已保存", flush=True)
    # A backfill is authoritative for its interval: remove all old test or
    # partial snapshots in that interval, then keep today's normal snapshot.
    combined = existing
    combined.sort(key=lambda r: (r["snapshot_date"], int(float(r["rank"]))))
    with history_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["snapshot_date", "ts_code", "name", "rank", "total_score"])
        writer.writeheader(); writer.writerows(combined)
    audit_path = ROOT / "output" / f"{history_path.stem}_negative_quarter_profit_exclusions.csv"
    if exclusion_audit:
        with audit_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["snapshot_date", "report_period", "ts_code", "name", "current_quarter_net_profit", "exclusion_reason"])
            writer.writeheader(); writer.writerows(exclusion_audit)
    if not args.no_publish:
        subprocess.run([sys.executable, str(ROOT / "src" / "top20_history.py")], cwd=ROOT, check=True)
    print(f"历史回填完成：{history_path}")


if __name__ == "__main__":
    main()
