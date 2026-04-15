#!/usr/bin/env python3
"""
AI News Daily — Personal AI news aggregator
Fetches AI news from RSS feeds, GitHub AI trending, and hot microservices repos.
"""

import feedparser
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from pathlib import Path
import re
import sys

# ── Configuration ─────────────────────────────────────────────────────────────

RSS_FEEDS = [
    # ── English sources ────────────────────────────────────────────────────────
    {"name": "The Verge · AI",       "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"},
    {"name": "VentureBeat · AI",     "url": "https://venturebeat.com/category/ai/feed/"},
    {"name": "TechCrunch · AI",      "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
    {"name": "MIT Tech Review",      "url": "https://www.technologyreview.com/feed/"},
    {"name": "Hacker News · AI",     "url": "https://hnrss.org/frontpage?q=AI+OR+LLM+OR+GPT+OR+Claude+OR+Gemini+OR+%22machine+learning%22&count=15"},
    {"name": "DeepMind Blog",        "url": "https://deepmind.google/blog/rss.xml"},
    {"name": "OpenAI Blog",          "url": "https://openai.com/news/rss/"},
    {"name": "Anthropic News",       "url": "https://www.anthropic.com/rss.xml"},
    # ── Chinese sources ────────────────────────────────────────────────────────
    {"name": "量子位",               "url": "https://www.qbitai.com/feed"},
    {
        "name": "36氪 · AI",
        "url": "https://36kr.com/feed",
        # 36氪 is general tech; match against title only to avoid false positives
        "title_keywords": [
            "AI", "人工智能", "大模型", "LLM", "GPT", "Claude", "Gemini",
            "机器学习", "深度学习", "神经网络", "智能体", "Agent",
            "多模态", "生成式", "语言模型", "OpenAI", "Anthropic",
        ],
    },
]

MAX_NEWS_PER_FEED   = 5
MAX_NEWS_TOTAL      = 30
MAX_AI_REPOS        = 12   # GitHub Trending (today)
MAX_MS_REPOS        = 12   # Microservices search results
NEWS_MAX_AGE_DAYS   = 3

GITHUB_TRENDING_URL = "https://github.com/trending?since=daily"
# GitHub Search API — recently active microservices repos with ≥500 stars
GITHUB_SEARCH_BASE = "https://api.github.com/search/repositories"
# Separate queries per topic to avoid GitHub API OR+qualifier limitation
GITHUB_MS_TOPICS = [
    ("topic:microservices stars:>500",  10),
    ("topic:service-mesh stars:>100",    5),
    ("topic:api-gateway stars:>200",     5),
]

OUTPUT_FILE    = Path(__file__).parent / "index.html"
REPORTS_DIR    = Path(__file__).parent / "reports"
SEEN_REPOS_FILE = Path(__file__).parent / "data" / "seen_repos.json"
DEDUP_DAYS     = 30   # suppress repos seen within the last N days

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Fetching ──────────────────────────────────────────────────────────────────

def fetch_news() -> list[dict]:
    articles: list[dict] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=NEWS_MAX_AGE_DAYS)

    for feed_info in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            count = 0
            for entry in feed.entries:
                if count >= MAX_NEWS_PER_FEED:
                    break
                pub = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    except Exception:
                        pass
                if pub and pub < cutoff:
                    continue

                # Keyword filter — title_keywords checks title only (stricter)
                title_kws = feed_info.get("title_keywords")
                if title_kws:
                    title = entry.get("title", "")
                    if not any(kw.lower() in title.lower() for kw in title_kws):
                        continue

                summary = ""
                if entry.get("summary"):
                    raw = entry["summary"]
                    summary = re.sub(r"<[^>]+>", "", raw).strip()
                    summary = re.sub(r"\s+", " ", summary)[:220]

                articles.append({
                    "title":     entry.get("title", "").strip(),
                    "url":       entry.get("link", ""),
                    "source":    feed_info["name"],
                    "published": pub,
                    "summary":   summary,
                })
                count += 1
        except Exception as exc:
            print(f"  [WARN] {feed_info['name']}: {exc}", file=sys.stderr)

    articles.sort(
        key=lambda a: a["published"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return articles[:MAX_NEWS_TOTAL]


def _parse_trending_page(html: str, limit: int) -> list[dict]:
    """Parse a github.com/trending HTML page into repo dicts."""
    soup = BeautifulSoup(html, "html.parser")
    repos: list[dict] = []

    for article in soup.select("article.Box-row")[:limit]:
        h2 = article.select_one("h2 a")
        if not h2:
            continue
        repo_path = h2.get("href", "").strip("/")
        parts = repo_path.split("/")
        if len(parts) != 2:
            continue
        owner, name = parts

        desc_el = article.select_one("p")
        description = desc_el.get_text(strip=True) if desc_el else ""

        stars_el = article.select_one(f"a[href='/{repo_path}/stargazers']")
        stars = stars_el.get_text(strip=True).replace(",", "") if stars_el else "0"

        today_el = article.select_one("span.d-inline-block.float-sm-right")
        stars_today = today_el.get_text(strip=True) if today_el else ""

        lang_el = article.select_one("span[itemprop='programmingLanguage']")
        language = lang_el.get_text(strip=True) if lang_el else ""

        repos.append({
            "owner": owner, "name": name,
            "url": f"https://github.com/{repo_path}",
            "description": description,
            "stars": stars, "stars_today": stars_today,
            "language": language,
        })
    return repos


def fetch_github_ai_trending() -> list[dict]:
    try:
        resp = requests.get(GITHUB_TRENDING_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return _parse_trending_page(resp.text, MAX_AI_REPOS)
    except Exception as exc:
        print(f"  [WARN] GitHub AI trending: {exc}", file=sys.stderr)
        return []


def fetch_github_microservices() -> list[dict]:
    """Query GitHub Search API per topic and merge, sorted by stars."""
    api_headers = {**HEADERS, "Accept": "application/vnd.github+json"}
    seen: set[str] = set()
    all_items: list[dict] = []

    for query, per_page in GITHUB_MS_TOPICS:
        try:
            resp = requests.get(
                GITHUB_SEARCH_BASE,
                params={"q": query, "sort": "stars", "order": "desc", "per_page": per_page},
                headers=api_headers,
                timeout=15,
            )
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                full = item.get("full_name", "")
                if full not in seen:
                    seen.add(full)
                    all_items.append(item)
        except Exception as exc:
            print(f"  [WARN] GitHub search ({query[:40]}): {exc}", file=sys.stderr)

    # Sort by star count descending and format
    all_items.sort(key=lambda x: x.get("stargazers_count", 0), reverse=True)
    repos: list[dict] = []
    for item in all_items[:MAX_MS_REPOS]:
        full = item.get("full_name", "")
        owner, _, name = full.partition("/")
        stars = item.get("stargazers_count", 0)
        stars_fmt = f"{stars / 1000:.1f}k" if stars >= 1000 else str(stars)
        repos.append({
            "owner": owner, "name": name,
            "url": item.get("html_url", ""),
            "description": (item.get("description") or "")[:160],
            "stars": stars_fmt, "stars_today": "",
            "language": item.get("language") or "",
        })
    return repos


# ── HTML helpers ──────────────────────────────────────────────────────────────

LANG_COLORS: dict[str, str] = {
    "Python":           "#3572A5",
    "JavaScript":       "#f6c90e",
    "TypeScript":       "#2b7489",
    "Rust":             "#dea584",
    "Go":               "#00ADD8",
    "C++":              "#f34b7d",
    "C":                "#555555",
    "Java":             "#b07219",
    "Kotlin":           "#A97BFF",
    "Ruby":             "#701516",
    "Swift":            "#F05138",
    "Shell":            "#4CAF50",
    "Jupyter Notebook": "#DA5B0B",
    "CUDA":             "#3A4E3A",
    "Scala":            "#c22d40",
    "Dockerfile":       "#384d54",
}

SOURCE_COLORS: dict[str, str] = {
    "The Verge · AI":   "#FA4550",
    "VentureBeat · AI": "#6D46E8",
    "TechCrunch · AI":  "#0FA0CE",
    "MIT Tech Review":  "#E81224",
    "Hacker News · AI": "#FF6600",
    "DeepMind Blog":    "#4285F4",
    "OpenAI Blog":      "#10A37F",
    "Anthropic News":   "#C96830",
    "量子位":           "#1677FF",
    "36氪 · AI":        "#00C48C",
}


def fmt_date(dt: datetime | None) -> str:
    if dt is None:
        return ""
    diff = datetime.now(timezone.utc) - dt
    if diff.total_seconds() < 3600:
        return f"{int(diff.total_seconds() / 60)}m ago"
    if diff.days == 0:
        return f"{int(diff.total_seconds() / 3600)}h ago"
    if diff.days == 1:
        return "yesterday"
    return f"{diff.days}d ago"


def _repo_item(r: dict) -> str:
    lang_color = LANG_COLORS.get(r["language"], "#aaa")
    lang_html = (
        f'<span class="lang-dot"><span class="lang-circle" style="background:{lang_color}"></span>'
        f'{r["language"]}</span>'
    ) if r["language"] else ""
    stars_today_html = (
        f'<span class="stars-today">▲ {r["stars_today"]}</span>'
    ) if r["stars_today"] else ""

    star_svg = '<svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><path d="M8 .25a.75.75 0 01.673.418l1.882 3.815 4.21.612a.75.75 0 01.416 1.279l-3.046 2.97.719 4.192a.75.75 0 01-1.088.791L8 12.347l-3.766 1.98a.75.75 0 01-1.088-.79l.72-4.194L.818 6.374a.75.75 0 01.416-1.28l4.21-.611L7.327.668A.75.75 0 018 .25z"/></svg>'

    return f"""<a href="{r['url']}" target="_blank" rel="noopener noreferrer" class="repo-item">
      <div class="repo-name">
        <span class="repo-owner">{r['owner']}/</span>{r['name']}
      </div>
      {f'<div class="repo-desc">{r["description"]}</div>' if r["description"] else ""}
      <div class="repo-meta">
        {lang_html}
        <span class="stars">{star_svg}{r['stars']}</span>
        {stars_today_html}
      </div>
    </a>"""


# ── HTML generation ───────────────────────────────────────────────────────────

# Spring leaf SVG logo (simplified)
SPRING_LEAF_SVG = """<svg width="26" height="26" viewBox="0 0 50 50" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M42 6C42 6 28 4 18 16C10 26 12 40 12 40C12 40 14 34 22 30C18 38 20 44 20 44
           C20 44 30 42 36 32C44 20 42 6 42 6Z" fill="#6DB33F"/>
  <path d="M12 40C12 40 8 44 6 46" stroke="#6DB33F" stroke-width="3" stroke-linecap="round"/>
</svg>"""

GITHUB_SVG = '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/></svg>'


def generate_html(
    articles: list[dict],
    ai_repos: list[dict],
    ms_repos: list[dict],
) -> str:
    now_str   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    today_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    # ── News cards ──
    if not articles:
        news_html = "<p style='color:#999;font-size:13px'>No articles fetched.</p>"
    else:
        cards = []
        for a in articles:
            color    = SOURCE_COLORS.get(a["source"], "#6B7280")
            date_str = fmt_date(a["published"])
            date_html = f'<span class="news-date">{date_str}</span>' if date_str else ""
            summary_html = (
                f'<div class="news-summary">{a["summary"]}</div>'
                if a["summary"] else ""
            )
            cards.append(f"""<a href="{a['url']}" target="_blank" rel="noopener noreferrer" class="news-card">
          <div class="news-meta">
            <span class="source-badge" style="background:{color}1A;color:{color};border:1px solid {color}33">{a['source']}</span>
            {date_html}
          </div>
          <div class="news-title">{a['title']}</div>
          {summary_html}
        </a>""")
        news_html = "\n".join(cards)

    # ── Repo items ──
    ai_repos_html  = "\n".join(_repo_item(r) for r in ai_repos)  if ai_repos  else "<p style='color:#999;padding:12px 16px;font-size:12px'>Could not fetch.</p>"
    ms_repos_html  = "\n".join(_repo_item(r) for r in ms_repos)  if ms_repos  else "<p style='color:#999;padding:12px 16px;font-size:12px'>Could not fetch.</p>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI News Daily · {today_str}</title>
  <style>
    :root {{
      --green:        #6DB33F;
      --green-dark:   #4e8a2a;
      --green-hover:  #f4faf0;
      --dark:         #1E1E1E;
      --dark-2:       #2d2d2d;
      --bg:           #F5F5F5;
      --card:         #FFFFFF;
      --border:       #E5E5E5;
      --border-light: #F0F0F0;
      --text:         #2D2D2D;
      --text-2:       #5a5a5a;
      --text-muted:   #999999;
      --shadow:       0 1px 3px rgba(0,0,0,.06), 0 0 0 1px rgba(0,0,0,.04);
      --shadow-hover: 0 3px 12px rgba(0,0,0,.10), 0 0 0 1px rgba(109,179,63,.18);
    }}
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      background: var(--bg); color: var(--text);
      font-size: 14px; line-height: 1.5; min-height: 100vh;
    }}

    /* ── Header ── */
    .site-header {{
      background: var(--dark);
      border-bottom: 3px solid var(--green);
      position: sticky; top: 0; z-index: 100;
    }}
    .header-inner {{
      max-width: 1200px; margin: 0 auto; padding: 0 28px;
      height: 58px; display: flex; align-items: center; justify-content: space-between;
    }}
    .logo {{ display: flex; align-items: center; gap: 10px; text-decoration: none; }}
    .logo-text {{ color: #fff; font-size: 17px; font-weight: 700; letter-spacing: -.3px; }}
    .logo-text em {{ color: var(--green); font-style: normal; }}
    .header-right {{ display: flex; align-items: center; gap: 16px; }}
    .header-date {{ color: #888; font-size: 12px; }}
    .btn-refresh {{
      background: transparent; border: 1px solid #444; color: #bbb;
      padding: 5px 14px; border-radius: 3px; cursor: pointer; font-size: 12px;
      transition: border-color .15s, color .15s;
    }}
    .btn-refresh:hover {{ border-color: var(--green); color: var(--green); }}

    /* ── Stats bar ── */
    .stats-bar {{
      background: #fff; border-bottom: 1px solid var(--border);
    }}
    .stats-inner {{
      max-width: 1200px; margin: 0 auto; padding: 7px 28px;
      display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-muted);
    }}
    .stat {{ display: flex; align-items: center; gap: 4px; }}
    .stat-num {{ font-weight: 600; color: var(--text-2); }}
    .stat-sep {{ color: #ddd; margin: 0 6px; }}

    /* ── Main layout ── */
    .site-main {{
      max-width: 1200px; margin: 0 auto; padding: 28px 28px 60px;
      display: grid; grid-template-columns: 1fr 320px; gap: 28px;
    }}
    @media (max-width: 880px) {{ .site-main {{ grid-template-columns: 1fr; }} }}

    /* ── Section header ── */
    .sec-head {{
      display: flex; align-items: center; gap: 9px;
      margin-bottom: 14px; padding-bottom: 10px;
      border-bottom: 2px solid var(--border);
    }}
    .sec-head h2 {{
      font-size: 11px; font-weight: 700; text-transform: uppercase;
      letter-spacing: 1.1px; color: var(--text-2);
    }}
    .sec-icon {{ color: var(--green); display: flex; align-items: center; }}
    .sec-meta {{ margin-left: auto; font-size: 11px; color: var(--text-muted); }}
    .sec-count {{
      background: var(--green); color: #fff;
      font-size: 10px; font-weight: 600; padding: 1px 7px; border-radius: 10px;
    }}

    /* ── News card ── */
    .news-list {{ display: flex; flex-direction: column; gap: 2px; }}
    .news-card {{
      display: block; text-decoration: none;
      background: var(--card); border: 1px solid var(--border);
      border-left: 3px solid transparent;
      border-radius: 4px; padding: 13px 16px;
      box-shadow: var(--shadow);
      transition: border-left-color .15s, box-shadow .15s, transform .15s;
    }}
    .news-card:hover {{
      border-left-color: var(--green);
      box-shadow: var(--shadow-hover);
      transform: translateX(2px);
    }}
    .news-meta {{
      display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap;
    }}
    .source-badge {{
      font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 3px;
      text-transform: uppercase; letter-spacing: .5px; white-space: nowrap;
    }}
    .news-date {{ font-size: 11px; color: var(--text-muted); }}
    .news-title {{
      font-size: 13px; font-weight: 500; color: var(--text); line-height: 1.45;
      display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
    }}
    .news-card:hover .news-title {{ color: var(--green-dark); }}
    .news-summary {{
      font-size: 11.5px; color: var(--text-2); margin-top: 5px; line-height: 1.5;
      display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
    }}

    /* ── Sidebar panels ── */
    .sidebar {{ display: flex; flex-direction: column; gap: 28px; }}
    .panel {{
      background: var(--card); border: 1px solid var(--border);
      border-radius: 4px; overflow: hidden; box-shadow: var(--shadow);
    }}
    .panel-head {{
      padding: 11px 16px; background: #FAFAFA; border-bottom: 1px solid var(--border);
      display: flex; align-items: center; gap: 8px;
    }}
    .panel-title {{
      font-size: 11px; font-weight: 700; text-transform: uppercase;
      letter-spacing: 1px; color: var(--text-2);
    }}
    .panel-icon {{ color: var(--green); display: flex; align-items: center; }}
    .panel-meta {{ margin-left: auto; font-size: 10px; color: var(--text-muted); }}

    /* ── Repo items ── */
    .repo-item {{
      display: block; text-decoration: none;
      padding: 10px 16px; border-bottom: 1px solid var(--border-light);
      transition: background .1s;
    }}
    .repo-item:last-child {{ border-bottom: none; }}
    .repo-item:hover {{ background: var(--green-hover); }}
    .repo-name {{
      font-size: 12px; font-weight: 600; color: var(--text);
      margin-bottom: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }}
    .repo-item:hover .repo-name {{ color: var(--green-dark); }}
    .repo-owner {{ font-weight: 400; color: var(--text-muted); }}
    .repo-desc {{
      font-size: 11px; color: var(--text-2); line-height: 1.45; margin-bottom: 6px;
      display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
    }}
    .repo-meta {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
    .lang-dot {{ display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: var(--text-muted); }}
    .lang-circle {{ width: 9px; height: 9px; border-radius: 50%; display: inline-block; flex-shrink: 0; }}
    .stars {{ display: inline-flex; align-items: center; gap: 3px; font-size: 11px; color: var(--text-muted); }}
    .stars-today {{ font-size: 11px; color: #c47f00; font-weight: 600; }}

    /* ── Footer ── */
    .site-footer {{
      background: var(--dark); border-top: 2px solid #333;
      padding: 16px 28px;
    }}
    .footer-inner {{
      max-width: 1200px; margin: 0 auto;
      display: flex; justify-content: space-between; align-items: center;
      font-size: 11px; color: #666;
    }}
    .footer-brand {{ color: var(--green); font-weight: 600; }}

    ::-webkit-scrollbar {{ width: 5px; }}
    ::-webkit-scrollbar-track {{ background: #eee; }}
    ::-webkit-scrollbar-thumb {{ background: #ccc; border-radius: 3px; }}
    ::-webkit-scrollbar-thumb:hover {{ background: var(--green); }}
  </style>
</head>
<body>

  <!-- Header -->
  <header class="site-header">
    <div class="header-inner">
      <a class="logo" href="#">
        {SPRING_LEAF_SVG}
        <span class="logo-text">AI <em>News</em> Daily</span>
      </a>
      <div class="header-right">
        <span class="header-date">{today_str}</span>
        <button class="btn-refresh" onclick="location.reload()">↻ Refresh</button>
      </div>
    </div>
  </header>

  <!-- Stats bar -->
  <div class="stats-bar">
    <div class="stats-inner">
      <span class="stat"><span class="stat-num">{len(articles)}</span>&nbsp;articles</span>
      <span class="stat-sep">·</span>
      <span class="stat"><span class="stat-num">{len(RSS_FEEDS)}</span>&nbsp;sources</span>
      <span class="stat-sep">·</span>
      <span class="stat"><span class="stat-num">{len(ai_repos)}</span>&nbsp;AI trending</span>
      <span class="stat-sep">·</span>
      <span class="stat"><span class="stat-num">{len(ms_repos)}</span>&nbsp;microservices repos</span>
      <span class="stat-sep">·</span>
      <span>Updated {now_str}</span>
    </div>
  </div>

  <!-- Main -->
  <main class="site-main">

    <!-- Left: News -->
    <section>
      <div class="sec-head">
        <span class="sec-icon">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 22h16a2 2 0 002-2V4a2 2 0 00-2-2H8a2 2 0 00-2 2v16a2 2 0 01-2 2zm0 0a2 2 0 01-2-2v-9c0-1.1.9-2 2-2h2"/>
            <path d="M18 14h-8M15 18h-5M10 6h8v4h-8z"/>
          </svg>
        </span>
        <h2>AI News</h2>
        <span class="sec-meta">past {NEWS_MAX_AGE_DAYS} days</span>
        <span class="sec-count">{len(articles)}</span>
      </div>
      <div class="news-list">
        {news_html}
      </div>
    </section>

    <!-- Right: Sidebar -->
    <aside class="sidebar">

      <!-- AI Trending panel -->
      <div class="panel">
        <div class="panel-head">
          <span class="panel-icon">{GITHUB_SVG}</span>
          <span class="panel-title">AI Trending</span>
          <span class="panel-meta">today · {len(ai_repos)} repos</span>
        </div>
        <div class="repo-list">
          {ai_repos_html}
        </div>
      </div>

      <!-- Microservices panel -->
      <div class="panel">
        <div class="panel-head">
          <span class="panel-icon">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <rect x="2" y="3" width="6" height="6" rx="1"/><rect x="16" y="3" width="6" height="6" rx="1"/>
              <rect x="9" y="15" width="6" height="6" rx="1"/>
              <path d="M5 9v3a2 2 0 002 2h10a2 2 0 002-2V9"/><line x1="12" y1="12" x2="12" y2="15"/>
            </svg>
          </span>
          <span class="panel-title">Microservices Hot</span>
          <span class="panel-meta">⭐ popular · {len(ms_repos)} repos</span>
        </div>
        <div class="repo-list">
          {ms_repos_html}
        </div>
      </div>

    </aside>
  </main>

  <!-- Footer -->
  <footer class="site-footer">
    <div class="footer-inner">
      <span><span class="footer-brand">AI News Daily</span> — personal aggregator</span>
      <span>{now_str}</span>
    </div>
  </footer>

</body>
</html>"""


# ── Repo deduplication ────────────────────────────────────────────────────────

def load_seen_repos() -> dict[str, str]:
    """Return {full_name: first_seen_date_str} from the tracking file."""
    if SEEN_REPOS_FILE.exists():
        try:
            return json.loads(SEEN_REPOS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_seen_repos(seen: dict[str, str]) -> None:
    SEEN_REPOS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_REPOS_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")


def filter_new_repos(repos: list[dict], seen: dict[str, str]) -> list[dict]:
    """Remove repos seen on a PREVIOUS day within DEDUP_DAYS.
    Repos seen today are always kept (safe to re-run same day).
    """
    today  = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=DEDUP_DAYS)
    result = []
    for r in repos:
        key = f"{r['owner']}/{r['name']}"
        last_seen_str = seen.get(key)
        if last_seen_str:
            try:
                last_seen = datetime.fromisoformat(last_seen_str).date()
                # Skip only if seen on a *previous* day within the window
                if cutoff <= last_seen < today:
                    continue
            except Exception:
                pass
        result.append(r)
    return result


def mark_repos_seen(repos: list[dict], seen: dict[str, str]) -> None:
    """Update seen dict with today's date for every repo in the list."""
    today_str = datetime.now(timezone.utc).date().isoformat()
    for r in repos:
        seen[f"{r['owner']}/{r['name']}"] = today_str


# ── Markdown report ───────────────────────────────────────────────────────────

def _md_repo_table(repos: list[dict]) -> str:
    if not repos:
        return "_暂无数据_\n"
    lines = ["| 项目 | 语言 | ⭐ Stars | 简介 |",
             "|------|------|---------|------|"]
    for r in repos:
        desc  = r["description"].replace("|", "\\|") if r["description"] else "—"
        today = f" (+{r['stars_today']})" if r.get("stars_today") else ""
        lang  = r["language"] or "—"
        lines.append(f"| [{r['owner']}/{r['name']}]({r['url']}) | {lang} | {r['stars']}{today} | {desc} |")
    return "\n".join(lines) + "\n"


def generate_markdown(
    articles: list[dict],
    ai_repos: list[dict],
    ms_repos: list[dict],
) -> str:
    today     = datetime.now(timezone.utc).date()
    now_str   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    date_str  = today.isoformat()

    lines: list[str] = []

    # Title & meta
    lines += [
        f"# {date_str} AI 动态日报",
        "",
        f"> 生成时间：{now_str}　|　文章：{len(articles)} 篇　|　"
        f"AI 热门项目：{len(ai_repos)} 个　|　微服务项目：{len(ms_repos)} 个",
        "",
        "---",
        "",
    ]

    # News section
    lines += ["## 📰 AI 新闻", ""]
    if not articles:
        lines.append("_暂无文章。_\n")
    else:
        for i, a in enumerate(articles, 1):
            date_label = fmt_date(a["published"])
            date_part  = f"　**发布**：{date_label}" if date_label else ""
            lines.append(f"### {i}. [{a['title']}]({a['url']})")
            lines.append(f"**来源**：{a['source']}{date_part}")
            if a["summary"]:
                lines.append(f"\n> {a['summary']}")
            lines.append("")

    lines += ["---", "", "## 🤖 GitHub AI 热门项目（今日）", ""]
    lines.append(_md_repo_table(ai_repos))

    lines += ["---", "", "## 🔧 微服务热门项目", ""]
    lines.append(_md_repo_table(ms_repos))

    lines += ["---", "", f"*由 AI News Daily 自动生成 · {now_str}*", ""]
    return "\n".join(lines)


def save_report(md: str) -> Path:
    today = datetime.now(timezone.utc).date()
    report_dir = REPORTS_DIR / str(today.year) / f"{today.month:02d}"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{today.isoformat()}.md"
    path.write_text(md, encoding="utf-8")
    return path


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # ── Fetch ──────────────────────────────────────────────────────────────────
    print("Fetching AI news...")
    articles = fetch_news()
    print(f"  Got {len(articles)} articles")

    print("Fetching GitHub AI trending...")
    ai_repos_raw = fetch_github_ai_trending()
    print(f"  Got {len(ai_repos_raw)} repos")

    print("Fetching GitHub microservices hot repos...")
    ms_repos_raw = fetch_github_microservices()
    print(f"  Got {len(ms_repos_raw)} repos")

    # ── Deduplicate repos ──────────────────────────────────────────────────────
    seen = load_seen_repos()

    ai_repos = filter_new_repos(ai_repos_raw, seen)
    ms_repos = filter_new_repos(ms_repos_raw, seen)
    skipped  = (len(ai_repos_raw) - len(ai_repos)) + (len(ms_repos_raw) - len(ms_repos))
    if skipped:
        print(f"  Dedup: skipped {skipped} repos seen within the last {DEDUP_DAYS} days")

    mark_repos_seen(ai_repos, seen)
    mark_repos_seen(ms_repos, seen)
    save_seen_repos(seen)

    # ── Generate HTML ──────────────────────────────────────────────────────────
    print("Generating HTML...")
    html = generate_html(articles, ai_repos, ms_repos)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"  Saved → {OUTPUT_FILE}")

    # ── Generate Markdown report ───────────────────────────────────────────────
    print("Generating Markdown report...")
    md   = generate_markdown(articles, ai_repos, ms_repos)
    path = save_report(md)
    print(f"  Saved → {path}")

    print("Done.")


if __name__ == "__main__":
    main()
