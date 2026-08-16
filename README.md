# kaoyan-english-mirror

考研英语题源境外镜像仓库（**私有**，仅个人学习）。

## 合规声明

- 仅使用媒体**官方 RSS** + 公开网页可见文本
- **绝不绕过付费墙**：付费墙截断内容（<220 词）自动丢弃
- 内容仅用于个人学习精读，**不公开分发**；推送仅节选引用 + 翻译讲解
- 仓库必须保持 **private**，禁止公开 / 转移

## 结构

```
.github/workflows/sync.yml   GitHub Actions：每 6h 境外抓取 → push
fetch_sources.py             抓取主脚本（P0: Economist/Guardian/NYT）
mirror_common.py             公共模块（与本地 scripts/english-daily/main.py 同源复用）
articles/YYYY-MM-DD/*.json   当日候选文章（上海日期）
manifest.json                各源抓取统计（本地陈旧告警/观测用）
```

## 抓取清单（P0+P1 已接入）

| 源 | 频道 | 模式 | 题材组 |
|---|---|---|---|
| The Economist | Leaders / Briefing | rss-summary（付费墙，截断自动弃） | 时政 |
| The Guardian | Opinion / Education / Society / World | rss-full（RSS 自带全文） | 社会/时政 |
| NYT | Opinion / World / Business | rss-summary | 时政 |
| The Atlantic (P1) | All / Ideas | rss-summary | 社会/时政 |
| Christian Science Monitor (P1) | All / Commentary / World | rss-summary | 时政 |
| Harvard Business Review (P1) | All | rss-summary | 社会 |
| Scientific American (P1) | Health / Mind / All | rss-summary | 科技 |

> 多候选 RSS：部分频道配置 `urls` 列表（主→备依次尝试），防源站 URL 变更导致 404。
> manifest.json 记录各源实际可用 URL 与候选数，供本地观测/排查。

## 本地拉取（阶段 2 接入）

- 通道：`api.github.com` contents API（fine-grained PAT，仅本仓库 `Contents: Read-only`）
- 接入：`scripts/english-daily/main.py` 新增 `fetch_gh_mirror()` → 候选池 level=L4（最高优先但不强制）
- 降级：镜像失败 → 现有 15 源 → AI 兜底，日报不断更
