# Deflated Sharpe ratios

Historical backtest, CSI 300 + CSI 500, net of costs, 2019-11-01 to 2025-12-31 (1497 trading days).

- Trials: 80 offset-level backtests (4 weighting methods x 20 start offsets).
- Dispersion of trial Sharpe ratios: annualized std 0.19.
- Effective independent trials (participation ratio of the correlation matrix): 2.0.
- Sharpe ratio the best of N pure-noise trials would show by luck (annualized): 0.10 at N_eff, 0.50 at N = 141.

PSR: probability that the true Sharpe ratio is above 0, ignoring how many variants were tried. DSR: the same probability after deflating for N trials. 0.95 is the usual bar.

| Portfolio | Sharpe (ann.) | Skew | Kurtosis | PSR | DSR, N_eff | DSR, N = 141 |
|---|---|---|---|---|---|---|
| Equal weight top 20, 20 offsets combined | 0.47 | -0.55 | 7.2 | 0.87 | 0.82 | 0.47 |
| Max score, beta <= 0.7, 20 offsets combined | 0.46 | -0.61 | 8.3 | 0.87 | 0.81 | 0.46 |
| Min beta, 20 offsets combined | 0.75 | -0.62 | 8.0 | 0.96 | 0.94 | 0.72 |
| Control: min beta, whole universe (no model), 20 offsets combined | 0.74 | -0.41 | 7.7 | 0.96 | 0.94 | 0.71 |
| Best single backtest (Min beta | offset 3) | 0.91 | -0.44 | 8.1 | 0.99 | 0.97 | 0.84 |
| CSI All Share index | 0.37 | -0.33 | 10.8 | 0.81 | 0.74 | 0.37 |
