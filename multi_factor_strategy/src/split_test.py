"""Out-of-sample test of adding slow (valuation / financial) factors to the LightGBM top 20.

Selection, using only data before 2023 (src/factor_horizon_ic.py, cross-sections 2020-02 to
2022-09, so every 60-day return window ends before 2023): from the financial and valuation factors
not already in the model, keep those with |mean 60-day Rank IC| >= 0.03. Result: sp, bp, profit_yoy.

Test: the baseline model and the model with the selected factors, same data, costs and 20 start
offsets (timing_tests.sleeve). Reported separately for 2020-2022 (the period the factors were
chosen on) and 2023-2025 (out of sample). Only the 2023-2025 rows test the idea.

Output: results/split_test.md
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import timing_tests as tt  # noqa: E402
import factor_horizon_ic as fh  # noqa: E402

jq = tt.jq
SELECTED = ["sp", "bp", "profit_yoy"]
BASE = list(jq.FEATURES)
PERIODS = {"2020-2022 (selection period)": ("2020-01-01", "2022-12-31"),
           "2023-2025 (out of sample)": ("2023-01-01", "2025-12-31")}


def install_extra_factors(data):
    """Wrap Data.raw_frame so every cross-section also carries the selected factors (percentile ranks)."""
    fin, _ = fh.load_financials()
    codes = sorted(set().union(*data.universe.values))
    val = fh.load_valuation(codes, data.days)
    original = jq.Data.raw_frame

    def raw_frame(self, date, stocks):
        out = original(self, date, stocks)
        if out is None:
            return None
        df = out[0]
        for col in SELECTED:
            x = val[col].loc[date].reindex(df.index) if col in val else fh.latest(fin, date, col).reindex(df.index)
            x = x.rank(pct=True)
            df[col] = x.fillna(x.median() if pd.notna(x.median()) else 0.5)
        return (df,) + tuple(out[1:])

    jq.Data.raw_frame = raw_frame


def main():
    out_dir = jq.OUT.parent / "split_test"
    out_dir.mkdir(parents=True, exist_ok=True)
    for col in SELECTED:
        jq.LITE_FACTORS.append((col, "all", {}))   # weight 0 in the linear score; LightGBM only
    install_extra_factors(jq.Data())   # loads once to build the valuation table, then patches raw_frame
    data, days, close, ret, feats, tradable, buyable = tt.load()
    pos = {d: i for i, d in enumerate(days)}
    ctx = (data, days, close, ret, feats, tradable, buyable, pos, jq.Model())

    navs = {}
    for name, cols in (("Baseline (10 active factors)", BASE), ("Baseline + sp, bp, profit_yoy", BASE + SELECTED)):
        jq.FEATURES[:] = cols
        navs[name] = [tt.sleeve(ctx, k, 20) for k in range(20)]
        print(f"{name}: done", flush=True)
    jq.FEATURES[:] = BASE

    idx = data.index_close.reindex(days).ffill()
    pct = lambda x: f"{x * 100:.1f}%"  # noqa: E731
    lines = ["# Out-of-sample test: adding slow factors chosen on pre-2023 data", "",
             "Factors chosen on 2020-02 to 2022-09 cross-sections (|60-day Rank IC| >= 0.03): "
             + ", ".join(SELECTED) + ". Historical backtest, CSI 300 + CSI 500, net of costs, 20 start offsets.", ""]
    rows = []
    for pname, (a, b) in PERIODS.items():
        lines += [f"## {pname}", "",
                  "| Model | Ann. return (min / median / max) | Sharpe (min / median / max) | "
                  "Combined: ann. return / Sharpe / max DD |", "|---|---|---|---|"]
        for name, ns in navs.items():
            per = []
            for k, n in enumerate(ns):
                s = n.loc[a:b]
                st, _ = tt.stats(s)
                per.append(st)
                rows.append({"period": pname, "model": name, "offset": k, **st})
            per = pd.DataFrame(per)
            start = max(n.loc[a:b].index[0] for n in ns)
            end = min(n.loc[a:b].index[-1] for n in ns)
            comb, _ = tt.stats(tt.combine(ns, start, end))
            lines.append(f"| {name} | {pct(per.ann_return.min())} / {pct(per.ann_return.median())} / "
                         f"{pct(per.ann_return.max())} | {per.sharpe.min():.2f} / {per.sharpe.median():.2f} / "
                         f"{per.sharpe.max():.2f} | {pct(comb['ann_return'])} / {comb['sharpe']:.2f} / {pct(comb['max_dd'])} |")
        st, _ = tt.stats(idx.loc[a:b])
        lines += [f"| CSI All Share index | | | {pct(st['ann_return'])} / {st['sharpe']:.2f} / {pct(st['max_dd'])} |", ""]
    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "offsets.csv", index=False)
    # paired comparison: same offset, same period
    lines += ["## Paired by start offset", "", "| Period | Offsets where the augmented model has the higher Sharpe | "
              "Median Sharpe difference |", "|---|---|---|"]
    names = list(navs)
    for pname in PERIODS:
        p = df[df.period == pname].pivot(index="offset", columns="model", values="sharpe")
        diff = p[names[1]] - p[names[0]]
        lines.append(f"| {pname} | {(diff > 0).sum()} / 20 | {diff.median():+.2f} |")
    res = Path(__file__).resolve().parent.parent / "results"
    (res / "split_test.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
