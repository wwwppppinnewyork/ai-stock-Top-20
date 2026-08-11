# AI选股 Workflow

这个仓库提供一套可执行的 A 股选股 workflow 骨架，分成两段：

1. 候选池生成
   - `AI 相关赛道股票`（半导体，计算机，通讯）
   - `ETF（A股） 今年涨幅前 50 名`对应的`每日前十重仓股`
   - 两部分合并后去重，得到几百只候选股票
2. 多因子打分与滚动排名
   - 按照你的 5 个因子和权重进行归一化打分
   - 每周更新一次数据并重算总分
   - 输出前 `20` 只

## 目录

- `src/stock_workflow.py`
  - 主流程脚本
- `config/weights.json`
  - 权重与参数配置
- `data/sample_ai_theme.csv`
  - AI 赛道样例股票池
- `data/sample_etf_top_holdings.csv`
  - ETF 盈利前 50 的前十大持仓样例
- `data/sample_factor_data.csv`
  - 因子样例数据
- `output/`
  - 运行后输出目录

## 评分规则

总分 = 100 分，按以下权重：

1. 分析师预测盈利同比增速：`15%`
2. PEG：`20%`
   - PEG 越小越好
3. 环比增速：`50%`
   - 这里的环比增速，固定定义为 `净利润环比增速`
   - 常规情况下按数值归一化评分
   - 若 `Q1 基本面加速`，该因子至少拿到 `50% * 90%`
   - 若 `1/2/3/4/1` 五个季度的`净利润环比增速`都大于 `0`，直接拿到 `50% * 100%`
4. 每次财报期分析师预测更新变化率：`10%`
   - 这里只看 `分析师净利润预测上修`
   - 预期向上越多越好
5. ROE / 毛利率 / 净利润同比：`5%`
   - 三项同比改善越明显越好

## 标准化规则

这套模型里，连续因子不会直接用原始值打分，而是先做横截面标准化，再乘权重。当前建议的调仓/排名频率是 `周频`。

当前脚本采用的是 `min-max` 标准化：

```text
score = (x - min) / (max - min)
```

其中：

- `x` 是某只股票当前因子的原始值
- `min` 和 `max` 是当天候选池内该因子的最小值和最大值
- 标准化后分数落在 `0-1`
- PEG 属于越小越好，所以会做反向处理

适用到当前五因子时，口径是：

- `analyst_profit_yoy`：先标准化，再乘 `15%`
- `peg`：先反向标准化，再乘 `20%`
- `net_profit_qoq_growth`：先标准化，再乘 `50%`
- `analyst_net_profit_revision_change`：先标准化，再乘 `10%`
- `roe_yoy / gross_margin_yoy / net_profit_yoy`：分别标准化后合成质量分，再乘 `5%`

## 增速分差示例

假设当天候选池里，环比增速最差是 `-10%`，最好是 `70%`。

- 股票 A：环比增速 `60%`
- 股票 B：环比增速 `20%`

标准化后：

```text
A = (60 - (-10)) / (70 - (-10)) = 0.875
B = (20 - (-10)) / (70 - (-10)) = 0.375
```

两者在环比增速因子上的分差是：

```text
0.875 - 0.375 = 0.5
```

由于环比增速权重是 `50%`，所以对应到总分里的差距是：

```text
0.5 * 50 = 25分
```

也就是说，`60%` 和 `20%` 的原始增速不会固定差多少分，分差取决于它们在当天候选池中的相对位置。

## 当前版本的处理方式

当前脚本已经实现的是：

```text
原始值 -> min-max标准化 -> 乘权重 -> 合成总分
```

建议的实际运行节奏是：

```text
每周更新候选池和因子数据 -> 重新排名 -> 输出前20只
```

后续如果你要提高稳健性，可以升级成：

```text
原始值 -> 去极值 -> 标准化或分位数打分 -> 乘权重 -> 合成总分
```

## 输入数据格式

### 1. AI赛道股票池

`data/sample_ai_theme.csv`

- `ts_code`: 股票代码
- `name`: 股票名称
- `theme`: 赛道标签

### 2. ETF前十持仓池

`data/sample_etf_top_holdings.csv`

- `trade_date`: 日期
- `etf_code`: ETF代码
- `etf_rank_50`: ETF 在盈利前 50 中的名次
- `holding_rank_10`: 该 ETF 的前十大持仓名次
- `ts_code`: 成分股代码
- `name`: 股票名称

### 3. 因子数据

`data/sample_factor_data.csv`

- `ts_code`
- `name`
- `analyst_profit_yoy`
- `peg`
- `net_profit_qoq_growth`
- `q1_acceleration_flag`
- `quarter_cycle_positive_flag`
  - 定义：`1/2/3/4/1` 五个季度的净利润环比增速都大于 `0`
- `analyst_net_profit_revision_change`
- `roe_yoy`
- `gross_margin_yoy`
- `net_profit_yoy`

## 运行

```bash
python3 src/stock_workflow.py \
  --ai-universe data/sample_ai_theme.csv \
  --etf-holdings data/sample_etf_top_holdings.csv \
  --factor-data data/sample_factor_data.csv \
  --config config/weights.json \
  --top-n 20
```

## 输出

- `output/candidate_pool.csv`
  - 去重后的候选池
- `output/ranked_stocks.csv`
  - 因子总分排名结果
- `output/top_picks.csv`
  - 最终前 `20` 只
- `output/dashboard.html`
  - 本地可视化网页
- `site/index.html`
  - 可发布的静态网站首页

## 周更网站

这套现在最适合做成 `静态网站 + 周更自动任务`。

### 网站生成流程

```text
每周更新数据 -> 重新跑选股脚本 -> 生成 dashboard.html -> 构建 site/index.html
```

### 一键更新网站

```bash
zsh /Users/ping/Desktop/选股框架搭建/scripts/update_weekly_site.sh
```

执行后会更新：

- `output/dashboard.html`
- `site/index.html`
- `site/candidate_pool.csv`
- `site/ranked_stocks.csv`
- `site/top_picks.csv`

### 本地预览网站

```bash
cd /Users/ping/Desktop/选股框架搭建/site
python3 -m http.server 8000
```

然后浏览器打开：

```text
http://127.0.0.1:8000
```

### Mac 周更自动任务

已经提供了 `launchd` 模板：

- `deploy/com.ping.stock-dashboard.weekly.plist`

默认设置是 `每周日 09:00` 自动执行一次。

安装方式：

```bash
mkdir -p ~/Library/LaunchAgents
cp /Users/ping/Desktop/选股框架搭建/deploy/com.ping.stock-dashboard.weekly.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.ping.stock-dashboard.weekly.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.ping.stock-dashboard.weekly.plist
```

查看是否加载成功：

```bash
launchctl list | grep stock-dashboard
```

手动触发一次：

```bash
launchctl kickstart -k gui/$(id -u)/com.ping.stock-dashboard.weekly
```

日志输出在：

- `output/launchd.log`
- `output/launchd.err.log`

## 后续接真实数据

现在这版先把 workflow 和规则固化下来。你下一步只需要把真实数据源接进来，例如：

- AI赛道股票池：Wind / 同花顺 / Tushare 概念板块
- ETF盈利前50：ETF日收益排名结果
- ETF前十持仓：ETF持仓披露
- 分析师预期与预测变动：Wind一致预期、卖方预期明细
- 财务同比与环比：半年报、季报、年报字段

如果你要，我下一步可以继续把这套骨架直接接成：

1. `Tushare` 版本
2. `Wind` 版本
3. `Excel/CSV 人工维护 + 自动打分` 版本

## GitHub 整理

这个项目已经整理成适合放到 GitHub 的结构：

- `src/`：策略脚本、网页生成脚本、站点同步脚本
- `config/`：权重配置
- `data/`：样例输入
- `scripts/`：周更与 GitHub 整理脚本
- `webapp/`：可公开部署的网站源码

如果你要上传到 GitHub，建议先生成干净副本：

```bash
zsh /Users/ping/Desktop/选股框架搭建/scripts/prepare_github_repo.sh
```

详细说明见：

- [GITHUB_UPLOAD.md](/Users/ping/Desktop/选股框架搭建/GITHUB_UPLOAD.md)
