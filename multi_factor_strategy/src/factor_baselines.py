"""Linear factor baselines: cross-sectional z-scores, ICIR weighting and weighted regression.

These are the control group for the rule-based score and the XGBoost model.
All three use the same 16 raw factors (feature list in ``config/xgboost.json``)
and the same training panel (``data/xgb_panel.csv``).

Models
------
1. **Z-score, equal weight**: each factor is standardized cross-sectionally
   within each snapshot (3x MAD winsorization, then ``(x - mean) / std``),
   reversed where lower is better (PEG), missing values set to 0 (the
   cross-sectional mean), and the 16 z-scores are averaged.
2. **ICIR-weighted z-score**: for each factor, the Rank IC between its z-score
   and the forward return is computed every snapshot.  On snapshot t, each
   factor's ICIR (mean IC / std IC) is estimated from a trailing window of
   *past* snapshots only, ending ``embargo_snapshots`` before t because the
   most recent labels are not yet observable.  Weights are proportional to
   ICIR (negative ICIR set to 0 by default) and scaled to sum to 1.
3. **Weighted regression (WLS, Fama-MacBeth style)**: on every past snapshot,
   the forward return is regressed cross-sectionally on the 16 z-scores by
   weighted least squares, with weights sqrt(market cap) so that large,
   liquid stocks are fitted more closely and the influence of small-cap noise
   is reduced.  A small ridge penalty stabilizes the coefficients because the
   FY1-FY3 variants of a factor are highly correlated.  On snapshot t, the
   score is the z-score vector times the average coefficient vector over the
   trailing window of past snapshots (same embargo).

Evaluation is out of sample for every model (rank IC, ICIR, hit rate, top-N
return net of cost) and is reported side by side with the rule-based score
and, when ``output/xgb_oos_predictions.csv`` exists, the XGBoost model.

Usage
-----
    python src/factor_baselines.py --mode backtest   # comparison report
    python src/factor_baselines.py --mode live       # score this week's pool

Outputs (``output/``): ``factor_ic.csv``, ``factor_icir_summary.csv``,
``baseline_oos_scores.csv``, ``model_comparison.csv``, ``model_comparison.md``,
``baseline_ranked_stocks.csv``.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parent.parent
FEATURE_CONFIG = ROOT / "config" / "xgboost.json"
BASELINE_CONFIG = ROOT / "config" / "baselines.json"
PANEL_FILE = ROOT / "data" / "xgb_panel.csv"
CURRENT_FACTORS = ROOT / "data" / "wind" / "factor_data.csv"
XGB_OOS = ROOT / "output" / "xgb_oos_predictions.csv"
OUT = ROOT / "output"


# ------------------------------------------------------------- z-scores
def mad_winsorize(series: pd.Series, k: float = 3.0) -> pd.Series:
    center = series.median()
    mad = (series - center).abs().median()
    if pd.isna(mad) or mad == 0:
        return series
    radius = k * 1.4826 * mad
    return series.clip(center - radius, center + radius)


def zscore(series: pd.Series) -> pd.Series:
    std = series.std(ddof=0)
    if pd.isna(std) or std == 0:
        return pd.Series(0.0, index=series.index)
    return (series - series.mean()) / std


def cross_sectional_zscores(frame: pd.DataFrame, features: list, cfg: dict) -> pd.DataFrame:
    """Winsorize and z-score every factor within each snapshot; all factors end up 'higher is better'."""
    frame = frame.copy()
    reverse = set(cfg.get("reverse_factors", []))
    no_winsor = set(cfg.get("no_winsorize", []))
    for col in features:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
        if col in reverse:
            frame.loc[frame[col] < 0, col] = np.nan  # negative PEG is not a valid valuation
    for _, idx in frame.groupby("snapshot_date").groups.items():
        for col in features:
            values = frame.loc[idx, col]
            values = values if col in no_winsor else mad_winsorize(values)
            z = zscore(values)
            frame.loc[idx, col] = (-z if col in reverse else z).fillna(0.0)
    return frame


# ------------------------------------------------------------------- IC
def combine(frame, weights):
    """Weighted sum of z-scored factors; missing weights count as 0."""
    w = weights.reindex(frame.columns.intersection(weights.index)).fillna(0.0)
    return frame[w.index].mul(w, axis=1).sum(axis=1)


def rank_ic(score: pd.Series, realized: pd.Series) -> float:
    mask = score.notna() & realized.notna()
    if mask.sum() < 10 or score[mask].nunique() < 2:
        return np.nan
    return spearmanr(score[mask], realized[mask]).correlation


def factor_ic_table(z: pd.DataFrame, features: list) -> pd.DataFrame:
    """Rank IC of every factor on every snapshot (rows: snapshots, columns: factors)."""
    rows = {}
    for date, group in z.groupby("snapshot_date"):
        rows[date] = {f: rank_ic(group[f], group["fwd_return"]) for f in features}
    return pd.DataFrame.from_dict(rows, orient="index").sort_index()


def icir(ic: pd.DataFrame) -> pd.Series:
    std = ic.std(ddof=1)
    return ic.mean() / std.replace(0, np.nan)


def icir_weights(past_ic: pd.DataFrame, negative: str) -> pd.Series:
    raw = icir(past_ic).fillna(0.0)
    if negative == "zero":
        raw = raw.clip(lower=0.0)
    total = raw.abs().sum()
    return raw / total if total > 0 else pd.Series(0.0, index=raw.index)


# ----------------------------------------------------------- regression
def regression_weights(group: pd.DataFrame, scheme: str) -> np.ndarray:
    if scheme == "sqrt_mkt_cap" and "mkt_cap" in group:
        cap = pd.to_numeric(group["mkt_cap"], errors="coerce")
        if cap.notna().any():
            return np.sqrt(cap.fillna(cap.median()).clip(lower=0)).to_numpy()
    return np.ones(len(group))  # fall back to OLS when market cap is unavailable


def wls_coefficients(group: pd.DataFrame, features: list, scheme: str, ridge: float) -> pd.Series:
    """Cross-sectional WLS (with ridge) of forward return on factor z-scores; intercept not penalized."""
    group = group.dropna(subset=["fwd_return"])
    if len(group) <= len(features) + 1:
        return pd.Series(np.nan, index=features)
    X = np.column_stack([np.ones(len(group)), group[features].to_numpy()])
    y = group["fwd_return"].to_numpy()
    w = regression_weights(group, scheme)
    Xw = X * w[:, None]
    penalty = ridge * np.eye(X.shape[1])
    penalty[0, 0] = 0.0
    with np.errstate(all="ignore"):  # numpy 2.0 + macOS Accelerate emits spurious matmul warnings
        beta = np.linalg.solve(X.T @ Xw + penalty, Xw.T @ y)
    return pd.Series(beta[1:], index=features)


def wls_coefficient_table(z: pd.DataFrame, features: list, cfg: dict) -> pd.DataFrame:
    rows = {
        date: wls_coefficients(group, features, cfg["weight"], float(cfg["ridge_lambda"]))
        for date, group in z.groupby("snapshot_date")
    }
    return pd.DataFrame.from_dict(rows, orient="index").sort_index()


# ------------------------------------------------------ walk-forward scores
def past_window(table: pd.DataFrame, i: int, cfg: dict) -> pd.DataFrame:
    """Rows of `table` usable on snapshot index i: end `embargo` snapshots before i."""
    end = i - int(cfg["embargo_snapshots"])
    start = max(0, end - int(cfg["window_snapshots"]))
    return table.iloc[start:end] if end > 0 else table.iloc[0:0]


def walk_forward_scores(z: pd.DataFrame, features: list, cfg: dict) -> tuple:
    ic = factor_ic_table(z, features)
    coefs = wls_coefficient_table(z, features, cfg["wls"])
    dates = list(ic.index)
    scored, weight_log = [], []
    for i, date in enumerate(dates):
        group = z[z["snapshot_date"] == date].copy()
        group["zscore_equal"] = group[features].mean(axis=1)

        past_ic = past_window(ic, i, cfg["icir"]).dropna(how="all")
        if len(past_ic) >= int(cfg["icir"]["min_snapshots"]):
            w = icir_weights(past_ic, cfg["icir"]["negative_icir"])
            group["icir_weighted"] = combine(group, w)
            weight_log.append({"snapshot_date": date, "model": "icir_weighted", **w.to_dict()})
        else:
            group["icir_weighted"] = np.nan

        past_beta = past_window(coefs, i, cfg["wls"]).dropna(how="all")
        if len(past_beta) >= int(cfg["wls"]["min_snapshots"]):
            beta = past_beta.mean()
            group["wls_regression"] = combine(group, beta)
            weight_log.append({"snapshot_date": date, "model": "wls_regression", **beta.to_dict()})
        else:
            group["wls_regression"] = np.nan
        scored.append(group)
    return pd.concat(scored, ignore_index=True), ic, coefs, pd.DataFrame(weight_log)


# ------------------------------------------------------------- evaluation
MODELS = {
    "total_score": "Rule-based six-factor score",
    "zscore_equal": "Z-score, equal weight",
    "icir_weighted": "Z-score, ICIR weighted",
    "wls_regression": "WLS regression (sqrt market cap)",
    "xgb_score": "XGBoost",
}


def summarize(values: pd.Series) -> dict:
    values = values.dropna()
    if values.empty:
        return {"mean_ic": np.nan, "icir": np.nan, "hit_rate": np.nan, "n": 0}
    std = values.std(ddof=1)
    return {
        "mean_ic": values.mean(),
        "icir": values.mean() / std if std and std > 0 else np.nan,
        "hit_rate": (values > 0).mean(),
        "n": int(values.size),
    }


def compare_models(scores: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    n = int(cfg["portfolio"]["top_n"])
    cost = float(cfg["portfolio"]["round_trip_cost"])
    rows = []
    for col, label in MODELS.items():
        if col not in scores or scores[col].notna().sum() == 0:
            continue
        ics, tops = [], []
        for _, group in scores.dropna(subset=[col]).groupby("snapshot_date"):
            ics.append(rank_ic(group[col], group["fwd_return"]))
            tops.append(group.nlargest(n, col)["fwd_return"].mean() - cost)
        stats = summarize(pd.Series(ics))
        rows.append({"model": label, **stats, f"top{n}_net_return": np.nanmean(tops)})
    pool = scores.groupby("snapshot_date")["fwd_return"].mean().mean()
    rows.append({"model": "Equal-weight pool", "mean_ic": np.nan, "icir": np.nan, "hit_rate": np.nan,
                 "n": scores["snapshot_date"].nunique(), f"top{n}_net_return": pool})
    return pd.DataFrame(rows)


def write_report(comparison: pd.DataFrame, icir_summary: pd.DataFrame, cfg: dict):
    n = cfg["portfolio"]["top_n"]
    lines = [
        "# Model comparison (out of sample)",
        "",
        "Every model is scored on each snapshot using only information from earlier snapshots, "
        f"with a {cfg['icir']['embargo_snapshots']}-snapshot embargo. Returns are historical backtest results.",
        "",
        f"| Model | Mean Rank IC | ICIR | IC > 0 | Snapshots | Top{n} return per period (net) |",
        "|---|---|---|---|---|---|",
    ]
    for r in comparison.itertuples(index=False):
        d = r._asdict()
        fmt = lambda v, spec: "-" if pd.isna(v) else format(v, spec)
        lines.append(
            f"| {d['model']} | {fmt(d['mean_ic'], '.4f')} | {fmt(d['icir'], '.3f')} | "
            f"{fmt(d['hit_rate'], '.1%')} | {d['n']} | {fmt(d[f'top{n}_net_return'], '.4%')} |"
        )
    lines += [
        "",
        "## Single-factor IC summary (full sample, descriptive only)",
        "",
        "| Factor | Mean IC | IC std | ICIR | IC > 0 |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {r.factor} | {r.mean_ic:.4f} | {r.ic_std:.4f} | {r.icir:.3f} | {r.hit_rate:.1%} |"
        for r in icir_summary.itertuples(index=False)
    ]
    lines += [
        "",
        "## Reading the results",
        "",
        "- The linear baselines are the control group: XGBoost or the rule-based score is only worth keeping "
        "if it beats them consistently on both IC and net return.",
        "- The full-sample single-factor table uses all snapshots and is for description only; "
        "the ICIR weights used for scoring are estimated from past snapshots only.",
        "- With few snapshots the standard error of mean IC is large; treat small differences as noise.",
    ]
    (OUT / "model_comparison.md").write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------ modes
def load_configs(path: str) -> tuple:
    """A config may carry its own "features", "panel" and "output_dir" (e.g. config/open_data.json)."""
    global PANEL_FILE, OUT, XGB_OOS
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    features = cfg.get("features") or json.loads(FEATURE_CONFIG.read_text(encoding="utf-8"))["features"]
    if "panel" in cfg:
        PANEL_FILE = ROOT / cfg["panel"]
    if "output_dir" in cfg:
        OUT = ROOT / cfg["output_dir"]
        XGB_OOS = OUT / "xgb_oos_predictions.csv"
    return features, cfg


def load_panel() -> pd.DataFrame:
    if not PANEL_FILE.exists():
        raise FileNotFoundError(f"Missing {PANEL_FILE}; build the panel first (build_xgb_panel.py or open_data.py)")
    panel = pd.read_csv(PANEL_FILE, encoding="utf-8-sig", dtype={"snapshot_date": str, "ts_code": str})
    panel["fwd_return"] = pd.to_numeric(panel["fwd_return"], errors="coerce")
    return panel.dropna(subset=["fwd_return"])


def run_backtest(features: list, cfg: dict):
    z = cross_sectional_zscores(load_panel(), features, cfg)
    scores, ic, coefs, weights = walk_forward_scores(z, features, cfg)
    if XGB_OOS.exists():
        xgb = pd.read_csv(XGB_OOS, encoding="utf-8", dtype={"snapshot_date": str, "ts_code": str})
        scores = scores.merge(xgb[["snapshot_date", "ts_code", "xgb_score"]], on=["snapshot_date", "ts_code"], how="left")
    comparison = compare_models(scores, cfg)
    icir_summary = pd.DataFrame({
        "factor": features,
        "mean_ic": ic.mean().values,
        "ic_std": ic.std(ddof=1).values,
        "icir": icir(ic).values,
        "hit_rate": (ic > 0).mean().values,
    }).sort_values("icir", ascending=False)

    OUT.mkdir(parents=True, exist_ok=True)
    ic.to_csv(OUT / "factor_ic.csv", index_label="snapshot_date", encoding="utf-8")
    icir_summary.to_csv(OUT / "factor_icir_summary.csv", index=False, encoding="utf-8")
    coefs.to_csv(OUT / "wls_coefficients.csv", index_label="snapshot_date", encoding="utf-8")
    weights.to_csv(OUT / "baseline_weights.csv", index=False, encoding="utf-8")
    keep = ["snapshot_date", "ts_code", "name", "fwd_return", *[c for c in MODELS if c in scores]]
    scores[keep].to_csv(OUT / "baseline_oos_scores.csv", index=False, encoding="utf-8")
    comparison.to_csv(OUT / "model_comparison.csv", index=False, encoding="utf-8")
    write_report(comparison, icir_summary, cfg)
    print(comparison.to_string(index=False))


def run_live(features: list, cfg: dict):
    """Score this week's pool with weights estimated from all labeled snapshots."""
    z_hist = cross_sectional_zscores(load_panel(), features, cfg)
    ic = factor_ic_table(z_hist, features).tail(int(cfg["icir"]["window_snapshots"]))
    coefs = wls_coefficient_table(z_hist, features, cfg["wls"]).tail(int(cfg["wls"]["window_snapshots"]))

    current = pd.read_csv(CURRENT_FACTORS, encoding="utf-8-sig", dtype={"ts_code": str})
    current["snapshot_date"] = current["trade_date"] if "trade_date" in current else "current"
    current = cross_sectional_zscores(current, features, cfg)
    current["zscore_equal"] = current[features].mean(axis=1)
    current["icir_weighted"] = combine(current, icir_weights(ic, cfg["icir"]["negative_icir"]))
    current["wls_regression"] = combine(current, coefs.mean())
    current = current.sort_values("icir_weighted", ascending=False)
    OUT.mkdir(exist_ok=True)
    cols = ["ts_code", "name", "zscore_equal", "icir_weighted", "wls_regression"]
    current[[c for c in cols if c in current]].to_csv(OUT / "baseline_ranked_stocks.csv", index=False, encoding="utf-8")
    print(f"Wrote {OUT / 'baseline_ranked_stocks.csv'}")


def main():
    parser = argparse.ArgumentParser(description="Z-score / ICIR / WLS factor baselines")
    parser.add_argument("--mode", choices=["backtest", "live"], default="backtest")
    parser.add_argument("--config", default=str(BASELINE_CONFIG))
    args = parser.parse_args()
    features, cfg = load_configs(args.config)
    run_backtest(features, cfg) if args.mode == "backtest" else run_live(features, cfg)


if __name__ == "__main__":
    main()
