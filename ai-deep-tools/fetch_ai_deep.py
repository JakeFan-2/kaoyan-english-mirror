#!/usr/bin/env python3
"""
AI 深度阅读 — 境外镜像抓取（GitHub Actions 运行）

为什么需要镜像（2026-09-11 实测结论）：
  - 甲子光年 / 归藏：纯客户端渲染（jQuery AJAX / Next.js），requests 只能拿到空壳，
    必须无头浏览器渲染后再提取正文
  - 机器之心：整站是 PRO 通讯会员墙（首页即会员落地页），公开页拿不到全文 → 未接入
  - The Batch：无需镜像（deeplearning.ai 国内直连可抓），本地 deep_publish.py 直接处理

产物（写入私有仓库 kaoyan-english-mirror）：
    ai-deep/<YYYY-MM-DD>/<source>.json
        {"source", "label", "date", "articles":[{"title","link","text","words"}, ...]}

设计：
  - 单源失败不影响其它源（逐源 try/except）
  - 中文按「中文字数 / 2」折算成可比阅读量（中文无空格，len(split()) 会严重低估）
  - 只抓公开页面可见文本，不绕任何付费墙
"""

import json
import os
import re
import sys
import html as html_mod
import datetime
from urllib.parse import urljoin

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

MAX_ARTICLES_PER_RUN = 5      # 每源每次最多渲染的文章数（控时长）
MIN_SIZE = 150                # 折算阅读量低于此值视为抓取失败（≈300 中文字 / 150 英文词）
DATE = os.environ.get("MIRROR_DATE") or datetime.date.today().isoformat()
OUT_DIR = os.path.join("ai-deep", DATE)


# ---------------------------------------------------------------- 工具
def size(text: str) -> float:
    """英文词数 + 中文字数/2 —— 中英可比的阅读量。"""
    if not text:
        return 0.0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z][A-Za-z'\-]*", text))
    return latin + cjk / 2.0


def clean(t: str) -> str:
    t = html_mod.unescape(t or "")
    t = re.sub(r"[ \t\u00a0]+", " ", t)
    lines = [ln.strip() for ln in t.split("\n")]
    return "\n\n".join(ln for ln in lines if ln)


def extract(html_text: str) -> str:
    if not html_text:
        return ""
    try:
        import trafilatura
        t = (trafilatura.extract(html_text, include_comments=False) or "").strip()
        if size(t) >= MIN_SIZE:
            return clean(t)
    except Exception:
        pass
    try:                                              # 兜底：最长容器
        from bs4 import BeautifulSoup
        s = BeautifulSoup(html_text, "lxml")
        best = ""
        for n in s.find_all(["article", "div", "section"]):
            txt = n.get_text("\n")
            if len(txt) > len(best):
                best = txt
        return clean(best)
    except Exception:
        return ""


def title_of(html_text: str, fallback: str = "") -> str:
    for pat in (r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
                r"<title[^>]*>(.*?)</title>"):
        m = re.search(pat, html_text, re.S | re.I)
        if m:
            t = clean(m.group(1))
            t = re.sub(r"\s*[-|｜]\s*(量子位|机器之心|甲子光年|歸藏|归藏).*$", "", t)
            # og:title 有时返回站名（如「解藏的 AI 资讯」）→ 过短则改用首个 h1
            if t and len(t) > 8:
                return t.strip()
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html_text, re.S | re.I)
    if m:
        t = clean(re.sub(r"<[^>]+>", " ", m.group(1))).strip()
        if t:
            return t
    return fallback


def save(source: str, label: str, articles: list):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{source}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"source": source, "label": label, "date": DATE, "articles": articles},
                  f, ensure_ascii=False, indent=1)
    print(f"✅ [{source}] 写入 {path}（{len(articles)} 篇）")


# ---------------------------------------------------------------- 无头浏览器
class Renderer:
    def __init__(self):
        self._pw = None
        self._browser = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            executable_path=os.environ.get("PW_CHROMIUM") or None,
            args=["--no-sandbox", "--disable-dev-shm-usage"])
        return self

    def __exit__(self, *a):
        try:
            self._browser.close()
        finally:
            self._pw.stop()

    def html(self, url: str) -> str:
        """渲染：domcontentloaded + 等网络静默（超时可容忍）+ 滚动触发懒加载。"""
        page = self._browser.new_page(user_agent=UA)
        try:
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            page.wait_for_timeout(2500)
            for _ in range(3):
                page.mouse.wheel(0, 5000)
                page.wait_for_timeout(900)
            page.wait_for_timeout(1200)
            return page.content()
        finally:
            page.close()


def fetch_js(rend: Renderer, label: str, list_url: str, link_re: str, base: str) -> list:
    """通用 JS 站：渲染列表页 → 抽文章链接 → 渲染正文页 → 提取。"""
    html_text = rend.html(list_url)
    links, seen = [], set()
    for m in re.findall(link_re, html_text):
        u = urljoin(base, m)
        if u not in seen:
            seen.add(u)
            links.append(u)
    print(f"   [{label}] 列表页发现 {len(links)} 个链接", file=sys.stderr)
    out = []
    for u in links[:MAX_ARTICLES_PER_RUN]:
        try:
            h = rend.html(u)
            text = extract(h)
            if size(text) >= MIN_SIZE:
                out.append({"title": title_of(h, u), "link": u,
                            "text": text, "words": int(size(text))})
            else:
                print(f"   ⚠️ {label} 正文过短({size(text):.0f})，跳过 {u}", file=sys.stderr)
        except Exception as ex:
            print(f"   ⚠️ {label} {u}: {ex}", file=sys.stderr)
    return out


# ---------------------------------------------------------------- 主流程
JS_JOBS = [
    ("jazzyear", "甲子光年", "https://www.jazzyear.com/article_list.html",
     r'href="(\.?/?article_info\.html\?id=\d+)"', "https://www.jazzyear.com/article_list.html"),
    ("guizang", "归藏", "https://guizang.ai/articles",
     r'href="(/articles/[a-z0-9\-]+)"', "https://guizang.ai/"),
]


def main():
    try:
        with Renderer() as rend:
            for key, label, list_url, link_re, base in JS_JOBS:
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
        print(f"❌ 无头浏览器启动失败: {ex}", file=sys.stderr)

    idx_path = os.path.join("ai-deep", "index.json")
    cur = {}
    if os.path.exists(idx_path):
        try:
            cur = json.load(open(idx_path, encoding="utf-8"))
        except Exception:
            cur = {}
    cur[DATE] = sorted(f[:-5] for f in os.listdir(OUT_DIR)) if os.path.isdir(OUT_DIR) else []
    os.makedirs(os.path.dirname(idx_path), exist_ok=True)
    json.dump(cur, open(idx_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"✅ 索引已更新: {idx_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
