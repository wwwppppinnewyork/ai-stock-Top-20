"""Deflated Sharpe ratio (Bailey and Lopez de Prado, 2014) for the backtests in this repository.

A Sharpe ratio picked as the best of many tries is biased upward. The deflated Sharpe ratio (DSR)
is the probability that the true Sharpe ratio is above zero after accounting for
- the number of independent trials N (how many variants were tried before picking this one),
- the dispersion of Sharpe ratios across those trials,
- the length of the track record, and the skewness and fat tails of the returns.

    SR0 = sqrt(Var[SR_trials]) * ((1 - g) * Z(1 - 1/N) + g * Z(1 - 1/(N e))),   g = Euler-Mascheroni
    DSR = Phi( (SR - SR0) * sqrt(T - 1) / sqrt(1 - skew * SR + (kurt - 1) / 4 * SR^2) )

All Sharpe ratios inside the formula are daily (not annualized). PSR is the same with SR0 = 0
(no correction for multiple trials).

Trials: the 80 offset-level backtests of beta_tests.py (4 weighting methods x 20 start offsets),
2019-11 to 2025-12, net of costs. Many of them are near-duplicates, so the effective number of
independent trials is estimated from the eigenvalues of their return correlation matrix
(participation ratio). As a conservative bound the report also uses the total number of variants
run across timing_tests, beta_tests, the profit-QoQ test and the split test (N = 141).

Output: results/deflated_sharpe.md
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import kurtosis, norm, skew

sys.path.insert(0, str(Path(__file__).resolve().parent))
import beta_tests as bt  # noqa: E402

tt, jq = bt.tt, bt.jq
EULER = 0.5772156649
# variants run so far: timing (20 offsets + 16 holding-period sleeves + 5 daily-buffer settings),
# beta tests (3 new methods x 20), profit-QoQ model (20), split-test augmented model (20)
N_TOTAL = 20 + 16 + 5 + 60 + 20 + 20


def expected_max_sr(var_sr, n):
    if n <= 1:
        return 0.0
    return np.sqrt(var_sr) * ((1 - EULER) * norm.ppf(1 - 1 / n) + EULER * norm.ppf(1 - 1 / (n * np.e)))


def psr(r, sr0):
    sr = r.mean() / r.std()
    g3, g4 = skew(r), kurtosis(r, fisher=False)
    z = (sr - sr0) * np.sqrt(len(r) - 1) / np.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr ** 2)
    return float(norm.cdf(z)), sr, g3, g4


def main():
    data, days, close, ret, feats, tradable, buyable = tt.load()
    pos = {d: i for i, d in enumerate(days)}
    ctx = (data, days, close, ret, feats, tradable, buyable, pos, jq.Model())
    iret = data.index_close.reindex(days).ffill().pct_change().fillna(0.0)
    runs = {k: bt.sleeve_multi(ctx, iret, k) for k in range(20)}
    start = max(r[m][0].index[0] for r in runs.values() for m in bt.METHODS)
    end = min(r[m][0].index[-1] for r in runs.values() for m in bt.METHODS)

    trials = pd.DataFrame({f"{m} | offset {k}": runs[k][m][0].loc[start:end].pct_change()
                           for k in runs for m in bt.METHODS}).dropna()
    out_dir = jq.OUT.parent / "deflated_sharpe"
    out_dir.mkdir(parents=True, exist_ok=True)
    trials.to_csv(out_dir / "trial_returns.csv")
    sr_trials = trials.mean() / trials.std()
    var_sr = float(sr_trials.var())
    lam = np.linalg.eigvalsh(trials.corr().values)
    n_eff = float(lam.sum() ** 2 / (lam ** 2).sum())

    cands = {}
    for m in bt.METHODS:
        cands[f"{m}, 20 offsets combined"] = tt.combine([runs[k][m][0] for k in runs], start, end).pct_change().dropna()
    best = sr_trials.idxmax()
    cands[f"Best single backtest ({best})"] = trials[best]
    idx = data.index_close.reindex(days).ffill().loc[start:end].pct_change().dropna()
    cands["CSI All Share index"] = idx

    rows = []
    for name, r in cands.items():
        p0, sr, g3, g4 = psr(r, 0.0)
        row = {"Portfolio": name, "Sharpe (ann.)": sr * np.sqrt(252), "Skew": g3, "Kurtosis": g4, "PSR (SR>0)": p0}
        for label, n in (("DSR, N_eff", n_eff), ("DSR, N = 141", N_TOTAL)):
            row[label], *_ = psr(r, expected_max_sr(var_sr, n))
        rows.append(row)
    tab = pd.DataFrame(rows)
    sr0_eff = expected_max_sr(var_sr, n_eff) * np.sqrt(252)
    sr0_all = expected_max_sr(var_sr, N_TOTAL) * np.sqrt(252)

    lines = ["# Deflated Sharpe ratios", "",
             f"Historical backtest, CSI 300 + CSI 500, net of costs, {start.date()} to {end.date()} "
             f"({len(trials)} trading days).", "",
             f"- Trials: {trials.shape[1]} offset-level backtests (4 weighting methods x 20 start offsets).",
             f"- Dispersion of trial Sharpe ratios: annualized std {np.sqrt(var_sr * 252):.2f}.",
             f"- Effective independent trials (participation ratio of the correlation matrix): {n_eff:.1f}.",
             f"- Sharpe ratio the best of N pure-noise trials would show by luck (annualized): "
             f"{sr0_eff:.2f} at N_eff, {sr0_all:.2f} at N = {N_TOTAL}.", "",
             "PSR: probability that the true Sharpe ratio is above 0, ignoring how many variants were tried. "
             "DSR: the same probability after deflating for N trials. 0.95 is the usual bar.", "",
             "| Portfolio | Sharpe (ann.) | Skew | Kurtosis | PSR | DSR, N_eff | DSR, N = 141 |",
             "|---|---|---|---|---|---|---|"]
    for r in tab.itertuples(index=False):
        lines.append(f"| {r[0]} | {r[1]:.2f} | {r[2]:.2f} | {r[3]:.1f} | {r[4]:.2f} | {r[5]:.2f} | {r[6]:.2f} |")
    res = Path(__file__).resolve().parent.parent / "results"
    (res / "deflated_sharpe.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
