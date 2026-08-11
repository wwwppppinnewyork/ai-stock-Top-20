# GitHub Upload Guide

这个项目建议作为一个仓库整体上传，包含两部分：

1. `src/`, `config/`, `data/`, `scripts/`
   - 选股策略和周更脚本
2. `webapp/`
   - 公开网站源码

## 推荐上传方式

先生成一份干净副本：

```bash
zsh /Users/ping/Desktop/选股框架搭建/scripts/prepare_github_repo.sh
```

生成目录：

```text
/Users/ping/Desktop/选股框架搭建/github_ready/ai-stock-weekly
```

这份副本已经排除了：

- 本地 `.git`
- `node_modules`
- `.artifacts`
- `output/`
- `site/`
- `.DS_Store`

## 上传到 GitHub

进入生成后的目录：

```bash
cd /Users/ping/Desktop/选股框架搭建/github_ready/ai-stock-weekly
git init -b main
git add .
git commit -m "Initial commit"
git remote add origin <your-github-repo-url>
git push -u origin main
```

## 上传后别人如何使用

### 跑本地策略

```bash
python3 src/stock_workflow.py \
  --ai-universe data/sample_ai_theme.csv \
  --etf-holdings data/sample_etf_top_holdings.csv \
  --factor-data data/sample_factor_data.csv \
  --config config/weights.json \
  --top-n 20
```

### 生成看板

```bash
python3 src/render_dashboard.py
```

### 更新网站静态资源

```bash
zsh scripts/update_weekly_site.sh
```

### 启动网站开发环境

```bash
cd webapp
npm ci
npm run build
```
