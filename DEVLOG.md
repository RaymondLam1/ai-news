# AI News Daily — 开发日志

> 本文档记录了与 Claude Code 协作完成 AI News Daily 项目的完整过程，包含需求沟通、技术决策和最终结果。
>
> 日期：2026-04-15

---

## 项目概述

**目标**：构建一个个人用 AI 新闻聚合网站，每天自动展示：
- 最新 AI 动态（多源 RSS 聚合）
- GitHub 当日 AI 热门项目
- GitHub 微服务热门项目

最终通过 GitHub Actions + GitHub Pages 实现全自动每日更新，无需维护服务器。

---

## 一、项目初始化

**需求**：从零实现，每天展示最新 AI 动态和 GitHub 流行 AI 项目。

**技术选型决策**：
- 语言：Python（feedparser + requests + beautifulsoup4）
- 输出：静态 HTML，无需服务器
- 运行方式：每天执行一次脚本，覆盖生成 `index.html`

**数据来源**：

| 类型 | 来源 |
|------|------|
| AI 新闻 | The Verge · AI、VentureBeat、TechCrunch、MIT Tech Review、Hacker News（关键词过滤）、DeepMind Blog、OpenAI Blog、Anthropic News |
| GitHub AI 热门 | 抓取 `github.com/trending?since=daily` |

**初始文件结构**：
```
ai-news/
├── fetch.py        # 主脚本：抓取 → 生成 HTML
├── requirements.txt
└── index.html      # 生成产物
```

---

## 二、切换到 uv 管理依赖

**需求**：用 `uv` 替代 pip 管理 Python 依赖。

**执行**：
```bash
uv init --bare
uv add feedparser requests beautifulsoup4 lxml
```

删除 `requirements.txt`，改由 `pyproject.toml` + `uv.lock` 管理。

---

## 三、增加浅色风格 + 微服务项目栏

**需求**：
1. 整体改为浅色风格
2. 侧边栏增加"微服务热门项目"

**技术决策**：

*样式*：从暗色（gray-900 系）全面切换为浅色（slate-50 / white），使用 Tailwind CDN。

*微服务数据源*：GitHub Search API（无需 Token，匿名 60 次/小时）。

由于 GitHub Search API 不支持 `OR` 跨 qualifier 组合查询，改为**分 topic 多次请求后合并**：

```python
GITHUB_MS_TOPICS = [
    ("topic:microservices stars:>500",  10),
    ("topic:service-mesh stars:>100",    5),
    ("topic:api-gateway stars:>200",     5),
]
```

结果按 star 数排序，去重后取前 12 个。

---

## 四、增加中文新闻来源

**需求**：补充中文 AI 媒体来源。

**调研结果**（测试 RSS 可用性）：

| 来源 | RSS 状态 | 备注 |
|------|---------|------|
| 量子位 | ✅ 可用 | AI 专注，直接收录 |
| 36氪 | ✅ 可用 | 综合媒体，需关键词过滤 |
| 机器之心 | ❌ 空响应 | — |
| InfoQ 中文 | ❌ 空响应 | — |
| 新智元 | ❌ 空响应 | — |
| 极客公园 | ❌ 连接失败 | — |

**过滤策略**：36氪 采用**标题关键词过滤**（比全文过滤更精准，避免"AI时代"等泛用词误匹配）：

```python
"title_keywords": [
    "AI", "人工智能", "大模型", "LLM", "GPT", "Claude", "Gemini",
    "机器学习", "深度学习", "神经网络", "智能体", "Agent",
    "多模态", "生成式", "语言模型", "OpenAI", "Anthropic",
]
```

---

## 五、重新设计为 Spring 官网风格

**需求**：参考 spring.io 的视觉风格重新设计页面。

**Spring 风格核心元素还原**：

| 元素 | 实现 |
|------|------|
| Header | 深色背景 `#1E1E1E`，底部 3px Spring 绿色线 `#6DB33F`，树叶 SVG logo |
| 主色调 | Spring 绿 `#6DB33F`，hover 交互色 `#f4faf0` |
| 页面背景 | 浅灰 `#F5F5F5`，卡片纯白 `#FFFFFF` |
| 新闻卡片 | `border-left: 3px solid transparent`，hover 时变绿 + 轻微右移 |
| 侧边 panel | 头部 `#FAFAFA`，列表项 hover 变浅绿 |
| 排版 | 全大写小字 section 标题，系统 sans-serif |
| Footer | 深色背景与 header 呼应，Spring 绿品牌名 |

**重要决策**：放弃 Tailwind CDN，改用原生 CSS + CSS 变量，精确控制每个细节：

```css
:root {
  --green:      #6DB33F;
  --green-dark: #4e8a2a;
  --dark:       #1E1E1E;
  --bg:         #F5F5F5;
  --card:       #FFFFFF;
  --border:     #E5E5E5;
}
```

---

## 六、每日 Markdown 报告 + GitHub 项目去重

**需求**：
1. 每天生成 Markdown 格式总结报告，按 `reports/YYYY/MM/YYYY-MM-DD.md` 存放
2. GitHub 热门项目跨天去重，避免每天看到相同内容

**Markdown 报告格式**：

```
# 2026-04-15 AI 动态日报

> 生成时间 | 文章数 | 项目数

## 📰 AI 新闻
### 1. [标题](url)
**来源**：xxx  **发布**：2h ago
> 摘要...

## 🤖 GitHub AI 热门项目（今日）
| 项目 | 语言 | ⭐ Stars | 简介 |
...

## 🔧 微服务热门项目
...
```

**去重逻辑**（`data/seen_repos.json`）：

```python
# 结构：{"owner/repo": "YYYY-MM-DD"}
# 规则：跳过在"之前某天"出现过且在 30 天窗口内的 repo
#       今天已见过的 → 保留（同天多次运行安全）
if cutoff <= last_seen < today:
    continue  # 过滤
```

关键细节：`last_seen < today`（严格小于）确保当天重复运行不会把自己过滤掉。

---

## 七、一键运行脚本

```bash
# run.sh
#!/bin/bash
set -e
uv run python fetch.py
open index.html
```

使用方式：
```bash
./run.sh
```

---

## 八、GitHub Actions + GitHub Pages 自动化

**需求**：每天凌晨（北京时间 09:00）自动更新，通过 GitHub Pages 访问。

**Workflow 设计**（`.github/workflows/daily-update.yml`）：

```yaml
on:
  schedule:
    - cron: '0 1 * * *'   # 01:00 UTC = 09:00 北京时间
  workflow_dispatch:        # 支持手动触发

permissions:
  contents: write           # 允许 commit & push 回仓库
```

**运行流程**：
```
每天 09:00 北京时间
  └── GitHub Actions 触发
        ├── actions/checkout@v4
        ├── astral-sh/setup-uv@v5
        ├── uv sync
        ├── uv run python fetch.py
        │     ├── 抓取 RSS / GitHub Trending / GitHub Search API
        │     ├── 去重过滤（对比 data/seen_repos.json）
        │     ├── 生成 index.html
        │     └── 生成 reports/YYYY/MM/YYYY-MM-DD.md
        ├── git add index.html reports/ data/
        ├── git commit（有变更才提交）
        └── git push → GitHub Pages 自动刷新
```

**GitHub Pages 配置**：
- Settings → Pages → Deploy from branch → `main` → `/ (root)`
- 访问：`https://<用户名>.github.io/ai-news/`

---

## 最终文件结构

```
ai-news/
├── .github/
│   └── workflows/
│       └── daily-update.yml   # GitHub Actions 定时任务
├── data/
│   └── seen_repos.json        # GitHub 项目去重记录（自动维护）
├── reports/
│   └── YYYY/
│       └── MM/
│           └── YYYY-MM-DD.md  # 每日 Markdown 报告
├── fetch.py                   # 核心脚本
├── run.sh                     # 一键本地运行
├── index.html                 # 生成的静态网页
├── pyproject.toml             # uv 依赖配置
├── uv.lock
├── .gitignore
└── DEVLOG.md                  # 本文档
```

---

## 技术栈汇总

| 层次 | 技术 |
|------|------|
| 语言 | Python 3.14 |
| 依赖管理 | uv |
| 数据抓取 | feedparser、requests、beautifulsoup4、lxml |
| 前端 | 原生 HTML + CSS（Spring 风格，无框架依赖） |
| 自动化 | GitHub Actions（cron 定时） |
| 托管 | GitHub Pages（静态托管，免费） |
| 持久化 | JSON 文件（去重记录）+ Markdown 文件（历史报告） |
