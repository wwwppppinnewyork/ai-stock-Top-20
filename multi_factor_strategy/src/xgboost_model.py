"""XGBoost cross-sectional stock ranking on the same factors as the rule-based score.

The rule-based model (``stock_workflow.py``) combines six factor groups with
hand-set weights.  This module asks a different question: given the *same*
raw factors, can a gradient-boosted tree model learn a better mapping from
factors to next-period relative returns, including non-linear effects and
interactions that fixed weights cannot capture?

Pipeline
--------
1. Features: the 16 raw factor fields listed in ``config/xgboost.json``.
   Each snapshot is processed cross-sectionally, exactly like the rule-based
   score: 3x MAD winsorization, then rank-normalization to [0, 1].  PEG < 0 is
   treated as missing.  Missing values stay NaN; XGBoost learns a default
   split direction for them.
2. Label: the cross-sectional percentile rank of the forward return over
   ``label.horizon_trade_days``.  Ranking removes the market-wide move, so the
   model learns relative (stock-selection) skill rather than market timing.
3. Walk-forward evaluation (``--mode backtest``): for every snapshot t after
   ``min_train_snapshots``, train only on snapshots that end at least
   ``embargo_snapshots`` before t (labels of the most recent snapshots would
   overlap t's return window), hold out the last ``validation_snapshots`` of
   the training window for early stopping, then predict t.  No snapshot is
   ever scored by a model that saw its label.
4. Metrics, out of sample: Rank IC (Spearman) per snapshot, mean IC, ICIR,
   hit rate, and the top-N portfolio return net of round-trip cost versus the
   equal-weight candidate pool.  The rule-based score is evaluated on the same
   snapshots as a baseline when ``total_score`` is available.
5. Live scoring (``--mode live``): train on the full panel and score this
   week's ``data/wind/factor_data.csv``; optionally blend with the rule-based
   ``total_score`` (``blend`` in the config).

Outputs (in ``output/``): ``xgb_oos_predictions.csv``, ``xgb_ic.csv``,
``xgb_feature_importance.csv``, ``xgb_report.md``, ``xgb_ranked_stocks.csv``.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from xgboost import XGBRegressor

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config" / "xgboost.json"
PANEL_FILE = ROOT / "data" / "xgb_panel.csv"
CURRENT_FACTORS = ROOT / "data" / "wind" / "factor_data.csv"
RULE_RANKED = ROOT / "output" / "ranked_stocks.csv"
OUT = ROOT / "output"


# ---------------------------------------------------------------- features
def mad_winsorize(series: pd.Series, k: float = 3.0) -> pd.Series:
    """Clip to median ± k·1.4826·MAD within one cross-section (same rule as the rule model)."""
    center = series.median()
    mad = (series - center).abs().median()
    if pd.isna(mad) or mad == 0:
        return series
    radius = k * 1.4826 * mad
    return series.clip(center - radius, center + radius)


def preprocess(frame: pd.DataFrame, features: list) -> pd.DataFrame:
    """Cross-sectional winsorize + rank-normalize each feature within each snapshot."""
    frame = frame.copy()
    for col in features:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
        if col.startswith("peg_"):
            frame.loc[frame[col] < 0, col] = np.nan  # negative PEG is not a valid valuation
    def per_snapshot(group):
        for col in features:
            clipped = group[col] if col == "consecutive_profit_growth_quarters" else mad_winsorize(group[col])
            group[col] = clipped.rank(pct=True)
        return group
    return frame.groupby("snapshot_date", group_keys=False).apply(per_snapshot)


def add_label(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["fwd_return"] = pd.to_numeric(frame["fwd_return"], errors="coerce")
    frame = frame.dropna(subset=["fwd_return"])
    frame["label"] = frame.groupby("snapshot_date")["fwd_return"].rank(pct=True)
    return frame


# ------------------------------------------------------------------- model
def make_model(params: dict) -> XGBRegressor:
    params = dict(params)
    return XGBRegressor(tree_method="hist", n_jobs=-1, **params)


def fit(model: XGBRegressor, train: pd.DataFrame, valid: pd.DataFrame, features: list) -> XGBRegressor:
    model.fit(
        train[features], train["label"],
        eval_set=[(valid[features], valid["label"])],
        verbose=False,
    )
    return model


def split_train_valid(history: pd.DataFrame, n_valid: int):
    dates = sorted(history["snapshot_date"].unique())
    valid_dates = set(dates[-n_valid:])
    return history[~history["snapshot_date"].isin(valid_dates)], history[history["snapshot_date"].isin(valid_dates)]


# -------------------------------------------------------------- evaluation
def rank_ic(pred: pd.Series, realized: pd.Series) -> float:
    mask = pred.notna() & realized.notna()
    if mask.sum() < 10:
        return np.nan
    return spearmanr(pred[mask], realized[mask]).correlation


def top_n_return(group: pd.DataFrame, score_col: str, n: int) -> float:
    return group.nlargest(n, score_col)["fwd_return"].mean()


def summarize(ic: pd.Series) -> dict:
    ic = ic.dropna()
    if ic.empty:
        return {"mean_ic": np.nan, "icir": np.nan, "hit_rate": np.nan, "n": 0}
    std = ic.std(ddof=1)
    return {
        "mean_ic": ic.mean(),
        "icir": ic.mean() / std if std and std > 0 else np.nan,
        "hit_rate": (ic > 0).mean(),
        "n": int(ic.size),
    }


def walk_forward(panel: pd.DataFrame, config: dict) -> pd.DataFrame:
    features = config["features"]
    wf = config["walk_forward"]
    dates = sorted(panel["snapshot_date"].unique())
    predictions = []
    for i, test_date in enumerate(dates):
        train_end = i - int(wf["embargo_snapshots"])
        if train_end < int(wf["min_train_snapshots"]):
            continue
        history = panel[panel["snapshot_date"].isin(dates[:train_end])]
        train, valid = split_train_valid(history, int(wf["validation_snapshots"]))
        model = fit(make_model(config["model"]), train, valid, features)
        test = panel[panel["snapshot_date"] == test_date].copy()
        test["xgb_score"] = model.predict(test[features])
        predictions.append(test)
        print(f"{test_date}: 训练 {train['snapshot_date'].nunique()} 期，预测 {len(test)} 只")
    if not predictions:
        raise ValueError("快照数量不足以做滚动样本外测试，请继续每周归档因子数据")
    return pd.concat(predictions, ignore_index=True)


def evaluate(oos: pd.DataFrame, config: dict) -> tuple:
    n = int(config["portfolio"]["top_n"])
    cost = float(config["portfolio"]["round_trip_cost"])
    rows = []
    for date, group in oos.groupby("snapshot_date"):
        row = {
            "snapshot_date": date,
            "xgb_ic": rank_ic(group["xgb_score"], group["fwd_return"]),
            "xgb_top_return_net": top_n_return(group, "xgb_score", n) - cost,
            "pool_return": group["fwd_return"].mean(),
        }
        if "total_score" in group and group["total_score"].notna().any():
            row["rule_ic"] = rank_ic(group["total_score"], group["fwd_return"])
            row["rule_top_return_net"] = top_n_return(group, "total_score", n) - cost
        rows.append(row)
    ic_table = pd.DataFrame(rows)
    stats = {"XGBoost": summarize(ic_table["xgb_ic"])}
    if "rule_ic" in ic_table:
        stats["规则打分（基准）"] = summarize(ic_table["rule_ic"])
    return ic_table, stats


def write_report(ic_table: pd.DataFrame, stats: dict, importance: pd.DataFrame, config: dict):
    n = config["portfolio"]["top_n"]
    lines = [
        "# XGBoost 样本外评估报告",
        "",
        "所有结果均为滚动样本外（walk-forward）历史回测：每一期只用该期之前、并留出隔离期的数据训练。",
        "",
        "## Rank IC 汇总",
        "",
        "| 模型 | 平均 IC | ICIR | IC>0 比例 | 期数 |",
        "|---|---|---|---|---|",
    ]
    for name, s in stats.items():
        lines.append(f"| {name} | {s['mean_ic']:.4f} | {s['icir']:.3f} | {s['hit_rate']:.1%} | {s['n']} |")
    lines += ["", f"## Top{n} 组合（扣除双边成本后）每期平均收益", "", "| 组合 | 每期平均收益 |", "|---|---|"]
    lines.append(f"| XGBoost Top{n} | {ic_table['xgb_top_return_net'].mean():.4%} |")
    if "rule_top_return_net" in ic_table:
        lines.append(f"| 规则打分 Top{n} | {ic_table['rule_top_return_net'].mean():.4%} |")
    lines.append(f"| 候选池等权 | {ic_table['pool_return'].mean():.4%} |")
    lines += ["", "## 特征重要性（gain，全样本模型）", "", "| 特征 | 重要性 |", "|---|---|"]
    lines += [f"| {r.feature} | {r.importance:.4f} |" for r in importance.itertuples()]
    lines += [
        "",
        "## 解读提示",
        "",
        "- XGBoost 只有在 IC 和扣费后收益都稳定高于规则打分时才值得替换；否则说明增加的复杂度没有带来收益，应保留更简单的规则模型。",
        "- 样本期数较少时，IC 的标准误差很大，单期结果不具参考性。",
    ]
    (OUT / "xgb_report.md").write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------- modes
def load_panel(config: dict) -> pd.DataFrame:
    if not PANEL_FILE.exists():
        raise FileNotFoundError("缺少 data/xgb_panel.csv，请先运行 build_xgb_panel.py panel")
    panel = pd.read_csv(PANEL_FILE, encoding="utf-8-sig", dtype={"snapshot_date": str, "ts_code": str})
    return add_label(preprocess(panel, config["features"]))


def run_backtest(config: dict):
    panel = load_panel(config)
    oos = walk_forward(panel, config)
    ic_table, stats = evaluate(oos, config)
    importance = feature_importance(panel, config)
    OUT.mkdir(exist_ok=True)
    oos.to_csv(OUT / "xgb_oos_predictions.csv", index=False, encoding="utf-8")
    ic_table.to_csv(OUT / "xgb_ic.csv", index=False, encoding="utf-8")
    importance.to_csv(OUT / "xgb_feature_importance.csv", index=False, encoding="utf-8")
    write_report(ic_table, stats, importance, config)
    for name, s in stats.items():
        print(f"{name}: 平均IC={s['mean_ic']:.4f} ICIR={s['icir']:.3f} 期数={s['n']}")


def feature_importance(panel: pd.DataFrame, config: dict) -> pd.DataFrame:
    features = config["features"]
    train, valid = split_train_valid(panel, int(config["walk_forward"]["validation_snapshots"]))
    model = fit(make_model(config["model"]), train, valid, features)
    gain = model.get_booster().get_score(importance_type="gain")
    total = sum(gain.values()) or 1.0
    rows = [{"feature": f, "importance": gain.get(f, 0.0) / total} for f in features]
    return pd.DataFrame(rows).sort_values("importance", ascending=False)


def run_live(config: dict):
    features = config["features"]
    panel = load_panel(config)
    train, valid = split_train_valid(panel, int(config["walk_forward"]["validation_snapshots"]))
    model = fit(make_model(config["model"]), train, valid, features)

    current = pd.read_csv(CURRENT_FACTORS, encoding="utf-8-sig", dtype={"ts_code": str})
    current["snapshot_date"] = current.get("trade_date", "current")
    current = preprocess(current, features)
    current["xgb_score"] = model.predict(current[features])

    blend = config.get("blend", {})
    if blend.get("enabled") and RULE_RANKED.exists():
        rule = pd.read_csv(RULE_RANKED, encoding="utf-8-sig", dtype={"ts_code": str})[["ts_code", "total_score"]]
        current = current.merge(rule, on="ts_code", how="inner")  # keep only stocks that passed the rule filters
        current["blend_score"] = (
            float(blend["xgb_weight"]) * current["xgb_score"].rank(pct=True)
            + float(blend["rule_weight"]) * current["total_score"].rank(pct=True)
        )
        sort_col = "blend_score"
    else:
        sort_col = "xgb_score"
    current = current.sort_values(sort_col, ascending=False)
    current["xgb_rank"] = range(1, len(current) + 1)
    keep = ["xgb_rank", "ts_code", "name", "xgb_score"] + (["total_score", "blend_score"] if sort_col == "blend_score" else [])
    OUT.mkdir(exist_ok=True)
    current[[c for c in keep if c in current]].to_csv(OUT / "xgb_ranked_stocks.csv", index=False, encoding="utf-8")
    print(f"已输出 XGBoost 排名：{OUT / 'xgb_ranked_stocks.csv'}（按 {sort_col} 排序）")


def main():
    parser = argparse.ArgumentParser(description="XGBoost 横截面选股模型")
    parser.add_argument("--mode", choices=["backtest", "live"], default="backtest")
    parser.add_argument("--config", default=str(CONFIG_FILE))
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    run_backtest(config) if args.mode == "backtest" else run_live(config)


if __name__ == "__main__":
    main()
