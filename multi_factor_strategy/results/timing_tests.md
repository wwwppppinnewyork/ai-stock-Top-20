# Rebalance timing tests (LightGBM top 20, no industry classification)

Historical backtest on free BaoStock / AkShare data, CSI 300 + CSI 500, net of costs.

## A. Timing luck: 20 start offsets, 2019-11-01 to 2025-12-31

| | Ann. return | Sharpe | Max drawdown |
|---|---|---|---|
| worst | 1.2% | 0.17 | -68.2% |
| 25th pct | 4.5% | 0.31 | -51.5% |
| median | 8.0% | 0.45 | -48.1% |
| 75th pct | 10.0% | 0.53 | -42.9% |
| best | 15.1% | 0.72 | -34.1% |

Per offset: 0: 5.3%, 1: 14.0%, 2: 7.7%, 3: 5.9%, 4: 5.5%, 5: 3.9%, 6: 8.7%, 7: 15.1%, 8: 9.9%, 9: 10.4%, 10: 10.6%, 11: 2.8%, 12: 11.4%, 13: 9.6%, 14: 9.4%, 15: 8.2%, 16: 4.6%, 17: 2.7%, 18: 2.2%, 19: 1.2%

## B. Signal decay (mean Rank IC on every trading day)

| Horizon (days) | 1 | 5 | 10 | 20 | 40 | 60 |
|---|---|---|---|---|---|---|
| Mean IC | 0.022 | 0.024 | 0.023 | 0.037 | 0.042 | 0.049 |
| IC / sqrt(h) | 0.022 | 0.011 | 0.007 | 0.008 | 0.007 | 0.006 |

## C and D. Holding period and signal-triggered trading, 2020-01-17 to 2025-12-31

| Portfolio | Ann. return | Sharpe | Max drawdown | Round trips per slot per year |
|---|---|---|---|---|
| Hold 5d, 4 staggered sleeves | -4.4% | -0.11 | -49.6% |  |
| Hold 10d, 4 staggered sleeves | 6.6% | 0.41 | -39.0% |  |
| Hold 20d, 20 staggered sleeves | 6.6% | 0.42 | -42.2% |  |
| Hold 40d, 4 staggered sleeves | 11.9% | 0.65 | -35.3% |  |
| Hold 60d, 4 staggered sleeves | 6.3% | 0.40 | -41.9% |  |
| Hold 20d, 4 sleeves (offsets 0/5/10/15) | 6.5% | 0.41 | -44.2% |  |
| Hold 20d, single schedule (offset 0) | 4.1% | 0.29 | -48.8% |  |
| Daily scoring, sell below rank 30 | -11.3% | -0.39 | -70.5% | 59.1 |
| Daily scoring, sell below rank 40 | -7.9% | -0.23 | -66.8% | 47.6 |
| Daily scoring, sell below rank 60 | -4.0% | -0.05 | -61.6% | 36.9 |
| Daily scoring, sell below rank 100 | -0.5% | 0.10 | -61.3% | 26.8 |
| Daily scoring, sell below rank 200 | 0.7% | 0.15 | -56.5% | 16.8 |
| CSI All Share index | 4.4% | 0.31 | -39.3% |  |

## Calendar-year returns

| Year | hold 5d | hold 10d | hold 20d | hold 40d | hold 60d | 4 sleeves | offset 0 | buffer 30 | buffer 40 | buffer 60 | buffer 100 | buffer 200 | index |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2020 | 15.3% | 29.2% | 46.4% | 38.3% | 35.9% | 42.3% | 32.4% | 29.0% | 32.9% | 36.6% | 46.9% | 55.1% | 21.2% |
| 2021 | 0.3% | 15.2% | 12.2% | 12.1% | 4.9% | 14.3% | 17.0% | -13.6% | -10.7% | -7.5% | -4.3% | -9.3% | 6.2% |
| 2022 | -29.6% | -24.3% | -26.1% | -17.4% | -24.5% | -28.3% | -29.3% | -40.3% | -36.7% | -32.8% | -36.7% | -31.9% | -20.3% |
| 2023 | -7.7% | -10.0% | -3.3% | 6.8% | 3.1% | -6.3% | -3.7% | -20.4% | -18.1% | -14.4% | -8.2% | -4.6% | -7.0% |
| 2024 | -0.7% | 25.7% | 15.6% | 17.4% | 22.8% | 19.5% | 17.2% | 1.8% | 7.3% | 5.3% | 12.1% | 16.1% | 7.4% |
| 2025 | 3.6% | 12.9% | 6.3% | 18.5% | 3.9% | 9.6% | 2.0% | -6.6% | -5.4% | 3.4% | 6.2% | -2.0% | 24.6% |
