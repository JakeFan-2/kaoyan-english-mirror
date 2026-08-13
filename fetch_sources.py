#!/usr/bin/env python3
"""境外抓取考研题源 → 私有仓库镜像（GitHub Actions 运行）

合规红线（2026-08-13 与用户确认）：
  1. 绝不绕过付费墙：仅使用媒体官方 RSS + 公开网页可见文本；付费墙截断内容
     （提取 < 220 词）自动丢弃，宁缺毋滥
  2. 绝不公开分发：本仓库必须保持私有，内容仅用于个人学习精读
  3. 输出为「节选 + 解读」素材，不整篇搬运

产物：
  articles/YYYY-MM-DD/<source>_<n>.json   当日候选文章（上海日期）
  manifest.json                           各源抓取统计（观测用）

单源失败不影响其他源；全源失败仍写 manifest 供本地降级判断。
"""
import datetime
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import feedparser
import requests

from mirror_common import MIN_WORDS, clean_html, fetch_page_text

CN_TZ = datetime.timezone(datetime.timedelta(hours=8))
OUT_DIR = "articles"
MANIFEST = "manifest.json"
MAX_PER_CHANNEL = 8  # 每频道最多保留候选数

# 标题黑名单：非新闻/非文章条目（与本地 v6.5 一致 + 频道特有）
TITLE_BLACKLIST = re.compile(
    r"correction|erratum|retraction|sponsored|advertorial|podcast|newsletter|"
    r"live blog|watch:|quiz|obituary|letters|cartoon|briefing notes", re.I)

# ── P0 考研题源（2026-08-13 首批；P1: Atlantic/CSM/HBR/SciAm 后续加）──
# mode:
#   rss-full    = RSS 自带全文（Guardian content:encoded），正文页仅作兜底
#   rss-summary = 付费墙源（Economist/NYT）：RSS 摘要 + 公开正文尽力而为，
#                 截断(<MIN_WORDS)自动弃文 —— 合规关键
SOURCES = {
    "economist": {
        "name": "The Economist", "group": "时政", "level": "L4", "mode": "rss-summary",
        "channels": {
            "Leaders":  {"url": "https://www.economist.com/leaders/rss.xml",  "zh": "Leaders"},
            "Briefing": {"url": "https://www.economist.com/briefing/rss.xml", "zh": "Briefing"},
        },
    },
    "guardian": {
        "name": "The Guardian", "group": "社会", "level": "L4", "mode": "rss-full",
        # 频道级 group 覆盖：World/Opinion 国际议题归「时政」，Education/Society 归「社会」
        "channels": {
            "World":     {"url": "https://www.theguardian.com/world/rss",         "zh": "World",     "group": "时政"},
            "Opinion":   {"url": "https://www.theguardian.com/commentisfree/rss", "zh": "Opinion",   "group": "时政"},
            "Education": {"url": "https://www.theguardian.com/education/rss",     "zh": "Education"},
            "Society":   {"url": "https://www.theguardian.com/society/rss",       "zh": "Society"},
        },
    },
    "nytimes": {
        "name": "The New York Times", "group": "时政", "level": "L4", "mode": "rss-summary",
        "channels": {
            "Opinion":  {"url": "https://rss.nytimes.com/services/xml/rss/nyt/Opinion.xml",  "zh": "Opinion"},
            "World":    {"url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",    "zh": "World"},
            "Business": {"url": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", "zh": "Business"},
        },
    },
}


def parse_rss(url):
    """带超时的 RSS 拉取 + 解析（feedparser.parse(url) 内部无超时，会挂起）。"""
    try:
        r = requests.get(url, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        if r.status_code != 200:
            return None
        return feedparser.parse(r.content)
    except Exception:
        return None


def build_candidate(e, cfg, ch_zh):
    """单个 RSS entry → 候选 dict；不达标返回 None（词数过滤 / 黑名单 / 缺链接）。"""
    title = (getattr(e, "title", "") or "").strip()
    link = (getattr(e, "link", "") or "").strip()
    if not title or not link or TITLE_BLACKLIST.search(title):
        return None
    raw = ""
    if getattr(e, "content", None) and e.content:
        raw = e.content[0].get("value", "") or ""
    if not raw and getattr(e, "summary", None):
        raw = e.summary
    text = clean_html(raw)
    words = len(text.split())
    # 正文页尽力而为（公开可见文本；付费墙截断自然 < MIN_WORDS 被弃）
    if words < MIN_WORDS:
        page = fetch_page_text(link)
        if len(page.split()) > words:
            text, words = page, len(page.split())
    if words < MIN_WORDS:
        return None
    return {
        "title": title,
        "link": link,
        "source": cfg["name"],
        "channel_zh": ch_zh,
        "group": cfg["group"],
        "level": cfg["level"],
        "text": text,
        "words": words,
        "mode": cfg["mode"],
    }


def main():
    today = datetime.datetime.now(CN_TZ).strftime("%Y-%m-%d")
    day_dir = os.path.join(OUT_DIR, today)
    os.makedirs(day_dir, exist_ok=True)

    tasks = []  # (cfg, ch_zh, url)
    for cfg in SOURCES.values():
        for ch_zh, ch in cfg["channels"].items():
            tasks.append((cfg, ch_zh, ch["url"]))

    results = {}   # source_key -> stats
    all_candidates = []  # (source_key, cand)
    seen_links = set()

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(parse_rss, url): (cfg, ch_zh) for cfg, ch_zh, url in tasks}
        for fut in futures:
            cfg, ch_zh = futures[fut]
            key = cfg["name"]
            st = results.setdefault(key, {"ok": True, "candidates": 0, "channels": 0, "error": None})
            try:
                feed = fut.result()
                if feed is None:
                    st["ok"] = False
                    st["error"] = "rss_fetch_failed"
                    continue
                st["channels"] += 1
                for e in feed.entries[:MAX_PER_CHANNEL]:
                    c = build_candidate(e, cfg, ch_zh)
                    if c is None:
                        continue
                    if "group" in ch:
                        c["group"] = ch["group"]  # 频道级题材覆盖
                    link_norm = c["link"].rstrip("/").lower()
                    if link_norm in seen_links:
                        continue
                    seen_links.add(link_norm)
                    st["candidates"] += 1  # 修复：统计字段自增（v1 bug）
                    all_candidates.append((key, c))
            except Exception as ex2:
                st["ok"] = False
                st["error"] = str(ex2)[:120]

    # 落盘：<source>_<n>.json
    written = 0
    for key, c in all_candidates:
        slug = re.sub(r"[^a-z0-9]+", "-", c["title"][:50].lower()).strip("-") or "untitled"
        path = os.path.join(day_dir, f"{key.split()[0].lower()}_{slug[:60]}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=1)
        written += 1

    manifest = {
        "date": today,
        "generated_at": datetime.datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "total": written,
        "sources": results,
    }
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    # 打印摘要（Actions 日志可观测）
    for key, st in results.items():
        flag = "✅" if st["ok"] else "❌"
        print(f"{flag} {key}: {st['candidates']} 候选 / {st['channels']} 频道"
              + (f" ({st['error']})" if st.get("error") else ""))
    print(f"📦 共落盘 {written} 篇 → {day_dir}")
    return 0 if written else 1  # 0 篇也返回 1，让 workflow 仍 commit manifest（降级标记）


if __name__ == "__main__":
    sys.exit(main())
