# JoinQuant Strategy: 12-Factor Score + LightGBM, 20-Day Rebalancing

`strategy_lite_lightgbm.py` is a backtest strategy for the [JoinQuant](https://www.joinquant.com) platform. It uses the platform's `jqdata` API and only runs inside a JoinQuant backtest. Paste the file into a new strategy there and run it.

## Universe

CSI All Share (`000985.XSHG`, also the benchmark) intersected with six Shenwan level-1 industries (computers, semiconductors, telecom, banks, securities, non-ferrous metals) plus stocks in AI-related concept boards. Concepts are matched by keyword and only used after their start date. ST, suspended and limit-up/limit-down stocks are excluded.

## Scoring (on rebalance days only)

1. **Linear score**: 12 representative factors, condensed from an earlier four-core version (trend, industry, leader, defensive):
   - trend: 20-day relative strength, 10-day slope of MA60, 120-day alpha, volume quality (5-day / 20-day turnover ratio near 1.15);
   - liquidity and leadership: log 20-day average turnover, number of limit-up closes in 10 days;
   - defensive: ROE, low debt-to-assets, low 60-day drawdown, low 120-day beta;
   - industry: 20-day industry money flow and 20-day industry return.

   Factors are winsorized at 3×MAD and z-scored. Trend and defensive factors blend industry-neutral and market-wide z-scores. The core weights move with a continuous market-strength measure and the market regime.
2. **Model score**: LightGBM, falling back to XGBoost, then to the linear score alone.
   - Features: the 12 z-scored factors, plus industry as a categorical feature for LightGBM.
   - Training data: 18 historical cross-sections, 20 trading days apart, so label windows never overlap. Cross-sections are cached, so each rebalance computes only one new one.
   - Label: the cross-sectional percentile rank of the 20-day forward return. Every label window ends on or before the decision date.
   - Validation: the most recent 25% of cross-sections are held out. The model is used only if its out-of-sample Rank IC is at least 0; otherwise the strategy falls back to the linear score.
3. **Final score**: 0.5 × model rank + 0.5 × linear rank.

## Portfolio

- **Rebalancing**: every 20 trading days, matching the label horizon.
- **Entry signal**: a limit-up close in the last 10 trading days, with the current close below that limit-up close. Signals stay valid for 20 trading days.
- **Ranking pool**: stocks with a valid entry signal plus current holdings.
- **Buffer**: a new stock is bought only if it ranks in the top 20; a holding is sold only if it drops out of the top 40.
- **Tolerance band**: holdings within 1.5 percentage points of their target weight are not traded.
- **Sizing**: 20 stocks, equal weight, at most 12% per stock.
- **Asset allocation by market regime** (stocks / gold ETF `518880` / cash):

  | Regime | Stocks | Gold ETF | Cash |
  |---|---|---|---|
  | risk-on | 95% | 2% | 3% |
  | neutral | 80% | 8% | 12% |
  | risk-off | 40% | 18% | 42% |

- **Costs**: 0.03% commission each way, 0.1% stamp duty on sales, minimum commission 5 CNY. Look-ahead protection (`avoid_future_data`) is enabled.

## Not yet implemented

Daily checks of total exposure, stop-losses on individual stocks, and staggered rebalancing.
