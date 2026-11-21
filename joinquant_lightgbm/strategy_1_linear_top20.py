# -*- coding: utf-8 -*-
from jqdata import *
import pandas as pd
import numpy as np

try:
    import lightgbm as lgb
except Exception:
    lgb = None

try:
    import xgboost as xgb
except Exception:
    xgb = None


"""
Strategy 1: linear 12-factor score, top 20
==========================================

Ranks stocks by the hand-weighted linear score only (g.use_ml_score = False).

Shared rules (all three variants)
---------------------------------
- Universe: CSI 300 + CSI 500 constituents on each decision date
  (g.universe_mode = 'csi800'; set 'sector' for the original six Shenwan
  industries + AI concept boards). ST, suspended and limit-up/limit-down
  stocks are excluded.
- Every 20 trading days, hold the top 20 stocks by final_score, equal weight,
  99% invested (1% cash for fees), no gold ETF, no buffer, no tolerance band.
- 12 factors: 20-day relative strength, MA60 slope, 120-day alpha, volume
  quality, log 20-day turnover, 10-day limit-up count, ROE, low debt, low
  60-day drawdown, low 120-day beta, 20-day industry flow and return.
- LightGBM (XGBoost fallback): 18 cross-sections 20 trading days apart,
  label = cross-sectional rank of the 20-day forward return, every label
  window ends on or before the decision date; the latest 25% of
  cross-sections are held out to measure validation Rank IC.
- Costs: 0.03% commission each way, 0.1% stamp duty on sales, 5 CNY minimum.

Inline comments below are from the original Chinese version
(strategy_lite_lightgbm.py); the switches in initialize() control everything
that differs between variants.
"""



def initialize(context):
    g.index = '000985.XSHG'
    g.strategy_name = 'Strategy 1'
    set_benchmark(g.index)
    set_option('use_real_price', True)

    try:
        set_option('avoid_future_data', True)
    except Exception:
        pass

    set_order_cost(
        OrderCost(
            open_tax=0,
            close_tax=0.001,
            open_commission=0.0003,
            close_commission=0.0003,
            close_today_commission=0,
            min_commission=5
        ),
        type='stock'
    )
    try:
        set_order_cost(
            OrderCost(
                open_tax=0,
                close_tax=0,
                open_commission=0.0003,
                close_commission=0.0003,
                close_today_commission=0,
                min_commission=5
            ),
            type='fund'
        )
    except Exception:
        pass

    # ---- 股票池 ----
    g.target_industry_codes = {
        'AI计算机': '801750',
        '半导体': '801081',
        '通信': '801770',
        '银行': '801780',
        '证券': '801193',
        '有色金属': '801050',
    }
    g.ai_concept_keywords = [
        '人工智能', 'AI', 'AIGC', 'ChatGPT', '大模型',
        '算力', '云计算', '数据要素', '机器视觉',
    ]
    g.ai_concepts_catalog = None
    g.ai_concept_codes_by_date = {}
    g.sector_universe_cache_date = None
    g.sector_universe_cache_stocks = []
    g.min_sector_universe_size = 50

    # ---- 入场事件与调仓节奏（与完整版保持一致，便于对照） ----
    g.target_stock_num = 20
    g.limit_up_lookback_trading_days = 10
    g.limit_up_min_count_10d = 1
    g.limit_up_tolerance = 1e-4
    g.rebalance_interval_trading_days = 20
    g.entry_signal_valid_trading_days = 20
    g.max_single_stock_weight = 0.12

    # ---- 四核打分 ----
    g.trend_neutral_weak = 0.75
    g.trend_neutral_strong = 0.20
    g.defensive_neutral_weak = 0.90
    g.defensive_neutral_strong = 0.55
    g.trend_industry_neutral_strength = 0.475
    g.defensive_industry_neutral_strength = 0.725
    g.industry_neutral_min_group_size = 5
    g.risk_on_leader_weight = 0.40

    # ---- 机器学习打分 ----
    g.use_ml_score = False
    g.ml_backend = 'auto'         # 'auto'=先试LightGBM再试XGBoost；也可写 'lightgbm' 或 'xgboost'
    g.ml_blend_weight = 1.0       # 模型排名占比：1=只用模型，0=只用线性打分
    g.ml_label_days = 20          # 标签：未来20个交易日收益的截面排名
    g.ml_section_step = 20        # 历史截面间隔（不小于标签天数，避免标签重叠）
    g.ml_train_sections = 18      # 历史截面个数，约一年半
    g.ml_min_sections = 8         # 少于这个数不训练
    g.ml_valid_ratio = 0.25       # 最近25%的截面只用于样本外验证
    g.ml_min_valid_ic = 0.0       # 验证RankIC低于此值时本期不采用模型（仅在启用门槛时生效）
    g.ml_use_valid_gate = False   # False=不设门槛，模型可用就用
    g.ml_use_industry_feature = False  # LightGBM 是否把行业作为类别特征
    g.ml_num_rounds = 200
    g.lgb_params = {
        'objective': 'regression',
        'learning_rate': 0.05,
        'num_leaves': 8,          # 叶子少 + 叶子最小样本大，控制过拟合
        'max_depth': 3,
        'min_data_in_leaf': 100,
        'feature_fraction': 0.8,
        'bagging_fraction': 0.8,
        'bagging_freq': 1,
        'lambda_l2': 5.0,
        'min_data_per_group': 50,  # 行业类别特征：每组至少50个样本
        'seed': 42,
        'verbose': -1,
    }
    g.xgb_params = {
        'max_depth': 3,
        'eta': 0.05,
        'subsample': 0.8,
        'colsample_bytree': 0.8,
        'min_child_weight': 50,
        'lambda': 5.0,
        'seed': 42,
    }
    g.ml_neutral_strength = {'trend': 0.5, 'defensive': 0.7}
    g.ml_section_cache = {}
    g.ml_industry_codes = {}

    # ---- 换股缓冲区与权重容忍带 ----
    g.buffer_buy_rank = 20        # 新股票排进前20才买入
    g.buffer_sell_rank = 20       # 设成与买入名次相同 = 不设缓冲区（原版40）
    g.rebalance_tolerance = 0.0   # 0 = 不设容忍带（原版0.015）
    g.use_entry_signal_pool = False  # False=全股票池排名；True=只在涨停回落信号股+持仓里排名（原版）
    g.universe_mode = 'csi800'    # 'csi800'=沪深300+中证500；'sector'=原版六个行业+AI概念
    g.last_market_strength = 0.50

    # ---- 市场档位与仓位 ----
    g.market_forecast_on_threshold = 0.35
    g.market_forecast_off_threshold = -0.35
    g.market_breadth_weak_threshold = 0.45
    g.market_regime_confirm_days = 3
    g.gold_hedge_etf = '518880.XSHG'  # 华安黄金ETF
    # 满仓股票（留1%现金付手续费）；原版：risk_on 95/2/3，neutral 80/8/12，risk_off 40/18/42
    g.sleeve_weights_by_regime = {
        'risk_on':  {'equity': 0.99, 'gold': 0.0, 'cash': 0.01},
        'neutral':  {'equity': 0.99, 'gold': 0.0, 'cash': 0.01},
        'risk_off': {'equity': 0.99, 'gold': 0.0, 'cash': 0.01},
    }

    g.price_count = 170
    g.last_rebalance_date = None
    g.pending_entry_signals = {}
    g.last_regime = 'neutral'

    log.set_level('order', 'error')
    run_daily(rebalance, time='open', reference_security=g.index)
    log.info('%s initialized: universe=%s rebalance every %d days, model weight=%.2f, ML=%s' % (
        g.strategy_name, g.universe_mode, g.rebalance_interval_trading_days, g.ml_blend_weight, g.use_ml_score
    ))


def rebalance(context):
    """每日扫描入场事件；到调仓日时按因子排名和市场档位调整组合。"""
    current_data = get_current_data()
    decision_date = context.previous_date
    held_stocks = get_held_equity_stocks(context)

    if bool(getattr(g, 'use_entry_signal_pool', True)):
        entry_stocks = scan_limit_up_pullback(decision_date, held_stocks)
        active_entry_signals = update_entry_signal_cache(decision_date, entry_stocks)
    else:
        active_entry_signals = []
    if not should_rebalance_today(context):
        return

    # 唯一的个股退出来源：持仓已不在目标行业池内。
    exit_stocks = set()
    focused_universe = set(get_stock_universe(decision_date))
    if len(focused_universe) >= int(g.min_sector_universe_size):
        exit_stocks = set(stock for stock in held_stocks if stock not in focused_universe)

    factor_df, regime = get_factor_df(context, decision_date, current_data)
    if factor_df is None or factor_df.empty:
        log.info('因子表为空，保持原仓位不动')
        g.last_rebalance_date = pd.Timestamp(context.current_dt.date())
        return

    survivors = [stock for stock in held_stocks if stock not in exit_stocks]
    target_stocks, new_candidates, rank_dropped = build_buffered_target_stocks(
        factor_df, survivors, active_entry_signals, int(g.target_stock_num)
    )

    sleeve_weights = get_sleeve_weights(regime)
    target_weights = build_equal_event_weights(target_stocks, sleeve_weights['equity'])
    hedge_weights = build_hedge_sleeve_weights(sleeve_weights, current_data)
    target_weights.update(hedge_weights)

    frozen = find_positions_within_tolerance(context, target_weights)
    execute_orders(context, target_weights, current_data, frozen)

    g.last_rebalance_date = pd.Timestamp(context.current_dt.date())
    g.last_regime = regime
    log.info('调仓完成 regime=%s 股票仓=%.2f 黄金ETF=%.2f 持仓=%d 新买入=%d 排名掉队卖出=%d 出池卖出=%d 容忍带内不动=%d' % (
        regime, sleeve_weights['equity'], hedge_weights.get(g.gold_hedge_etf, 0.0),
        len(target_stocks), len(new_candidates), len(rank_dropped), len(exit_stocks), len(frozen)
    ))
    log.info('目标股票：%s' % ', '.join(target_stocks))


def get_sleeve_weights(regime):
    """按 regime 返回 股票/黄金ETF/现金 三个仓位比例。"""
    table = g.sleeve_weights_by_regime
    return dict(table.get(regime, table['neutral']))


def get_held_equity_stocks(context):
    gold_code = getattr(g, 'gold_hedge_etf', None)
    result = []
    for stock, position in context.portfolio.positions.items():
        if position.total_amount <= 0 or stock == gold_code:
            continue
        result.append(stock)
    return sorted(result)


def scan_limit_up_pullback(decision_date, held_stocks):
    """近10个交易日有收盘涨停，且当前收盘价低于最近一次涨停收盘价。"""
    stocks = set(get_stock_universe(decision_date))
    stocks.update(held_stocks)
    stocks = sorted(stocks)
    if not stocks:
        return []

    lookback = int(g.limit_up_lookback_trading_days)
    try:
        price = get_price(
            stocks,
            count=max(11, lookback + 1),
            end_date=decision_date,
            frequency='daily',
            fields=['close', 'high_limit'],
            panel=False
        )
    except Exception as exc:
        log.info('[扫描] 读取行情失败：%s' % str(exc))
        return []

    close_df = normalize_price_matrix(price_to_field_matrix(price, stocks, 'close'))
    high_limit_df = normalize_price_matrix(price_to_field_matrix(price, stocks, 'high_limit'))
    if close_df is None or high_limit_df is None:
        return []
    close_df = close_df.sort_index()
    if len(close_df) < max(11, lookback + 1):
        return []
    common_stocks = sorted(set(close_df.columns).intersection(set(high_limit_df.columns)))
    if not common_stocks:
        return []

    close_df = close_df.reindex(columns=common_stocks)
    high_limit_df = high_limit_df.reindex(index=close_df.index, columns=common_stocks)
    recent_close = close_df.tail(lookback)
    recent_limit = high_limit_df.tail(lookback)
    closed_limit = (
        (recent_close >= recent_limit * (1.0 - float(g.limit_up_tolerance))) &
        recent_close.notna() & recent_limit.notna() & (recent_limit > 0)
    )
    limit_ok = closed_limit.sum(axis=0) >= int(g.limit_up_min_count_10d)
    last_limit_up_close = recent_close.where(closed_limit).ffill().iloc[-1]
    entry_mask = (limit_ok & (close_df.iloc[-1] < last_limit_up_close)).fillna(False)
    return list(entry_mask[entry_mask].index)


# ======================= 机器学习打分（LightGBM 优先，XGBoost 备用） =======================

def ml_feature_names():
    return [column for column, _, _ in LITE_FACTORS]


def resolve_ml_backend():
    """按 g.ml_backend 选择模型库：'auto' 先试 LightGBM 再试 XGBoost。都不可用时返回 None。"""
    preferred = str(getattr(g, 'ml_backend', 'auto')).lower()
    order = ['lightgbm', 'xgboost'] if preferred == 'auto' else [preferred]
    for name in order:
        if name == 'lightgbm' and lgb is not None:
            return 'lightgbm'
        if name == 'xgboost' and xgb is not None:
            return 'xgboost'
    return None


def build_ml_features(df):
    """12个代表因子做截面标准化，外加行业名称（只有 LightGBM 把它当类别特征用）。

    行业中性化强度用固定值（g.ml_neutral_strength），不随市场强度变化，
    保证历史训练截面和当期预测截面的特征口径一致。
    """
    ind = df['industry']
    strengths = getattr(g, 'ml_neutral_strength', {'trend': 0.5, 'defensive': 0.7})
    features = {}
    for column, mode, _ in LITE_FACTORS:
        if mode == 'all':
            features[column] = zscore(df[column])
        else:
            features[column] = blended_zscore(ind, df[column], float(strengths[mode]))
    frame = pd.DataFrame(features, index=df.index).fillna(0.0)
    frame['industry'] = ind.reindex(frame.index).fillna('其他').astype(str)
    return frame


def encode_industry(industry_series):
    """行业名称 -> 固定整数编码。编码表存在 g 里，保证不同截面、不同调仓日之间一致。"""
    if not hasattr(g, 'ml_industry_codes'):
        g.ml_industry_codes = {}
    codes = g.ml_industry_codes
    for name in pd.unique(industry_series):
        if name not in codes:
            codes[name] = len(codes)
    return industry_series.map(codes).astype(int)


def calc_forward_return(stocks, start_date, end_date):
    """start_date 收盘到 end_date 收盘的区间收益。"""
    price = get_price(
        stocks,
        start_date=start_date,
        end_date=end_date,
        frequency='daily',
        fields=['close'],
        panel=False
    )
    close_df = price_to_field_matrix(price, stocks, 'close')
    if close_df is None or close_df.empty or len(close_df) < 2:
        return None
    close_df = close_df.sort_index()
    first = close_df.iloc[0].replace(0, np.nan)   # 起点当天没有收盘价的股票不进样本
    last = close_df.ffill().iloc[-1]
    return (last / first - 1.0).replace([np.inf, -np.inf], np.nan).dropna()


def build_training_sections(decision_date):
    """生成历史训练截面：每个截面 = (日期, 特征表, 标签)。

    标签 = 截面日之后 label_days 个交易日的收益在截面内的百分位排名(减0.5)。
    每个截面的标签区间都在 decision_date 当日或之前结束，不使用未来数据。
    截面间隔不小于标签区间，相邻截面的标签区间互不重叠。
    已经算过的截面缓存在 g.ml_section_cache 里：调仓间隔和截面间隔相同时，
    每次调仓只需要新算一个截面。
    """
    label_days = int(g.ml_label_days)
    step = max(int(g.ml_section_step), label_days)
    section_num = int(g.ml_train_sections)
    days = list(get_trade_days(
        end_date=decision_date, count=label_days + step * (section_num - 1) + 1
    ))
    if not hasattr(g, 'ml_section_cache'):
        g.ml_section_cache = {}
    cache = g.ml_section_cache
    sections = []
    needed_keys = set()
    new_count = 0
    for k in range(section_num):
        position = len(days) - 1 - label_days - k * step
        if position < 0:
            break
        section_date = days[position]
        label_end_date = days[position + label_days]
        key = str(pd.Timestamp(section_date).date())
        needed_keys.add(key)
        if key in cache:
            features, label = cache[key]
            sections.append((pd.Timestamp(section_date), features, label))
            continue
        try:
            stocks = get_stock_universe(section_date)
            if len(stocks) < int(g.min_sector_universe_size):
                continue
            raw = build_raw_factor_frame(stocks, section_date)
            if raw is None:
                continue
            features = build_ml_features(raw[0])
            forward_return = calc_forward_return(list(features.index), section_date, label_end_date)
        except Exception as exc:
            log.info('[ML] 截面%s生成失败，跳过：%s' % (key, str(exc)))
            continue
        if forward_return is None:
            continue
        common = features.index.intersection(forward_return.index)
        if len(common) < 30:
            continue
        features = features.loc[common]
        label = forward_return.loc[common].rank(pct=True) - 0.5
        cache[key] = (features, label)
        new_count += 1
        sections.append((pd.Timestamp(section_date), features, label))
    for key in list(cache.keys()):
        if key not in needed_keys:
            del cache[key]
    sections.sort(key=lambda item: item[0])
    log.info('[ML] 历史截面=%d 其中新计算=%d 来自缓存=%d' % (
        len(sections), new_count, len(sections) - new_count
    ))
    return sections


def to_model_matrix(backend, features):
    """LightGBM：12个因子 + 行业编码(类别特征)；XGBoost：只用12个因子。"""
    values = features[ml_feature_names()].values.astype(float)
    if backend == 'lightgbm' and bool(getattr(g, 'ml_use_industry_feature', True)):
        industry_codes = encode_industry(features['industry']).values.astype(float).reshape(-1, 1)
        return np.hstack([values, industry_codes]), ml_feature_names() + ['industry_code']
    return values, ml_feature_names()


def fit_ml_model(backend, sections):
    features = pd.concat([item[1] for item in sections], axis=0)
    labels = pd.concat([item[2] for item in sections], axis=0)
    matrix, names = to_model_matrix(backend, features)
    rounds = int(g.ml_num_rounds)
    if backend == 'lightgbm':
        use_industry = bool(getattr(g, 'ml_use_industry_feature', True))
        dataset = lgb.Dataset(
            matrix, label=labels.values, feature_name=names,
            categorical_feature=[len(names) - 1] if use_industry else [], free_raw_data=False
        )
        booster = lgb.train(dict(g.lgb_params), dataset, num_boost_round=rounds)
    else:
        dataset = xgb.DMatrix(matrix, label=labels.values, feature_names=names)
        booster = xgb.train(dict(g.xgb_params), dataset, num_boost_round=rounds)
    return booster, len(features)


def predict_ml(backend, booster, features):
    matrix, names = to_model_matrix(backend, features)
    if backend == 'lightgbm':
        values = booster.predict(matrix)
    else:
        values = booster.predict(xgb.DMatrix(matrix, feature_names=names))
    return pd.Series(np.asarray(values, dtype=float), index=features.index)


def get_ml_importance(backend, booster):
    if backend == 'lightgbm':
        return dict(zip(booster.feature_name(), booster.feature_importance(importance_type='gain')))
    return booster.get_score(importance_type='gain')


def apply_ml_score(df, decision_date):
    """用模型预测的未来收益排名改写 final_score；任何一步不满足条件都退回线性打分。

    1. 生成历史截面，按时间切成“较早的训练段 + 最近的验证段”。
    2. 只用训练段拟合，在验证段上算 RankIC（样本外检验）。
    3. 验证 RankIC 低于 g.ml_min_valid_ic 时不采用模型，沿用线性打分。
    4. 通过检验后用全部截面重新拟合，预测当期截面。
    5. final_score = 模型预测排名 与 线性打分排名 按 g.ml_blend_weight 混合。
    """
    result = df.copy()
    result['final_score_linear'] = result['final_score']
    backend = resolve_ml_backend()
    if backend is None:
        log.info('[ML] 当前环境无法 import lightgbm / xgboost，沿用线性打分')
        return result

    try:
        sections = build_training_sections(decision_date)
        min_sections = int(g.ml_min_sections)
        if len(sections) < min_sections:
            log.info('[ML] 可用历史截面只有%d个(需要%d个)，沿用线性打分' % (len(sections), min_sections))
            return result

        valid_num = max(1, int(round(len(sections) * float(g.ml_valid_ratio))))
        train_sections = sections[:-valid_num]
        valid_sections = sections[-valid_num:]
        booster, train_rows = fit_ml_model(backend, train_sections)
        valid_ics = []
        for _, features, label in valid_sections:
            prediction = predict_ml(backend, booster, features)
            valid_ics.append(safe_corr(prediction.rank(), label.rank()))
        valid_ic = float(np.nanmean(valid_ics)) if len(valid_ics) > 0 else np.nan
        log.info('[ML] 模型=%s 截面=%d(训练%d/验证%d) 训练样本=%d 验证RankIC=%.4f 各期=%s' % (
            backend, len(sections), len(train_sections), len(valid_sections), train_rows, valid_ic,
            '/'.join('%.3f' % safe_float(value) for value in valid_ics)
        ))
        use_gate = bool(getattr(g, 'ml_use_valid_gate', True))
        if use_gate and (pd.isna(valid_ic) or valid_ic < float(g.ml_min_valid_ic)):
            log.info('[ML] 验证RankIC未达到%.3f，本期不采用模型，沿用线性打分' % float(g.ml_min_valid_ic))
            return result

        booster, total_rows = fit_ml_model(backend, sections)
        prediction = predict_ml(backend, booster, build_ml_features(result))
    except Exception as exc:
        log.info('[ML] 训练或预测失败，沿用线性打分：%s' % str(exc))
        return result

    blend = clip_value(g.ml_blend_weight, 0.0, 1.0)
    result['ml_score'] = prediction
    result['final_score'] = (
        blend * prediction.rank(pct=True) +
        (1.0 - blend) * result['final_score_linear'].rank(pct=True)
    )
    try:
        importance = get_ml_importance(backend, booster)
        total_gain = float(sum(importance.values())) or 1.0
        ranked = sorted(importance.items(), key=lambda item: -item[1])
        log.info('[ML] 特征重要性(gain占比)：%s' % ', '.join(
            '%s=%.1f%%' % (name, value / total_gain * 100.0) for name, value in ranked
        ))
    except Exception:
        pass
    log.info('[ML] 已采用模型 全量样本=%d 混合权重=%.2f 模型排名与线性排名秩相关=%.3f' % (
        total_rows, blend,
        safe_float(safe_corr(prediction.rank(), result['final_score_linear'].rank()))
    ))
    return result


def safe_corr(x, y):
    if len(x) < 2 or len(y) < 2:
        return np.nan
    if pd.isna(x.std()) or pd.isna(y.std()) or x.std() <= 0 or y.std() <= 0:
        return np.nan
    return x.corr(y)


# ======================= 换股缓冲区与权重容忍带 =======================

def build_buffered_target_stocks(factor_df, survivors, valid_entry_stocks, target_num):
    """带缓冲区的换股：新股票排进前 buy_rank 才买，旧持仓跌出前 sell_rank 才卖。

    排名范围 = 有效期内有涨停回落信号的股票 + 现有持仓，按 final_score 从高到低。
    返回 (目标持仓, 新买入, 因排名掉队卖出)。
    """
    survivor_set = set(survivors)
    # 因子缺失的旧持仓（停牌、开盘涨跌停等）先保留，避免取不到数据时误卖。
    unscored = [stock for stock in survivors if stock not in factor_df.index]
    if bool(getattr(g, 'use_entry_signal_pool', True)):
        pool = factor_df.index[factor_df.index.isin(set(valid_entry_stocks) | survivor_set)]
    else:
        pool = factor_df.index
    ranked = list(factor_df.loc[pool].sort_values('final_score', ascending=False).index)
    rank_of = dict((stock, position + 1) for position, stock in enumerate(ranked))

    buy_rank = int(g.buffer_buy_rank)
    sell_rank = max(buy_rank, int(g.buffer_sell_rank))
    kept = [stock for stock in ranked if stock in survivor_set and rank_of[stock] <= sell_rank]
    dropped = [stock for stock in ranked if stock in survivor_set and rank_of[stock] > sell_rank]
    target = (unscored + kept)[:target_num]
    slots = max(0, target_num - len(target))
    new_buys = [
        stock for stock in ranked
        if stock not in survivor_set and rank_of[stock] <= buy_rank
    ][:slots]
    log.info('[换股] 排名池=%d(信号股+持仓) 保留=%d 因子缺失保留=%d 掉出前%d卖出=%d 新买入=%d' % (
        len(ranked), len(kept), len(unscored), sell_rank, len(dropped), len(new_buys)
    ))
    return target + new_buys, new_buys, dropped


def find_positions_within_tolerance(context, target_weights):
    """已有持仓的当前权重与目标权重相差不到容忍带时，本次不调整这只标的。"""
    tolerance = float(getattr(g, 'rebalance_tolerance', 0.0))
    total_value = float(context.portfolio.total_value)
    frozen = set()
    if tolerance <= 0 or total_value <= 0:
        return frozen
    for stock, weight in target_weights.items():
        position = context.portfolio.positions.get(stock)
        if position is None or position.total_amount <= 0:
            continue
        value = safe_float(getattr(position, 'value', np.nan))
        if not np.isfinite(value) or value <= 0:
            continue
        if abs(value / total_value - float(weight)) < tolerance:
            frozen.add(stock)
    return frozen


def get_factor_df(context, decision_date, current_data=None):
    stocks = set(get_stock_universe(decision_date))
    stocks.update(get_held_equity_stocks(context))
    stocks = sorted(stocks)
    stocks = filter_stock_list(context, stocks, current_data)
    if len(stocks) == 0:
        return None, 'neutral'

    raw = build_raw_factor_frame(stocks, decision_date)
    if raw is None:
        return None, 'neutral'
    df, index_close, close_df, money_df = raw
    df = add_multifactor_scores(df, index_close, close_df, money_df)
    df = df.dropna(subset=['final_score'])
    if bool(getattr(g, 'use_ml_score', True)):
        df = apply_ml_score(df, decision_date)
    return df, getattr(g, 'last_regime', 'neutral')


def build_raw_factor_frame(stocks, as_of_date):
    """只用 as_of_date 当日及之前的数据，算出12个代表因子的原始值。

    当期打分和 XGBoost 的历史训练截面共用这一个函数，保证两边口径一致。
    """
    fundamentals = get_fundamental_factors(stocks, as_of_date)
    if fundamentals is None or fundamentals.empty:
        return None

    valid_stocks = list(fundamentals.index)
    close_df, money_df, index_close, high_limit_df = get_price_matrix(valid_stocks, as_of_date)
    if close_df is None or money_df is None or index_close is None:
        return None

    valid_stocks = sorted(list(
        set(valid_stocks)
        .intersection(set(close_df.columns))
        .intersection(set(money_df.columns))
    ))
    if len(valid_stocks) == 0:
        return None

    close_df = close_df[valid_stocks]
    money_df = money_df[valid_stocks]
    if high_limit_df is not None and not high_limit_df.empty:
        high_limit_df = high_limit_df.reindex(index=close_df.index, columns=valid_stocks)

    df = fundamentals.loc[valid_stocks].copy()
    df = add_price_features(df, close_df, money_df, index_close)
    df = add_limit_up_count(df, close_df, high_limit_df)
    df = add_industry_features(df, close_df, money_df, as_of_date)
    df = df.replace([np.inf, -np.inf], np.nan)
    df = fill_missing_values(df)
    return df, index_close, close_df, money_df


def get_fundamental_factors(stocks, decision_date):
    q = query(
        valuation.code,
        indicator.roe,
        balance.total_liability,
        balance.total_assets
    ).filter(
        valuation.code.in_(stocks)
    )

    df = get_fundamentals(q, date=decision_date)
    if df is None or df.empty:
        return None

    df = df.set_index('code')
    for col in ['roe', 'total_liability', 'total_assets']:
        if col not in df.columns:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df['debt_to_asset'] = df['total_liability'] / df['total_assets'].replace(0, np.nan) * 100.0
    return df


def get_price_matrix(stocks, decision_date):
    price = get_price(
        stocks,
        count=g.price_count,
        end_date=decision_date,
        frequency='daily',
        fields=['close', 'money', 'high_limit'],
        panel=False
    )
    close_df = normalize_price_matrix(price_to_field_matrix(price, stocks, 'close'))
    money_df = normalize_price_matrix(price_to_field_matrix(price, stocks, 'money'))
    high_limit_df = normalize_price_matrix(price_to_field_matrix(price, stocks, 'high_limit'))

    index_price = get_price(
        g.index,
        count=g.price_count,
        end_date=decision_date,
        frequency='daily',
        fields=['close'],
        panel=False
    )
    index_close = price_to_close_series(index_price)
    return close_df, money_df, index_close, high_limit_df


def add_price_features(df, close_df, money_df, index_close):
    common_dates = close_df.index.intersection(index_close.index)
    close_df = close_df.loc[common_dates].sort_index()
    money_df = money_df.loc[money_df.index.intersection(common_dates)].sort_index()
    index_close = index_close.loc[common_dates].sort_index()

    stocks = sorted(list(
        set(df.index)
        .intersection(set(close_df.columns))
        .intersection(set(money_df.columns))
    ))
    df = df.loc[stocks].copy()
    close_df = close_df[stocks]
    money_df = money_df[stocks]

    close_now = close_df.iloc[-1]
    ret_df = close_df.pct_change()
    index_ret = index_close.pct_change()

    # 短期动量：20日收益减指数20日收益
    df['relative_strength_20'] = calc_return(close_df, 20).reindex(df.index) - calc_index_return(index_close, 20)
    # 中期趋势：MA60 的10日斜率；120日 Alpha
    df['ma60_slope'] = calc_ma_slope(close_df, 60, 10).reindex(df.index)
    df['alpha_120'] = calc_alpha(ret_df, index_ret, 120).reindex(df.index)
    # 风险：60日回撤、120日贝塔，取负号后越大越稳健
    df['low_drawdown_60'] = -(1.0 - close_now / close_df.tail(60).max().replace(0, np.nan)).reindex(df.index)
    df['low_beta_120'] = -calc_beta(ret_df, index_ret, 120).reindex(df.index)
    # 流动性：20日日均成交额（取对数）
    df['log_avg_money_20'] = np.log1p(money_df.tail(20).mean().reindex(df.index))
    # 量能：5日/20日成交额之比越接近1.15越好（沿用原版设定）
    money_ratio_5_20 = (money_df.tail(5).mean() / money_df.tail(20).mean().replace(0, np.nan)).reindex(df.index)
    df['money_quality'] = -1.0 * (money_ratio_5_20 - 1.15).abs()
    # 负债：资产负债率取负号
    df['low_debt'] = -df['debt_to_asset']
    return df


# 12个代表因子：(因子列, 标准化方式, {核: 归并到该因子的核内权重之和})
# 标准化方式：'trend'/'defensive' = 行业内与全市场混合(强度随市场变化)，'all' = 全市场。
# 权重来自原版四个核：把同一方向的因子权重合并到代表因子上；
# 防守核的权重已按原版除以权重总和0.82。
# 原版中 beta_quality / vol_quality / natr_quality 三个“越接近某固定值越好”的因子
# 没有合适的代表，直接去掉（在趋势核里合计占0.14）。
LITE_FACTORS = [
    ('relative_strength_20', 'trend',     {'trend': 0.34, 'leader': 0.36}),
    ('ma60_slope',           'trend',     {'trend': 0.23, 'leader': 0.12}),
    ('alpha_120',            'all',       {'trend': 0.10, 'leader': 0.12, 'defensive': 0.10 / 0.82}),
    ('money_quality',        'trend',     {'trend': 0.10}),
    ('log_avg_money_20',     'all',       {'trend': 0.04, 'leader': 0.32}),
    ('limit_up_count_10d',   'all',       {'leader': 0.08}),
    ('roe',                  'defensive', {'trend': 0.05, 'defensive': 0.38 / 0.82}),
    ('low_debt',             'defensive', {'defensive': 0.10 / 0.82}),
    ('low_drawdown_60',      'defensive', {'defensive': 0.14 / 0.82}),
    ('low_beta_120',         'defensive', {'defensive': 0.10 / 0.82}),
    ('industry_flow_20d',    'all',       {'industry': 0.655}),
    ('industry_return_20d',  'all',       {'industry': 0.345}),
]


def add_multifactor_scores(df, index_close, close_df, money_df):
    result = df.copy()
    breadth = calc_market_breadth(close_df)
    forecast_score, _ = calc_market_forecast_score(index_close, close_df, money_df)
    market_strength = calc_market_strength(index_close, breadth, forecast_score)
    apply_market_strength_params(market_strength)
    regime = judge_market_regime(index_close, breadth, forecast_score)
    g.last_regime = regime

    # 四个核的权重仍按市场强度和档位动态变化，再分摊到12个代表因子上。
    core_weights = build_dynamic_factor_weights(market_strength, regime)
    ind = result['industry']
    neutral_strength = {
        'trend': float(g.trend_industry_neutral_strength),
        'defensive': float(g.defensive_industry_neutral_strength),
    }
    final_score = pd.Series(0.0, index=result.index)
    factor_weights = {}
    for column, mode, core_shares in LITE_FACTORS:
        weight = sum(core_weights[core] * share for core, share in core_shares.items())
        if mode == 'all':
            standardized = zscore(result[column])
        else:
            standardized = blended_zscore(ind, result[column], neutral_strength[mode])
        final_score = final_score + weight * standardized
        factor_weights[column] = round(float(weight), 3)
    result['final_score'] = final_score.fillna(0.0)

    log.info('regime=%s strength=%.3f breadth=%.3f forecast=%.3f' % (
        regime, safe_float(market_strength), safe_float(breadth), safe_float(forecast_score)
    ))
    log.info('[因子权重] %s' % ', '.join(
        '%s=%.3f' % (name, weight)
        for name, weight in sorted(factor_weights.items(), key=lambda item: -item[1])
    ))
    return result


def add_limit_up_count(df, close_df, high_limit_df):
    """近10个交易日收盘封板次数，只作为龙头核的一个打分项。"""
    result = df.copy()
    result['limit_up_count_10d'] = 0
    if (close_df is None or close_df.empty or
            high_limit_df is None or high_limit_df.empty):
        return result

    lookback = int(g.limit_up_lookback_trading_days)
    tolerance = float(g.limit_up_tolerance)
    common_dates = sorted(close_df.index.intersection(high_limit_df.index))
    common_stocks = list(
        set(result.index)
        .intersection(close_df.columns)
        .intersection(high_limit_df.columns)
    )
    if len(common_dates) == 0 or len(common_stocks) == 0:
        return result

    recent_close = close_df.loc[common_dates, common_stocks].sort_index().tail(lookback)
    recent_limit = high_limit_df.loc[common_dates, common_stocks].sort_index().tail(lookback)
    closed_limit = (
        recent_close >= recent_limit * (1.0 - tolerance)
    ) & recent_close.notna() & recent_limit.notna() & (recent_limit > 0)
    result['limit_up_count_10d'] = closed_limit.sum(axis=0).reindex(result.index).fillna(0).astype(int)
    return result


def should_rebalance_today(context):
    last_date = getattr(g, 'last_rebalance_date', None)
    if last_date is None:
        return True
    trade_days = get_trade_days(
        start_date=last_date,
        end_date=pd.Timestamp(context.current_dt.date())
    )
    if trade_days is None:
        return False
    # trade_days 含上次调仓日和今天两端，所以用“大于”才是整整隔了N个交易日。
    return len(trade_days) > int(getattr(g, 'rebalance_interval_trading_days', 20))


def update_entry_signal_cache(decision_date, new_signal_stocks):
    """保存最近若干交易日的技术入场信号，供两周调仓日使用。"""
    current_date = pd.Timestamp(decision_date).normalize()
    cache = dict(getattr(g, 'pending_entry_signals', {}))
    for stock in new_signal_stocks:
        cache[stock] = current_date

    if not cache:
        g.pending_entry_signals = {}
        return []

    valid_days = int(getattr(g, 'entry_signal_valid_trading_days', 5))
    signal_dates = [pd.Timestamp(value).normalize() for value in cache.values()]
    try:
        trade_days = get_trade_days(
            start_date=min(signal_dates), end_date=current_date
        )
        normalized_trade_days = pd.DatetimeIndex(
            pd.to_datetime(trade_days)
        ).normalize()
    except Exception:
        normalized_trade_days = pd.DatetimeIndex([])

    active = {}
    for stock, signal_date in cache.items():
        signal_date = pd.Timestamp(signal_date).normalize()
        if len(normalized_trade_days) > 0:
            age = int((normalized_trade_days >= signal_date).sum())
            is_valid = 1 <= age <= valid_days
        else:
            # 数据接口异常时使用保守的自然日兜底，周末不会让信号立刻失效。
            calendar_age = int((current_date - signal_date).days)
            is_valid = 0 <= calendar_age <= valid_days * 2
        if is_valid:
            active[stock] = signal_date

    g.pending_entry_signals = active
    if new_signal_stocks or active:
        log.info('[信号缓存] 当日新增=%d 有效期=%d个交易日 当前有效=%d' % (
            len(new_signal_stocks), valid_days, len(active)
        ))
    return sorted(active.keys())


def build_equal_event_weights(target_stocks, equity_total_weight):
    if not target_stocks:
        return {}
    target_num = len(target_stocks)
    equal_weight = float(equity_total_weight) / float(target_num)
    equal_weight = min(equal_weight, float(getattr(g, 'max_single_stock_weight', equal_weight)))
    return dict((stock, equal_weight) for stock in target_stocks)


def build_hedge_sleeve_weights(sleeve_weights, current_data=None):
    """
    构建独立黄金ETF对冲仓位的目标权重。
    黄金板块股票已经作为普通股票候选，不在这里重复分配仓位。
    黄金ETF不可交易（停牌、涨跌停、无报价等）时直接跳过，对应权重计入现金。

    current_data: 由调用方（rebalance）传入同一份行情快照，避免重复
    调用get_current_data()；未传入时兜底自己取一次，保持向后兼容。
    """
    weights = {}
    if current_data is None:
        current_data = get_current_data()
    gold_code = getattr(g, 'gold_hedge_etf', None)
    if gold_code:
        try:
            cd = current_data[gold_code]
            if is_tradable_current_data(cd):
                weights[gold_code] = float(sleeve_weights.get('gold', 0.0))
            else:
                log.info('黄金ETF%s当前不可交易，本次跳过，权重计入现金' % gold_code)
        except Exception:
            log.info('黄金ETF%s取不到行情，本次跳过' % gold_code)

    return weights


def get_ai_concept_codes(decision_date):
    """按决策日从聚宽概念表中找AI相关概念，避免引入未来概念。"""
    normalized_date = pd.Timestamp(decision_date).normalize()
    cache_key = str(normalized_date.date())
    cached = getattr(g, 'ai_concept_codes_by_date', {}).get(cache_key)
    if cached is not None:
        return list(cached)

    result = []
    try:
        concepts = getattr(g, 'ai_concepts_catalog', None)
        if concepts is None:
            concepts = get_concepts()
            g.ai_concepts_catalog = concepts
        if concepts is not None and not concepts.empty:
            keywords = [
                str(value).lower()
                for value in getattr(g, 'ai_concept_keywords', [])
            ]
            for concept_code, row in concepts.iterrows():
                concept_name = str(row.get('name', ''))
                lower_name = concept_name.lower()
                start_date = row.get('start_date', None)
                if start_date is not None and pd.notna(start_date):
                    try:
                        if pd.Timestamp(start_date).normalize() > normalized_date:
                            continue
                    except Exception:
                        pass
                matched = False
                for keyword in keywords:
                    if keyword in lower_name:
                        matched = True
                        break
                if matched:
                    result.append(str(row.get('code', concept_code)))
    except Exception as exc:
        log.info('[行业池] 读取AI概念目录失败，仅使用申万计算机行业：%s' % str(exc))

    result = sorted(set(result))
    if not hasattr(g, 'ai_concept_codes_by_date'):
        g.ai_concept_codes_by_date = {}
    g.ai_concept_codes_by_date[cache_key] = result
    log.info('[行业池] 日期=%s 可用AI相关概念代码=%d' % (
        cache_key, len(result)
    ))
    return list(result)


def get_stock_universe(decision_date):
    """中证全指与指定行业/AI概念的交集；universe_mode='csi800' 时为沪深300+中证500成分股。"""
    cache_date = getattr(g, 'sector_universe_cache_date', None)
    normalized_date = pd.Timestamp(decision_date).normalize()
    if cache_date is not None and pd.Timestamp(cache_date).normalize() == normalized_date:
        return list(getattr(g, 'sector_universe_cache_stocks', []))

    if getattr(g, 'universe_mode', 'sector') == 'csi800':
        try:
            stocks = set(get_index_stocks('000300.XSHG', date=decision_date))
            stocks.update(get_index_stocks('000905.XSHG', date=decision_date))
        except Exception as exc:
            log.info('[股票池] 读取沪深300/中证500失败：%s' % str(exc))
            return []
        result = sorted(stocks)
        g.sector_universe_cache_date = normalized_date
        g.sector_universe_cache_stocks = result
        return result

    try:
        all_share_stocks = set(get_index_stocks(g.index, date=decision_date))
    except Exception as exc:
        log.info('[行业池] 读取中证全指失败：%s' % str(exc))
        return []
    if not all_share_stocks:
        return []

    focused_stocks = set()
    segment_counts = {}
    for label, industry_code in getattr(g, 'target_industry_codes', {}).items():
        try:
            members = set(get_industry_stocks(industry_code, date=decision_date))
        except Exception as exc:
            members = set()
            log.info('[行业池] %s(%s)读取失败：%s' % (
                label, industry_code, str(exc)
            ))
        members &= all_share_stocks
        focused_stocks.update(members)
        segment_counts[label] = len(members)

    ai_concept_stocks = set()
    for concept_code in get_ai_concept_codes(decision_date):
        try:
            members = set(get_concept_stocks(concept_code, date=decision_date))
        except Exception:
            continue
        ai_concept_stocks.update(members.intersection(all_share_stocks))
    focused_stocks.update(ai_concept_stocks)
    segment_counts['AI概念'] = len(ai_concept_stocks)

    result = sorted(focused_stocks.intersection(all_share_stocks))
    g.sector_universe_cache_date = normalized_date
    g.sector_universe_cache_stocks = result
    count_detail = ', '.join([
        '%s=%d' % (label, count) for label, count in segment_counts.items()
    ])
    log.info('[行业池] 日期=%s 中证全指=%d %s 去重后=%d' % (
        str(normalized_date.date()), len(all_share_stocks), count_detail, len(result)
    ))
    return result


def filter_stock_list(context, stocks, current_data=None):
    if current_data is None:
        current_data = get_current_data()
    result = []
    for stock in stocks:
        try:
            data = current_data[stock]
        except Exception:
            continue

        if data.paused or data.is_st:
            continue
        if 'ST' in data.name or '*' in data.name or '退' in data.name:
            continue
        if data.last_price is None or data.last_price <= 0:
            continue
        if data.last_price >= data.high_limit or data.last_price <= data.low_limit:
            continue
        result.append(stock)
    return result


def add_industry_features(df, close_df, money_df, decision_date):
    stocks = list(df.index)
    industry_map = {}
    try:
        raw = get_industry(stocks, date=decision_date)
        for stock in stocks:
            value = raw.get(stock, {})
            name = '其他'
            if isinstance(value, dict):
                if 'sw_l1' in value and value['sw_l1'] is not None:
                    name = value['sw_l1'].get('industry_name', '其他')
                elif 'jq_l1' in value and value['jq_l1'] is not None:
                    name = value['jq_l1'].get('industry_name', '其他')
            industry_map[stock] = name
    except Exception:
        for stock in stocks:
            industry_map[stock] = '其他'

    df['industry'] = pd.Series(industry_map)

    rows = []
    if len(close_df) >= 61 and len(money_df) >= 60:
        ret20 = close_df.iloc[-1] / close_df.iloc[-21].replace(0, np.nan) - 1.0
        ret60 = close_df.iloc[-1] / close_df.iloc[-61].replace(0, np.nan) - 1.0
        flow20 = money_df.tail(20).mean() / money_df.tail(60).mean().replace(0, np.nan)
        flow60 = money_df.tail(60).mean()
        for stock in stocks:
            rows.append({
                'stock': stock,
                'industry': industry_map.get(stock, '其他'),
                'ret20': ret20.get(stock, np.nan),
                'ret60': ret60.get(stock, np.nan),
                'flow20': flow20.get(stock, np.nan),
                'flow60': flow60.get(stock, np.nan),
            })
    stat = pd.DataFrame(rows)
    if stat.empty:
        df['industry_flow_20d'] = 0.0
        df['industry_flow_60d'] = 0.0
        df['industry_return_20d'] = 0.0
        df['industry_return_60d'] = 0.0
        df['industry_preheat_confirm_score'] = 0.0
        df['industry_relative_strength_20'] = df['relative_strength_20']
        return df

    agg = stat.groupby('industry').agg({
        'ret20': 'mean',
        'ret60': 'mean',
        'flow20': 'mean',
        'flow60': 'mean',
    }).rename(columns={
        'ret20': 'industry_return_20d',
        'ret60': 'industry_return_60d',
        'flow20': 'industry_flow_20d',
        'flow60': 'industry_flow_60d',
    })
    agg['industry_preheat_confirm_score'] = (
        0.4 * zscore(agg['industry_flow_20d']) +
        0.3 * zscore(agg['industry_flow_60d']) +
        0.2 * zscore(agg['industry_return_20d']) +
        0.1 * zscore(agg['industry_return_60d'])
    )
    for col in agg.columns:
        df[col] = df['industry'].map(agg[col]).fillna(0.0)

    industry_ret20 = stat.groupby('industry')['ret20'].mean()
    df['industry_relative_strength_20'] = df['relative_strength_20'] - df['industry'].map(industry_ret20).fillna(0.0)
    return df


def calc_market_breadth(close_df):
    if close_df is None or close_df.empty or len(close_df) < 20:
        return 0.5
    close_now = close_df.iloc[-1]
    ma20 = close_df.tail(20).mean()
    breadth = ((close_now >= ma20) & ma20.notna()).mean()
    return float(breadth)


def calc_market_forecast_score(index_close, close_df, money_df):
    """
    只使用截至决策日的日线历史数据，做一个偏前瞻的市场方向分。
    分数越高表示短线走强、宽度扩散、成交扩张同时出现；越低表示反向。
    """
    detail = {
        'score': 0.0,
        'idx_ret5': 0.0,
        'idx_ret10': 0.0,
        'ma5_ma20': 0.0,
        'breadth': 0.5,
        'breadth_chg5': 0.0,
        'money_ratio5_20': 0.0,
    }
    if index_close is None or close_df is None or close_df.empty or len(index_close) < 25:
        return 0.0, detail

    idx = pd.to_numeric(index_close, errors='coerce').dropna()
    if len(idx) < 25:
        return 0.0, detail

    idx_ret5 = float(idx.iloc[-1] / idx.iloc[-6] - 1.0) if len(idx) > 5 and idx.iloc[-6] != 0 else 0.0
    idx_ret10 = float(idx.iloc[-1] / idx.iloc[-11] - 1.0) if len(idx) > 10 and idx.iloc[-11] != 0 else 0.0
    ma5 = float(idx.tail(5).mean())
    ma20 = float(idx.tail(20).mean())
    ma5_ma20 = ma5 / ma20 - 1.0 if ma20 != 0 and not pd.isna(ma20) else 0.0

    ma20_df = close_df.rolling(20).mean()
    breadth_series = ((close_df >= ma20_df) & ma20_df.notna()).mean(axis=1)
    breadth_series = breadth_series.dropna()
    breadth_now = float(breadth_series.iloc[-1]) if len(breadth_series) > 0 else 0.5
    if len(breadth_series) > 5:
        breadth_chg5 = float(breadth_now - breadth_series.iloc[-6])
    else:
        breadth_chg5 = 0.0

    money_ratio5_20 = 0.0
    if money_df is not None and not money_df.empty and len(money_df) >= 20:
        total_money = money_df.sum(axis=1).replace([np.inf, -np.inf], np.nan).dropna()
        if len(total_money) >= 20:
            money20 = float(total_money.tail(20).mean())
            money5 = float(total_money.tail(5).mean())
            if money20 > 0 and not pd.isna(money20):
                money_ratio5_20 = money5 / money20 - 1.0

    score = (
        0.25 * clip_value(idx_ret5 / 0.03, -1.0, 1.0) +
        0.20 * clip_value(idx_ret10 / 0.05, -1.0, 1.0) +
        0.20 * clip_value(ma5_ma20 / 0.025, -1.0, 1.0) +
        0.15 * clip_value((breadth_now - 0.50) / 0.25, -1.0, 1.0) +
        0.15 * clip_value(breadth_chg5 / 0.12, -1.0, 1.0) +
        0.05 * clip_value(money_ratio5_20 / 0.25, -1.0, 1.0)
    )

    detail.update({
        'score': float(score),
        'idx_ret5': idx_ret5,
        'idx_ret10': idx_ret10,
        'ma5_ma20': ma5_ma20,
        'breadth': breadth_now,
        'breadth_chg5': breadth_chg5,
        'money_ratio5_20': money_ratio5_20,
    })
    return float(score), detail


def calc_market_strength(index_close, breadth, forecast_score):
    """
    返回0~1的连续市场强度。0偏弱市，1偏强市。
    同时参考20/60/120日趋势，避免仅凭一轮短线反弹就大幅提高趋势因子。
    这里只用决策日前已经收盘的日线、宽度和成交信息，不引入未来数据。
    """
    if index_close is None or len(index_close) < 120:
        return 0.50

    idx = pd.to_numeric(index_close, errors='coerce').dropna()
    if len(idx) < 120:
        return 0.50

    last_close = float(idx.iloc[-1])
    ma20 = float(idx.tail(20).mean())
    ma60 = float(idx.tail(60).mean())
    ma120 = float(idx.tail(120).mean())
    if (ma20 <= 0 or ma60 <= 0 or ma120 <= 0 or
            pd.isna(ma20) or pd.isna(ma60) or pd.isna(ma120)):
        return 0.50

    ma20_position = last_close / ma20 - 1.0
    ma20_ma60 = ma20 / ma60 - 1.0
    ma60_ma120 = ma60 / ma120 - 1.0
    raw = (
        0.20 * clip_value(ma20_position / 0.06, -1.0, 1.0) +
        0.15 * clip_value(ma20_ma60 / 0.08, -1.0, 1.0) +
        0.15 * clip_value(ma60_ma120 / 0.10, -1.0, 1.0) +
        0.30 * clip_value(forecast_score / 0.60, -1.0, 1.0) +
        0.20 * clip_value((breadth - 0.50) / 0.30, -1.0, 1.0)
    )
    return clip_value(0.50 + 0.50 * raw, 0.0, 1.0)


def blend_by_market_strength(weak_value, strong_value, market_strength):
    strength = clip_value(market_strength, 0.0, 1.0)
    return float(weak_value) + (float(strong_value) - float(weak_value)) * strength


def apply_market_strength_params(market_strength):
    strength = clip_value(market_strength, 0.0, 1.0)
    g.last_market_strength = strength
    g.trend_industry_neutral_strength = clip_value(
        blend_by_market_strength(
            getattr(g, 'trend_neutral_weak', 0.75),
            getattr(g, 'trend_neutral_strong', 0.20),
            strength
        ),
        0.0,
        1.0
    )
    g.defensive_industry_neutral_strength = clip_value(
        blend_by_market_strength(
            getattr(g, 'defensive_neutral_weak', 0.90),
            getattr(g, 'defensive_neutral_strong', 0.55),
            strength
        ),
        0.0,
        1.0
    )


def build_dynamic_factor_weights(market_strength, regime):
    """
    同一套因子连续调参，不做策略切换：
    市场越强，trend/leader权重越高；市场越弱，defensive权重越高。
    regime只做小幅风控偏置，避免权重在阈值附近跳变太大。
    """
    strength = clip_value(market_strength, 0.0, 1.0)
    weights = {
        'trend': 0.18 + 0.30 * strength,
        'industry': 0.22,
        'leader': 0.15 + 0.05 * strength,
        'defensive': 0.45 - 0.30 * strength,
    }

    if regime == 'risk_on':
        # 强市以龙头为核心：龙头固定40%，其余在趋势/行业/防守之间分配。
        leader_weight = clip_value(
            getattr(g, 'risk_on_leader_weight', 0.40), 0.36, 0.50
        )
        weights = {
            'trend': 0.30 + 0.05 * strength,
            'industry': 0.15,
            'leader': leader_weight,
            'defensive': 0.85 - leader_weight - (0.30 + 0.05 * strength),
        }
    elif regime == 'risk_off':
        weights['trend'] -= 0.03
        weights['defensive'] += 0.03

    bounds = {
        'trend': (0.10, 0.55),
        'industry': (0.15, 0.30),
        'leader': (0.15, 0.50),
        'defensive': (0.10, 0.55),
    }
    for key, (lower, upper) in bounds.items():
        weights[key] = clip_value(weights[key], lower, upper)

    weight_items = [(key, float(weights[key])) for key in ['trend', 'industry', 'leader', 'defensive']]
    total = float(np.sum([value for _, value in weight_items]))
    if total <= 0:
        return {'trend': 0.30, 'industry': 0.25, 'leader': 0.20, 'defensive': 0.25}
    return {key: value / total for key, value in weight_items}


def judge_market_regime(index_close, breadth, forecast_score=0.0):
    if index_close is None or len(index_close) < 120:
        return 'neutral'

    idx = pd.to_numeric(index_close, errors='coerce').dropna()
    if len(idx) < 120:
        return 'neutral'

    ma5 = idx.tail(5).mean()
    ma20 = idx.tail(20).mean()
    ma60 = idx.tail(60).mean()
    last_close = float(idx.iloc[-1])
    forecast_on = float(getattr(g, 'market_forecast_on_threshold', 0.35))
    forecast_off = float(getattr(g, 'market_forecast_off_threshold', -0.35))
    breadth_weak = float(getattr(g, 'market_breadth_weak_threshold', 0.45))
    confirm_days = max(2, int(getattr(g, 'market_regime_confirm_days', 3)))

    ma60_series = idx.rolling(60).mean()
    weak_series = (
        (idx < ma60_series) &
        (ma60_series < idx.rolling(120).mean())
    ).fillna(False)
    weak_confirmed = bool(weak_series.tail(confirm_days).all())

    weak_market = (
        weak_confirmed and
        breadth <= breadth_weak and
        forecast_score <= 0.0 and
        last_close < float(ma20)
    )

    # 按用户设定：指数收盘价在MA20之上就视为强市，优先响应市场转强。
    if last_close >= float(ma20):
        return 'risk_on'
    if weak_market and forecast_score <= forecast_off:
        return 'risk_off'

    # 弱市保留滞后保护，避免在MA20下方的短暂反弹中立即切回中性。
    previous_regime = getattr(g, 'last_regime', 'neutral')
    if previous_regime == 'risk_off':
        if (last_close < float(ma60) and float(ma5) < float(ma20) and
                breadth <= 0.50 and forecast_score < forecast_on):
            return 'risk_off'

    return 'neutral'


def execute_orders(context, target_weights, current_data=None, frozen=None):
    if current_data is None:
        current_data = get_current_data()
    frozen = frozen or set()   # 容忍带内的持仓：本次既不减仓也不加仓
    target_stocks = set(target_weights.keys())
    total_value = context.portfolio.total_value

    for stock, position in list(context.portfolio.positions.items()):
        if position.total_amount <= 0:
            continue
        if stock in target_stocks:
            continue
        if position.closeable_amount < 100:
            continue
        try:
            cd = current_data[stock]
        except Exception:
            continue
        if cd.paused or cd.last_price is None or cd.last_price <= 0:
            continue
        place_order_target(context, stock, 0, cd)

    target_amount_dict = {}
    for stock, weight in target_weights.items():
        if stock in frozen:
            continue
        try:
            cd = current_data[stock]
        except Exception:
            continue
        if not is_tradable_current_data(cd):
            continue
        target_value = total_value * float(weight)
        last_price = float(cd.last_price)
        target_amount = int(target_value / last_price / 100.0) * 100
        target_amount_dict[stock] = target_amount

        if stock in context.portfolio.positions:
            position = context.portfolio.positions[stock]
            current_amount = int(position.total_amount)
            closeable_amount = int(position.closeable_amount)
        else:
            current_amount = 0
            closeable_amount = 0

        if target_amount >= current_amount:
            continue
        reduce_amount = current_amount - target_amount
        if reduce_amount < 100 or closeable_amount < 100:
            continue
        final_target_amount = target_amount if closeable_amount >= reduce_amount else max(0, current_amount - int(closeable_amount / 100.0) * 100)
        place_order_target(context, stock, final_target_amount, cd)

    for stock, weight in target_weights.items():
        if stock in frozen:
            continue
        try:
            cd = current_data[stock]
        except Exception:
            continue
        if not is_tradable_current_data(cd):
            continue

        target_amount = target_amount_dict.get(stock, 0)
        if target_amount < 100:
            continue
        current_amount = int(context.portfolio.positions[stock].total_amount) if stock in context.portfolio.positions else 0
        delta_amount = target_amount - current_amount
        if delta_amount < 100:
            continue
        place_order_target(context, stock, target_amount, cd)


def is_tradable_current_data(cd):
    if cd.paused or cd.is_st:
        return False
    if cd.last_price is None or cd.last_price <= 0:
        return False
    if cd.last_price >= cd.high_limit or cd.last_price <= cd.low_limit:
        return False
    return True


def place_order_target(context, stock, target_amount, cd):
    try:
        order_target(stock, int(target_amount))
    except Exception as e:
        log.info('下单失败 %s target=%s: %s' % (stock, str(target_amount), str(e)))


def calc_return(close_df, window):
    if len(close_df) <= window:
        return pd.Series(np.nan, index=close_df.columns)
    return close_df.iloc[-1] / close_df.iloc[-window - 1].replace(0, np.nan) - 1.0


def calc_index_return(index_close, window):
    if index_close is None or len(index_close) <= window:
        return 0.0
    return float(index_close.iloc[-1] / index_close.iloc[-window - 1] - 1.0)


def calc_ma_slope(close_df, window, lookback):
    if len(close_df) < window + lookback:
        return pd.Series(0.0, index=close_df.columns)
    ma_now = close_df.tail(window).mean()
    ma_prev = close_df.iloc[-window - lookback:-lookback].mean().replace(0, np.nan)
    return ma_now / ma_prev - 1.0


def calc_beta(stock_ret, index_ret, window):
    """
    向量化实现：beta = cov(x, y) / var(x)，对所有股票同时计算，
    避免原实现中逐股票 Python for 循环带来的性能开销。
    仍保留原逻辑：样本不足 60 期的股票 beta 记为 1.0（中性）。

    注意事项：
    1. 自由度：原实现用 np.var(x)（ddof=0，除以 N）计算方差，
       用 np.cov(x, y)[0, 1]（ddof=1，除以 N-1）计算协方差，
       这里保持完全一致的自由度，避免向量化引入系统性偏差。
    2. 全程使用 numpy array 而非 pandas DataFrame 做逐元素布尔运算，
       因为部分 pandas 版本里 DataFrame 与 (n,1) 形状的 numpy array
       做 & 运算不会自动按列广播，而是报 shape 不匹配错误。
       用 numpy array 的 (window, n_stocks) & (window, 1) 天然广播即可。
    已用多组随机数据（含无缺失值、随机缺失值、样本不足 60 期、
    500 只股票等规模）核对过，与原始逐股循环结果误差在浮点精度以内。
    """
    if len(stock_ret) < window:
        return pd.Series(1.0, index=stock_ret.columns)

    y_df = stock_ret.tail(window)
    x = index_ret.reindex(y_df.index)

    y_arr = y_df.values.astype(float)               # (window, n_stocks)
    x_arr = x.values.astype(float).reshape(-1, 1)    # (window, 1)

    y_isnan = np.isnan(y_arr)
    x_isnan = np.isnan(x_arr)

    valid_mask = (~y_isnan) & (~x_isnan)             # numpy 广播 -> (window, n_stocks)
    n = valid_mask.sum(axis=0).astype(float)
    n_safe = np.where(n > 0, n, np.nan)

    x_filled = np.where(x_isnan, 0.0, x_arr)
    y_filled = np.where(y_isnan, 0.0, y_arr)

    x_sum = (x_filled * valid_mask).sum(axis=0)
    y_sum = (y_filled * valid_mask).sum(axis=0)
    x_mean = x_sum / n_safe
    y_mean = y_sum / n_safe

    x_dev = (x_filled - x_mean.reshape(1, -1)) * valid_mask
    y_dev = (y_filled - y_mean.reshape(1, -1)) * valid_mask

    n_minus_1 = np.where(n > 1, n - 1, np.nan)
    cov = (x_dev * y_dev).sum(axis=0) / n_minus_1
    var = (x_dev ** 2).sum(axis=0) / n_safe

    beta = np.where((n >= 60) & (var > 0), cov / var, 1.0)
    return pd.Series(beta, index=stock_ret.columns)


def calc_alpha(stock_ret, index_ret, window):
    if len(stock_ret) < window:
        return pd.Series(0.0, index=stock_ret.columns)
    beta = calc_beta(stock_ret, index_ret, window)
    y_mean = stock_ret.tail(window).mean()
    x_mean = index_ret.reindex(stock_ret.tail(window).index).mean()
    return (y_mean - beta * x_mean) * 252.0


def fill_missing_values(df):
    result = df.copy()
    for col in result.columns:
        if pd.api.types.is_numeric_dtype(result[col]):
            median = result[col].median()
            if pd.isna(median):
                median = 0.0
            result[col] = result[col].fillna(median)
    return result


def zscore(series):
    s = pd.to_numeric(series, errors='coerce')
    s = s.replace([np.inf, -np.inf], np.nan)
    if s.dropna().empty:
        return pd.Series(0.0, index=s.index)
    median = s.median()
    mad = (s - median).abs().median()
    if pd.isna(mad) or mad <= 0:
        std = s.std()
        if pd.isna(std) or std <= 0:
            return pd.Series(0.0, index=s.index)
        clipped = s.clip(lower=s.mean() - 3.0 * std, upper=s.mean() + 3.0 * std)
    else:
        scale = 1.4826 * mad
        clipped = s.clip(lower=median - 3.0 * scale, upper=median + 3.0 * scale)
    std = clipped.std()
    if pd.isna(std) or std <= 0:
        return pd.Series(0.0, index=s.index)
    return ((clipped - clipped.mean()) / std).fillna(0.0)


def industry_zscore(industry_series, value_series):
    """
    行业内标准化：和全市场比改成和同行业股票比，避免“行业整体强弱”
    被误判成“个股选股能力”。比如白酒行业整体估值系统性偏高时，
    全市场zscore会让白酒股普遍拿高分（哪怕它们在行业内部只是平庸），
    行业内zscore会把这部分行业贝塔剥离掉，只保留“在同行里排第几”这个信息。

    行业里只有1只股票时该组zscore为0（没有对比对象，视为中性），是预期行为，
    不是bug——已用模拟数据验证过：全市场zscore下各行业均值明显偏离0，
    行业内zscore下各行业均值精确收敛到0（浮点精度以内）。

    只用于 trend_score / dividend_defensive_score 这类“个股选股能力”因子。
    industry_flow_score 本身就是在比较行业和行业之间的强弱，不能用这个函数，
    否则同行业股票的值全部相同、组内标准差为0，会被压成全0，信号直接消失。
    leader_score 里的市值/流动性类因子代表的是“在全市场里是不是龙头”，
    也不适合按行业内部再比一次，会扭曲“龙头”本身的含义。
    """
    if industry_series is None:
        return zscore(value_series)
    s = pd.to_numeric(value_series, errors='coerce')
    frame = pd.DataFrame({'industry': industry_series, 'value': s})
    result = frame.groupby('industry')['value'].transform(lambda x: zscore(x))
    return result.reindex(value_series.index).fillna(0.0)


def blended_zscore(industry_series, value_series, neutral_strength):
    neutral_strength = clip_value(neutral_strength, 0.0, 1.0)
    global_score = zscore(value_series)
    if neutral_strength <= 0:
        return global_score

    group_sizes = pd.Series(industry_series).groupby(industry_series).transform('size').reindex(value_series.index).fillna(0.0)
    min_group = max(1, int(getattr(g, 'industry_neutral_min_group_size', 5)))
    shrink = (group_sizes / float(min_group)).clip(upper=1.0)
    effective_strength = neutral_strength * shrink
    industry_score = industry_zscore(industry_series, value_series)
    return ((1.0 - effective_strength) * global_score + effective_strength * industry_score).fillna(0.0)


def clip_value(value, lower, upper):
    return max(lower, min(upper, float(value)))


def safe_float(value):
    if pd.isna(value):
        return np.nan
    return float(value)


def normalize_price_matrix(df):
    if df is None or df.empty:
        return None
    df = df.sort_index().ffill()
    df = df.dropna(axis=1, how='all')
    if df.empty:
        return None
    return df.loc[:, df.iloc[-1].notna()]


def price_to_field_matrix(price, stocks, field_name):
    if price is None or price.empty:
        return None
    df = price.copy()
    if 'time' in df.columns and 'code' in df.columns and field_name in df.columns:
        return df.pivot(index='time', columns='code', values=field_name)
    if 'date' in df.columns and 'code' in df.columns and field_name in df.columns:
        return df.pivot(index='date', columns='code', values=field_name)
    if isinstance(df.index, pd.MultiIndex):
        tmp = df.reset_index()
        date_col = None
        code_col = None
        for candidate in ['time', 'date', 'level_0']:
            if candidate in tmp.columns:
                date_col = candidate
                break
        for candidate in ['code', 'level_1']:
            if candidate in tmp.columns:
                code_col = candidate
                break
        if date_col is not None and code_col is not None and field_name in tmp.columns:
            return tmp.pivot(index=date_col, columns=code_col, values=field_name)
    if field_name in df.columns and len(stocks) == 1:
        return pd.DataFrame({stocks[0]: df[field_name]}, index=df.index)
    all_stocks_present = True
    for stock in stocks:
        if stock not in df.columns:
            all_stocks_present = False
            break
    if all_stocks_present:
        return df[stocks]
    return None


def price_to_close_series(price):
    if price is None or price.empty:
        return None
    df = price.copy()
    if isinstance(df, pd.Series):
        return df.sort_index().ffill().dropna()
    if 'time' in df.columns and 'close' in df.columns:
        s = df.set_index('time')['close']
    elif 'date' in df.columns and 'close' in df.columns:
        s = df.set_index('date')['close']
    elif 'close' in df.columns:
        s = df['close']
    else:
        return None
    return s.sort_index().ffill().dropna()
