"""Which factors are slow and which are fast? Rank IC of single factors at 20 / 60 / 120 trading days.

Hypothesis to test: financial (fundamental) factors describe the slow, long-horizon trend and
momentum factors the medium-horizon trend. If so, financial factors' IC should hold up or grow
with the horizon, and momentum factors' IC should peak at a medium horizon.

Universe: CSI 300 + CSI 500 members on each date, tradable. Cross-sections every 20 trading days,
2019-11 to 2025-12. Financial data are point in time: each stock's latest non-blank value
announced (or due by the statutory deadline) in the last 270 days. Signs are as defined (no
flipping), so a negative IC means the opposite tilt worked.

Note: the 60- and 120-day IC series overlap (cross-sections are 20 days apart), so their means are
fine but their t-statistics would be overstated; the report gives mean IC and the share of
cross-sections with a positive IC.

Output: results/factor_horizon_ic.md
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jq_port_backtest as jq  # noqa: E402
import open_data as od  # noqa: E402

HORIZONS = (20, 60, 120)
STEP = 20


def load_financials():
    frames = []
    for path in sorted(od.FUND_DIR.glob("*.csv")):
        df = pd.read_csv(path, dtype={"股票代码": str})
        period = pd.Timestamp(path.stem)
        ann = pd.to_datetime(df.get("最新公告日期"), errors="coerce")
        dl = od.statutory_deadline(period)
        num = lambda c: pd.to_numeric(df[c], errors="coerce")  # noqa: E731
        eps = num("每股收益")
        frames.append(pd.DataFrame({
            "code": df["股票代码"].map(od.to_bs_code), "period": period,
            "available": ann.where(ann > period, dl).fillna(dl),
            "roe": num("净资产收益率"), "gross_margin": num("销售毛利率"),
            "revenue_yoy": num("营业总收入-同比增长"), "profit_yoy": num("净利润-同比增长"),
            "revenue_qoq": num("营业总收入-季度环比增长"), "profit_qoq": num("净利润-季度环比增长"),
            # cash earnings quality: operating cash flow per share relative to EPS (positive EPS only)
            "cash_quality": (num("每股经营现金流量") / eps).where(eps > 0),
        }))
    fin = pd.concat(frames, ignore_index=True)
    bal = []
    for path in sorted((jq.EXTRA / "balance").glob("*.csv")):
        df = pd.read_csv(path, dtype={"股票代码": str})
        period = pd.Timestamp(path.stem)
        ann = pd.to_datetime(df["公告日期"], errors="coerce")
        dl = od.statutory_deadline(period)
        bal.append(pd.DataFrame({
            "code": df["股票代码"].map(od.to_bs_code), "period": period,
            "available": ann.where(ann > period, dl).fillna(dl),
            "low_debt": -pd.to_numeric(df["资产负债率"], errors="coerce"),
            "low_asset_growth": -pd.to_numeric(df["资产-总资产同比"], errors="coerce"),
        }))
    return fin, pd.concat(bal, ignore_index=True)


def latest(frame, date, col):
    f = frame.dropna(subset=[col])
    f = f[(f["available"] <= date) & (f["available"] > date - pd.Timedelta(days=270))]
    return f.sort_values(["period", "available"]).groupby("code")[col].last()


def load_valuation(codes, days):
    cols = {"peTTM": "ep", "pbMRQ": "bp", "psTTM": "sp", "pcfNcfTTM": "cfp"}
    out = {v: {} for v in cols.values()}
    for c in codes:
        p = od.PRICE_DIR / f"{c}.csv"
        if not p.exists():
            continue
        px = pd.read_csv(p, usecols=["date"] + list(cols), parse_dates=["date"]).set_index("date")
        px = px[~px.index.duplicated()].reindex(days).ffill()
        for k, v in cols.items():
            x = pd.to_numeric(px[k], errors="coerce")
            out[v][c] = (1 / x).where(x > 0)   # yield form; negative earnings -> missing
    return {k: pd.DataFrame(v) for k, v in out.items()}


def main():
    data = jq.Data()
    days = list(data.days[data.days >= data.universe.index[0]])
    close = data.close.loc[days].ffill()
    pos = {d: i for i, d in enumerate(days)}
    fin, bal = load_financials()
    codes = sorted(set().union(*data.universe.values))
    val = load_valuation(codes, pd.DatetimeIndex(days))
    groups = {
        "Financial": ["roe", "gross_margin", "revenue_yoy", "profit_yoy", "revenue_qoq", "profit_qoq",
                      "cash_quality", "low_debt", "low_asset_growth"],
        "Valuation": ["ep", "bp", "sp", "cfp"],
        "Momentum / reversal": ["ret_20", "ret_60", "ret_120", "mom_250_20", "ma60_slope"],
    }
    rows = []
    for d in days[250::STEP]:
        i = pos[d]
        if i + max(HORIZONS) >= len(days) or i < 250:
            continue
        stocks = sorted(s for s in data.members(d) if s in close.columns and bool(data.tradable.at[d, s]))
        if len(stocks) < 100:
            continue
        c = close[stocks]
        f = pd.DataFrame(index=stocks)
        for col in groups["Financial"][:7]:
            f[col] = latest(fin, d, col)
        for col in ("low_debt", "low_asset_growth"):
            f[col] = latest(bal, d, col)
        for col in groups["Valuation"]:
            f[col] = val[col].loc[d].reindex(stocks) if col in val else np.nan
        now = c.iloc[i]
        f["ret_20"] = now / c.iloc[i - 20] - 1
        f["ret_60"] = now / c.iloc[i - 60] - 1
        f["ret_120"] = now / c.iloc[i - 120] - 1
        f["mom_250_20"] = c.iloc[i - 20] / c.iloc[i - 250] - 1
        f["ma60_slope"] = c.iloc[i - 59:i + 1].mean() / c.iloc[i - 69:i - 9].mean() - 1
        # returns measured from the next day's close (signal at t, trade at t+1)
        for h in HORIZONS:
            fwd = c.iloc[i + 1 + h] / c.iloc[i + 1] - 1
            for col in f.columns:
                rows.append({"date": d, "factor": col, "h": h, "ic": jq.rank_ic(f[col].dropna(), fwd.dropna()),
                             "coverage": f[col].notna().mean()})
    ic = pd.DataFrame(rows)
    out = Path(__file__).resolve().parent.parent / "results"
    ic.to_csv(jq.OUT.parent / "factor_horizon_ic.csv", index=False)

    mean = ic.pivot_table(index="factor", columns="h", values="ic", aggfunc="mean")
    pos_share = ic.assign(p=ic.ic > 0).pivot_table(index="factor", columns="h", values="p", aggfunc="mean")
    cov = ic.groupby("factor").coverage.mean()
    n = ic.date.nunique()
    lines = ["# Single-factor Rank IC by horizon", "",
             f"CSI 300 + CSI 500, {n} cross-sections every {STEP} trading days, "
             f"{ic.date.min().date()} to {ic.date.max().date()}. Historical data, free BaoStock / AkShare sources.", "",
             "| Group | Factor | Coverage | IC 20d | IC 60d | IC 120d | Share > 0 (20d / 60d / 120d) |",
             "|---|---|---|---|---|---|---|"]
    for g, cols in groups.items():
        for col in cols:
            if col not in mean.index:
                continue
            m, p = mean.loc[col], pos_share.loc[col]
            lines.append(f"| {g} | {col} | {cov[col]:.0%} | {m[20]:.3f} | {m[60]:.3f} | {m[120]:.3f} | "
                         f"{p[20]:.0%} / {p[60]:.0%} / {p[120]:.0%} |")
    out.mkdir(exist_ok=True)
    (out / "factor_horizon_ic.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
