from pathlib import Path
from shutil import copyfile


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "output"
VIEW = ROOT / "查看结果"

FILES = {
    "dashboard.html": "多因子选股看板.html",
    "top5_backtest_2016_2026.html": "2016年至2026年TOP5累计回测.html",
    "top5_backtest_2016_2026_weekly.csv": "2016年至2026年TOP5累计回测.csv",
    "top10_backtest_2016_2026.html": "2016年至2026年TOP10累计回测.html",
    "top10_backtest_2016_2026_weekly.csv": "2016年至2026年TOP10累计回测.csv",
    "top5_backtest_2024.html": "2024年周频回测TOP5.html",
    "top5_backtest_2024_weekly.csv": "2024年周频回测TOP5.csv",
    "top5_backtest_2025.html": "2025年周频回测TOP5.html",
    "top5_backtest_2025_weekly.csv": "2025年周频回测TOP5.csv",
    "top10_backtest_2024.html": "2024年周频回测TOP10.html",
    "top10_backtest_2024_weekly.csv": "2024年周频回测TOP10.csv",
    "top10_backtest_2025.html": "2025年周频回测TOP10.html",
    "top10_backtest_2025_weekly.csv": "2025年周频回测TOP10.csv",
    "top20_frequency.html": "近6个月高频入选股票.html",
    "top20_frequency_6m.csv": "近6个月高频入选股票.csv",
    "top_picks.csv": "前10名.csv",
    "ranked_stocks.csv": "完整排名.csv",
    "candidate_pool.csv": "候选股票池.csv",
    "etf_holdings_pool.html": "ETF前十大重仓去重池.html",
    "etf_top_holdings_dedup.csv": "ETF前十大重仓去重池.csv",
    "factor_rank_analyst_profit_yoy.csv": "因子排名-分析师净利润同比.csv",
    "factor_rank_peg.csv": "因子排名-PEG.csv",
    "factor_rank_qoq_growth.csv": "因子排名-单季度净利润环比.csv",
    "factor_rank_consecutive_profit_growth_quarters.csv": "因子排名-连续增长季度数.csv",
    "factor_rank_analyst_revision.csv": "因子排名-分析师预测变化率.csv",
    "factor_rank_quality.csv": "因子排名-质量.csv",
    "top10_backtest.html": "2026年周频TOP10回测.html",
    "top10_backtest_weekly.csv": "2026年周频TOP10回测明细.csv",
    "top5_backtest.html": "2026年周频回测TOP5.html",
    "top5_backtest_weekly.csv": "2026年周频回测TOP5.csv",
    "frequency_top10_backtest.html": "近6个月高频前10名等权周频回测.html",
    "frequency_top10_backtest_weekly.csv": "近6个月高频前10名等权周频回测明细.csv",
}

for year in range(2016, 2024):
    FILES[f"top5_backtest_{year}.html"] = f"{year}年周频回测TOP5.html"
    FILES[f"top5_backtest_{year}_weekly.csv"] = f"{year}年周频回测TOP5.csv"
    FILES[f"top10_backtest_{year}.html"] = f"{year}年周频回测TOP10.html"
    FILES[f"top10_backtest_{year}_weekly.csv"] = f"{year}年周频回测TOP10.csv"


def safe_copy(source, target):
    try:
        copyfile(source, target)
        return True
    except PermissionError:
        print(f"跳过正在被占用的文件：{target}")
        return False


def main():
    VIEW.mkdir(parents=True, exist_ok=True)
    safe_copy(ROOT / "src" / "backtests.html", VIEW / "backtests.html")
    for source_name, target_name in FILES.items():
        source = SOURCE / source_name
        if source.exists():
            safe_copy(source, VIEW / target_name)
    # 保留旧文件名作为兼容别名，内容与当前Top10完全一致。
    if (SOURCE / "top_picks.csv").exists():
        safe_copy(SOURCE / "top_picks.csv", VIEW / "前20名.csv")
        safe_copy(SOURCE / "top_picks.csv", VIEW / "前10名.csv")
    if (SOURCE / "top20_frequency.html").exists():
        safe_copy(SOURCE / "top20_frequency.html", VIEW / "top20_frequency.html")
    if (SOURCE / "dashboard.html").exists():
        safe_copy(SOURCE / "dashboard.html", VIEW / "dashboard.html")
    if (SOURCE / "etf_holdings_pool.html").exists():
        safe_copy(SOURCE / "etf_holdings_pool.html", VIEW / "etf_holdings_pool.html")
    if (SOURCE / "top30_backtest.html").exists():
        safe_copy(SOURCE / "top30_backtest.html", VIEW / "top30_backtest.html")
    if (SOURCE / "top10_backtest.html").exists():
        safe_copy(SOURCE / "top10_backtest.html", VIEW / "top10_backtest.html")
    if (SOURCE / "top5_backtest.html").exists():
        safe_copy(SOURCE / "top5_backtest.html", VIEW / "top5_backtest.html")
    if (SOURCE / "frequency_top10_backtest.html").exists():
        safe_copy(SOURCE / "frequency_top10_backtest.html", VIEW / "frequency_top10_backtest.html")
    if (SOURCE / "frequency_top3_backtest.html").exists():
        safe_copy(SOURCE / "frequency_top3_backtest.html", VIEW / "frequency_top3_backtest.html")
    print(f"结果已同步到：{VIEW}")


if __name__ == "__main__":
    main()
