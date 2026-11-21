"""Monthly multi-factor panel from free, open data (no Wind terminal needed).

Sources
-------
* BaoStock (http://baostock.com, free, no registration):
  trading calendar, point-in-time CSI 300 / CSI 500 constituents, and daily
  back-adjusted prices with turnover, PE(TTM), PB(MRQ), PS(TTM), PCF(TTM),
  trading status and ST flag.
* AkShare (https://github.com/akfamily/akshare, open source), Eastmoney
  quarterly earnings reports for all A-shares (``stock_yjbb_em``): ROE, gross
  margin, revenue and net profit growth, with the announcement date.

Point-in-time rules
-------------------
* Universe on each month-end = CSI 300 + CSI 500 constituents on that date
  (removes survivorship bias within the index universe); ST stocks, stocks not
  trading that day and stocks with fewer than 120 trading days of history are
  dropped.
* A quarterly report is used only after its announcement date.  When the date
  is missing, the statutory deadline is used (Q1: Apr 30, H1: Aug 31,
  Q3: Oct 31, annual: Apr 30 of the next year).  Eastmoney reports the latest
  announcement date, which can be later than the first release; this only
  makes the data staler, never earlier than it was available.
* The label is the return from the close of the first trading day after the
  signal date to the close of the first trading day after the next signal
  date, so signals are never traded at a price they were computed from.

Usage
-----
    pip install baostock akshare
    python src/open_data.py all --start 2019-01-01 --end 2025-10-31
    python src/factor_baselines.py --mode backtest --config config/open_data.json

Each step caches to ``data/open/`` and can be re-run; already downloaded
stocks and quarters are skipped.
"""
import argparse
import socket
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "open"
PRICE_DIR = CACHE / "prices"
FUND_DIR = CACHE / "fundamentals"
UNIVERSE_FILE = CACHE / "universe.csv"
PANEL_FILE = CACHE / "open_panel.csv"

PRICE_FIELDS = "date,code,close,amount,turn,peTTM,pbMRQ,psTTM,pcfNcfTTM,tradestatus,isST"
FEATURES = [
    "ep", "bp", "sp", "cfp",                       # value
    "size",                                         # small cap (negative log float cap)
    "mom_12_1", "rev_1m", "low_vol", "low_turn",   # price and liquidity
    "roe", "gross_margin",                          # quality
    "revenue_yoy", "profit_yoy", "profit_qoq",      # growth
]


# ------------------------------------------------------------ BaoStock
def bs_login():
    import baostock as bs
    socket.setdefaulttimeout(60)  # a stalled BaoStock socket otherwise hangs forever
    result = bs.login()
    if result.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {result.error_msg}")
    return bs


def bs_frame(result) -> pd.DataFrame:
    if result.error_code != "0":
        raise RuntimeError(result.error_msg)
    rows = []
    while result.next():
        rows.append(result.get_row_data())
    return pd.DataFrame(rows, columns=result.fields)


def month_ends(bs, start: str, end: str) -> list:
    cal = bs_frame(bs.query_trade_dates(start_date=start, end_date=end))
    days = pd.to_datetime(cal.loc[cal["is_trading_day"] == "1", "calendar_date"])
    return sorted(days.groupby(days.dt.to_period("M")).max().dt.strftime("%Y-%m-%d"))


def build_universe(start: str, end: str):
    bs = bs_login()
    try:
        frames = []
        for date in month_ends(bs, start, end):
            for query in (bs.query_hs300_stocks, bs.query_zz500_stocks):
                cons = bs_frame(query(date=date))
                frames.append(pd.DataFrame({"snapshot_date": date, "code": cons["code"], "name": cons["code_name"]}))
        universe = pd.concat(frames).drop_duplicates(["snapshot_date", "code"])
    finally:
        bs.logout()
    CACHE.mkdir(parents=True, exist_ok=True)
    universe.to_csv(UNIVERSE_FILE, index=False, encoding="utf-8")
    print(f"Universe: {universe['snapshot_date'].nunique()} month-ends, {universe['code'].nunique()} stocks")


def download_prices(start: str, end: str, shard: int = 0, shards: int = 1):
    universe = pd.read_csv(UNIVERSE_FILE, dtype=str)
    codes = sorted(universe["code"].unique())
    first = (pd.Timestamp(start) - pd.DateOffset(years=1, months=2)).strftime("%Y-%m-%d")
    last = (pd.Timestamp(end) + pd.DateOffset(months=2)).strftime("%Y-%m-%d")
    PRICE_DIR.mkdir(parents=True, exist_ok=True)
    bs = bs_login()
    try:
        todo = [c for k, c in enumerate(codes) if k % shards == shard and not (PRICE_DIR / f"{c}.csv").exists()]
        for i, code in enumerate(todo, 1):
            for attempt in range(3):
                try:
                    data = bs_frame(bs.query_history_k_data_plus(
                        code, PRICE_FIELDS, start_date=first, end_date=last, frequency="d", adjustflag="1"))
                    data.to_csv(PRICE_DIR / f"{code}.csv", index=False)
                    break
                except Exception as exc:  # network hiccup: re-login and retry
                    print(f"{code} attempt {attempt + 1} failed: {exc}")
                    time.sleep(2)
                    bs.logout()
                    bs = bs_login()
            if i % 50 == 0:
                print(f"prices {i}/{len(todo)}", flush=True)
    finally:
        bs.logout()


# ------------------------------------------------------------- AkShare
def quarter_ends(start: str, end: str) -> list:
    first = pd.Timestamp(start) - pd.DateOffset(months=15)
    return [d.strftime("%Y%m%d") for d in pd.date_range(first, end, freq="Q")]


def download_fundamentals(start: str, end: str):
    import akshare as ak
    FUND_DIR.mkdir(parents=True, exist_ok=True)
    for q in quarter_ends(start, end):
        path = FUND_DIR / f"{q}.csv"
        if path.exists():
            continue
        try:
            ak.stock_yjbb_em(date=q).to_csv(path, index=False, encoding="utf-8")
            print(f"fundamentals {q}", flush=True)
        except Exception as exc:  # not yet published or temporary failure
            print(f"fundamentals {q} skipped: {exc}")


def statutory_deadline(period: pd.Timestamp) -> pd.Timestamp:
    if period.month == 12:
        return pd.Timestamp(period.year + 1, 4, 30)
    return {3: pd.Timestamp(period.year, 4, 30), 6: pd.Timestamp(period.year, 8, 31),
            9: pd.Timestamp(period.year, 10, 31)}[period.month]


def to_bs_code(code: str) -> str:
    code = str(code).zfill(6)
    prefix = "sh" if code[0] in "69" else "bj" if code[0] in "48" else "sz"
    return f"{prefix}.{code}"


def load_fundamentals() -> pd.DataFrame:
    rename = {
        "股票代码": "code", "净资产收益率": "roe", "销售毛利率": "gross_margin",
        "营业总收入-同比增长": "revenue_yoy", "净利润-同比增长": "profit_yoy",
        "净利润-季度环比增长": "profit_qoq", "最新公告日期": "announced",
    }
    frames = []
    for path in sorted(FUND_DIR.glob("*.csv")):
        df = pd.read_csv(path, dtype={"股票代码": str}).rename(columns=rename)
        period = pd.Timestamp(path.stem)
        df["period"] = period
        announced = pd.to_datetime(df.get("announced"), errors="coerce")
        deadline = statutory_deadline(period)
        # never before the period ends; fall back to the legal deadline
        df["available"] = announced.where(announced > period, deadline).fillna(deadline)
        frames.append(df[["code", "period", "available", "roe", "gross_margin",
                          "revenue_yoy", "profit_yoy", "profit_qoq"]])
    fund = pd.concat(frames, ignore_index=True)
    fund["code"] = fund["code"].map(to_bs_code)
    for col in ["roe", "gross_margin", "revenue_yoy", "profit_yoy", "profit_qoq"]:
        fund[col] = pd.to_numeric(fund[col], errors="coerce")
    return fund


# --------------------------------------------------------------- panel
def price_features(code: str, dates: list) -> pd.DataFrame:
    path = PRICE_DIR / f"{code}.csv"
    if not path.exists():
        return pd.DataFrame()
    px = pd.read_csv(path)
    if px.empty:
        return pd.DataFrame()
    px["date"] = pd.to_datetime(px["date"])
    for col in ["close", "amount", "turn", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM"]:
        px[col] = pd.to_numeric(px[col], errors="coerce")
    px = px.set_index("date").sort_index()
    traded = px[px["tradestatus"] == 1] if px["tradestatus"].dtype != object else px[px["tradestatus"] == "1"]
    close = traded["close"]
    ret = close.pct_change()
    float_cap = (traded["amount"] / (traded["turn"] / 100)).replace([np.inf, -np.inf], np.nan)

    rows = []
    for i, date in enumerate(dates):
        t = pd.Timestamp(date)
        if t not in px.index or px.at[t, "tradestatus"] in (0, "0") or str(px.at[t, "isST"]) == "1":
            continue
        hist = close.loc[:t]
        if len(hist) < 120:
            continue
        row = px.loc[t]
        inv = lambda v: 1.0 / v if pd.notna(v) and v != 0 else np.nan
        cap = float_cap.loc[:t].tail(20).median()
        rec = {
            "snapshot_date": date, "code": code,
            "ep": inv(row["peTTM"]), "bp": inv(row["pbMRQ"]), "sp": inv(row["psTTM"]), "cfp": inv(row["pcfNcfTTM"]),
            "size": -np.log(cap) if pd.notna(cap) and cap > 0 else np.nan,
            "mom_12_1": hist.iloc[-22] / hist.iloc[-253] - 1 if len(hist) >= 253 else np.nan,
            "rev_1m": -(hist.iloc[-1] / hist.iloc[-22] - 1),
            "low_vol": -ret.loc[:t].tail(60).std(),
            "low_turn": -traded["turn"].loc[:t].tail(20).mean(),
            "mkt_cap": cap,
        }
        # label: next trading day after t -> next trading day after the next signal date
        if i + 1 < len(dates):
            after = close.loc[close.index > t]
            after_next = close.loc[close.index > pd.Timestamp(dates[i + 1])]
            if len(after) and len(after_next):
                rec["fwd_return"] = after_next.iloc[0] / after.iloc[0] - 1
        rows.append(rec)
    return pd.DataFrame(rows)


def build_panel():
    universe = pd.read_csv(UNIVERSE_FILE, dtype=str)
    dates = sorted(universe["snapshot_date"].unique())
    members = set(zip(universe["snapshot_date"], universe["code"]))
    parts = [price_features(code, dates) for code in sorted(universe["code"].unique())]
    panel = pd.concat([p for p in parts if not p.empty], ignore_index=True)
    panel = panel[[m in members for m in zip(panel["snapshot_date"], panel["code"])]]

    fund = load_fundamentals().sort_values("available")
    panel["_t"] = pd.to_datetime(panel["snapshot_date"])
    panel = pd.merge_asof(panel.sort_values("_t"), fund.drop(columns="period"),
                          left_on="_t", right_on="available", by="code", direction="backward")
    stale = panel["_t"] - panel["available"] > pd.Timedelta(days=270)  # no report for ~3 quarters
    panel.loc[stale, ["roe", "gross_margin", "revenue_yoy", "profit_yoy", "profit_qoq"]] = np.nan

    names = universe.drop_duplicates("code", keep="last").set_index("code")["name"]
    panel["name"] = panel["code"].map(names)
    panel["ts_code"] = panel["code"].str[3:] + "." + panel["code"].str[:2].str.upper()
    cols = ["snapshot_date", "ts_code", "name", *FEATURES, "mkt_cap", "fwd_return"]
    panel = panel[cols].sort_values(["snapshot_date", "ts_code"])
    panel.to_csv(PANEL_FILE, index=False, encoding="utf-8")
    print(f"Panel: {len(panel)} rows, {panel['snapshot_date'].nunique()} month-ends -> {PANEL_FILE}")
    print(panel[FEATURES].notna().mean().round(3).to_string())


def main():
    parser = argparse.ArgumentParser(description="Build a monthly factor panel from BaoStock + AkShare")
    parser.add_argument("step", choices=["universe", "prices", "fundamentals", "panel", "all"])
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--end", default="2025-10-31")
    parser.add_argument("--shard", type=int, default=0, help="prices: this worker's index (run several in parallel)")
    parser.add_argument("--shards", type=int, default=1, help="prices: number of parallel workers")
    args = parser.parse_args()
    if args.step in ("universe", "all"):
        build_universe(args.start, args.end)
    if args.step in ("prices", "all"):
        download_prices(args.start, args.end, args.shard, args.shards)
    if args.step in ("fundamentals", "all"):
        download_fundamentals(args.start, args.end)
    if args.step in ("panel", "all"):
        build_panel()


if __name__ == "__main__":
    main()
