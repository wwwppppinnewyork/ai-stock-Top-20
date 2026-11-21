# Multi-Factor Stock Selection: Factor Definitions

This document follows the current code in `src/wind_data.py`, `src/stock_workflow.py` and `config/weights.json`.

## 1. Eligibility before ranking

The candidate pool merges AI-theme stocks with the top-10 holdings of the 50 A-share equity ETFs with the highest year-to-date returns, deduplicated, with ST, *ST and delisting stocks removed. A stock must meet both conditions before it is scored: (1) its latest disclosed actual single-quarter net profit from the Wind consolidated income statement is available and strictly positive; (2) at least one of the Wind analyst forecast growth rates for this year, next year or the year after is valid. Companies with no forecast in any of the three years are excluded from the ranking.

## 2. Standardization

Unless a special rule applies, each sub-factor is min-max standardized cross-sectionally within that week's eligible pool:

- Positive direction: `S = (x - min) / (max - min)`; higher values score higher.
- Reverse direction: `S = 1 - (x - min) / (max - min)`; used for PEG, where lower values score higher.
- Missing values: scored at the neutral value 0.5, except for analyst forecast change; missing PEG is still 0.5 after reversal. Missing analyst forecast change scores 0.
- If all valid values of a factor are identical: valid values of a positive factor score 1 and missing values 0.5; valid values of reversed PEG score 0 and missing values 0.5. This is the actual boundary behavior of the current code.

## 3. Six primary factors

### 3.1 Analyst forecast net profit growth (YoY): 15 points

Year weights: this year 50%, next year 30%, the year after 20%, i.e. maximum contributions of 7.5, 4.5 and 3 points.

- This year: Wind `WEST_NETPROFIT_YOY`.
- Next year: `(next-year consensus net profit - this-year consensus net profit) / |this-year consensus net profit| × 100`.
- The year after: `(year-after consensus net profit - next-year consensus net profit) / |next-year consensus net profit| × 100`.
- Each year is standardized (higher is better), then combined 5:3:2.

Formula: `growth score = 15 × (S_this × 0.50 + S_next × 0.30 + S_after × 0.20)`.

### 3.2 PEG: 20 points

Year weights: this year 50%, next year 30%, the year after 20%, i.e. maximum contributions of 10, 6 and 4 points.

- PE for each year: `total market cap / Wind consensus net profit for that year`.
- PEG for each year: `PE for that year / forecast net profit growth for that year`.
- Forecast net profit and forecast growth must both be positive; otherwise that year's PEG is missing.
- Each year's PEG is reverse-standardized, then combined 5:3:2.

Formula: `PEG score = 20 × (reverse S_this × 0.50 + reverse S_next × 0.30 + reverse S_after × 0.20)`.

Special rule: if either next-year or year-after PEG is negative, the whole PEG factor scores 0. Negative PEG values are excluded from the cross-sectional standardization of valid PEG; the other factors are scored normally.

### 3.3 Actual single-quarter net profit QoQ growth: 48 points

Data field: Wind `QFA_NET_PROFIT_IS` with `rptType=1`, i.e. actual single-quarter net profit from the consolidated income statement (not cumulative profit, profit attributable to the parent, or net profit excluding non-recurring items).

- QoQ growth: `(this-quarter net profit - last-quarter net profit) / |last-quarter net profit| × 100`.
- If last quarter's profit is 0, QoQ growth is missing and gets the neutral score 0.5.
- Base QoQ growth is standardized cross-sectionally (higher is better).
- A stock whose QoQ growth was positive in each of the last 5 consecutive quarters (requires 6 consecutive calendar quarters of data) gets the full 48 points.
- These 5 quarters need not all be profitable: losses may narrow quarter by quarter and turn into profit. The latest quarter, however, must be strictly positive for the stock to be ranked.
- Otherwise: `QoQ score = base standardized score × 0.80 × 48`, for a maximum of 38.4 points.

### 3.4 Consecutive growth quarters (latest streak): 1 point

Counts consecutive strict increases in actual single-quarter net profit backward from the latest quarter: 0 if the latest quarter did not grow, 1 for one consecutive increase, up to 4 for four consecutive increases (five quarters of rising profit). The raw count is not MAD-winsorized and is min-max standardized directly within the eligible pool, for a maximum of 1 point.

### 3.5 Analyst forecast change: -10 to +10 points

Year weights: this year 50%, next year 50%, each with a maximum contribution of 5 points.

For this year and next year, the Wind consensus net profit growth is taken at two points in time, today and 2026-07-28:

- `forecast change = current forecast net profit growth - forecast net profit growth on 2026-07-28`, in percentage points.
- Changes with `|forecast change| < 0.5 percentage points` are set to 0 ("no material change").
- Upgrades and downgrades are scaled separately with sign preserved: the strongest upgrade in each year maps to +1, the strongest downgrade to -1, and other values are scaled by the largest absolute value in the same direction.
- A positive change scores positive, a negative change scores negative, and no change or no analyst forecast scores 0.
- This year and next year are combined 5:5, so each year contributes between -5 and +5 points.
- At year-end the code switches automatically between FY1, FY2 and FY3 so that the same calendar forecast year is tracked.

Formula: `forecast change score = 10 × (signed S_this × 0.50 + signed S_next × 0.50)`.

### 3.6 Quality composite: 10 points

Year weights: same period last year 50%, same period this year 50%, each with a maximum contribution of 2.5 points.

Weights within each year: single-quarter ROE 34%, single-quarter gross margin 33%, single-quarter net margin 33%.

- Wind `QFA_ROE`.
- Wind `QFA_GROSSPROFITMARGIN`.
- Wind `QFA_NETPROFITMARGIN`.
- Each of the three metrics is standardized (higher is better) separately for last year's and this year's same reporting period.
- Each year is combined 34:33:33, then the two years are combined 50% / 50%.
- If a metric is missing, its standardized score is 0 (not the neutral 0.5).

Formula: `annual quality S = S_ROE × 0.34 + S_gross margin × 0.33 + S_net margin × 0.33`.

`quality score = 5 × (quality S_last year × 0.50 + quality S_this year × 0.50)`.

The current code measures the combined level of single-quarter ROE, gross margin and net margin for the same period of last year and this year, using reporting periods with the same month and day in both years.

## 4. Total score

`total score = forecast growth score + PEG score + QoQ growth score + forecast change score + quality score`

The theoretical maximum is still 100 points; the forecast change factor can contribute negative points through downgrades. After the candidate pool and Wind data are refreshed at 21:00 each day, eligibility, cross-sectional standardization and the ranking are all recomputed.

## 5. XGBoost comparison model (experimental)

The XGBoost model uses the same 16 raw factor fields as the rule-based score, but no hand-set weights or piecewise rules: within each snapshot the features are winsorized at 3×MAD and rank-normalized, and a gradient-boosted tree model learns the relationship between the factors and the next-period (5 trading days) relative return rank. Training uses an expanding window with a 1-snapshot embargo, and all evaluation is out of sample. See the "XGBoost ranking model" section of `README.md` for details and `config/xgboost.json` for parameters.
