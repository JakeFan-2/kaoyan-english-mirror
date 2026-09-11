#!/usr/bin/env python3
"""
AI 深度阅读 — 境外镜像抓取（GitHub Actions 运行）

为什么需要镜像：
  - The Batch：官方 RSS 已下线（deeplearning.ai 站点只剩 HTML）
  - 机器之心 / 甲子光年 / 归藏：纯客户端渲染（Next.js / jQuery + JS 懒加载），
    直接 requests 只能拿到空壳，必须用无头浏览器渲染后再提取正文

产物（写入私有仓库 kaoyan-english-mirror）：
    ai-deep/<YYYY-MM-DD>/<source>.json
        {"source": str, "label": str, "date": str, "articles": [
            {"title": str, "link": str, "text": str, "words": int}, ...]}

设计原则：
  - 单源失败不影响其它源（逐源 try/except）
  - 每源最多保留 MAX_PER_SOURCE 篇，正文按词数过滤
  - 只抓公开页面可见文本，不绕任何付费墙
"""

import json
import os
import re
import sys
import datetime
from urllib.parse import urljoin

import requests

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

MAX_PER_SOURCE = 5
MAX_ARTICLES_PER_RUN = 5      # 每源每次最多渲染的文章数（控时长）
MIN_WORDS = 300               # 正文低于此词数视为抓取失败
DATE = os.environ.get("MIRROR_DATE") or datetime.date.today().isoformat()
OUT_DIR = os.path.join("ai-deep", DATE)


# ---------------------------------------------------------------- 工具
def clean(t: str) -> str:
    import html as html_mod
    t = html_mod.unescape(t or "")
    t = re.sub(r"[ \t\u00a0]+", " ", t)
    lines = [ln.strip() for ln in t.split("\n")]
    return "\n\n".join(ln for ln in lines if ln)


def extract(html: str) -> str:
    if not html:
        return ""
    try:
        import trafilatura
        t = (trafilatura.extract(html, include_comments=False) or "").strip()
        if len(t.split()) >= MIN_WORDS:
            return clean(t)
    except Exception:
        pass
    # 兜底：取最长容器
    try:
        from bs4 import BeautifulSoup
        s = BeautifulSoup(html, "lxml")
        best = ""
        for n in s.find_all(["article", "div", "section"]):
            txt = n.get_text("\n")
            if len(txt) > len(best):
                best = txt
        return clean(best)
    except Exception:
        return ""


def title_of(html: str, fallback: str = "") -> str:
    for pat in (r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
                r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\']([^"\']+)',
                r"<title[^>]*>(.*?)</title>"):
        m = re.search(pat, html, re.S | re.I)
        if m:
            t = clean(m.group(1))
            t = re.sub(r"\s*[-|｜]\s*(量子位|机器之心|甲子光年|歸藏|归藏).*$", "", t)
            if t:
                return t.strip()
    return fallback


def save(source: str, label: str, articles: list):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{source}.json")
    payload = {"source": source, "label": label, "date": DATE, "articles": articles}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"✅ [{source}] 写入 {path}（{len(articles)} 篇）")


# ---------------------------------------------------------------- Playwright 渲染
class Renderer:
    """无头浏览器渲染器（一个浏览器实例复用，避免反复启动）。"""

    def __init__(self):
        self._pw = None
        self._browser = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        return self

    def __exit__(self, *a):
        try:
            self._browser.close()
        finally:
            self._pw.stop()

    def html(self, url: str, wait_ms: int = 3500) -> str:
        page = self._browser.new_page(user_agent=UA)
        try:
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(wait_ms)
            for _ in range(3):                      # 触发懒加载
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(800)
            return page.content()
        finally:
            page.close()


# ---------------------------------------------------------------- 各源
def fetch_the_batch() -> list:
    """The Batch：列表页 → issue 链接 → 正文页（纯 HTTP 即可）。"""
    idx = requests.get("https://www.deeplearning.ai/the-batch/", headers={"User-Agent": UA}, timeout=30)
    issues = sorted(set(re.findall(r"/the-batch/(issue-\d+)", idx.text)),
                    key=lambda x: int(x.split("-")[1]), reverse=True)
    out = []
    for slug in issues[:MAX_ARTICLES_PER_RUN]:
        link = f"https://www.deeplearning.ai/the-batch/{slug}"
        try:
            r = requests.get(link, headers={"User-Agent": UA}, timeout=30)
            text = extract(r.text)
            if len(text.split()) >= MIN_WORDS:
                out.append({"title": title_of(r.text, slug), "link": link,
                            "text": text, "words": len(text.split())})
        except Exception as ex:
            print(f"   ⚠️ batch {slug}: {ex}", file=sys.stderr)
    return out[:MAX_PER_SOURCE]


def fetch_js(rend: Renderer, label: str, list_url: str, link_re: str,
             base: str, max_pages: int = MAX_ARTICLES_PER_RUN) -> list:
    """通用 JS 站：渲染列表页 → 抽文章链接 → 渲染正文页 → 提取。"""
    html = rend.html(list_url)
    links, seen = [], set()
    for m in re.findall(link_re, html):
        u = urljoin(base, m)
        if u not in seen:
            seen.add(u)
            links.append(u)
    print(f"   [{label}] 列表页发现 {len(links)} 个链接", file=sys.stderr)
    out = []
    for u in links[:max_pages]:
        try:
            h = rend.html(u)
            text = extract(h)
            if len(text.split()) >= MIN_WORDS:
                out.append({"title": title_of(h, u), "link": u,
                            "text": text, "words": len(text.split())})
        except Exception as ex:
            print(f"   ⚠️ {label} {u}: {ex}", file=sys.stderr)
    return out[:MAX_PER_SOURCE]


# ---------------------------------------------------------------- 主流程
def main():
    jobs = []

    print("▶️ The Batch ...", file=sys.stderr)
    try:
        arts = fetch_the_batch()
        if arts:
            save("the-batch", "The Batch · DeepLearning.AI", arts)
        else:
            print("   ⚠️ The Batch 未取到正文", file=sys.stderr)
    except Exception as ex:
        print(f"   ❌ The Batch: {ex}", file=sys.stderr)

    js_jobs = [
        ("jiqizhixin", "机器之心", "https://www.jiqizhixin.com/",
         r'href="(/articles/[0-9a-zA-Z][0-9a-zA-Z\-]*)"', "https://www.jiqizhixin.com/"),
        ("jazzyear", "甲子光年", "https://www.jazzyear.com/article_list.html",
         r'href="[\./]*article_info\.html\?id=(\d+)"', "https://www.jazzyear.com/article_info.html?id="),
        ("guizang", "归藏", "https://guizang.ai/articles",
         r'href="(/articles/[a-z0-9\-]+)"', "https://guizang.ai/"),
    ]

    if js_jobs:
        try:
            with Renderer() as rend:
                for key, label, list_url, link_re, base in js_jobs:
                    print(f"▶️ {label} ...", file=sys.stderr)
                    try:
                        arts = fetch_js(rend, label, list_url, link_re, base)
                        if arts:
                            save(key, label, arts)
                        else:
                            print(f"   ⚠️ {label} 未取到正文", file=sys.stderr)
                    except Exception as ex:
                        print(f"   ❌ {label}: {ex}", file=sys.stderr)
        except Exception as ex:
            print(f"❌ 无头浏览器启动失败（跳过 JS 源）: {ex}", file=sys.stderr)

    # 索引（供本地快速判断当天有没有内容）
    idx_path = os.path.join("ai-deep", "index.json")
    cur = {}
    if os.path.exists(idx_path):
        try:
            cur = json.load(open(idx_path, encoding="utf-8"))
        except Exception:
            cur = {}
    d = os.path.join(OUT_DIR)
    cur[DATE] = sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json")) if os.path.isdir(d) else []
    os.makedirs(os.path.dirname(idx_path), exist_ok=True)
    json.dump(cur, open(idx_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"✅ 索引已更新: {idx_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
