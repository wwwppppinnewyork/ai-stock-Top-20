# Wind API 版 AI 选股 Workflow

本版本已将样例因子数据替换为 Windows 本机 WindPy 调用。运行前保持 Wind 金融终端已登录，然后执行：

```powershell
python run_wind_strategy.py
```

候选池为截至数据日的全部A股（含沪、深、北交所，不含三板），剔除ST、*ST及退市股票，随后运行六因子评分并输出排名。不设置年度涨幅门槛。

所有内容集中在本目录：`config/` 是权重和选股池映射，`src/` 是 Wind 接口、评分、XGBoost 和回测脚本，`data/wind/` 是逐步中间数据，`output/` 是最终结果。

## 选股池 Wind 映射

- 基础范围：Wind `全部A股`，并仅接受 `.SH`、`.SZ`、`.BJ` 代码，防御性排除三板及其他市场。
- 名称过滤：剔除ST、*ST、退市及退市整理股票。
- 年度涨幅仅作为审计字段，不参与股票池筛选。
- 历史回测：每个周信号日按当时的全部A股成分、当时名称和当时年度涨幅独立重建股票池。

## 数据口径

- 分析师盈利同比：`WEST_NETPROFIT_YOY`
- 分析师预测调整：FY1 净利润近 1 月变化减近 3 月变化
- ROE、毛利率、净利润同比：Wind 最新可用财务快照
- 季度趋势：`NETPROFIT_TTM` 的季度序列变化（当前 WindPy 环境中单季度净利润字段返回空值，因此使用 TTM 代理，并明确保留该口径）
- PEG：今年、明年、后年分别按对应年度PE÷对应年度预测净利润增长率计算并反向标准化，再按 `50% / 30% / 20%` 合成PEG得分。
- 分析师预测净利润同比：今年、明年、后年分别做横截面正向标准化，再按 `50% / 30% / 20%` 合成该因子得分。
- 分析师预测变化率：今年和明年分别计算“当前预测净利润增长率－2026-08-22预测净利润增长率”。绝对差值小于 `0.5个百分点` 时得0分；其余上修与下修分开做有符号标准化，正值得正分、负值得负分、无预测得0分，再按 `50% / 50%` 合成，该因子范围为 `-10分至+10分`。
- 去极值：所有连续型原始因子在标准化前统一按合格候选池横截面进行3倍MAD缩尾，边界为 `中位数 ± 3 × 1.4826 × MAD`；超出边界的值截至边界，缺失值保持缺失，MAD为0时不截断。
- 单季度净利润环比：满分48分；最近连续5个季度环比均严格增长（需要6个连续自然季度数据）时取得满分，否则横截面标准分乘 `0.80` 后再乘48分，非满分最高38.4分。
- 最近一期连续增长不中断季度数：满分1分；从最新一期向前统计严格增长次数，不做MAD去极值，直接正向min-max标准化。

当前合格股票池及年度涨幅审计字段写入 `data/wind/ai_theme.csv`（保留旧文件名以兼容现有流程）；排除原因写入 `data/wind/excluded_stocks.csv`。Wind 返回缺失值会写入 `data/wind/missing_data.csv`，不会静默伪造数据。

## 选股与调仓周期

每周最后一个交易日收盘后生成六因子信号，下一交易日调仓并持有至下一周信号。回测净值同样按周计算。

## 回测

- `src/backfill_top20_history.py`：从指定日期起按周回填历史 Top30 排名（每个信号日独立重建股票池并打分），写入 `data/top20_history.csv`。
- `src/backtest_top30.py`：每年 4 月 30 日、8 月 31 日、10 月 31 日三个时点调仓的回测。
- `src/backtest_weekly_curve.py`：同样三时点调仓、按周计算净值曲线，扣除双边 0.2% 成本，并与基准对比。
- `src/top20_history.py`：记录每周 Top30 快照，统计近 6 个月高频入选股票。

## 文件结构

```text
run_wind_strategy.py        主流程：股票池 → Wind 取数 → 六因子打分 → XGBoost（可选）→ 历史记录
config/                     六因子权重、股票池规则、XGBoost 参数
src/wind_universe.py        构建股票池
src/wind_data.py            从 Wind 获取因子数据
src/stock_workflow.py       六因子打分与排名
src/build_xgb_panel.py      XGBoost 训练面板
src/xgboost_model.py        XGBoost 模型与样本外评估
src/backfill_top20_history.py, backtest_*.py, top20_history.py   历史回填与回测
```

## XGBoost 机器学习打分（实验模块）

在六因子规则打分之外，新增一个用**同一套原始因子**训练的 XGBoost 横截面模型，用来检验：固定权重之外，非线性关系和因子交互能否带来额外的选股能力。规则打分仍是主输出，XGBoost 只作对照或可选融合。

文件：

- `src/build_xgb_panel.py`：`archive` 每周把 `data/wind/factor_data.csv` 和当周 `output/ranked_stocks.csv` 归档到 `data/history_factors/<交易日>/`（时点正确，只保存当时可得的数据）；`panel` 把所有快照拼成训练面板，并从 Wind 取下一交易日起 5 个交易日的前复权收益作为标签，写入 `data/xgb_panel.csv`。
- `src/xgboost_model.py`：`--mode backtest` 做滚动样本外评估；`--mode live` 用全部历史训练后给本周候选池打分。
- `config/xgboost.json`：特征列表、标签周期、滚动窗口、模型超参数、Top N、交易成本、是否与规则打分融合。
- `requirements-xgboost.txt`：额外依赖（xgboost、pandas、numpy、scipy）。

方法：

1. **特征**：16 个原始因子字段（分析师预测同比 FY1–FY3、PEG FY1–FY3、单季度净利润环比、连续增长季度数、预测变化率 FY1–FY2、上年/当年单季度 ROE、毛利率、净利率）。每期横截面内先做 3 倍 MAD 缩尾，再做排名标准化到 [0, 1]；负 PEG 视为缺失；缺失值保留，由 XGBoost 学习缺失方向。
2. **标签**：前瞻收益在当期横截面内的百分位排名，剔除市场整体涨跌，只学习选股能力。
3. **防泄漏**：滚动扩展窗口训练；每期只用该期之前、并隔离 1 期（避免收益窗口重叠）的数据；训练窗口最后 4 期用作早停验证集。
4. **评估**：样本外 Rank IC、ICIR、IC>0 比例、Top10 扣除双边 0.2% 成本后的收益，与同期规则打分和候选池等权对比；输出特征重要性（gain）。

运行（需要 Wind 终端已登录，并已积累至少约 30 周快照）：

```powershell
pip install -r requirements-xgboost.txt
python src/build_xgb_panel.py panel
python src/xgboost_model.py --mode backtest   # 输出 output/xgb_report.md 等
python src/xgboost_model.py --mode live       # 输出 output/xgb_ranked_stocks.csv
```

`run_wind_strategy.py` 每次运行会自动归档当周快照；若已存在训练面板，会顺带输出 XGBoost 排名。该步骤失败不会影响规则打分的主流程。

判断标准：只有当 XGBoost 的样本外 IC 和扣费后收益都稳定高于规则打分时才考虑替换或融合；否则说明增加的模型复杂度没有带来收益，应保留更简单的规则模型。
