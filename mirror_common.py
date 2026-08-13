#!/usr/bin/env python3
"""公共抓取模块 —— 与 scripts/english-daily/main.py 同源复用（v6.5.2 抽取）

Actions 境外 runner 与本地 main.py 共用同一套 HTML 清洗 / 正文提取逻辑，
保证抓取行为一致，修改只需一处。
"""
import html as html_mod
import re

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

MIN_WORDS = 220       # 候选最低词数（与本地一致）
PAGE_MIN_WORDS = 100  # trafilatura 提取低于此词数时走容器正则兜底（与本地一致）


def clean_html(raw: str) -> str:
    """HTML → 纯文本，保留自然段（</p> → 空行）。"""
    if not raw:
        return ""
    text = html_mod.unescape(raw)
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"</p>|</div>|</li>|</h[1-6]>", "\n\n", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def fetch_page_text(url: str) -> str:
    """抓正文页并用 trafilatura 提取纯文本。提取过短时用主流文章容器正则兜底。失败返回空串。

    合规说明：仅访问公开 URL 并提取页面可见文本，不破解、不绕过任何付费墙。
    付费墙截断内容（提取 < MIN_WORDS）由调用方自然过滤。
    """
    if not url:
        return ""
    try:
        import requests
        r = requests.get(url, timeout=15, headers={"User-Agent": UA})
        if r.status_code != 200:
            return ""
        try:
            import trafilatura
            txt = (trafilatura.extract(r.text) or "").strip()
        except ImportError:
            txt = ""
        if len(txt.split()) < PAGE_MIN_WORDS:
            # 兜底：正则提取主流文章容器（Nature 等 trafilatura 提取不全的页面）
            m = re.search(
                r'<div[^>]*(?:articleBody|c-article-body|article-body|entry-content)[^>]*>(.*?)</div>\s*(?:<div|</article|<footer)',
                r.text, flags=re.S)
            if m:
                txt2 = clean_html(m.group(1))
                if len(txt2.split()) > len(txt.split()):
                    txt = txt2
        return txt
    except Exception:
        return ""
