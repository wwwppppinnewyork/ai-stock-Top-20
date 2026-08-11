export default function Home() {
  return (
    <main className="site-shell">
      <section className="hero">
        <div className="hero-eyebrow">AI Stock Dashboard</div>
        <h1>AI 选股周频网站</h1>
        <p className="hero-copy">
          这个站点按周更新，展示 AI 赛道与 A 股 ETF 候选池筛选后的多因子排名结果，
          包括前 20 名、核心因子分数，以及最近 20 个季度净利润柱状图。
        </p>
        <div className="hero-links">
          <a href="/dashboard.html" target="_blank" rel="noreferrer">
            单独打开完整看板
          </a>
          <a href="/top_picks.csv" target="_blank" rel="noreferrer">
            下载本周前 20 名 CSV
          </a>
          <a href="/ranked_stocks.csv" target="_blank" rel="noreferrer">
            下载完整排名 CSV
          </a>
        </div>
      </section>

      <section className="viewer">
        <div className="viewer-head">
          <div>
            <h2>在线预览</h2>
            <p>下方直接嵌入本周看板。每次周更后，同一个网址会自动替换成最新版本。</p>
          </div>
        </div>
        <div className="frame-wrap">
          <iframe
            title="AI选股周频看板"
            src="/dashboard.html"
            className="dashboard-frame"
          />
        </div>
      </section>
    </main>
  );
}
