# ai-stock-Top-20

Multi-factor stock selection for China A-shares: factor research, linear baselines, machine-learning ranking models and JoinQuant backtest strategies.

| Folder | Contents |
|---|---|
| [`multi_factor_strategy/`](multi_factor_strategy/) | Weekly Top ranking built on Wind data (single-quarter profit growth, analyst consensus, PEG, revisions, quality); XGBoost cross-sectional model with walk-forward tests; linear baselines (cross-sectional z-score, ICIR weighting, rolling WLS); a free-data pipeline (BaoStock / AkShare) and a local port of the JoinQuant strategy |
| [`joinquant_lightgbm/`](joinquant_lightgbm/) | JoinQuant strategies: 12-factor score + LightGBM, top 20, rebalanced every 20 trading days, with linear, LightGBM-only, blended and equal-weight variants |

Highlights:

- Point-in-time data handling: fundamentals keyed to announcement dates, historical index constituents, non-overlapping label windows.
- Leakage checks: placebo runs on shuffled labels, ablations of the industry feature, out-of-sample validation gates.
- Execution realism: lot sizes (STAR Market 200-share minimum), limit-up / suspension filters, commission and stamp duty.

All performance figures in this repository are historical backtests and are not investment advice.
