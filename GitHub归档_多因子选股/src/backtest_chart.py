def nav_chart(rows):
    width, height, left, right, top, bottom = 1180, 390, 64, 28, 30, 54
    strategy = [1.0] + [float(row["net_nav"]) for row in rows]
    benchmark = [1.0] + [float(row["benchmark_nav"]) for row in rows]
    dates = [rows[0]["buy_date"]] + [row["sell_date"] for row in rows]
    low, high = min(strategy + benchmark), max(strategy + benchmark)
    padding = max((high - low) * 0.08, 0.05)
    low, high = max(0, low - padding), high + padding
    plot_w, plot_h = width - left - right, height - top - bottom
    def points(series):
        return " ".join(f"{left+i*plot_w/(len(series)-1):.1f},{top+(high-v)*plot_h/(high-low):.1f}" for i, v in enumerate(series))
    grid = []
    for i in range(5):
        value, y = high-i*(high-low)/4, top+i*plot_h/4
        grid.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" stroke="#d9dfdc"/><text x="{left-10}" y="{y+5:.1f}" text-anchor="end" fill="#66706c" font-size="12">{value:.2f}</text>')
    sy = top+(high-strategy[-1])*plot_h/(high-low); by = top+(high-benchmark[-1])*plot_h/(high-low)
    return f'''<section class="chart-panel"><div class="chart-heading"><h2>累计净值曲线</h2><div class="legend"><span class="strategy-key">策略成本后净值</span><span class="benchmark-key">沪深300净值</span></div></div><div class="nav-chart"><svg viewBox="0 0 {width} {height}" role="img" aria-label="策略与沪深300累计净值对比">{''.join(grid)}<line x1="{left}" y1="{top+plot_h}" x2="{width-right}" y2="{top+plot_h}" stroke="#8d9692"/><polyline points="{points(benchmark)}" fill="none" stroke="#16727a" stroke-width="3" stroke-linejoin="round"/><polyline points="{points(strategy)}" fill="none" stroke="#c5162e" stroke-width="3.5" stroke-linejoin="round"/><circle cx="{width-right}" cy="{sy:.1f}" r="4" fill="#c5162e"/><circle cx="{width-right}" cy="{by:.1f}" r="4" fill="#16727a"/><text x="{width-right-8}" y="{sy-9:.1f}" text-anchor="end" fill="#c5162e" font-weight="700">策略 {strategy[-1]:.2f}</text><text x="{width-right-8}" y="{by+20:.1f}" text-anchor="end" fill="#16727a" font-weight="700">沪深300 {benchmark[-1]:.2f}</text><text x="{left}" y="{height-18}" fill="#66706c" font-size="12">{dates[0]}</text><text x="{width-right}" y="{height-18}" text-anchor="end" fill="#66706c" font-size="12">{dates[-1]}</text></svg></div></section>'''
