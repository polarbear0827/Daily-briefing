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
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse
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


def enrich_file(path: str, force: bool = False) -> tuple[int, int]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    articles = data.get("articles", [])
    if not articles:
        return 0, 0

    def work(a: dict) -> None:
        if a.get("image_url") and not force:
            return
        url = (a.get("source") or {}).get("url")
        if not url:
            return
        img = extract_og_image(url)
        if img:
            a["image_url"] = img

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        list(pool.map(work, articles))

    got = sum(1 for a in articles if a.get("image_url"))
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
