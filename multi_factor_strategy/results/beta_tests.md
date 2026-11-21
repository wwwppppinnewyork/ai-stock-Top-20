# Beta-constrained weights (LightGBM top 20, long only)

Historical backtest on free BaoStock / AkShare data, CSI 300 + CSI 500, net of costs, 2019-11-01 to 2025-12-31. Settings fixed before the run: 100 candidates, 5% cap per stock, 250-day beta, beta limit 0.7.

## Distribution over the 20 start offsets

| Method | Ann. return (min / median / max) | Sharpe (min / median / max) | Max DD (median) | Ex-ante beta | Realized beta |
|---|---|---|---|---|---|
| Equal weight top 20 | 1.2% / 8.0% / 15.1% | 0.17 / 0.45 / 0.72 | -48.1% | 0.83 | 0.84 |
| Max score, beta <= 0.7 | 2.2% / 6.7% / 13.1% | 0.21 / 0.44 / 0.73 | -35.3% | 0.60 | 0.67 |
| Min beta | 5.8% / 11.5% / 15.3% | 0.42 / 0.69 / 0.91 | -21.7% | 0.44 | 0.52 |
| Control: min beta, whole universe (no model) | 9.2% / 10.5% / 12.2% | 0.65 / 0.73 / 0.82 | -16.6% | 0.20 | 0.34 |

## All 20 offsets combined (equal capital, staggered sleeves)

| Portfolio | Ann. return | Sharpe | Max drawdown |
|---|---|---|---|
| Equal weight top 20 | 7.8% | 0.47 | -42.3% |
| Max score, beta <= 0.7 | 6.6% | 0.46 | -30.7% |
| Min beta | 11.3% | 0.75 | -15.4% |
| Control: min beta, whole universe (no model) | 10.6% | 0.74 | -16.3% |
| CSI All Share index | 5.5% | 0.37 | -39.3% |

Beta limit reachable on 81% of rebalance days (median minimum attainable beta 0.45; median beta of the equal-weight top 20 0.88).
