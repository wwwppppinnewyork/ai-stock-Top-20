# -*- coding: utf-8 -*-
"""
Strategy 5: equal-weight CSI 300 + CSI 500 portfolio
====================================================

Benchmark for strategies 1-3: hold every CSI 300 and CSI 500 constituent at
equal weight, rebalanced every 20 trading days.  No factors, no model and no
industry filter.

- Universe: CSI 300 (000300.XSHG) + CSI 500 (000905.XSHG) constituents on the
  decision date (the previous trading day).  ST, suspended and
  limit-up/limit-down stocks are skipped on the trading day.
- Rebalance: every 20 trading days at the open; 99% invested, 1% cash for fees.
- Tolerance: an existing position is only resized when it deviates from its
  target value by more than 20%, to avoid hundreds of tiny orders.
- Costs: 0.03% commission each way, 0.1% stamp duty on sales, 5 CNY minimum
  commission.  With about 800 positions the 5 CNY minimum dominates for small
  accounts; use at least 10 million CNY of starting capital.
"""
from jqdata import *


def initialize(context):
    g.index = '000985.XSHG'  # CSI All Share, same benchmark as strategies 1-3
    set_benchmark(g.index)
    set_option('use_real_price', True)
    try:
        set_option('avoid_future_data', True)
    except Exception:
        pass
    set_order_cost(
        OrderCost(open_tax=0, close_tax=0.001, open_commission=0.0003,
                  close_commission=0.0003, close_today_commission=0, min_commission=5),
        type='stock'
    )

    g.index_codes = ['000300.XSHG', '000905.XSHG']
    g.rebalance_interval_trading_days = 20
    g.cash_buffer = 0.01
    g.resize_tolerance = 0.20
    g.last_rebalance_date = None

    log.set_level('order', 'error')
    run_daily(rebalance, time='open', reference_security=g.index)
    log.info('Strategy 5 initialized: equal-weight CSI 300 + CSI 500, rebalance every %d trading days'
             % g.rebalance_interval_trading_days)


def should_rebalance_today(context):
    if g.last_rebalance_date is None:
        return True
    days = get_trade_days(start_date=g.last_rebalance_date, end_date=context.current_dt.date())
    # the range includes both the last rebalance day and today
    return len(days) > g.rebalance_interval_trading_days


def is_tradable(data):
    if data.paused or data.is_st:
        return False
    if 'ST' in data.name or '*' in data.name or '退' in data.name:
        return False
    if data.last_price is None or data.last_price <= 0:
        return False
    return data.low_limit < data.last_price < data.high_limit


def rebalance(context):
    if not should_rebalance_today(context):
        return
    decision_date = context.previous_date
    universe = set()
    for code in g.index_codes:
        universe.update(get_index_stocks(code, date=decision_date))
    current = get_current_data()
    targets = sorted(s for s in universe if is_tradable(current[s]))
    if not targets:
        log.info('No tradable constituents today; keeping positions')
        return

    # sell holdings that left the universe (skipped automatically if not tradable)
    for stock, position in list(context.portfolio.positions.items()):
        if stock not in universe and position.closeable_amount > 0 and is_tradable(current[stock]):
            order_target_value(stock, 0)

    target_value = context.portfolio.total_value * (1.0 - g.cash_buffer) / len(targets)
    positions = context.portfolio.positions

    def current_value(stock):
        return positions[stock].value if stock in positions else 0.0

    # trim overweight positions first to free cash, then buy
    for stock in targets:
        value = current_value(stock)
        if value > target_value * (1 + g.resize_tolerance):
            order_target_value(stock, target_value)
    for stock in targets:
        value = current_value(stock)
        if value < target_value * (1 - g.resize_tolerance):
            order_target_value(stock, target_value)

    g.last_rebalance_date = context.current_dt.date()
    log.info('Rebalanced: %d constituents, %d tradable, target %.0f CNY each'
             % (len(universe), len(targets), target_value))
