# JoinQuant Strategy: 12-Factor Score + LightGBM, 20-Day Rebalancing

`strategy_lite_lightgbm.py` is a backtest strategy for the [JoinQuant](https://www.joinquant.com) platform. It uses the platform's `jqdata` API and only runs inside a JoinQuant backtest. Paste the file into a new strategy there and run it.

## Files

| File | Strategy |
|---|---|
| `strategy_lite_lightgbm.py` | Original: sector + AI-concept pool, limit-up pullback entry pool, 50/50 blend, buffer, stock / gold / cash sleeves (described below) |
| `strategy_1_linear_top20.py` | Strategy 1: linear 12-factor score, top 20 |
| `strategy_2_lightgbm_top20.py` | Strategy 2: LightGBM only, top 20 (no validation gate, no industry feature) |
| `strategy_2b_lightgbm_top20_no_industry.py` | Strategy 2b: strategy 2 without industry classification (`g.use_industry = False`) |
| `strategy_3_blend_top20.py` | Strategy 3: 50/50 blend of LightGBM and linear score with validation gate, top 20 |
| `strategy_5_equal_weight_csi800.py` | Strategy 5: equal-weight CSI 300 + CSI 500 benchmark, no factors |

Strategies 1-3 share the original code and differ only in switches in `initialize()`. They use the CSI 300 + CSI 500 universe (`g.universe_mode = 'csi800'`; set `'sector'` for the original pool), rebalance every 20 trading days into the top 20 stocks at equal weight, stay 99% invested, and drop the entry-signal pool, buffer, tolerance band and gold sleeve. Strategy 5 is a standalone benchmark; with about 800 positions, use at least 10 million CNY of starting capital so the 5 CNY minimum commission does not dominate. The numbering follows the rows of the local results table below (strategy 4, the full original strategy, is `strategy_lite_lightgbm.py`).

The section below describes the original strategy.

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

## Local test on free data

`multi_factor_strategy/src/jq_port_backtest.py` re-implements the scoring and portfolio rules outside JoinQuant, using the free BaoStock / AkShare data cache from `multi_factor_strategy/src/open_data.py`. It is an approximation, not a replica:

- **Universe**: CSI 300 + CSI 500 constituents, point in time, instead of the sector and AI-concept pool, which has no free history.
- **Industry**: CSRC industry from BaoStock (today's snapshot) instead of point-in-time Shenwan level 1.
- **ROE**: year-to-date ROE from Eastmoney.
- **Limit-up closes**: inferred from daily returns.
- **Execution**: signals at the decision-day close, trades at the next day's close.

Everything else follows the original: the 12 factors and their standardization, the market-strength and regime rules, LightGBM on 18 cross-sections with the 25% validation gate, the 50/50 blend, the limit-up pullback pool, the top-20 / top-40 buffer, the stock / gold / cash sleeves and JoinQuant's cost schedule.

```bash
cd multi_factor_strategy
pip install baostock akshare lightgbm pandas numpy
python src/open_data.py all
python src/jq_port_backtest.py download
python src/jq_port_backtest.py backtest                         # writes output/jq_port/report.md
python src/jq_port_backtest.py backtest --no-industry-feature   # ablation without the industry categorical
```

### Results (historical backtest, 2019-09 to 2025-12, 75 rebalances every 20 trading days, net of costs)

| Portfolio | Ann. return | Sharpe | Max drawdown | Excess vs CSI All Share (ann.) |
|---|---|---|---|---|
| Linear 12-factor score, top 20 | -1.4% | 0.12 | -72.3% | -3.2% |
| LightGBM only, top 20 | 12.1% | 0.60 | -33.6% | +6.8% |
| LightGBM only, no industry feature, top 20 | 22.5% | 0.90 | -36.3% | +16.3% |
| 50/50 blend with validation gate, top 20 | 0.6% | 0.16 | -61.6% | -3.0% |
| Full strategy approximation | -6.1% | -0.11 | -68.9% | -10.3% |
| Equal-weight CSI 300 + CSI 500 | 8.5% | 0.51 | -28.6% | +2.6% |
| CSI All Share index | 5.5% | 0.36 | -37.1% | 0 |

Mean Rank IC per 20-day period: linear score -0.058, LightGBM 0.047 (0.056 without the industry feature), blend -0.025.

What the numbers say:

- **The linear score points the wrong way in this universe.** 10 of the 12 factors had negative IC over the period, including industry money flow (-0.055), 20-day relative strength (-0.045), turnover size and limit-up count. The hand-set weights treat them as positive, so the composite has negative IC. Only money quality and low beta were positive.
- **LightGBM learns the reversal from past data**, so it ranks stocks in the right direction. It does not depend on the industry categorical: removing it improves the result, which also rules out the look-ahead from using today's industry snapshot.
- **No leakage**: a placebo run that trains LightGBM on shuffled labels gives a mean out-of-sample IC of -0.001.
- **The blend and the full strategy inherit the linear score's negative IC.** The validation gate passed in 79% of rebalances, so the strategy kept blending in the losing linear rank. The limit-up pullback pool and the momentum-heavy weights in risk-on periods add further drawdown.

**Timing luck.** The LightGBM figures above come from one rebalance schedule. Rerun on all 20 possible start days, the same strategy (without industry classification) earned between 1.2% and 15.1% a year, median 8.0%, and the schedule used here was the best of the 20. See `multi_factor_strategy/results/timing_tests.md`.

These results come from a broad large/mid-cap universe and may not transfer to the original sector and AI-concept pool on JoinQuant. They do suggest testing, on JoinQuant itself, a higher model weight (or the model alone) and a sign check on the trend and leader factors.

