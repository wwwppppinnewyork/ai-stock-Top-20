"""Build the weekly rotating universe from Wind themes and ETF holdings."""
import argparse
import csv
import json
import math
import re
import time
from datetime import date
from pathlib import Path
from WindPy import w


def write(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def ok(result, label):
    if result.ErrorCode != 0:
        raise RuntimeError(f"{label}: {result.ErrorCode} {result.Data}")
    return result

def wset_retry(name, options, label, attempts=4):
    for attempt in range(attempts):
        result = w.wset(name, options)
        if result.ErrorCode == 0: return result
        if attempt + 1 < attempts: time.sleep(2 * (attempt + 1))
    return ok(result, label)


def chunks(items, size=300):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def exclusion_reason(code, name):
    text = str(name or "").strip()
    if not str(code).endswith((".SH", ".SZ", ".BJ")): return "非标准A股代码"
    if not text: return "证券简称为空"
    if re.search(r"(?i)\*?ST", text): return "ST或*ST证券"
    if "退市" in text or text.endswith("退"): return "退市或退市整理证券"
    return ""


def all_a_share_universe(cfg, out, asof, td):
    """Build the point-in-time A-share pool and apply the annual-return gate.

    Wind's 全部A股 sector excludes NEEQ/三板 instruments. Exchange suffixes are
    checked again defensively. Stocks listed during the calendar year use the
    first listing day's unadjusted open as the return base; all older stocks use
    Wind's calendar-year PCT_CHG_PER.
    """
    sector = cfg.get("a_share_sector", "全部A股")
    result = ok(w.wset("sectorconstituent", f"date={td:%Y-%m-%d};sector={sector}"), sector)
    if not result.Data:
        raise RuntimeError(f"Wind {sector} 成分为空")
    allowed = tuple(f".{x}" for x in cfg.get("allowed_exchanges", ["SH", "SZ", "BJ"]))
    raw = [(str(code), str(name or "")) for code, name in zip(result.Data[1], result.Data[2])]
    raw = [(code, name) for code, name in raw if code.endswith(allowed)]
    excluded, observations = [], []
    minimum = cfg.get("minimum_ytd_return_pct")
    minimum = float(minimum) if minimum is not None else None
    if minimum is None:
        included = []
        for code, name in raw:
            reason = exclusion_reason(code, name)
            if reason:
                excluded.append({"ts_code": code, "name": name, "source": sector, "reason": reason})
            else:
                included.append({"ts_code": code, "name": name, "theme": "全部A股", "wind_sector": sector, "trade_date": td, "ipo_date": "", "ytd_return_pct": "", "ytd_return_basis": "不参与筛选", "listing_first_day_open": ""})
        included.sort(key=lambda item: item["ts_code"])
        write(out / "ai_theme.csv", included, ["ts_code", "name", "theme", "wind_sector", "trade_date", "ipo_date", "ytd_return_pct", "ytd_return_basis", "listing_first_day_open"])
        write(out / "etf_top50.csv", [], ["trade_date", "etf_code", "etf_name", "ytd_return", "invest_type"])
        write(out / "etf_top_holdings.csv", [], ["trade_date", "holding_report_period", "etf_code", "etf_name", "etf_rank_50", "etf_ytd_return", "holding_rank_10", "holding_weight_pct", "ts_code", "name", "holding_source"])
        write(out / "excluded_stocks.csv", excluded, ["ts_code", "name", "source", "reason"])
        print(f"trade day={td}; 全部A股={len(raw)}; 无年度涨幅门槛={len(included)}; 排除={len(excluded)}")
        return included
    for batch in chunks(raw):
        codes = [item[0] for item in batch]
        names = dict(batch)
        query = ok(
            w.wss(
                ",".join(codes), "sec_name,ipo_date,pct_chg_per,close",
                f"startDate={td.year}0101;endDate={td:%Y%m%d};tradeDate={td:%Y%m%d};priceAdj=U",
            ),
            "全部A股年度涨幅",
        )
        fields = dict(zip([str(field).lower() for field in query.Fields], query.Data))
        for index, code in enumerate(codes):
            name = str(fields.get("sec_name", [None] * len(codes))[index] or names[code])
            reason = exclusion_reason(code, name)
            ipo_value = fields.get("ipo_date", [None] * len(codes))[index]
            ipo_date = ipo_value.date() if hasattr(ipo_value, "date") else ipo_value
            close = fields.get("close", [None] * len(codes))[index]
            wind_ytd = fields.get("pct_chg_per", [None] * len(codes))[index]
            if reason:
                excluded.append({"ts_code": code, "name": name, "source": sector, "reason": reason})
                continue
            observations.append({
                "ts_code": code, "name": name, "ipo_date": ipo_date,
                "close": close, "wind_ytd": wind_ytd,
            })

    # Each IPO date needs its own tradeDate. Grouping avoids one Wind call per IPO.
    ipo_groups = {}
    for row in observations:
        if isinstance(row["ipo_date"], date) and row["ipo_date"].year == td.year:
            ipo_groups.setdefault(row["ipo_date"], []).append(row)
    first_opens = {}
    for ipo_date, rows in ipo_groups.items():
        for batch in chunks(rows):
            codes = [row["ts_code"] for row in batch]
            opened = ok(w.wss(",".join(codes), "open", f"tradeDate={ipo_date:%Y%m%d};priceAdj=U"), f"新股首日开盘价 {ipo_date}")
            for code, value in zip(codes, opened.Data[0]):
                first_opens[code] = value

    included = []
    for row in observations:
        is_current_year_ipo = isinstance(row["ipo_date"], date) and row["ipo_date"].year == td.year
        basis = "年初至今（Wind PCT_CHG_PER）"
        ytd_return = row["wind_ytd"]
        base_open = ""
        if is_current_year_ipo:
            base_open = first_opens.get(row["ts_code"])
            ytd_return = ((float(row["close"]) / float(base_open)) - 1) * 100 if finite(row["close"]) and finite(base_open) and float(base_open) != 0 else None
            basis = "当年上市：上市首日开盘价至今"
        if minimum is not None and not finite(ytd_return):
            excluded.append({"ts_code": row["ts_code"], "name": row["name"], "source": sector, "reason": "年度涨幅无法计算"})
        elif minimum is not None and float(ytd_return) < minimum:
            excluded.append({"ts_code": row["ts_code"], "name": row["name"], "source": sector, "reason": f"年度涨幅{float(ytd_return):.6f}%低于{minimum:g}%"})
        else:
            included.append({
                "ts_code": row["ts_code"], "name": row["name"], "theme": "全部A股",
                "wind_sector": sector, "trade_date": td, "ipo_date": row["ipo_date"] or "",
                "ytd_return_pct": "" if not finite(ytd_return) else round(float(ytd_return), 6), "ytd_return_basis": basis,
                "listing_first_day_open": "" if not finite(base_open) else round(float(base_open), 6),
            })
    included.sort(key=lambda item: item["ts_code"] if minimum is None else (-(item["ytd_return_pct"] or float("-inf")), item["ts_code"]))
    write(out / "ai_theme.csv", included, ["ts_code", "name", "theme", "wind_sector", "trade_date", "ipo_date", "ytd_return_pct", "ytd_return_basis", "listing_first_day_open"])
    write(out / "etf_top50.csv", [], ["trade_date", "etf_code", "etf_name", "ytd_return", "invest_type"])
    write(out / "etf_top_holdings.csv", [], ["trade_date", "holding_report_period", "etf_code", "etf_name", "etf_rank_50", "etf_ytd_return", "holding_rank_10", "holding_weight_pct", "ts_code", "name", "holding_source"])
    write(out / "excluded_stocks.csv", excluded, ["ts_code", "name", "source", "reason"])
    rule = "无年度涨幅门槛" if minimum is None else f"年度涨幅>={minimum:g}%"
    print(f"trade day={td}; 全部A股={len(raw)}; {rule}={len(included)}; 排除={len(excluded)}")
    return included


def finite(value):
    if value is None or value == "":
        return False
    try:
        return not math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def normalized_fund_name(value):
    return str(value or "").replace("交易型开放式指数证券投资基金", "ETF").replace(" ", "")


def report_period_candidates(asof):
    """Return completed standard report periods, newest first."""
    periods = []
    for year in range(asof.year, asof.year - 3, -1):
        for month, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
            period = date(year, month, day)
            if period <= asof:
                periods.append(period)
    return sorted(periods, reverse=True)


def cached_holding_fields(cache_dir, period, etf_code):
    cache_path = cache_dir / f"{period:%Y%m%d}" / f"{etf_code.replace('.', '_')}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    result = wset_retry(
        "allfundhelddetail",
        f"rptdate={period:%Y%m%d};windcode={etf_code}",
        f"{etf_code}@{period:%Y-%m-%d}",
    )
    fields = {str(field).lower(): values for field, values in zip(result.Fields, result.Data)} if result.Data else {}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(fields, ensure_ascii=False, default=str), encoding="utf-8")
    return fields


def cached_holding_batch(cache_dir, period, etfs):
    results, missing = {}, []
    for etf in etfs:
        cache_path = cache_dir / f"{period:%Y%m%d}" / f"{etf['etf_code'].replace('.', '_')}.json"
        if cache_path.exists():
            fields = json.loads(cache_path.read_text(encoding="utf-8"))
            # Do not make a transient empty/mismatched Wind response permanent.
            # Empty batch matches are retried below and, if necessary, queried
            # one fund at a time.
            if any(fields.get("stock_code", [])):
                results[etf["etf_code"]] = fields
            else:
                missing.append(etf)
        else:
            missing.append(etf)
    if not missing:
        return results
    query = wset_retry(
        "allfundhelddetail",
        f"rptdate={period:%Y%m%d};windcode={','.join(etf['etf_code'] for etf in missing)}",
        f"ETF batch holdings@{period:%Y-%m-%d}",
    )
    all_fields = {str(field).lower(): values for field, values in zip(query.Fields, query.Data)} if query.Data else {}
    fund_names = all_fields.get("sec_name", [])
    name_query = ok(w.wss(",".join(etf["etf_code"] for etf in missing), "fund_fullname", ""), "ETF full names")
    full_names = dict(zip((etf["etf_code"] for etf in missing), name_query.Data[0]))
    for etf in missing:
        expected_name = full_names.get(etf["etf_code"], etf["etf_name"])
        indices = [i for i, name in enumerate(fund_names) if normalized_fund_name(name) == normalized_fund_name(expected_name)]
        fields = {field: [values[i] for i in indices] for field, values in all_fields.items()}
        # Wind's allfundhelddetail sec_name is not always the same as fund_fullname
        # (for example, some names contain an index provider or the word "主题").
        # A single-fund query is unambiguous and repairs those batch mismatches.
        if not any(fields.get("stock_code", [])):
            single = wset_retry(
                "allfundhelddetail",
                f"rptdate={period:%Y%m%d};windcode={etf['etf_code']}",
                f"{etf['etf_code']}@{period:%Y-%m-%d}",
            )
            fields = {str(field).lower(): values for field, values in zip(single.Fields, single.Data)} if single.Data else {}
        cache_path = cache_dir / f"{period:%Y%m%d}" / f"{etf['etf_code'].replace('.', '_')}.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(fields, ensure_ascii=False, default=str), encoding="utf-8")
        results[etf["etf_code"]] = fields
    return results


def latest_available_holding_period(asof, top, required_holdings, cache_dir, allow_partial=False):
    """Select the newest period for which every selected ETF has enough Wind holdings."""
    best_partial = None
    for period in report_period_candidates(asof):
        results = cached_holding_batch(cache_dir, period, top)
        complete = True
        for etf in top:
            fields = results.get(etf["etf_code"], {})
            count = sum(1 for code in fields.get("stock_code", []) if code)
            if count < required_holdings:
                complete = False
                if not allow_partial:
                    break
                continue
        if complete and len(results) == len(top):
            return period, results
        if allow_partial and results and (best_partial is None or len(results) > len(best_partial[1])):
            best_partial = (period, results)
    if allow_partial and best_partial:
        return best_partial
    raise RuntimeError("Wind未找到覆盖全部入选ETF的有效持仓报告期")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/universe.json")
    parser.add_argument("--output-dir", default="data/wind")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--allow-partial-etf-history", action="store_true", help="历史回填时允许少数ETF缺少统一报告期持仓")
    args = parser.parse_args()
    cfg = json.load(open(args.config, encoding="utf-8-sig"))
    out = Path(args.output_dir)
    asof = date.fromisoformat(args.as_of)
    started = w.start()
    if started.ErrorCode or not w.isconnected(): raise RuntimeError(started)
    # The scheduled job runs after the A-share close at 21:00, so use the
    # current trading day when applicable; weekends/holidays resolve to the
    # most recent completed trading day.
    td = ok(w.tdaysoffset(0, asof.isoformat(), ""), "trade day").Data[0][0]
    td = td.date() if hasattr(td, "date") else td

    if cfg.get("mode") in {"all_a_shares", "all_a_shares_ytd"}:
        all_a_share_universe(cfg, out, asof, td)
        return

    theme = {}
    for broad, sectors in cfg["theme_mapping"].items():
        for sector in sectors:
            result = ok(w.wset("sectorconstituent", f"date={td};sector={sector}"), sector)
            if not result.Data: continue
            for code, name in zip(result.Data[1], result.Data[2]):
                if str(code).endswith((".SH", ".SZ", ".BJ")):
                    item = theme.setdefault(code, {"ts_code": code, "name": name, "broad_theme": set(), "wind_sector": set()})
                    item["broad_theme"].add(broad); item["wind_sector"].add(sector)
    excluded, theme_rows = [], []
    for item in theme.values():
        reason = exclusion_reason(item["ts_code"], item["name"])
        if reason: excluded.append({"ts_code": item["ts_code"], "name": item["name"], "source": "theme", "reason": reason})
        else: theme_rows.append({"ts_code": item["ts_code"], "name": item["name"], "theme": ",".join(sorted(item["broad_theme"])), "wind_sector": ",".join(sorted(item["wind_sector"]))})

    result = ok(w.wset("sectorconstituent", f'date={td};sector={cfg["etf"]["universe_sector"]}'), "ETF universe")
    etfs = [(c, n) for c, n in zip(result.Data[1], result.Data[2]) if str(c).endswith((".SH", ".SZ"))]
    stats = []
    for batch in chunks(etfs):
        codes, names = [x[0] for x in batch], dict(batch)
        query = ok(w.wss(",".join(codes), "pct_chg_per,fund_investtype", f"startDate={asof.year}0101;endDate={td:%Y%m%d};tradeDate={td:%Y%m%d}"), "ETF YTD")
        for code, ret, invest_type in zip(codes, query.Data[0], query.Data[1]):
            if ret is None or (isinstance(ret, float) and math.isnan(ret)): continue
            if any(x in str(invest_type) for x in cfg["etf"]["exclude_investment_types_containing"]): continue
            stats.append({"trade_date": td, "etf_code": code, "etf_name": names[code], "ytd_return": round(float(ret), 6), "invest_type": invest_type})
    stats.sort(key=lambda x: -x["ytd_return"])
    top = stats[:cfg["etf"]["top_n_ytd"]]

    holding_period, holding_results = latest_available_holding_period(asof, top, cfg["etf"]["top_holdings"], out.parent / "holding_cache", args.allow_partial_etf_history)
    holdings = []
    for rank, etf in enumerate(top, 1):
        fields = holding_results.get(etf["etf_code"])
        if fields is None:
            excluded.append({"ts_code": etf["etf_code"], "name": etf["etf_name"], "source": "ETF历史回填", "reason": f"{holding_period:%Y-%m-%d}无足够持仓数据"})
            continue
        if not fields: continue
        stocks = []
        for code, name, weight in zip(fields.get("stock_code", []), fields.get("stock_name", []), fields.get("proportiontonetvalue", [])):
            if weight is None or (isinstance(weight, float) and math.isnan(weight)): continue
            stocks.append((code, name, float(weight)))
        stocks.sort(key=lambda x: -x[2])
        for holding_rank, (code, name, weight) in enumerate(stocks[:cfg["etf"]["top_holdings"]], 1):
            reason = exclusion_reason(code, name)
            if reason:
                excluded.append({"ts_code": code, "name": name, "source": f'ETF:{etf["etf_code"]}', "reason": reason}); continue
            holdings.append({"trade_date": td, "holding_report_period": holding_period, "etf_code": etf["etf_code"], "etf_name": etf["etf_name"], "etf_rank_50": rank, "etf_ytd_return": etf["ytd_return"], "holding_rank_10": holding_rank, "holding_weight_pct": round(weight, 6), "ts_code": code, "name": name, "holding_source": "Wind基金定期报告前十大重仓股（按占基金净值比例降序）"})

    if top and not holdings:
        raise RuntimeError(f"{holding_period:%Y-%m-%d} ETF持仓匹配结果为空，拒绝生成不完整历史股票池")

    write(out / "ai_theme.csv", theme_rows, ["ts_code", "name", "theme", "wind_sector"])
    write(out / "etf_top50.csv", top, ["trade_date", "etf_code", "etf_name", "ytd_return", "invest_type"])
    write(out / "etf_top_holdings.csv", holdings, ["trade_date", "holding_report_period", "etf_code", "etf_name", "etf_rank_50", "etf_ytd_return", "holding_rank_10", "holding_weight_pct", "ts_code", "name", "holding_source"])
    write(out / "excluded_stocks.csv", excluded, ["ts_code", "name", "source", "reason"])
    print(f"trade day={td}; theme={len(theme_rows)}; ETF top50={len(top)}; holding period={holding_period}; holding rows={len(holdings)}")


if __name__ == "__main__":
    main()
