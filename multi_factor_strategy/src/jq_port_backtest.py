"""Local port of the JoinQuant 12-factor + LightGBM strategy on free data.

The JoinQuant original (``joinquant_lightgbm/strategy_lite_lightgbm.py``) only
runs on the JoinQuant platform.  This script re-implements its scoring and
portfolio rules on the open-data cache built by ``src/open_data.py`` so the
method can be tested offline.  It is an approximation, not a replica:

* Universe: CSI 300 + CSI 500 constituents (point in time) instead of the
  six Shenwan industries + AI concept boards, which have no free history.
* Industry: CSRC industry division from BaoStock (current snapshot, applied
  to all dates) instead of point-in-time Shenwan level-1.
* ROE: Eastmoney year-to-date ROE instead of JoinQuant ``indicator.roe``.
* Limit-up closes: inferred from the daily return (10% main board, 20% for
  STAR and, from 2020-08-24, ChiNext) instead of the exchange limit price.
* Execution: the signal is computed at the decision-day close and the
  portfolio is traded at the next day's close (JoinQuant trades at the open);
  stocks that close limit-up on the entry day cannot be bought.

Everything else follows the original: the 12 factors and their
standardization, the market-strength and regime rules and dynamic core
weights, LightGBM on 18 cross-sections 20 trading days apart with 20-day
rank labels, a 25% out-of-sample validation gate (Rank IC >= 0), a 50/50
blend with the linear score, rebalancing every 20 trading days, the
limit-up pullback entry signal, the top-20 buy / top-40 sell buffer, the
stock / gold ETF / cash sleeves by regime and JoinQuant's cost schedule.

Usage
-----
    python src/open_data.py all                 # prices, constituents, earnings
    python src/jq_port_backtest.py download     # balance sheets, industry, index, gold ETF
    python src/jq_port_backtest.py backtest     # writes output/jq_port/
"""
import argparse
import socket
from pathlib import Path

import numpy as np
import pandas as pd

import open_data as od

EXTRA = od.CACHE / "jq_port"
OUT = od.ROOT / "output" / "jq_port"
GOLD = "sh.518880"

# ------------------------------------------------------------ parameters
P = {
    "price_count": 170, "rebalance_days": 20, "label_days": 20, "train_sections": 18,
    "min_sections": 8, "valid_ratio": 0.25, "min_valid_ic": 0.0, "num_rounds": 200,
    "blend": 0.5, "target_num": 20, "buy_rank": 20, "sell_rank": 40, "max_weight": 0.12,
    "signal_valid_days": 20, "limit_lookback": 10, "min_group": 5,
    "ml_neutral": {"trend": 0.5, "defensive": 0.7},
    "sleeves": {"risk_on": (0.95, 0.02), "neutral": (0.80, 0.08), "risk_off": (0.40, 0.18)},
    "buy_cost": 0.0003, "sell_cost": 0.0013,
    "industry_feature": True,   # LightGBM industry categorical (ablation: --no-industry-feature)
    "use_industry": True,       # False (--no-industry): every stock in one group, so no industry
                                # neutralization and the two industry factors become constant
}
LGB_PARAMS = {
    "objective": "regression", "learning_rate": 0.05, "num_leaves": 8, "max_depth": 3,
    "min_data_in_leaf": 100, "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1,
    "lambda_l2": 5.0, "min_data_per_group": 50, "seed": 42, "verbose": -1,
}
LITE_FACTORS = [
    ("relative_strength_20", "trend", {"trend": 0.34, "leader": 0.36}),
    ("ma60_slope", "trend", {"trend": 0.23, "leader": 0.12}),
    ("alpha_120", "all", {"trend": 0.10, "leader": 0.12, "defensive": 0.10 / 0.82}),
    ("money_quality", "trend", {"trend": 0.10}),
    ("log_avg_money_20", "all", {"trend": 0.04, "leader": 0.32}),
    ("limit_up_count_10d", "all", {"leader": 0.08}),
    ("roe", "defensive", {"trend": 0.05, "defensive": 0.38 / 0.82}),
    ("low_debt", "defensive", {"defensive": 0.10 / 0.82}),
    ("low_drawdown_60", "defensive", {"defensive": 0.14 / 0.82}),
    ("low_beta_120", "defensive", {"defensive": 0.10 / 0.82}),
    ("industry_flow_20d", "all", {"industry": 0.655}),
    ("industry_return_20d", "all", {"industry": 0.345}),
]
FEATURES = [c for c, _, _ in LITE_FACTORS]


# -------------------------------------------------------------- download
def download(start: str, end: str):
    import akshare as ak
    EXTRA.mkdir(parents=True, exist_ok=True)
    (EXTRA / "balance").mkdir(exist_ok=True)
    idx = ak.stock_zh_index_hist_csindex(symbol="000985", start_date="20171101", end_date="20251231")
    idx.rename(columns={"日期": "date", "收盘": "close"})[["date", "close"]].to_csv(EXTRA / "index_000985.csv", index=False)
    bs = od.bs_login()
    try:
        od.bs_frame(bs.query_stock_industry()).to_csv(EXTRA / "industry.csv", index=False)
        od.bs_frame(bs.query_history_k_data_plus(GOLD, "date,close", start_date="2017-11-01",
                                                 end_date="2025-12-31", frequency="d", adjustflag="1")
                    ).to_csv(EXTRA / "gold_518880.csv", index=False)
    finally:
        bs.logout()
    for q in od.quarter_ends(start, end):
        path = EXTRA / "balance" / f"{q}.csv"
        if path.exists():
            continue
        try:
            ak.stock_zcfz_em(date=q).to_csv(path, index=False, encoding="utf-8")
            print(f"balance {q}", flush=True)
        except Exception as exc:
            print(f"balance {q} skipped: {exc}")


# ----------------------------------------------------------- data loading
def load_prices():
    frames = []
    for path in sorted(od.PRICE_DIR.glob("*.csv")):
        df = pd.read_csv(path, usecols=["date", "code", "close", "amount", "tradestatus", "isST"])
        if not df.empty:
            frames.append(df)
    px = pd.concat(frames, ignore_index=True)
    px["date"] = pd.to_datetime(px["date"])
    wide = lambda col: px.pivot(index="date", columns="code", values=col).sort_index()
    close, amount = wide("close").astype(float), wide("amount").astype(float)
    tradable = (wide("tradestatus") == 1) & (wide("isST") == 0)
    close = close.where(tradable)  # suspended / ST days carry no price information
    return close, amount.where(tradable), tradable


def limit_up_flags(close: pd.DataFrame) -> pd.DataFrame:
    ret = close / close.ffill().shift(1) - 1.0
    codes = pd.Series(close.columns, index=close.columns)
    star = codes.str.startswith("sh.688")
    chinext = codes.str.startswith("sz.300")
    thr = pd.DataFrame(0.10, index=close.index, columns=close.columns)
    thr.loc[:, star.values] = 0.20
    thr.loc[close.index >= "2020-08-24", chinext.values] = 0.20
    return (ret >= thr - 0.002) & ret.notna()


def point_in_time(frames: pd.DataFrame, date: pd.Timestamp, cols: list) -> pd.DataFrame:
    avail = frames[(frames["available"] <= date) & (frames["available"] > date - pd.Timedelta(days=270))]
    latest = avail.sort_values(["period", "available"]).groupby("code").tail(1)
    return latest.set_index("code")[cols]


def load_balance() -> pd.DataFrame:
    frames = []
    for path in sorted((EXTRA / "balance").glob("*.csv")):
        df = pd.read_csv(path, dtype={"股票代码": str})
        period = pd.Timestamp(path.stem)
        announced = pd.to_datetime(df["公告日期"], errors="coerce")
        deadline = od.statutory_deadline(period)
        frames.append(pd.DataFrame({
            "code": df["股票代码"].map(od.to_bs_code), "period": period,
            "available": announced.where(announced > period, deadline).fillna(deadline),
            "debt_to_asset": pd.to_numeric(df["资产负债率"], errors="coerce"),
        }))
    if not frames:
        return pd.DataFrame(columns=["code", "period", "available", "debt_to_asset"])
    return pd.concat(frames, ignore_index=True)


def load_industry() -> pd.Series:
    ind = pd.read_csv(EXTRA / "industry.csv", dtype=str)
    return ind.set_index("code")["industry"].fillna("").str[:3].replace("", "Other")


# ------------------------------------------------- helpers from the original
def clip(v, lo, hi):
    return max(lo, min(hi, float(v)))


def zscore(series):
    s = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if s.dropna().empty:
        return pd.Series(0.0, index=s.index)
    median = s.median()
    mad = (s - median).abs().median()
    if pd.isna(mad) or mad <= 0:
        std = s.std()
        if pd.isna(std) or std <= 0:
            return pd.Series(0.0, index=s.index)
        clipped = s.clip(s.mean() - 3 * std, s.mean() + 3 * std)
    else:
        clipped = s.clip(median - 3 * 1.4826 * mad, median + 3 * 1.4826 * mad)
    std = clipped.std()
    if pd.isna(std) or std <= 0:
        return pd.Series(0.0, index=s.index)
    return ((clipped - clipped.mean()) / std).fillna(0.0)


def blended_zscore(industry, values, strength):
    strength = clip(strength, 0, 1)
    global_score = zscore(values)
    if strength <= 0:
        return global_score
    sizes = industry.groupby(industry).transform("size").reindex(values.index).fillna(0.0)
    effective = strength * (sizes / float(P["min_group"])).clip(upper=1.0)
    within = pd.DataFrame({"i": industry, "v": values}).groupby("i")["v"].transform(zscore)
    return ((1 - effective) * global_score + effective * within.reindex(values.index).fillna(0.0)).fillna(0.0)


def calc_beta(stock_ret, index_ret, window):
    y = stock_ret.tail(window).values.astype(float)
    x = index_ret.reindex(stock_ret.tail(window).index).values.astype(float).reshape(-1, 1)
    valid = ~np.isnan(y) & ~np.isnan(x)
    n = valid.sum(axis=0).astype(float)
    xf, yf = np.where(np.isnan(x), 0, x), np.where(np.isnan(y), 0, y)
    with np.errstate(all="ignore"):
        xm = (xf * valid).sum(0) / n
        ym = (yf * valid).sum(0) / n
        xd, yd = (xf - xm) * valid, (yf - ym) * valid
        cov = (xd * yd).sum(0) / (n - 1)
        var = (xd ** 2).sum(0) / n
        beta = np.where((n >= 60) & (var > 0), cov / var, 1.0)
    return pd.Series(beta, index=stock_ret.columns)


# ------------------------------------------------------------ factor frame
class Data:
    def __init__(self):
        self.close, self.money, self.tradable = load_prices()
        self.flags = limit_up_flags(self.close)
        idx = pd.read_csv(EXTRA / "index_000985.csv", parse_dates=["date"]).set_index("date")["close"]
        self.index_close = idx.astype(float).sort_index()
        gold = pd.read_csv(EXTRA / "gold_518880.csv", parse_dates=["date"]).set_index("date")["close"]
        self.gold = gold.astype(float).sort_index()
        self.fund = od.load_fundamentals()
        self.balance = load_balance()
        self.industry = load_industry()
        uni = pd.read_csv(od.UNIVERSE_FILE, dtype=str)
        uni["snapshot_date"] = pd.to_datetime(uni["snapshot_date"])
        self.universe = uni.groupby("snapshot_date")["code"].apply(set).sort_index()
        self.days = self.close.index[self.close.index.isin(self.index_close.index)]
        # daily limit-up pullback signal (computed on all stocks; filtered to the universe later)
        lu_close = self.close.where(self.flags)
        last_lu = lu_close.ffill(limit=P["limit_lookback"] - 1)
        self.signal = last_lu.notna() & (self.close < last_lu)

    def members(self, date):
        past = self.universe.loc[:date]
        return past.iloc[-1] if len(past) else set()

    def raw_frame(self, date, stocks):
        """The original build_raw_factor_frame, as of the close of ``date``."""
        hist = self.close.loc[:date].tail(P["price_count"])
        stocks = [s for s in stocks if s in hist.columns]
        close_df = hist[stocks].ffill()
        close_df = close_df.loc[:, close_df.notna().sum() >= 120]
        close_df = close_df.loc[:, close_df.iloc[-1].notna()]
        money_df = self.money.loc[:date].tail(P["price_count"])[close_df.columns]
        index_close = self.index_close.loc[:date].tail(P["price_count"])
        common = close_df.index.intersection(index_close.index)
        close_df, money_df, index_close = close_df.loc[common], money_df.loc[common], index_close.loc[common]
        if close_df.shape[1] < 30 or len(close_df) < 121:
            return None

        df = pd.DataFrame(index=close_df.columns)
        fund = point_in_time(self.fund, date, ["roe"])
        bal = point_in_time(self.balance, date, ["debt_to_asset"])
        df["roe"] = fund["roe"].reindex(df.index)
        df["debt_to_asset"] = bal["debt_to_asset"].reindex(df.index)

        now = close_df.iloc[-1]
        ret = close_df.pct_change()
        iret = index_close.pct_change()
        df["relative_strength_20"] = now / close_df.iloc[-21] - 1 - (index_close.iloc[-1] / index_close.iloc[-21] - 1)
        df["ma60_slope"] = close_df.tail(60).mean() / close_df.iloc[-70:-10].mean() - 1
        beta = calc_beta(ret, iret, 120)
        df["alpha_120"] = (ret.tail(120).mean() - beta * iret.tail(120).mean()) * 252.0
        df["low_drawdown_60"] = -(1 - now / close_df.tail(60).max())
        df["low_beta_120"] = -beta
        df["log_avg_money_20"] = np.log1p(money_df.tail(20).mean())
        df["money_quality"] = -(money_df.tail(5).mean() / money_df.tail(20).mean() - 1.15).abs()
        df["low_debt"] = -df["debt_to_asset"]
        df["limit_up_count_10d"] = self.flags.loc[:date].tail(P["limit_lookback"])[df.index].sum().astype(int)

        if P["use_industry"]:
            df["industry"] = self.industry.reindex(df.index).fillna("Other")
        else:
            df["industry"] = "All"
        ret20 = now / close_df.iloc[-21] - 1
        flow20 = money_df.tail(20).mean() / money_df.tail(60).mean()
        df["industry_return_20d"] = df["industry"].map(ret20.groupby(df["industry"]).mean())
        df["industry_flow_20d"] = df["industry"].map(flow20.groupby(df["industry"]).mean())
        if not P["use_industry"]:
            # a single group would turn these into market-wide values that vary only over time
            df["industry_return_20d"] = 0.0
            df["industry_flow_20d"] = 0.0

        df = df.replace([np.inf, -np.inf], np.nan)
        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                df[col] = df[col].fillna(df[col].median() if pd.notna(df[col].median()) else 0.0)
        return df, close_df, money_df, index_close


# ------------------------------------------------- market regime and weights
def market_state(index_close, close_df, money_df, prev_regime):
    idx = index_close.dropna()
    ma20_df = close_df.rolling(20).mean()
    breadth_series = ((close_df >= ma20_df) & ma20_df.notna()).mean(axis=1)
    breadth = float(((close_df.iloc[-1] >= close_df.tail(20).mean()) & close_df.tail(20).mean().notna()).mean())

    ret5, ret10 = idx.iloc[-1] / idx.iloc[-6] - 1, idx.iloc[-1] / idx.iloc[-11] - 1
    ma5, ma20, ma60, ma120 = (idx.tail(n).mean() for n in (5, 20, 60, 120))
    b_now = float(breadth_series.iloc[-1])
    b_chg5 = float(b_now - breadth_series.iloc[-6])
    total_money = money_df.sum(axis=1)
    m_ratio = total_money.tail(5).mean() / total_money.tail(20).mean() - 1
    forecast = (0.25 * clip(ret5 / 0.03, -1, 1) + 0.20 * clip(ret10 / 0.05, -1, 1) +
                0.20 * clip((ma5 / ma20 - 1) / 0.025, -1, 1) + 0.15 * clip((b_now - 0.5) / 0.25, -1, 1) +
                0.15 * clip(b_chg5 / 0.12, -1, 1) + 0.05 * clip(m_ratio / 0.25, -1, 1))
    last = float(idx.iloc[-1])
    raw = (0.20 * clip((last / ma20 - 1) / 0.06, -1, 1) + 0.15 * clip((ma20 / ma60 - 1) / 0.08, -1, 1) +
           0.15 * clip((ma60 / ma120 - 1) / 0.10, -1, 1) + 0.30 * clip(forecast / 0.60, -1, 1) +
           0.20 * clip((breadth - 0.5) / 0.30, -1, 1))
    strength = clip(0.5 + 0.5 * raw, 0, 1)

    weak_series = ((idx < idx.rolling(60).mean()) & (idx.rolling(60).mean() < idx.rolling(120).mean()))
    weak = bool(weak_series.tail(3).all()) and breadth <= 0.45 and forecast <= 0 and last < ma20
    if last >= ma20:
        regime = "risk_on"
    elif weak and forecast <= -0.35:
        regime = "risk_off"
    elif prev_regime == "risk_off" and last < ma60 and ma5 < ma20 and breadth <= 0.5 and forecast < 0.35:
        regime = "risk_off"
    else:
        regime = "neutral"
    return strength, regime


def core_weights(strength, regime):
    w = {"trend": 0.18 + 0.30 * strength, "industry": 0.22, "leader": 0.15 + 0.05 * strength,
         "defensive": 0.45 - 0.30 * strength}
    if regime == "risk_on":
        leader = 0.40
        w = {"trend": 0.30 + 0.05 * strength, "industry": 0.15, "leader": leader,
             "defensive": 0.85 - leader - (0.30 + 0.05 * strength)}
    elif regime == "risk_off":
        w["trend"] -= 0.03
        w["defensive"] += 0.03
    bounds = {"trend": (0.10, 0.55), "industry": (0.15, 0.30), "leader": (0.15, 0.50), "defensive": (0.10, 0.55)}
    w = {k: clip(v, *bounds[k]) for k, v in w.items()}
    total = sum(w.values())
    return {k: v / total for k, v in w.items()}


def linear_score(df, strength, regime):
    w = core_weights(strength, regime)
    trend_n = clip(0.75 + (0.20 - 0.75) * strength, 0, 1)
    def_n = clip(0.90 + (0.55 - 0.90) * strength, 0, 1)
    neutral = {"trend": trend_n, "defensive": def_n}
    score = pd.Series(0.0, index=df.index)
    for col, mode, shares in LITE_FACTORS:
        weight = sum(w[c] * s for c, s in shares.items())
        z = zscore(df[col]) if mode == "all" else blended_zscore(df["industry"], df[col], neutral[mode])
        score += weight * z
    return score.fillna(0.0)


def ml_features(df):
    feats = {}
    for col, mode, _ in LITE_FACTORS:
        feats[col] = zscore(df[col]) if mode == "all" else blended_zscore(df["industry"], df[col], P["ml_neutral"][mode])
    frame = pd.DataFrame(feats, index=df.index).fillna(0.0)
    frame["industry"] = df["industry"].astype(str)
    return frame


# ---------------------------------------------------------------- LightGBM
class Model:
    def __init__(self):
        import lightgbm as lgb
        self.lgb = lgb
        self.codes = {}

    def matrix(self, feats):
        for name in pd.unique(feats["industry"]):
            self.codes.setdefault(name, len(self.codes))
        values = feats[FEATURES].values.astype(float)
        if not P["industry_feature"]:
            return values
        ind = feats["industry"].map(self.codes).values.astype(float).reshape(-1, 1)
        return np.hstack([values, ind])

    def fit(self, sections):
        X = np.vstack([self.matrix(f) for _, f, _ in sections])
        y = np.concatenate([lab.values for _, _, lab in sections])
        if P["industry_feature"]:
            data = self.lgb.Dataset(X, label=y, feature_name=FEATURES + ["industry_code"],
                                    categorical_feature=[len(FEATURES)], free_raw_data=False)
        else:
            data = self.lgb.Dataset(X, label=y, feature_name=FEATURES, free_raw_data=False)
        return self.lgb.train(dict(LGB_PARAMS), data, num_boost_round=P["num_rounds"])

    def predict(self, booster, feats):
        return pd.Series(booster.predict(self.matrix(feats)), index=feats.index)


def rank_ic(a, b):
    a, b = a.align(b, join="inner")
    if len(a) < 10 or a.std() == 0 or b.std() == 0:
        return np.nan
    return a.rank().corr(b.rank())


# ---------------------------------------------------------------- backtest
def run_backtest():
    data = Data()
    model = Model()
    days = data.days[data.days >= data.universe.index[0]]
    grid = list(days[::P["rebalance_days"]])
    day_pos = {d: i for i, d in enumerate(data.days)}

    def shift(date, n):
        i = day_pos[date] + n
        return data.days[i] if 0 <= i < len(data.days) else None

    # cross-section cache: features and 20-day labels (close t -> close t+20) per grid date
    sections, frames = {}, {}
    for d in grid:
        stocks = [s for s in data.members(d) if s in data.tradable.columns and bool(data.tradable.at[d, s])]
        raw = data.raw_frame(d, stocks)
        if raw is None:
            continue
        frames[d] = raw
        end = shift(d, P["label_days"])
        if end is not None:
            fwd = data.close.loc[end, raw[0].index] / data.close.loc[d, raw[0].index] - 1
            fwd = fwd.dropna()
            feats = ml_features(raw[0]).loc[fwd.index]
            if len(fwd) >= 30:
                sections[d] = (d, feats, fwd.rank(pct=True) - 0.5)
    print(f"grid dates={len(grid)} frames={len(frames)} sections={len(sections)}", flush=True)

    regime = "neutral"
    records, ic_rows, importances = [], [], []
    holdings = {k: {} for k in ["linear", "lightgbm", "blend", "strategy"]}
    last_strategy_stocks = []
    for j, d in enumerate(grid):
        if d not in frames:
            continue
        df, close_df, money_df, index_close = frames[d]
        strength, regime = market_state(index_close, close_df, money_df, regime)
        lin = linear_score(df, strength, regime)
        past = [sections[g] for g in grid[max(0, j - P["train_sections"]):j] if g in sections]
        if len(past) < P["min_sections"]:
            continue
        n_valid = max(1, int(round(len(past) * P["valid_ratio"])))
        booster = model.fit(past[:-n_valid])
        valid_ic = np.nanmean([rank_ic(model.predict(booster, f), lab) for _, f, lab in past[-n_valid:]])
        booster = model.fit(past)
        pred = model.predict(booster, ml_features(df))
        gate = bool(pd.notna(valid_ic) and valid_ic >= P["min_valid_ic"])
        blend = P["blend"] * pred.rank(pct=True) + (1 - P["blend"]) * lin.rank(pct=True) if gate else lin.rank(pct=True)
        importances.append(dict(zip(booster.feature_name(), booster.feature_importance("gain"))))

        entry, exit_ = shift(d, 1), shift(grid[j + 1], 1) if j + 1 < len(grid) else None
        if entry is None or exit_ is None:
            break
        px_in, px_out = data.close.loc[entry], data.close.loc[exit_].fillna(data.close.loc[:exit_].ffill().iloc[-1])
        fwd = (px_out / px_in - 1).reindex(df.index)
        tradable_in = data.tradable.loc[entry].reindex(df.index).fillna(False).astype(bool)
        limit_up_in = data.flags.loc[entry].reindex(df.index).fillna(False).astype(bool)
        buyable = tradable_in & ~limit_up_in  # cannot buy a stock that closes limit-up
        ic_rows.append({"date": d, "linear": rank_ic(lin, fwd), "lightgbm": rank_ic(pred, fwd),
                        "blend": rank_ic(blend, fwd), "valid_ic": valid_ic, "gate": gate, "regime": regime})

        rec = {"date": d, "entry": entry, "exit": exit_, "regime": regime, "gate": gate,
               "pool": fwd.mean(), "index": data.index_close.loc[exit_] / data.index_close.loc[entry] - 1}
        for name, score in [("linear", lin), ("lightgbm", pred), ("blend", blend)]:
            pick = score[buyable].nlargest(P["target_num"]).index
            w = {s: 1.0 / len(pick) for s in pick}
            rec[name], holdings[name] = period_return(w, holdings[name], fwd)

        # full strategy approximation: entry-signal pool + buffer + regime sleeves + gold ETF
        active = data.signal.loc[:d].tail(P["signal_valid_days"]).any()
        held = [s for s in last_strategy_stocks if s in df.index]
        pool = [s for s in df.index if (bool(active.get(s, False)) and bool(buyable[s])) or s in held]
        ranked = blend.loc[pool].sort_values(ascending=False).index.tolist()
        rank_of = {s: i + 1 for i, s in enumerate(ranked)}
        kept = [s for s in ranked if s in held and rank_of[s] <= P["sell_rank"]]
        new = [s for s in ranked if s not in held and rank_of[s] <= P["buy_rank"]][:max(0, P["target_num"] - len(kept))]
        target = kept[:P["target_num"]] + new
        equity, gold_w = P["sleeves"][regime]
        per = min(P["max_weight"], equity / len(target)) if target else 0.0
        w = {s: per for s in target}
        gold_ret = data.gold.asof(exit_) / data.gold.asof(entry) - 1
        w[GOLD] = gold_w
        fwd_g = pd.concat([fwd, pd.Series({GOLD: gold_ret})])
        rec["strategy"], holdings["strategy"] = period_return(w, holdings["strategy"], fwd_g)
        rec["strategy_names"] = len(target)
        last_strategy_stocks = target
        records.append(rec)

    write_results(pd.DataFrame(records), pd.DataFrame(ic_rows), pd.DataFrame(importances))


def period_return(weights, previous, fwd):
    """Net return of a portfolio rebalanced to ``weights``; residual weight is cash (0 return)."""
    names = set(weights) | set(previous)
    buys = sum(max(0.0, weights.get(s, 0) - previous.get(s, 0)) for s in names)
    sells = sum(max(0.0, previous.get(s, 0) - weights.get(s, 0)) for s in names)
    gross = sum(w * (fwd.get(s, 0.0) if pd.notna(fwd.get(s, np.nan)) else 0.0) for s, w in weights.items())
    net = gross - buys * P["buy_cost"] - sells * P["sell_cost"]
    # drift weights to the end of the period for the next turnover calculation
    grown = {s: w * (1 + (fwd.get(s, 0.0) if pd.notna(fwd.get(s, np.nan)) else 0.0)) for s, w in weights.items()}
    total = 1 + gross
    return net, {s: v / total for s, v in grown.items()}


def stats(r: pd.Series, per_year: float) -> dict:
    nav = (1 + r).cumprod()
    years = len(r) / per_year
    return {"ann_return": nav.iloc[-1] ** (1 / years) - 1, "ann_vol": r.std() * np.sqrt(per_year),
            "sharpe": r.mean() / r.std() * np.sqrt(per_year), "max_drawdown": (nav / nav.cummax() - 1).min(),
            "total_return": nav.iloc[-1] - 1}


def write_results(rec, ic, imp):
    OUT.mkdir(parents=True, exist_ok=True)
    rec.to_csv(OUT / "period_returns.csv", index=False)
    ic.to_csv(OUT / "period_ic.csv", index=False)
    per_year = 252 / P["rebalance_days"]
    labels = {"linear": "Linear 12-factor score, top 20", "lightgbm": "LightGBM only, top 20",
              "blend": "50/50 blend (with validation gate), top 20",
              "strategy": "Full strategy approximation (signal pool, buffer, sleeves, gold)",
              "pool": "Equal-weight universe (CSI 300 + CSI 500)", "index": "CSI All Share index"}
    rows = []
    for col, label in labels.items():
        s = stats(rec[col], per_year)
        excess = rec[col] - rec["index"]
        rows.append({"model": label, **s, "excess_vs_index_ann": excess.mean() * per_year,
                     "periods_beating_index": (excess > 0).mean()})
    table = pd.DataFrame(rows)
    table.to_csv(OUT / "summary.csv", index=False)
    ic_sum = ic[["linear", "lightgbm", "blend"]].agg(["mean", "std"]).T
    ic_sum["icir"] = ic_sum["mean"] / ic_sum["std"]
    ic_sum["ic_positive"] = (ic[["linear", "lightgbm", "blend"]] > 0).mean()
    imp_share = (imp.div(imp.sum(axis=1), axis=0).mean().sort_values(ascending=False) * 100).round(1)

    pct = lambda v: f"{v:.1%}"
    lines = [
        "# JoinQuant 12-factor + LightGBM strategy: local port results",
        "",
        f"Historical backtest, {rec['entry'].min():%Y-%m-%d} to {rec['exit'].max():%Y-%m-%d}, "
        f"{len(rec)} rebalances every {P['rebalance_days']} trading days, universe CSI 300 + CSI 500. "
        "Returns are net of JoinQuant's cost schedule (0.03% commission each way, 0.1% stamp duty on sales).",
        "",
        "| Portfolio | Ann. return | Ann. vol | Sharpe | Max drawdown | Excess vs index (ann.) | Periods beating index |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in table.itertuples(index=False):
        lines.append(f"| {r.model} | {pct(r.ann_return)} | {pct(r.ann_vol)} | {r.sharpe:.2f} | "
                     f"{pct(r.max_drawdown)} | {pct(r.excess_vs_index_ann)} | {pct(r.periods_beating_index)} |")
    lines += ["", "## Rank IC per 20-day period (whole universe)", "",
              "| Score | Mean IC | IC std | ICIR | IC > 0 |", "|---|---|---|---|---|"]
    for name, r in ic_sum.iterrows():
        lines.append(f"| {name} | {r['mean']:.4f} | {r['std']:.4f} | {r['icir']:.2f} | {pct(r['ic_positive'])} |")
    lines += ["", f"Validation gate passed in {pct(ic['gate'].mean())} of rebalances "
              f"(mean validation Rank IC {ic['valid_ic'].mean():.4f}). Regimes: "
              + ", ".join(f"{k} {v}" for k, v in ic["regime"].value_counts().items()) + ".",
              "", "## LightGBM feature importance (gain share, average)", ""]
    lines += [f"- {k}: {v}%" for k, v in imp_share.items()]
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="Local port of the JoinQuant 12-factor + LightGBM strategy")
    parser.add_argument("step", choices=["download", "backtest"])
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--end", default="2025-10-31")
    parser.add_argument("--no-industry-feature", action="store_true",
                        help="ablation: drop the industry categorical from LightGBM")
    parser.add_argument("--no-industry", action="store_true",
                        help="ablation: no industry classification at all (no neutralization, "
                             "industry factors constant, no industry categorical)")
    args = parser.parse_args()
    parser_ns = args
    global OUT
    if getattr(parser_ns, "no_industry", False):
        P["use_industry"] = False
        P["industry_feature"] = False
        OUT = OUT.parent / "jq_port_no_industry"
    elif args.no_industry_feature:
        P["industry_feature"] = False
        OUT = OUT.parent / "jq_port_no_industry_feature"
    socket.setdefaulttimeout(120)
    download(args.start, args.end) if args.step == "download" else run_backtest()


if __name__ == "__main__":
    main()
