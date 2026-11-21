# Out-of-sample test: adding slow factors chosen on pre-2023 data

Factors chosen on 2020-02 to 2022-09 cross-sections (|60-day Rank IC| >= 0.03): sp, bp, profit_yoy. Historical backtest, CSI 300 + CSI 500, net of costs, 20 start offsets.

## 2020-2022 (selection period)

| Model | Ann. return (min / median / max) | Sharpe (min / median / max) | Combined: ann. return / Sharpe / max DD |
|---|---|---|---|
| Baseline (10 active factors) | -12.2% / 7.4% / 36.9% | -0.36 / 0.40 / 1.30 | 8.1% / 0.45 / -36.8% |
| Baseline + sp, bp, profit_yoy | -6.9% / 9.9% / 44.4% | -0.11 / 0.48 / 1.50 | 11.5% / 0.57 / -40.6% |
| CSI All Share index | | | 1.4% / 0.17 / -28.0% |

## 2023-2025 (out of sample)

| Model | Ann. return (min / median / max) | Sharpe (min / median / max) | Combined: ann. return / Sharpe / max DD |
|---|---|---|---|
| Baseline (10 active factors) | -5.5% / 5.5% / 17.8% | -0.20 / 0.37 / 0.94 | 6.8% / 0.49 / -21.1% |
| Baseline + sp, bp, profit_yoy | -2.6% / 6.8% / 21.2% | -0.05 / 0.45 / 1.12 | 7.7% / 0.54 / -19.9% |
| CSI All Share index | | | 7.4% / 0.46 / -29.4% |

## Paired by start offset

| Period | Offsets where the augmented model has the higher Sharpe | Median Sharpe difference |
|---|---|---|
| 2020-2022 (selection period) | 16 / 20 | +0.08 |
| 2023-2025 (out of sample) | 11 / 20 | +0.06 |
