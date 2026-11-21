"""Beta-constrained portfolio construction for the LightGBM top-20 strategy (long only).

Same model, data, costs and 20 start offsets as timing_tests.py; only the weights change.
All settings were fixed before the first run and are not tuned on the results:

- Candidates: the 100 best-scored buyable stocks on each rebalance day.
- Beta: OLS beta of each stock's daily returns on the index over the last 250 trading days up
  to the signal day (no look-ahead).
- Weight cap 5% per stock, fully invested, long only, so every portfolio holds at least 20 names,
  like the equal-weight top 20.
- Candidates with fewer than 120 non-zero daily returns in the beta window are excluded from
  the optimized portfolios (their beta is not measurable).

Portfolios:
1. Equal weight top 20 (the existing strategy).
2. Max score s.t. beta <= 0.7: maximize sum(w * score z) subject to w'beta <= 0.7, sum(w) = 1,
   0 <= w <= 5%.  If 0.7 is unreachable inside the 100 candidates, take the minimum-beta portfolio
   and count the day as infeasible.
3. Min beta: minimize w'beta with the same constraints (ignores the score; a low-beta reference).
4. Control: min beta over every buyable stock in the universe, not just the model's top 100.
   If it does as well as 3, the gain comes from low beta, not from the model.

Output: output/beta_tests/report.md
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import linprog

sys.path.insert(0, str(Path(__file__).resolve().parent))
import timing_tests as tt  # noqa: E402  (sets the deterministic LightGBM params and no-industry flags)

jq = tt.jq
OUT = jq.OUT.parent / "beta_tests"
N_CAND, CAP, BETA_MAX, BETA_WIN = 100, 0.05, 0.7, 250
METHODS = ["Equal weight top 20", f"Max score, beta <= {BETA_MAX}", "Min beta",
           "Control: min beta, whole universe (no model)"]


def betas(ret, iret, pos, d, names):
    i = pos[d]
    r = ret.iloc[max(0, i - BETA_WIN + 1):i + 1][names]
    m = iret.iloc[max(0, i - BETA_WIN + 1):i + 1]
    mc = m - m.mean()
    b = r.sub(r.mean()).mul(mc, axis=0).sum() / (mc ** 2).sum()
    # returns are zero-filled before listing and during suspensions: such a window looks low-beta
    # only because the stock did not trade, so require 120 trading days with a price change
    return b.where((r != 0).sum() >= 120)


def lp(c, beta, beta_max=None):
    n = len(c)
    kw = dict(bounds=[(0, CAP)] * n, A_eq=np.ones((1, n)), b_eq=[1.0], method="highs")
    if beta_max is not None:
        kw.update(A_ub=beta.values[None, :], b_ub=[beta_max])
    res = linprog(c, **kw)
    return pd.Series(res.x, index=beta.index) if res.status == 0 else None


def targets(pred, ok, ret, iret, pos, d, log):
    cand = pred[ok].nlargest(N_CAND)
    top = cand.index[:tt.TOP]
    out = {METHODS[0]: pd.Series(1.0 / len(top), index=top)}
    b_all = betas(ret, iret, pos, d, list(cand.index))
    b = b_all.dropna()
    cand = cand[b.index]
    z = (cand - cand.mean()) / cand.std()
    w_min = lp(b.values, b)
    w_opt = lp(-z.values, b, BETA_MAX)
    log.append({"date": d, "feasible": w_opt is not None, "min_beta": float(w_min @ b),
                "ew_beta": float(b_all[top].fillna(1.0).mean()), "n_cand": len(b)})
    out[METHODS[1]] = w_opt if w_opt is not None else w_min
    out[METHODS[2]] = w_min
    # control: the same minimum-beta portfolio over every buyable stock, ignoring the model score
    b_univ = betas(ret, iret, pos, d, list(pred[ok].index)).dropna()
    out[METHODS[3]] = lp(b_univ.values, b_univ)
    log[-1]["univ_min_beta"] = float(out[METHODS[3]] @ b_univ)
    for k in out:
        w = out[k][out[k] > 1e-6]
        out[k] = w / w.sum()
    out["_beta"] = pd.concat([b_all, b_univ[~b_univ.index.isin(b_all.index)]]).fillna(1.0)
    return out


def sleeve_multi(ctx, iret, offset, h=20, log=None):
    """Like timing_tests.sleeve, but one model run feeds all three weighting methods."""
    data, days, close, ret, feats, tradable, buyable, pos, model = ctx
    grid = [d for d in days[offset::h] if d in feats]
    n_train = max(4, tt.TRAIN_DAYS // h)
    sections = {}
    state = {m: {"nav": 1.0, "w": {}, "parts": [], "beta": []} for m in METHODS}
    for j, d in enumerate(grid):
        past = [g for g in grid[max(0, j - n_train):j] if pos[g] + h <= pos[d]]
        for g in past:
            if g not in sections:
                sections[g] = tt.label(close, pos, days, g, h, feats[g].index)
        train = [(g, feats[g], sections[g].reindex(feats[g].index).dropna()) for g in past if sections[g] is not None]
        train = [(g, f.loc[lab.index], lab) for g, f, lab in train]
        if len(train) < max(3, int(n_train * 0.45)):
            continue
        pred = model.predict(model.fit(train), feats[d])
        e = pos[d] + 1
        e2 = min(pos[grid[j + 1]] + 1, len(days) - 1) if j + 1 < len(grid) else min(e + h, len(days) - 1)
        if e >= len(days) - 1:
            break
        ok = buyable.loc[days[e]].reindex(pred.index).fillna(False).astype(bool)
        tg = targets(pred, ok, ret, iret, pos, d, log if log is not None else [])
        b = tg.pop("_beta")
        for m in METHODS:
            st, target = state[m], tg[m].to_dict()
            names = set(target) | set(st["w"])
            cost = sum(max(0, target.get(s, 0) - st["w"].get(s, 0)) for s in names) * tt.BUY + \
                sum(max(0, st["w"].get(s, 0) - target.get(s, 0)) for s in names) * tt.SELL
            st["nav"] *= 1 - cost
            picks = list(target)
            w0 = np.array([target[s] for s in picks])
            path = (1 + ret.iloc[e + 1:e2 + 1][picks]).cumprod()
            value = (path * w0).sum(axis=1) * st["nav"]
            st["parts"].append(pd.concat([pd.Series({days[e]: st["nav"]}), value]))
            last = path.iloc[-1] * w0
            st["w"] = (last / last.sum()).to_dict()
            st["nav"] = float(value.iloc[-1])
            st["beta"].append(float(sum(target[s] * b[s] for s in picks)))
        if e2 >= len(days) - 1:
            break
    out = {}
    for m in METHODS:
        s = pd.concat(state[m]["parts"])
        out[m] = (s[~s.index.duplicated(keep="last")], float(np.mean(state[m]["beta"])))
    return out


def realized_beta(nav, idx):
    r = nav.pct_change().dropna()
    m = idx.pct_change().reindex(r.index)
    return float(r.cov(m) / m.var())


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    data, days, close, ret, feats, tradable, buyable = tt.load()
    pos = {d: i for i, d in enumerate(days)}
    ctx = (data, days, close, ret, feats, tradable, buyable, pos, jq.Model())
    idx = data.index_close.reindex(days).ffill()
    iret = idx.pct_change().fillna(0.0)

    runs, log = {}, []
    for k in range(20):
        runs[k] = sleeve_multi(ctx, iret, k, log=log)
        print(f"offset {k} done", flush=True)
    start = max(r[m][0].index[0] for r in runs.values() for m in METHODS)
    end = min(r[m][0].index[-1] for r in runs.values() for m in METHODS)
    idx_p = idx.loc[start:end]

    rows = []
    for k, r in runs.items():
        for m in METHODS:
            nav = r[m][0].loc[start:end]
            st, _ = tt.stats(nav)
            rows.append({"offset": k, "method": m, **st, "ex_ante_beta": r[m][1],
                         "realized_beta": realized_beta(nav, idx_p)})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "offsets.csv", index=False)
    lg = pd.DataFrame(log)
    lg.to_csv(OUT / "rebalance_log.csv", index=False)

    combo = {}
    for m in METHODS:
        combo[m] = tt.stats(tt.combine([runs[k][m][0] for k in range(20)], start, end))[0]
    combo["CSI All Share index"] = tt.stats(idx_p)[0]

    pct = lambda x: f"{x * 100:.1f}%"  # noqa: E731
    lines = ["# Beta-constrained weights (LightGBM top 20, long only)", "",
             "Historical backtest on free BaoStock / AkShare data, CSI 300 + CSI 500, net of costs, "
             f"{start.date()} to {end.date()}. Settings fixed before the run: 100 candidates, 5% cap per stock, "
             f"250-day beta, beta limit {BETA_MAX}.", "",
             "## Distribution over the 20 start offsets", "",
             "| Method | Ann. return (min / median / max) | Sharpe (min / median / max) | Max DD (median) | "
             "Ex-ante beta | Realized beta |", "|---|---|---|---|---|---|"]
    for m in METHODS:
        g = df[df.method == m]
        lines.append(f"| {m} | {pct(g.ann_return.min())} / {pct(g.ann_return.median())} / {pct(g.ann_return.max())} | "
                     f"{g.sharpe.min():.2f} / {g.sharpe.median():.2f} / {g.sharpe.max():.2f} | {pct(g.max_dd.median())} | "
                     f"{g.ex_ante_beta.median():.2f} | {g.realized_beta.median():.2f} |")
    lines += ["", "## All 20 offsets combined (equal capital, staggered sleeves)", "",
              "| Portfolio | Ann. return | Sharpe | Max drawdown |", "|---|---|---|---|"]
    for m, st in combo.items():
        lines.append(f"| {m} | {pct(st['ann_return'])} | {st['sharpe']:.2f} | {pct(st['max_dd'])} |")
    lines += ["", f"Beta limit reachable on {lg.feasible.mean():.0%} of rebalance days "
              f"(median minimum attainable beta {lg.min_beta.median():.2f}; median beta of the equal-weight top 20 "
              f"{lg.ew_beta.median():.2f})."]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
