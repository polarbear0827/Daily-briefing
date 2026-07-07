#!/usr/bin/env python3
"""Enrich a Daily Briefing issue JSON with per-article hero images.

For each article we fetch its source URL and extract the Open Graph image
(``og:image``, falling back to ``twitter:image``). This gives real, relevant
news images — free, no API key, far better journalism than generic stock photos.

Articles whose source has no usable OG image simply get no ``image_url`` and the
renderer falls back to a category gradient, so a missing image never breaks the
page.

Usage:
    python3 enrich_images.py data/morning.json [data/latest.json ...]

Runs in place. Safe to re-run (skips articles that already have image_url unless
--force). Parallel fetch with a bounded thread pool; per-request timeout keeps
the whole pass well under a minute for a typical ~27-article issue.
"""
from __future__ import annotations

import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

TIMEOUT = 10
MAX_WORKERS = 12
UA = "Mozilla/5.0 (compatible; DailyBriefingBot/1.0; +https://polarbear0827.github.io/Daily-briefing/)"

_OG_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image(?::url)?|twitter:image(?::src)?)["\'][^>]*>',
    re.I,
)
_CONTENT_RE = re.compile(r'content=["\']([^"\']+)["\']', re.I)


def _clean(url: str, base: str) -> str | None:
    url = (url or "").strip()
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = urljoin(base, url)
    if not url.lower().startswith("http"):
        return None
    # Skip obvious tracking pixels / SVG logos that render poorly as heroes.
    low = url.lower()
    if low.endswith(".svg") or "1x1" in low or "pixel" in low:
        return None
    return url


def extract_og_image(page_url: str) -> str | None:
    """Fetch a page and return its best OG/Twitter image URL, or None."""
    try:
        req = Request(page_url, headers={"User-Agent": UA, "Accept": "text/html"})
        with urlopen(req, timeout=TIMEOUT) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if "html" not in ctype.lower():
                return None
            # Only need the <head>; cap the read so huge pages stay cheap.
            raw = resp.read(300_000).decode("utf-8", "ignore")
    except Exception:
        return None

    base = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"
    for tag in _OG_RE.findall(raw):
        m = _CONTENT_RE.search(tag)
        if m:
            cleaned = _clean(m.group(1), base)
            if cleaned:
                return cleaned
    return None


# ---------------------------------------------------------------------------
# Unsplash 後援:來源網頁抓不到 og:image 時,用文章主題搜一張相關圖。
# key 只在伺服器端(產生器)使用,烘進 image_url;前端只拿網址、不碰 key。
# ---------------------------------------------------------------------------
_CAT_KW = {
    "ai-ml": "artificial intelligence neural network",
    "products": "technology device gadget",
    "industry": "data center technology industry",
    "tech-risk": "cybersecurity data security",
    "community": "software developer coding",
    "chinese-tech": "technology city china",
    "enterprise": "business technology office",
    "research": "science research laboratory",
}
_STOP = {"The", "And", "For", "With", "This", "That", "API", "AI", "New", "How", "Why"}


def _unsplash_key() -> str:
    for n in ("UNSPLASH_ACCESS_KEY", "UNSPLASH_KEY"):
        if os.environ.get(n):
            return os.environ[n].strip()
    p = Path.home() / ".hermes" / "credentials" / "unsplash.env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() in ("ACCESS_KEY", "UNSPLASH_ACCESS_KEY", "UNSPLASH_KEY"):
                return v.strip()
    return ""


def _query_from_article(a: dict) -> str:
    """品牌/產品專有名詞(標題裡的英文詞)+ 分類主題詞,組出相關又不亂跑的查詢。"""
    cat = _CAT_KW.get(a.get("category", ""), "technology abstract")
    text = f"{a.get('title_en', '')} {a.get('title_zh', '')}"
    toks = [t for t in re.findall(r"[A-Z][A-Za-z0-9]{2,}", text) if t not in _STOP][:2]
    return (" ".join(toks) + " " + cat).strip() if toks else cat


def unsplash_image(query: str, key: str) -> tuple[str | None, str | None]:
    """回 (圖片網址, 攝影師名)。失敗回 (None, None)。"""
    url = ("https://api.unsplash.com/search/photos?per_page=1&orientation=landscape"
           f"&content_filter=high&query={quote(query)}")
    try:
        req = Request(url, headers={"Authorization": f"Client-ID {key}", "Accept-Version": "v1"})
        data = json.loads(urlopen(req, timeout=TIMEOUT).read())
        res = data.get("results") or []
        if res:
            raw = res[0].get("urls", {}).get("raw")
            if raw:
                sep = "&" if "?" in raw else "?"
                credit = (res[0].get("user") or {}).get("name")
                return f"{raw}{sep}w=900&q=72&fit=crop", credit
    except Exception:
        pass
    return None, None


def enrich_file(path: str, force: bool = False) -> tuple[int, int]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    articles = data.get("articles", [])
    if not articles:
        return 0, 0

    ukey = _unsplash_key()

    def work(a: dict) -> None:
        if a.get("image_url") and not force:
            return
        url = (a.get("source") or {}).get("url")
        img = extract_og_image(url) if url else None
        if img:
            a["image_url"] = img
            return
        # 來源沒有 og:image → Unsplash 後援(用主題搜一張相關圖)
        if ukey:
            u, credit = unsplash_image(_query_from_article(a), ukey)
            if u:
                a["image_url"] = u
                a["image_source"] = "unsplash"
                if credit:
                    a["image_credit"] = credit

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        list(pool.map(work, articles))

    got = sum(1 for a in articles if a.get("image_url"))
    via_us = sum(1 for a in articles if a.get("image_source") == "unsplash")
    if via_us:
        print(f"[enrich_images]   ↳ {via_us} 張來自 Unsplash 後援", file=sys.stderr)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return got, len(articles)


def main(argv: list[str]) -> int:
    force = "--force" in argv
    files = [a for a in argv if not a.startswith("--")]
    if not files:
        print("usage: enrich_images.py <issue.json> [more.json ...] [--force]", file=sys.stderr)
        return 2
    for path in files:
        try:
            got, total = enrich_file(path, force=force)
            print(f"[enrich_images] {path}: {got}/{total} articles got an image", file=sys.stderr)
        except FileNotFoundError:
            print(f"[enrich_images] skip (not found): {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
