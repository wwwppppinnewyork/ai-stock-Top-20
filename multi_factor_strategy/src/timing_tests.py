"""Rebalance-timing tests for the LightGBM top-20 strategy (local port, no industry classification).

Uses the data cache of jq_port_backtest.py.  Four tests:

A. Timing luck: the same strategy (20-day rebalance, 20-day labels, 18 training cross-sections)
   started on each of the 20 possible offsets.  The spread across offsets is the part of the
   result that depends on which day of the cycle you rebalance.
B. Signal decay: Rank IC of the model score against forward returns of 1-60 trading days,
   measured on every trading day.
C. Holding period: rebalance and label horizon h in {5, 10, 20, 40, 60} days, each run as an
   equal-capital set of staggered sleeves so timing luck averages out.
D. Daily rank buffer: score every day, sell a holding only when it drops below rank S, refill
   with the best-ranked stock not held.  Trades are triggered by the signal, not the calendar.

All returns are net of JoinQuant's cost schedule, signals at the close of day t, trades at the
close of t+1.  Results go to output/timing_tests/report.md.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jq_port_backtest as jq  # noqa: E402

jq.P["use_industry"] = False
jq.P["industry_feature"] = False
# bit-for-bit reproducible LightGBM: without this, thread scheduling changes the trees slightly,
# which is enough to change the top-20 picks and move an offset's annual return by several points
jq.LGB_PARAMS.update(deterministic=True, force_row_wise=True)
OUT = jq.OUT.parent / "timing_tests"
TRAIN_DAYS = 360          # training window: 18 cross-sections x 20 days, as in the original
TOP = jq.P["target_num"]
BUY, SELL = jq.P["buy_cost"], jq.P["sell_cost"]


def load():
    data = jq.Data()
    days = data.days[data.days >= data.universe.index[0]]
    close = data.close.loc[data.days].ffill()
    ret = close.pct_change().fillna(0.0)
    feats = {}
    for d in days:
        # sorted: members() is a set, and its iteration order changes with Python's hash seed,
        # which reorders the training rows and changes LightGBM's bagging samples run to run
        stocks = sorted(s for s in data.members(d) if s in data.tradable.columns and bool(data.tradable.at[d, s]))
        raw = data.raw_frame(d, stocks)
        if raw is not None:
            feats[d] = jq.ml_features(raw[0])
    tradable = data.tradable.loc[data.days].fillna(False).astype(bool)
    buyable = tradable & ~data.flags.loc[data.days].fillna(False).astype(bool)
    print(f"cross-sections: {len(feats)}", flush=True)
    return data, list(data.days), close, ret, feats, tradable, buyable


def label(close, pos, days, d, h, index):
    i = pos[d] + h
    if i >= len(days):
        return None
    fwd = (close.loc[days[i], index] / close.loc[d, index] - 1).dropna()
    return fwd.rank(pct=True) - 0.5 if len(fwd) >= 30 else None


def sleeve(ctx, offset, h, keep_preds=None, keep_models=None):
    """One rebalance schedule: every h days starting at ``offset``. Returns a daily NAV series."""
    data, days, close, ret, feats, tradable, buyable, pos, model = ctx
    grid = [d for d in days[offset::h] if d in feats]
    n_train = max(4, TRAIN_DAYS // h)
    sections, nav_parts, weights, nav = {}, [], {}, 1.0
    for j, d in enumerate(grid):
        past = [g for g in grid[max(0, j - n_train):j] if pos[g] + h <= pos[d]]
        for g in past:
            if g not in sections:
                sections[g] = label(close, pos, days, g, h, feats[g].index)
        train = [(g, feats[g], sections[g].reindex(feats[g].index).dropna()) for g in past if sections[g] is not None]
        train = [(g, f.loc[lab.index], lab) for g, f, lab in train]
        if len(train) < max(3, int(n_train * 0.45)):
            continue
        booster = model.fit(train)
        pred = model.predict(booster, feats[d])
        if keep_preds is not None:
            keep_preds[d] = pred
        if keep_models is not None:
            keep_models[d] = booster
        e = pos[d] + 1
        e2 = min(pos[grid[j + 1]] + 1, len(days) - 1) if j + 1 < len(grid) else min(e + h, len(days) - 1)
        if e >= len(days) - 1:
            break
        ok = buyable.loc[days[e]].reindex(pred.index).fillna(False).astype(bool)
        picks = pred[ok].nlargest(TOP).index
        target = {s: 1.0 / len(picks) for s in picks}
        names = set(target) | set(weights)
        cost = sum(max(0, target.get(s, 0) - weights.get(s, 0)) for s in names) * BUY + \
            sum(max(0, weights.get(s, 0) - target.get(s, 0)) for s in names) * SELL
        nav *= 1 - cost
        path = (1 + ret.iloc[e + 1:e2 + 1][list(picks)]).cumprod()
        value = path.mean(axis=1) * nav
        nav_parts.append(pd.concat([pd.Series({days[e]: nav}), value]))
        last = path.iloc[-1]
        weights = (last / last.sum()).to_dict()
        nav = float(value.iloc[-1])
        if e2 >= len(days) - 1:
            break
    s = pd.concat(nav_parts)
    return s[~s.index.duplicated(keep="last")]


def stats(nav):
    nav = nav / nav.iloc[0]
    r = nav.pct_change().dropna()
    years = len(r) / 252
    yearly = nav.groupby(nav.index.year).last() / nav.groupby(nav.index.year).last().shift(1).fillna(1.0) - 1
    return {"ann_return": nav.iloc[-1] ** (1 / years) - 1, "sharpe": r.mean() / r.std() * np.sqrt(252),
            "max_dd": (nav / nav.cummax() - 1).min()}, yearly


def combine(navs, start, end):
    """Equal capital in each sleeve at ``start``, no rebalancing between sleeves."""
    return pd.concat([n.loc[start:end] / n.loc[start:end].iloc[0] for n in navs], axis=1).ffill().mean(axis=1)


def daily_buffer(ctx, preds, sell_rank, start, end):
    data, days, close, ret, feats, tradable, buyable, pos, model = ctx
    hold, cash, out, trades = {}, 1.0, {}, 0
    pred_days = [d for d in days if d in preds and start <= d <= end]
    for d in pred_days:
        e = pos[d] + 1
        if e >= len(days) or days[e] > end:
            break
        rank = preds[d].rank(ascending=False)
        day_e = days[e]
        # move to the trade day close
        for s in list(hold):
            hold[s] *= 1 + ret.at[day_e, s]
        can_trade = tradable.loc[day_e]
        for s in list(hold):
            if rank.get(s, 1e9) > sell_rank and bool(can_trade.get(s, False)):
                cash += hold.pop(s) * (1 - SELL)
                trades += 1
        ok = buyable.loc[day_e].reindex(rank.index).fillna(False).astype(bool)
        cand = [s for s in rank[ok].sort_values().index if s not in hold][:TOP - len(hold)]
        if cand:
            each = cash / len(cand)
            for s in cand:
                hold[s] = each * (1 - BUY)
                trades += 1
            cash = 0.0
        out[day_e] = cash + sum(hold.values())
    # decisions are on consecutive trading days, so each day's return is applied exactly once
    return pd.Series(out), trades


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    data, days, close, ret, feats, tradable, buyable = load()
    pos = {d: i for i, d in enumerate(days)}
    ctx = (data, days, close, ret, feats, tradable, buyable, pos, jq.Model())

    # A. 20 offsets of the 20-day strategy (also collects a model score for every trading day)
    preds, navs20, models0 = {}, {}, {}
    for k in range(20):
        navs20[k] = sleeve(ctx, k, 20, keep_preds=preds, keep_models=models0 if k == 0 else None)
        print(f"offset {k}: {navs20[k].index[0].date()} -> {navs20[k].index[-1].date()}", flush=True)
    start = max(n.index[0] for n in navs20.values())
    end = min(n.index[-1] for n in navs20.values())
    rows = []
    for k, n in navs20.items():
        st, _ = stats(n.loc[start:end])
        rows.append({"offset": k, **st})
    offsets = pd.DataFrame(rows)
    offsets.to_csv(OUT / "offsets.csv", index=False)

    # B. signal decay: IC of the score vs forward returns from the trade day
    ic_rows = []
    for d, pred in preds.items():
        e = pos[d] + 1
        row = {"date": d}
        for hz in (1, 5, 10, 20, 40, 60):
            if e + hz < len(days):
                fwd = close.loc[days[e + hz], pred.index] / close.loc[days[e], pred.index] - 1
                row[hz] = jq.rank_ic(pred, fwd)
        ic_rows.append(row)
    ic = pd.DataFrame(ic_rows).set_index("date")
    ic.to_csv(OUT / "ic_decay.csv")

    # C. holding periods with staggered sleeves
    hp = {}
    for h in (5, 10, 20, 40, 60):
        offs = list(range(20)) if h == 20 else sorted(set(int(round(i * h / 4)) for i in range(4)))
        navs = list(navs20.values()) if h == 20 else [sleeve(ctx, k, h) for k in offs]
        hp[h] = navs
        print(f"holding {h}: {len(navs)} sleeves", flush=True)
    start_c = max(n.index[0] for ns in hp.values() for n in ns)
    end_c = min(n.index[-1] for ns in hp.values() for n in ns)
    start_c, end_c = max(start, start_c), min(end, end_c)

    results, yearly = {}, {}
    for h, navs in hp.items():
        results[f"Hold {h}d, {len(navs)} staggered sleeves"], yearly[f"hold {h}d"] = stats(combine(navs, start_c, end_c))
    results["Hold 20d, 4 sleeves (offsets 0/5/10/15)"], yearly["4 sleeves"] = stats(
        combine([navs20[k] for k in (0, 5, 10, 15)], start_c, end_c))
    for k in (0,):
        results[f"Hold 20d, single schedule (offset {k})"], yearly["offset 0"] = stats(navs20[k].loc[start_c:end_c])

    # D. daily rank buffer.  Daily scores come from ONE model, retrained every 20 days (offset 0
    #    schedule) and applied to each day's factors, so rank changes reflect the data, not a
    #    different model each day.
    model = ctx[-1]
    fit_days = sorted(models0)
    daily_preds = {}
    for d in days:
        if d in feats and fit_days and d >= fit_days[0]:
            last_fit = max(f for f in fit_days if f <= d)
            daily_preds[d] = model.predict(models0[last_fit], feats[d])
    trades = {}
    for srank in (30, 40, 60, 100, 200):
        nav, n_tr = daily_buffer(ctx, daily_preds, srank, start_c, end_c)
        name = f"Daily scoring, sell below rank {srank}"
        results[name], yearly[f"buffer {srank}"] = stats(nav)
        trades[name] = n_tr / 2 / ((nav.index[-1] - nav.index[0]).days / 365.25) / TOP  # round trips per slot

    idx = data.index_close.reindex(days).ffill().loc[start_c:end_c]
    results["CSI All Share index"], yearly["index"] = stats(idx)

    # report
    pct = lambda x: f"{x * 100:.1f}%"  # noqa: E731
    lines = ["# Rebalance timing tests (LightGBM top 20, no industry classification)", "",
             "Historical backtest on free BaoStock / AkShare data, CSI 300 + CSI 500, net of costs.", "",
             f"## A. Timing luck: 20 start offsets, {start.date()} to {end.date()}", "",
             "| | Ann. return | Sharpe | Max drawdown |", "|---|---|---|---|"]
    for q, lab in [(0.0, "worst"), (0.25, "25th pct"), (0.5, "median"), (0.75, "75th pct"), (1.0, "best")]:
        lines.append(f"| {lab} | {pct(offsets['ann_return'].quantile(q))} | {offsets['sharpe'].quantile(q):.2f} | "
                     f"{pct(offsets['max_dd'].quantile(q))} |")
    lines += ["", "Per offset: " + ", ".join(f"{int(r.offset)}: {pct(r.ann_return)}" for r in offsets.itertuples()), "",
              "## B. Signal decay (mean Rank IC on every trading day)", "",
              "| Horizon (days) | " + " | ".join(str(c) for c in ic.columns) + " |",
              "|---|" + "---|" * len(ic.columns),
              "| Mean IC | " + " | ".join(f"{ic[c].mean():.3f}" for c in ic.columns) + " |",
              "| IC / sqrt(h) | " + " | ".join(f"{ic[c].mean() / np.sqrt(c):.3f}" for c in ic.columns) + " |", "",
              f"## C and D. Holding period and signal-triggered trading, {start_c.date()} to {end_c.date()}", "",
              "| Portfolio | Ann. return | Sharpe | Max drawdown | Round trips per slot per year |", "|---|---|---|---|---|"]
    for name, st in results.items():
        to = f"{trades[name]:.1f}" if name in trades else ""
        lines.append(f"| {name} | {pct(st['ann_return'])} | {st['sharpe']:.2f} | {pct(st['max_dd'])} | {to} |")
    yt = pd.DataFrame(yearly)
    lines += ["", "## Calendar-year returns", "", "| Year | " + " | ".join(yt.columns) + " |",
              "|---|" + "---|" * len(yt.columns)]
    for y, r in yt.iterrows():
        lines.append(f"| {y} | " + " | ".join(pct(v) for v in r) + " |")
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
