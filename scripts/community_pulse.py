#!/usr/bin/env python3
"""Add a live "社群觀點 / Community Pulse" to top stories using Grok + x_search.

Plays to each model's strength: gpt-5.5 writes the reliable structured briefing,
while Grok (xAI) — which can search X/Twitter live via the ``x_search`` tool —
pulls the real developer/community reaction for the handful of stories where it
matters most (headline + major model releases + hot topics).

Only the top ~5 stories get a pulse, so this stays fast (a few parallel Grok
calls) and never attempts the 27-article single-shot generation that times out.

Grounding: the prompt forbids fabrication and instructs Grok to say
「暫無明顯社群討論」 when x_search finds nothing, so an article without real
chatter simply gets no pulse rather than an invented one.

Usage:
    python3 community_pulse.py data/morning.json [data/latest.json ...] [--max N]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERMES_BIN = "/home/hermes/.local/bin/hermes"
MODEL = "grok-4.3"
PROVIDER = "xai-oauth"
TIMEOUT = 240
MAX_STORIES = 5
# Sequential: concurrent grok+x_search subprocesses contend on the xAI OAuth and
# fail. One at a time is reliable and still finishes a handful of stories in a
# few minutes.
MAX_WORKERS = 1

# Titles that look like a model / product release or an obviously hot topic.
_HOT_RE = re.compile(
    r"(claude|gpt|gemini|grok|llama|mistral|sonnet|opus|haiku|deepseek|qwen|"
    r"nano banana|發表|推出|開放|上線|release|launch|introduc|announc)",
    re.I,
)


def _pick_stories(articles: list[dict]) -> list[dict]:
    picked: list[dict] = []
    seen: set[str] = set()

    def add(a: dict) -> None:
        if a.get("id") and a["id"] not in seen and len(picked) < MAX_STORIES:
            seen.add(a["id"])
            picked.append(a)

    for a in articles:
        if a.get("is_headline"):
            add(a)
    for a in articles:
        if a.get("category") == "ai-ml" and _HOT_RE.search(a.get("title_zh", "")):
            add(a)
    for a in articles:
        if _HOT_RE.search(a.get("title_zh", "")):
            add(a)
    return picked


def _query_grok(title: str, source_name: str) -> str | None:
    prompt = (
        f"用 x_search 搜尋 X（Twitter）上關於這則 AI 新聞的真實討論：「{title}」"
        f"（來源：{source_name}）。\n"
        "請用繁體中文（台灣用語）寫 2-3 句『社群/開發者怎麼看』的重點摘要，"
        "涵蓋期待、爭議、實測評價或比較對象。\n"
        "嚴禁捏造：只根據 x_search 實際查到的內容；若查不到明顯討論，"
        "只回覆這五個字：暫無明顯社群討論。\n"
        "只輸出摘要本身，不要前言、不要標題、不要引用連結。"
    )
    cmd = [HERMES_BIN, "chat", "--provider", PROVIDER, "--model", MODEL,
           "--toolsets", "x_search", "--max-turns", "5", "-Q", "-q", prompt]
    # grok+x_search is latency-flaky; one retry meaningfully lifts the hit rate.
    for _attempt in range(2):
        try:
            r = subprocess.run(cmd, text=True, capture_output=True, timeout=TIMEOUT + 30)
        except Exception:
            continue
        if r.returncode != 0:
            continue
        lines = [ln for ln in r.stdout.splitlines() if not ln.startswith("session_id:")]
        text = "\n".join(lines).strip()
        if not text or "暫無明顯社群討論" in text:
            return None  # genuine "no chatter" — don't retry, don't fabricate
        return text[:400]  # keep it a tight pulse
    return None


def enrich_file(path: str, max_stories: int = MAX_STORIES) -> tuple[int, int]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    articles = data.get("articles", [])
    stories = _pick_stories(articles)[:max_stories]

    def work(a: dict) -> None:
        pulse = _query_grok(a.get("title_zh", ""), (a.get("source") or {}).get("name", ""))
        if pulse:
            a["community_pulse"] = pulse
            a["community_pulse_source"] = "Grok · x_search"

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        list(pool.map(work, stories))

    got = sum(1 for a in articles if a.get("community_pulse"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return got, len(stories)


def main(argv: list[str]) -> int:
    max_stories = MAX_STORIES
    if "--max" in argv:
        i = argv.index("--max")
        max_stories = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    files = [a for a in argv if not a.startswith("--")]
    if not files:
        print("usage: community_pulse.py <issue.json> [...] [--max N]", file=sys.stderr)
        return 2
    for path in files:
        try:
            got, n = enrich_file(path, max_stories)
            print(f"[community_pulse] {path}: {got}/{n} top stories got a pulse", file=sys.stderr)
        except FileNotFoundError:
            print(f"[community_pulse] skip (not found): {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
