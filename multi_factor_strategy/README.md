# Multi-Factor A-Share Stock Selection (Wind API)

All inputs are fetched with WindPy on a Windows machine with the Wind terminal installed. Keep the Wind terminal logged in, then run:

```powershell
python run_wind_strategy.py
```

The candidate pool is all A-shares as of the data date (Shanghai, Shenzhen and Beijing exchanges; NEEQ excluded), with ST, *ST and delisting stocks removed. The six-factor score is then computed and the ranking written out. There is no year-to-date return threshold.

Everything lives in this folder: `config/` holds factor weights and universe rules, `src/` holds the Wind interface, scoring, XGBoost and backtest scripts, `data/wind/` holds intermediate data, and `output/` holds final results.

## Universe (Wind mapping)

- Base universe: Wind `全部A股` (all A-shares); only `.SH`, `.SZ` and `.BJ` codes are accepted, which defensively excludes NEEQ and other markets.
- Name filter: ST, *ST, delisted and delisting-period stocks are removed.
- Year-to-date return is kept only as an audit field and is not used for screening.
- Historical backtests rebuild the universe independently on each weekly signal date, using the constituents, names and year-to-date returns as of that date.

## Data definitions

- Analyst profit growth (YoY): `WEST_NETPROFIT_YOY`.
- Analyst forecast revision: change in FY1 net profit over the last 1 month minus the change over the last 3 months.
- ROE, gross margin, net profit growth: latest available Wind financial snapshot.
- Quarterly trend: change in the quarterly `NETPROFIT_TTM` series (the single-quarter net profit field returned empty values in the current WindPy environment, so TTM is used as a proxy and this definition is kept explicitly).
- PEG: for this year, next year and the year after, PE of that year ÷ forecast net profit growth of that year, reverse-standardized and combined with weights `50% / 30% / 20%`.
- Analyst forecast profit growth: this year, next year and the year after are each standardized cross-sectionally (higher is better) and combined with weights `50% / 30% / 20%`.
- Analyst forecast change: for this year and next year, current forecast net profit growth minus the forecast growth on 2026-08-22. Absolute changes below `0.5 percentage points` score 0; upgrades and downgrades are scaled separately with sign preserved (upgrades positive, downgrades negative, no forecast 0), then combined `50% / 50%`. The factor ranges from `-10 to +10 points`.
- Outlier treatment: before standardization, every continuous raw factor is winsorized at 3×MAD within the eligible candidate pool, with bounds `median ± 3 × 1.4826 × MAD`. Values beyond the bounds are clipped, missing values stay missing, and nothing is clipped when MAD is 0.
- Single-quarter net profit QoQ growth: 48 points maximum. A stock that grew strictly in each of the last 5 consecutive quarters (requires 6 consecutive quarters of data) gets the full score; otherwise its cross-sectional standardized score is multiplied by `0.80` and then by 48, for a maximum of 38.4 points.
- Consecutive growth quarters: 1 point maximum. Counts strict quarter-on-quarter increases backward from the latest quarter; no MAD winsorization, direct min-max standardization.

The eligible pool and its year-to-date audit field are written to `data/wind/ai_theme.csv` (old filename kept for compatibility); exclusion reasons go to `data/wind/excluded_stocks.csv`. Missing values returned by Wind are written to `data/wind/missing_data.csv` and are never silently filled in.

## Rebalancing

Signals are generated after the close on the last trading day of each week; the portfolio is rebalanced on the next trading day and held until the next weekly signal. Backtest NAV is also computed weekly.

## Backtests

- `src/backfill_top20_history.py`: backfills historical weekly Top30 rankings from a given date (the universe is rebuilt and rescored on each signal date) into `data/top20_history.csv`.
- `src/backtest_top30.py`: backtest with rebalancing on three dates each year (April 30, August 31, October 31).
- `src/backtest_weekly_curve.py`: same three rebalancing dates, weekly NAV curve net of a 0.2% round-trip cost, compared with a benchmark.
- `src/top20_history.py`: records each week's Top30 snapshot and tracks the most frequently selected stocks over the last 6 months.

## File structure

```text
run_wind_strategy.py        Main pipeline: universe -> Wind data -> six-factor score -> XGBoost and linear baselines (optional) -> history
FACTORS.md                  Detailed factor definitions
config/                     Factor weights, universe rules, XGBoost and baseline parameters
src/wind_universe.py        Build the universe
src/wind_data.py            Fetch factor data from Wind
src/stock_workflow.py       Six-factor scoring and ranking
src/build_xgb_panel.py      XGBoost training panel
src/xgboost_model.py        XGBoost model and out-of-sample evaluation
src/factor_baselines.py     Linear baselines: z-score, ICIR weighting, WLS regression; model comparison
src/open_data.py            Free-data panel (BaoStock + AkShare) for running the study without Wind
src/jq_port_backtest.py     Local port of the JoinQuant 12-factor + LightGBM strategy (see joinquant_lightgbm/)
src/backfill_top20_history.py, backtest_*.py, top20_history.py   History backfill and backtests
```

## XGBoost ranking model (experimental)

Alongside the rule-based six-factor score, an XGBoost cross-sectional model is trained on **the same raw factors** to test whether non-linear effects and factor interactions add stock-selection power beyond fixed weights. The rule-based score remains the primary output; XGBoost serves as a comparison or an optional blend.

Files:

- `src/build_xgb_panel.py`: `archive` copies `data/wind/factor_data.csv` and the same week's `output/ranked_stocks.csv` to `data/history_factors/<trade_date>/` every week (point-in-time: only data available on that date is stored); `panel` stacks all snapshots into a training panel and attaches, as the label, the forward-adjusted return over 5 trading days starting the next trading day, fetched from Wind, written to `data/xgb_panel.csv`.
- `src/xgboost_model.py`: `--mode backtest` runs the walk-forward out-of-sample evaluation; `--mode live` trains on the full history and scores this week's candidate pool.
- `config/xgboost.json`: feature list, label horizon, walk-forward window, model hyperparameters, Top N, transaction cost and whether to blend with the rule-based score.
- `requirements-xgboost.txt`: extra dependencies (xgboost, pandas, numpy, scipy).

Method:

1. **Features**: 16 raw factor fields (analyst forecast growth FY1–FY3, PEG FY1–FY3, single-quarter net profit QoQ growth, consecutive growth quarters, forecast revision FY1–FY2, and prior-year/current-year single-quarter ROE, gross margin and net margin). Within each snapshot, features are winsorized at 3×MAD and then rank-normalized to [0, 1]; negative PEG is treated as missing; missing values are kept and XGBoost learns their split direction.
2. **Label**: the cross-sectional percentile rank of the forward return, which removes the market-wide move so the model learns stock selection only.
3. **Leakage control**: expanding-window training; each snapshot is predicted only from earlier snapshots with a 1-snapshot embargo (to avoid overlapping return windows); the last 4 snapshots of each training window are used for early stopping.
4. **Evaluation**: out-of-sample Rank IC, ICIR, share of positive IC, and Top10 return net of a 0.2% round-trip cost, compared with the rule-based score and the equal-weight pool over the same snapshots; feature importance (gain) is reported.

Run (requires a logged-in Wind terminal and roughly 30 weeks of archived snapshots):

```powershell
pip install -r requirements-xgboost.txt
python src/build_xgb_panel.py panel
python src/xgboost_model.py --mode backtest   # writes output/xgb_report.md and related files
python src/xgboost_model.py --mode live       # writes output/xgb_ranked_stocks.csv
```

`run_wind_strategy.py` archives the weekly snapshot automatically on every run and, if a training panel exists, also writes the XGBoost ranking. A failure in this step does not affect the rule-based pipeline.

Decision rule: replace or blend in XGBoost only if both its out-of-sample IC and its net-of-cost return are consistently higher than the rule-based score; otherwise the added complexity does not pay off and the simpler rule-based model should be kept.

## Linear baselines: z-score, ICIR weighting, WLS regression

To judge whether the hand-set weights or XGBoost add anything, `src/factor_baselines.py` builds three standard linear models from the same 16 raw factors and the same training panel (`data/xgb_panel.csv`). Parameters are in `config/baselines.json`.

1. **Cross-sectional z-score**: within each snapshot, every factor is winsorized at 3×MAD (except the consecutive-growth count) and standardized to `z = (x - mean) / std`. PEG is reversed (negative PEG is set to missing first), and missing values are set to 0, the cross-sectional mean. The **equal-weight composite** is the mean of the 16 z-scores.
2. **ICIR weighting**: for each snapshot, the Rank IC of every factor's z-score with the next-period return is computed. To score snapshot *t*, each factor's weight is its ICIR (mean IC / IC std) over the previous 12 snapshots, with a 1-snapshot embargo and at least 8 snapshots required; factors with negative ICIR get weight 0 and weights are normalized to sum to 1.
3. **Weighted regression (WLS)**: for each snapshot, the next-period return is regressed cross-sectionally on the 16 z-scores (Fama-MacBeth style) with weights `sqrt(market cap)` and a small ridge penalty (λ = 0.1, intercept not penalized) to handle collinear factors such as the three PEG years. To score snapshot *t*, the average coefficients over the previous 12 snapshots (same embargo) are applied to the current z-scores.

All three are scored only with information available before each snapshot, so they are directly comparable with the walk-forward XGBoost predictions.

Run:

```powershell
python src/factor_baselines.py --mode backtest   # writes output/model_comparison.md and related files
python src/factor_baselines.py --mode live       # writes output/baseline_ranked_stocks.csv
```

`--mode backtest` writes the per-snapshot factor ICs (`factor_ic.csv`), the single-factor ICIR summary, the WLS coefficients, the weights used at each snapshot, the out-of-sample scores, and `model_comparison.md`, which compares the rule-based score, the three baselines, XGBoost (if `output/xgb_oos_predictions.csv` exists) and the equal-weight pool on mean Rank IC, ICIR, share of positive IC and Top10 return net of a 0.2% round-trip cost. `run_wind_strategy.py` writes the live baseline ranking whenever a training panel exists.

## Open-data version (no Wind terminal)

`src/open_data.py` rebuilds a comparable study from free, open-source data so the method can be reproduced on any machine:

- **BaoStock** (free, no registration): trading calendar, point-in-time CSI 300 and CSI 500 constituents, daily back-adjusted prices with turnover, PE (TTM), PB (MRQ), PS (TTM), PCF (TTM), trading status and ST flag.
- **AkShare** (open source, Eastmoney earnings reports): quarterly ROE, gross margin, revenue growth and net profit growth (YoY and QoQ) for all A-shares, with announcement dates.

Design:

- **Universe**: CSI 300 + CSI 500 constituents on each month-end (about 800 stocks per month, 1,276 distinct stocks for 2019-2025), excluding ST stocks, suspended stocks and stocks with fewer than 120 trading days of history.
- **14 factors**: value (EP, BP, SP, CFP), size (negative log float market cap, estimated as turnover amount / turnover rate), 12-1 month momentum, 1-month reversal, low volatility (60 days), low turnover (20 days), ROE, gross margin, revenue YoY, net profit YoY and net profit QoQ. Signs are set so that higher is expected to be better.
- **Point in time**: a quarterly report is used only after its announcement date, or the statutory deadline if the date is missing; reports older than about three quarters are treated as missing.
- **Label**: monthly return from the close of the first trading day after the signal date to the close of the first trading day after the next signal date.
- **Models**: the same z-score, ICIR-weighted and WLS baselines, configured in `config/open_data.json` (Top50 portfolio, 0.2% round-trip cost per month).

Analyst forecast factors are not included because free sources do not provide their history.

```bash
pip install baostock akshare pandas numpy scipy
python src/open_data.py all --start 2019-01-01 --end 2025-10-31   # cached in data/open/
python src/factor_baselines.py --mode backtest --config config/open_data.json   # writes output/open_data/
```

### Results (historical backtest, CSI 300 + CSI 500, monthly, 2019-10 to 2025-09)

Out-of-sample over the 72 months where all three models have weights (the equal-weight z-score also covers the first 9 months). Top50 returns are net of a 0.2% round-trip cost per month; excess is relative to the equal-weight universe (10.9% simple annualized return over the same months).

| Model | Mean Rank IC | ICIR (monthly) | Top50 annualized excess | Months beating the pool |
|---|---|---|---|---|
| Z-score, equal weight | 0.040 | 0.21 | -2.9% | 43% |
| Z-score, ICIR weighted | 0.063 | 0.26 | +1.4% | 50% |
| WLS regression (sqrt market cap) | 0.033 | 0.14 | -1.9% | 47% |

Strongest single factors (full sample, descriptive): low volatility (IC 0.065), low turnover (0.059), cash-flow yield (ICIR 0.24) and 1-month reversal; growth factors (revenue and profit growth) had negative IC in this period. Positive rank IC did not translate into a Top50 return clearly above the universe after costs; only the ICIR-weighted composite was ahead, by a margin that is small relative to its month-to-month variation.
