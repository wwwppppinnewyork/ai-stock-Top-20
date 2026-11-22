"""Which of the model's own factors stopped working, and when?

For each factor the LightGBM model uses (the same standardized inputs as timing_tests.py), the daily
Rank IC against the forward 20-trading-day return (signal at t, trade at t+1), aggregated by
calendar year: mean IC, ICIR = mean / standard deviation of the daily IC, and the share of days
with a positive IC. The model score's own IC by year comes from timing_tests (output/timing_tests/ic_decay.csv).

Daily IC series with 20-day returns overlap, so ICIR here is a stability ratio, not a t-statistic.

Output: results/factor_ic_by_year.md
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import timing_tests as tt  # noqa: E402

jq = tt.jq
H = 20


def main():
    data, days, close, ret, feats, tradable, buyable = tt.load()
    pos = {d: i for i, d in enumerate(days)}
    active = [c for c in jq.FEATURES if not c.startswith("industry_")]   # industry factors are 0 without industries
    rows = []
    for d, f in feats.items():
        e = pos[d] + 1
        if e + H >= len(days):
            continue
        fwd = close.loc[days[e + H], f.index] / close.loc[days[e], f.index] - 1
        row = {"date": d}
        for c in active:
            row[c] = jq.rank_ic(f[c], fwd)
        rows.append(row)
    ic = pd.DataFrame(rows).set_index("date")
    model_path = jq.OUT.parent / "timing_tests" / "ic_decay.csv"
    if model_path.exists():
        m = pd.read_csv(model_path, parse_dates=["date"]).set_index("date")["20"]
        ic["MODEL SCORE"] = m.reindex(ic.index)
    ic.to_csv(jq.OUT.parent / "factor_ic_by_year.csv")

    yr = ic.index.year
    mean = ic.groupby(yr).mean()
    icir = mean / ic.groupby(yr).std()
    full_mean, full_icir = ic.mean(), ic.mean() / ic.std()
    cols = [c for c in ic.columns]
    years = list(mean.index)
    lines = ["# Model factors: 20-day Rank IC by year", "",
             f"CSI 300 + CSI 500, daily cross-sections {ic.index.min().date()} to {ic.index.max().date()}, "
             "the model's standardized inputs (no industry classification). Signs as defined in the model.", "",
             "## Mean IC", "", "| Factor | " + " | ".join(str(y) for y in years) + " | All |",
             "|---|" + "---|" * (len(years) + 1)]
    for c in cols:
        lines.append(f"| {c} | " + " | ".join(f"{mean.at[y, c]:.3f}" for y in years) + f" | {full_mean[c]:.3f} |")
    lines += ["", "## ICIR (mean / std of daily IC)", "", "| Factor | " + " | ".join(str(y) for y in years) + " | All |",
              "|---|" + "---|" * (len(years) + 1)]
    for c in cols:
        lines.append(f"| {c} | " + " | ".join(f"{icir.at[y, c]:.2f}" for y in years) + f" | {full_icir[c]:.2f} |")
    res = Path(__file__).resolve().parent.parent / "results"
    (res / "factor_ic_by_year.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
