# ai-stock-Top-20

A multi-factor ranking model for China A-shares. It scores stocks on six factor groups, with single-quarter net profit QoQ growth at the core, plus analyst consensus growth, PEG, forecast revisions and profitability quality, and outputs a weekly Top ranking. An XGBoost cross-sectional model trained on the same factors is included and compared with the rule-based score through walk-forward out-of-sample tests.

Data comes from Wind (WindPy). Code and documentation are in [`multi_factor_strategy/`](multi_factor_strategy/).
