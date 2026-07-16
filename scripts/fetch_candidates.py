#!/usr/bin/env python3
"""
Daily Briefing — AI-focused source pre-fetch script for Hermes cron.

This script is designed to be attached to a Hermes cron job via the --script
parameter. When the cron fires, Hermes runs this script first, captures its
stdout, and injects it as context into the agent prompt.

Responsibilities:
1. Load config/sources.yaml
2. Fetch all RSS feeds and configured official web pages
3. Deduplicate against recent issue history and the current candidate batch
4. Rank by editorial AI relevance, source tier, and impact signals
5. Output top N candidates as JSON to stdout

The agent then:
- Reads the JSON candidates from stdout context
- Uses the configured OpenAI generator to summarize and translate
- Assembles the final issue JSON
- Commits and pushes to GitHub

Usage:
    python fetch_candidates.py
    python fetch_candidates.py --dry-run  # print summary to stderr, no stdout JSON
    python fetch_candidates.py --repo-dir /path/to/Daily-briefing
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

# feedparser.parse(url) 自帶的網路抓取沒有 timeout——任何一個來源 tarpit 就會讓整支
# 腳本永久卡死（2026-07-16 早報事故根因）。全域 socket timeout 讓慢來源 25 秒放棄，
# 走各 feed 既有的 error 路徑優雅跳過，不影響其他來源。
import socket
socket.setdefaulttimeout(25)

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from weather_utils import fetch_issue_weather

# Third-party: feedparser, PyYAML, python-dateutil
try:
    import feedparser
    import yaml
    from dateutil import parser as date_parser
except ImportError as e:
    print(
        f"Missing dependency: {e}\n"
        "Install with: pip install feedparser PyYAML python-dateutil",
        file=sys.stderr,
    )
    sys.exit(2)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TAIPEI_TZ = timezone(timedelta(hours=8))
LOOKBACK_HOURS = int(os.environ.get("DAILY_BRIEFING_LOOKBACK_HOURS", "72"))
DEDUP_DAYS = 7
MAX_ENTRIES_PER_FEED = 30
MAX_CANDIDATES_PER_CATEGORY = 18
MAX_TOTAL_CANDIDATES = 120  # cap total to keep agent prompt size reasonable
TIER_WEIGHTS = {"primary": 2.0, "secondary": 1.1, "tertiary": 0.3}
STABLE_CANDIDATES_PATH = Path(
    os.environ.get(
        "DAILY_BRIEFING_CANDIDATES",
        "/home/hermes/.hermes/cache/daily_briefing/candidates.json",
    )
)
LEGACY_CANDIDATES_PATH = Path("/tmp/candidates.json")

AI_RE = re.compile(
    r"\b(ai|a\.i\.|artificial intelligence|machine learning|ml|llm|gpt|claude|"
    r"gemini|copilot|openai|anthropic|deepmind|mistral|llama|grok|xai|"
    r"agent|rag|inference|model|neural|transformer|chatbot)\b",
    re.I,
)
SEMICONDUCTOR_RE = re.compile(
    r"\b(nvidia|amd|intel|qualcomm|arm|tsmc|asml|broadcom|mediatek|gpu|"
    r"rtx|dgx|cuda|blackwell|rubin|gb200|gb300|nvlink|hbm|mi\d{3}|xeon|"
    r"semiconductor|chip|accelerator|data center|datacenter|ai pc|computex|gtc)\b",
    re.I,
)
SECURITY_RE = re.compile(
    r"\b(cve|zero-?day|vulnerability|exploit|ransomware|breach|leak|"
    r"supply chain|malware|phishing|patch|critical flaw|security incident)\b",
    re.I,
)
BUSINESS_RE = re.compile(
    r"\b(acquisition|acquires|funding|ipo|valuation|billion|antitrust|"
    r"regulation|regulator|export controls?|sanctions?|tariff|capex|"
    r"data center|datacenter|cloud deal|partnership)\b",
    re.I,
)
RESEARCH_TREND_RE = re.compile(
    r"\b(benchmark|sota|state of the art|leaderboard|eval|evaluation|"
    r"swe-bench|mmlu|gpqa|arc-agi|frontier|reasoning|multimodal|"
    r"long context|open weights?|post-training|reinforcement learning)\b",
    re.I,
)
PAPER_SIGNAL_RE = re.compile(
    r"\b(arxiv|paper|papers|preprint|dataset|ablations?|baseline|"
    r"benchmark suite|technical report|research preview)\b",
    re.I,
)
COMMUNITY_HEAT_RE = re.compile(
    r"\b(hacker news|hn|reddit|github trending|product hunt|discord|"
    r"viral|developer community|open source community)\b",
    re.I,
)
MODEL_RELEASE_RE = re.compile(
    r"\b(gpt|claude|gemini|gemma|mistral|llama|grok|sam|codestral|"
    r"devstral|magistral|nemotron|diffusiongemma|model|api|preview|"
    r"open weights?|multimodal|reasoning)\b",
    re.I,
)
ENTERPRISE_AI_RE = re.compile(
    r"\b(ai|a\.i\.|artificial intelligence|machine learning|ml|llm|gpt|claude|"
    r"gemini|copilot|openai|anthropic|deepmind|mistral|llama|grok|xai|"
    r"agent|rag|inference|model|neural|transformer|chatbot|bedrock|vertex ai|"
    r"azure ai|sagemaker|generative ai|genai|foundation model|large language model)\b",
    re.I,
)
PLATFORM_WAR_RE = re.compile(
    r"\b(platform|ecosystem|lock[- ]?in|cloud|api|marketplace|assistant|"
    r"workspace|copilot|agents? sdk|developer platform|chip war|compute)\b",
    re.I,
)
DEV_RE = re.compile(
    r"\b(github|developer|developers|api|sdk|open source|kubernetes|linux|"
    r"python|javascript|typescript|database|cloud|devops|copilot|code)\b",
    re.I,
)
SCIENCE_RE = re.compile(
    r"\b(space|climate|energy|battery|fusion|quantum|biology|genomics|"
    r"medicine|drug|neuroscience|physics|astronomy|materials)\b",
    re.I,
)
LOW_VALUE_RE = re.compile(
    r"\b(deal|coupon|discount|prime day|black friday|gift guide|best .* under|"
    r"how to watch|trailer|streaming|celebrity|movie|tv show|sports|"
    r"podcast|webinar|sponsored|newsletter only|roundup)\b",
    re.I,
)
TAIWAN_POLICY_RE = re.compile(
    r"(台積電|鴻海|聯發科|半導體|科技|AI|人工智慧|資安|經濟|產業|投資|能源|"
    r"國防|台海|外交|美國|中國|晶片|出口|供應鏈|法案|預算|政策|中研院|工研院)"
)
TAIWAN_LOW_SIGNAL_RE = re.compile(
    r"(黨團協商|藍綠互批|選罷法|罷免|人事案|口水|互槓|自殺|教師事件|"
    r"營養午餐|租補|普發|消防|化妝品|防護員|監委|實習|地方補助)"
)

MAJOR_EVENT_SOURCES = {
    "OpenAI Blog", "Google DeepMind Blog", "Google AI Blog", "NVIDIA Newsroom",
    "NVIDIA Blog", "AMD News Releases", "GitHub Blog", "Bloomberg Technology",
    "Financial Times Technology", "SemiAnalysis", "Anthropic News", "Mistral News",
    "Meta AI Blog", "xAI News", "Apple Machine Learning Research",
}
OFFICIAL_MODEL_SOURCES = {
    "OpenAI Blog", "Anthropic News", "Google DeepMind Blog", "Google AI Blog",
    "Mistral News", "Meta AI Blog", "xAI News", "Hugging Face Blog",
}

# Logging goes to stderr so it doesn't contaminate stdout JSON
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """A candidate news item for the agent to consider."""
    title: str
    url: str
    summary: str  # truncated to avoid huge prompts
    published_at: str
    source_name: str
    source_tier: str
    category: str
    rank_score: float = 0.0
    quality_signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Round for JSON readability
        d["rank_score"] = round(d["rank_score"], 3)
        return d


@dataclass
class FetchReport:
    """Diagnostic report about the fetch run."""
    total_feeds: int = 0
    successful_feeds: int = 0
    failed_feeds: list[dict[str, str]] = field(default_factory=list)
    total_entries_raw: int = 0
    after_time_filter: int = 0
    after_dedup: int = 0
    after_category_cap: int = 0
    final_count: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def strip_html_tags(text: str) -> str:
    """Cheap HTML tag stripper — we don't need bs4 for our purposes.

    Some feeds include invisible zero-width/BOM watermark characters in title or
    summary text (for example U+FEFF plus U+200B/U+200C/U+200D). Hermes' cron
    prompt-injection scanner blocks assembled prompts containing U+FEFF, so RSS
    text must be normalized before it is written to /tmp/candidates.json or
    injected into the cron prompt.
    """
    if not text:
        return ""
    text = html.unescape(text)
    # Remove tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Strip invisible prompt-scanner hazards / watermark characters.
    text = re.sub(r"[\ufeff\u200b\u200c\u200d\u2060]+", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _has_any(patterns: list[re.Pattern], text: str) -> bool:
    return any(p.search(text) for p in patterns)


def score_candidate(
    title: str,
    summary: str,
    category: str,
    source_name: str,
    source_tier: str,
    published: datetime,
) -> tuple[float, list[str]]:
    """Editorial ranking score. Higher means more likely to matter today."""
    text = f"{title}\n{summary}"
    score = TIER_WEIGHTS.get(source_tier, 0.5)
    signals: list[str] = []

    age_hours = max(0.0, (datetime.now(timezone.utc) - published.astimezone(timezone.utc)).total_seconds() / 3600)
    score += max(0.0, (LOOKBACK_HOURS - age_hours) / LOOKBACK_HOURS) * 0.5

    if source_name in MAJOR_EVENT_SOURCES:
        score += 0.35
        signals.append("trusted_source")
    if source_name in OFFICIAL_MODEL_SOURCES:
        score += 0.35
        signals.append("official_model_source")
    if source_name in OFFICIAL_MODEL_SOURCES and MODEL_RELEASE_RE.search(text):
        score += 0.35
        signals.append("official_model_release")

    if AI_RE.search(text):
        score += 0.8
        signals.append("ai_core")
    if SEMICONDUCTOR_RE.search(text):
        score += 1.0
        signals.append("semiconductor_platform")
    if SECURITY_RE.search(text):
        score += 0.9
        signals.append("security_critical")
    if BUSINESS_RE.search(text):
        score += 0.55
        signals.append("business_impact")
    if RESEARCH_TREND_RE.search(text):
        score += 0.65
        signals.append("benchmark_sota")
    if PAPER_SIGNAL_RE.search(text) and (AI_RE.search(text) or RESEARCH_TREND_RE.search(text)):
        score += 0.35
        signals.append("paper_signal")
    if COMMUNITY_HEAT_RE.search(text) and AI_RE.search(text):
        score += 0.35
        signals.append("community_heat")
    if PLATFORM_WAR_RE.search(text) and (AI_RE.search(text) or SEMICONDUCTOR_RE.search(text)):
        score += 0.45
        signals.append("platform_war")
    if DEV_RE.search(text):
        score += 0.35
        signals.append("developer_relevance")
    if SCIENCE_RE.search(text):
        score += 0.25
        signals.append("science_relevance")

    title_lower = title.lower()
    if re.search(r"\b(announce|announces|announced|launch|launches|unveil|unveils|release|ships|general availability|ga|preview|open source)\b", title_lower):
        score += 0.55
        signals.append("new_release")
    if re.search(r"\b(major|first|largest|record|breakthrough|landmark|critical|urgent)\b", title_lower):
        score += 0.35
        signals.append("high_impact_language")
    if "computex" in title_lower or "gtc" in title_lower:
        score += 0.8
        signals.append("conference_signal")
    if "rtx" in title_lower or "dgx" in title_lower or "blackwell" in title_lower or "rubin" in title_lower:
        score += 0.75
        signals.append("ai_hardware_signal")

    if LOW_VALUE_RE.search(text):
        score -= 0.85
        signals.append("low_value_penalty")
    if source_tier == "tertiary":
        score -= 0.25
        if not set(signals) & {
            "benchmark_sota",
            "official_model_release",
            "semiconductor_platform",
            "conference_signal",
            "security_critical",
            "community_heat",
        }:
            score -= 0.35
            signals.append("tertiary_low_signal")

    # Category gates keep broad RSS feeds from feeding random filler into the issue.
    if category in {"ai-ml", "ai-tools"} and not (AI_RE.search(text) or SEMICONDUCTOR_RE.search(text) or RESEARCH_TREND_RE.search(text)):
        score -= 1.2
        signals.append("weak_ai_match")
    elif category == "tech-product" and not _has_any([AI_RE, SEMICONDUCTOR_RE, DEV_RE, SECURITY_RE], text):
        score -= 1.0
        signals.append("weak_tech_match")
    elif category == "vc-business" and not _has_any([AI_RE, SEMICONDUCTOR_RE, BUSINESS_RE, DEV_RE, RESEARCH_TREND_RE], text):
        score -= 0.9
        signals.append("weak_business_match")
    elif category == "enterprise-cases" and not _has_any([ENTERPRISE_AI_RE, SEMICONDUCTOR_RE, RESEARCH_TREND_RE], text):
        score -= 1.6
        signals.append("weak_enterprise_ai_match")
    elif category == "world" and not _has_any([AI_RE, SEMICONDUCTOR_RE, SECURITY_RE, BUSINESS_RE], text):
        score -= 1.4
        signals.append("weak_world_match")
    elif category == "taiwan":
        if TAIWAN_POLICY_RE.search(text):
            score += 0.45
            signals.append("taiwan_relevant")
        else:
            score -= 0.75
            signals.append("weak_taiwan_match")
        if TAIWAN_LOW_SIGNAL_RE.search(text) and not SEMICONDUCTOR_RE.search(text):
            score -= 0.55
            signals.append("taiwan_politics_noise")

    return round(max(score, 0.0), 3), signals


def passes_quality_gate(category: str, score: float, signals: list[str]) -> bool:
    """Reject obvious filler while keeping smaller categories from going empty."""
    if "low_value_penalty" in signals and score < 1.4:
        return False
    minimums = {
        "ai-ml": 1.6,
        "ai-tools": 1.4,
        "research-papers": 1.2,
        "tech-product": 1.45,
        "dev": 1.1,
        "security": 1.2,
        "vc-business": 1.35,
        "enterprise-cases": 1.55,
        "science": 1.0,
        "world": 1.45,
        "taiwan": 1.35,
    }
    return score >= minimums.get(category, 1.0)


# Taiwan-local detection: positive markers (must contain ≥1) and negative
# markers (if any present and no positive marker, reject). Keeps the taiwan
# category honestly local even when the source feed mixes in world news.
_TAIWAN_POSITIVE = (
    "台灣", "臺灣", "本土", "國內", "我國", "全台", "全臺",
    "台北", "臺北", "新北", "桃園", "新竹", "苗栗", "台中", "臺中",
    "彰化", "南投", "雲林", "嘉義", "台南", "臺南", "高雄", "屏東",
    "宜蘭", "花蓮", "台東", "臺東", "澎湖", "金門", "馬祖", "基隆",
    "賴清德", "卓榮泰", "立法院", "立院", "行政院", "總統府", "民進黨",
    "國民黨", "民眾黨", "經濟部", "交通部", "勞動部", "教育部", "衛福部",
    "央行", "金管會", "中研院", "工研院", "台積電", "鴻海", "聯發科",
    "中華電信", "台電", "中油", "台鐵", "高鐵", "捷運",
)
_TAIWAN_NEGATIVE_STRONG = (
    "美國", "中國", "日本", "南韓", "北韓", "俄羅斯", "烏克蘭", "以色列",
    "伊朗", "伊拉克", "敘利亞", "巴勒斯坦", "加薩", "印度", "巴基斯坦",
    "英國", "法國", "德國", "義大利", "西班牙", "葡萄牙", "荷蘭", "比利時",
    "瑞士", "瑞典", "挪威", "丹麥", "芬蘭", "加拿大", "澳洲", "紐西蘭",
    "巴西", "阿根廷", "墨西哥", "緬甸", "泰國", "越南", "菲律賓", "印尼",
    "馬來西亞", "新加坡", "土耳其", "埃及", "南非", "白宮", "克里姆林",
    "聯合國", "歐盟", "北約",
)


def _is_taiwan_local(text: str) -> bool:
    """Return True if text looks like Taiwan-local news.

    Rule: must contain at least one positive Taiwan marker AND must not be
    dominated by foreign markers (i.e. positive count >= negative count).
    Empty / very short text → reject (be conservative).
    """
    if not text or len(text) < 4:
        return False
    pos = sum(1 for kw in _TAIWAN_POSITIVE if kw in text)
    if pos == 0:
        return False
    neg = sum(1 for kw in _TAIWAN_NEGATIVE_STRONG if kw in text)
    return pos >= neg


def normalize_url(url: str) -> str:
    """Normalize URL for dedup matching."""
    if not url:
        return ""
    url = url.strip().lower()
    url = url.rstrip("/")
    # Strip common tracking parameters
    for sep in ("?utm_", "&utm_", "?ref=", "#"):
        idx = url.find(sep)
        if idx > 0:
            url = url[:idx]
    return url


def parse_entry_datetime(entry: Any) -> datetime | None:
    """Extract timezone-aware datetime from feedparser entry."""
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue

    for attr in ("published", "updated", "created"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                dt = date_parser.parse(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except (ValueError, TypeError):
                continue
    return None


def fetch_text_url(url: str, timeout: int = 20) -> tuple[int, str]:
    """Fetch a text/html page with a browser-like UA."""
    try:
        req = Request(url, headers={"User-Agent": "DailyBriefingBot/1.0 Mozilla/5.0"})
        resp = urlopen(req, timeout=timeout)
        raw = resp.read(1_500_000)
        return getattr(resp, "status", 200), raw.decode("utf-8", errors="ignore")
    except Exception:
        return 0, ""


_DATE_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+20\d{2}\b",
    re.I,
)


def parse_web_page_date(text: str) -> datetime | None:
    m = _DATE_RE.search(text)
    if not m:
        return None
    try:
        dt = date_parser.parse(m.group(0))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def clean_web_page_title(text: str, strip_leading_label: bool = True) -> str:
    text = strip_html_tags(text)
    text = re.sub(r"^\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+20\d{2}\b\s+", "", text, flags=re.I)
    if strip_leading_label:
        text = re.sub(r"^(?:Product|Research|Company|Announcements?|Policy|Engineering|Solutions?)\s+", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:260]


def extract_web_page_title(raw_html: str) -> str:
    """Prefer the visible card heading over the whole link body."""
    title_patterns = [
        r"<h[1-4][^>]*>(.*?)</h[1-4]>",
        r"<span[^>]+class=[\"'][^\"']*(?:title|headline)[^\"']*[\"'][^>]*>(.*?)</span>",
    ]
    for pattern in title_patterns:
        for match in re.finditer(pattern, raw_html, re.S | re.I):
            title = clean_web_page_title(match.group(1), strip_leading_label=False)
            if len(title) >= 8:
                return title
    return clean_web_page_title(raw_html)


def fetch_one_web_page(
    page_spec: dict,
    category: str,
    cutoff: datetime,
    dedup: set[str],
) -> tuple[list[Candidate], str | None]:
    """Scrape official News/Blog listing pages that do not expose clean RSS."""
    name = page_spec.get("name", "<unnamed>")
    url = page_spec.get("url")
    tier = page_spec.get("tier", "primary")
    limit = int(page_spec.get("limit", 10))
    allow_path = re.compile(page_spec.get("allow_path_regex", r".*"))
    require_text = re.compile(page_spec.get("require_text_regex", r".+"), re.I)

    if not url:
        return [], "Missing URL in web page spec"

    status, body = fetch_text_url(url)
    if status != 200 or not body:
        return [], f"HTTP {status or 'failed'}"

    candidates: list[Candidate] = []
    seen: set[str] = set()
    for m in re.finditer(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", body, re.S | re.I):
        href = html.unescape(m.group(1)).strip()
        raw_text = m.group(2)
        abs_url = urljoin(url, href)
        parsed_path = urlparse(abs_url).path
        if not allow_path.search(parsed_path):
            continue

        norm = normalize_url(abs_url)
        if norm in seen or norm in dedup:
            continue

        text = extract_web_page_title(raw_text)
        if len(text) < 16 or not require_text.search(text):
            continue

        published = parse_web_page_date(raw_text) or parse_web_page_date(text) or datetime.now(timezone.utc)
        if published < cutoff:
            continue

        summary = text
        rank_score, quality_signals = score_candidate(
            title=text,
            summary=summary,
            category=category,
            source_name=name,
            source_tier=tier,
            published=published,
        )
        if not passes_quality_gate(category, rank_score, quality_signals):
            continue

        seen.add(norm)
        candidates.append(Candidate(
            title=text,
            url=abs_url,
            summary=summary,
            published_at=published.isoformat(),
            source_name=name,
            source_tier=tier,
            category=category,
            rank_score=rank_score,
            quality_signals=quality_signals + ["official_page"],
        ))
        if len(candidates) >= limit:
            break

    return candidates, None


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def load_config(config_path: Path) -> dict:
    """Load and validate sources.yaml."""
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if not isinstance(config, dict) or "rss_feeds" not in config:
        raise ValueError(f"Invalid config structure in {config_path}")
    return config


def _issue_date_from_stem(stem: str):
    """Parse both legacy YYYY-MM-DD and dual-edition YYYY-MM-DD-morning stems."""
    try:
        return datetime.strptime(stem[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def build_dedup_urls(issues_dir: Path, days: int = DEDUP_DAYS) -> set[str]:
    """Read recent issues and return set of normalized URLs."""
    dedup: set[str] = set()
    if not issues_dir.exists():
        log.info("No issues directory yet; dedup set empty")
        return dedup

    cutoff_date = (datetime.now(TAIPEI_TZ) - timedelta(days=days)).date()
    issue_files = sorted(issues_dir.glob("*.json"), reverse=True)

    for issue_file in issue_files:
        file_date = _issue_date_from_stem(issue_file.stem)
        if file_date is None:
            continue
        if file_date < cutoff_date:
            break

        try:
            with open(issue_file, encoding="utf-8") as f:
                data = json.load(f)
            for article in data.get("articles", []):
                url = article.get("source", {}).get("url")
                if url:
                    dedup.add(normalize_url(url))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Could not read %s: %s", issue_file.name, e)

    log.info("Dedup set: %d URLs from past %d days", len(dedup), days)
    return dedup


def _extract_balanced_json_objects(text: str, marker: str) -> list[str]:
    """Extract balanced JSON objects from an HTML-escaped blob."""
    objects: list[str] = []
    start = 0
    while True:
        idx = text.find(marker, start)
        if idx < 0:
            break
        depth = 0
        in_str = False
        esc = False
        for pos in range(idx, len(text)):
            ch = text[pos]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        objects.append(text[idx:pos + 1])
                        start = pos + 1
                        break
        else:
            break
    return objects


def fetch_huggingface_daily_papers(
    feed_spec: dict,
    category: str,
    cutoff: datetime,
    dedup: set[str],
) -> tuple[list[Candidate], str | None]:
    """Fallback for papers.takara.ai failures: parse embedded HF Papers cards."""
    name = feed_spec.get("name", "HuggingFace Daily Papers")
    tier = feed_spec.get("tier", "primary")
    status, body = fetch_text_url("https://huggingface.co/papers")
    if status != 200 or not body:
        return [], f"HF papers fallback HTTP {status or 'failed'}"
    decoded = html.unescape(body)
    candidates: list[Candidate] = []
    seen: set[str] = set()
    for raw in _extract_balanced_json_objects(decoded, '{"paper":{'):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        paper = obj.get("paper") or {}
        paper_id = str(paper.get("id") or "").strip()
        title = strip_html_tags(str(paper.get("title") or "")).strip()
        if not paper_id or not title:
            continue
        link = f"https://huggingface.co/papers/{paper_id}"
        norm = normalize_url(link)
        if norm in seen or norm in dedup:
            continue
        published_raw = paper.get("submittedOnDailyAt") or paper.get("publishedAt")
        try:
            published = date_parser.parse(str(published_raw)) if published_raw else datetime.now(timezone.utc)
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
        except Exception:
            published = datetime.now(timezone.utc)
        if published < cutoff:
            continue
        summary = strip_html_tags(str(paper.get("summary") or ""))[:1500]
        keywords = ", ".join(paper.get("ai_keywords") or [])
        if keywords:
            summary = f"{summary}\nKeywords: {keywords}".strip()
        rank_score, quality_signals = score_candidate(
            title=title,
            summary=summary,
            category=category,
            source_name=name,
            source_tier=tier,
            published=published,
        )
        if not passes_quality_gate(category, rank_score, quality_signals):
            continue
        upvotes = int(paper.get("upvotes") or obj.get("upvotes") or 0)
        if upvotes >= 20:
            rank_score += min(0.8, upvotes / 100)
            quality_signals.append("community_heat")
        seen.add(norm)
        candidates.append(Candidate(
            title=title,
            url=link,
            summary=summary,
            published_at=published.isoformat(),
            source_name=name,
            source_tier=tier,
            category=category,
            rank_score=round(rank_score, 3),
            quality_signals=quality_signals,
        ))
    candidates.sort(key=lambda c: c.rank_score, reverse=True)
    return candidates[:MAX_ENTRIES_PER_FEED], None


def fetch_one_feed(
    feed_spec: dict,
    category: str,
    cutoff: datetime,
    dedup: set[str],
) -> tuple[list[Candidate], str | None]:
    """Fetch one feed. Returns (candidates, error_msg). error_msg is None on success."""
    name = feed_spec.get("name", "<unnamed>")
    url = feed_spec.get("url")
    tier = feed_spec.get("tier", "secondary")

    if not url:
        return [], "Missing URL in feed spec"

    # feedparser 對病態 HTML 的 sanitizer 可能 CPU 死循環（2026-07-16 事故：
    # socket timeout 救不了解析卡死）。SIGALRM 硬時限 45 秒，超時放棄該來源。
    import signal

    def _parse_alarm(signum, frame):
        raise TimeoutError("feedparser parse timeout")

    old_handler = signal.signal(signal.SIGALRM, _parse_alarm)
    signal.alarm(45)
    try:
        parsed = feedparser.parse(
            url,
            request_headers={"User-Agent": "DailyBriefingBot/1.0 (Hermes cron)"},
        )
    except TimeoutError:
        return [], "parse timeout >45s (pathological feed content)"
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

    # feedparser sets bozo=1 for various reasons (soft errors). We accept
    # partial data if there are entries.
    if not parsed.entries:
        err = str(parsed.bozo_exception) if parsed.bozo else "No entries"
        if name == "HuggingFace Daily Papers":
            fallback, fallback_err = fetch_huggingface_daily_papers(feed_spec, category, cutoff, dedup)
            if fallback:
                return fallback, None
            return [], fallback_err or err
        return [], err

    candidates: list[Candidate] = []
    for entry in parsed.entries[:MAX_ENTRIES_PER_FEED]:
        published = parse_entry_datetime(entry)
        if not published or published < cutoff:
            continue

        link = getattr(entry, "link", None)
        if not link:
            continue

        norm = normalize_url(link)
        if norm in dedup:
            continue

        title = strip_html_tags(getattr(entry, "title", "")).strip()
        if not title or len(title) < 5:
            continue

        summary_raw = (
            getattr(entry, "summary", None)
            or getattr(entry, "description", None)
            or ""
        )
        summary = strip_html_tags(summary_raw)[:1500]  # cap length

        # Taiwan-local content filter: even Taiwan-tier feeds (e.g. CNA, PTS)
        # publish a lot of international news. Require a Taiwan-local marker
        # in title or summary to keep this category truly local.
        if category == "taiwan":
            blob = f"{title} {summary}"
            if not _is_taiwan_local(blob):
                continue

        rank_score, quality_signals = score_candidate(
            title=title,
            summary=summary,
            category=category,
            source_name=name,
            source_tier=tier,
            published=published,
        )
        if not passes_quality_gate(category, rank_score, quality_signals):
            continue

        candidates.append(Candidate(
            title=title,
            url=link,
            summary=summary,
            published_at=published.isoformat(),
            source_name=name,
            source_tier=tier,
            category=category,
            rank_score=rank_score,
            quality_signals=quality_signals,
        ))

    return candidates, None


def fetch_all_feeds(config: dict, dedup: set[str]) -> tuple[dict[str, list[Candidate]], FetchReport]:
    """Fetch all RSS feeds and official web pages."""
    report = FetchReport()
    candidates_by_category: dict[str, list[Candidate]] = {}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    rss_feeds = config.get("rss_feeds", {})
    web_pages = config.get("web_pages", {})
    total_feeds = sum(len(feeds) for feeds in rss_feeds.values()) + sum(len(pages) for pages in web_pages.values())
    report.total_feeds = total_feeds
    log.info("Fetching %d sources across %d RSS categories and %d web-page categories", total_feeds, len(rss_feeds), len(web_pages))

    for category, feeds in rss_feeds.items():
        candidates_by_category.setdefault(category, [])
        for feed_spec in feeds:
            candidates, error = fetch_one_feed(feed_spec, category, cutoff, dedup)

            if error:
                report.failed_feeds.append({
                    "name": feed_spec.get("name", "unknown"),
                    "category": category,
                    "error": error[:200],
                })
                log.warning("  ✗ %s: %s", feed_spec.get("name"), error[:100])
                continue

            report.successful_feeds += 1
            report.total_entries_raw += len(candidates)
            # Per-batch dedup
            seen = {normalize_url(c.url) for c in candidates_by_category[category]}
            for c in candidates:
                n = normalize_url(c.url)
                if n not in seen:
                    candidates_by_category[category].append(c)
                    seen.add(n)
            log.info("  ✓ %s: %d entries", feed_spec.get("name"), len(candidates))

    for category, pages in web_pages.items():
        candidates_by_category.setdefault(category, [])
        for page_spec in pages:
            candidates, error = fetch_one_web_page(page_spec, category, cutoff, dedup)

            if error:
                report.failed_feeds.append({
                    "name": page_spec.get("name", "unknown"),
                    "category": category,
                    "error": error[:200],
                })
                log.warning("  ✗ %s: %s", page_spec.get("name"), error[:100])
                continue

            report.successful_feeds += 1
            report.total_entries_raw += len(candidates)
            seen = {normalize_url(c.url) for c in candidates_by_category[category]}
            for c in candidates:
                n = normalize_url(c.url)
                if n not in seen:
                    candidates_by_category[category].append(c)
                    seen.add(n)
            log.info("  ✓ %s: %d web entries", page_spec.get("name"), len(candidates))

    report.after_time_filter = sum(len(v) for v in candidates_by_category.values())
    report.after_dedup = report.after_time_filter  # dedup is already applied

    return candidates_by_category, report


def cap_per_category(
    candidates_by_category: dict[str, list[Candidate]],
    cap: int = MAX_CANDIDATES_PER_CATEGORY,
    soft_caps: dict[str, int] | None = None,
) -> dict[str, list[Candidate]]:
    """Keep top N per category by rank_score."""
    capped: dict[str, list[Candidate]] = {}
    soft_caps = soft_caps or {}
    for cat, cands in candidates_by_category.items():
        sorted_cands = sorted(cands, key=lambda c: c.rank_score, reverse=True)
        capped[cat] = sorted_cands[:int(soft_caps.get(cat, cap))]
    return capped


def apply_global_cap(
    candidates_by_category: dict[str, list[Candidate]],
    total_cap: int = MAX_TOTAL_CANDIDATES,
    min_per_category: int = 3,
    min_category_floors: dict[str, int] | None = None,
) -> dict[str, list[Candidate]]:
    """
    Trim candidates to fit total_cap, but guarantee each non-empty category
    keeps at least min_per_category items (if available).

    This prevents low-tier categories (e.g. Taiwan-local secondary sources)
    from being completely squeezed out by high-tier categories
    (e.g. international primary sources like Nature, Reuters).
    """
    total = sum(len(v) for v in candidates_by_category.values())
    if total <= total_cap:
        return candidates_by_category

    result: dict[str, list[Candidate]] = {cat: [] for cat in candidates_by_category}

    # Phase 1: reserve explicit floor categories first, then give other
    # categories the default minimum only from remaining capacity.
    min_category_floors = min_category_floors or {"taiwan": 4, "enterprise-cases": 2}
    quota_used = 0
    remaining_pool: list[tuple[str, Candidate]] = []
    sorted_by_cat = {
        cat: sorted(cands, key=lambda c: c.rank_score, reverse=True)
        for cat, cands in candidates_by_category.items()
    }
    for cat, floor in min_category_floors.items():
        sorted_cands = sorted_by_cat.get(cat, [])
        quota = min(floor, len(sorted_cands), max(total_cap - quota_used, 0))
        result[cat] = sorted_cands[:quota]
        quota_used += quota

    for cat, sorted_cands in sorted_by_cat.items():
        current = len(result[cat])
        quota = min(max(min_per_category - current, 0), len(sorted_cands) - current, max(total_cap - quota_used, 0))
        if quota > 0:
            result[cat].extend(sorted_cands[current:current + quota])
            quota_used += quota
        # Remaining candidates compete for leftover slots
        remaining_pool.extend((cat, c) for c in sorted_cands[len(result[cat]):])

    # Phase 2: Distribute remaining slots by global rank_score
    leftover_slots = total_cap - quota_used
    if leftover_slots > 0 and remaining_pool:
        remaining_pool.sort(key=lambda x: x[1].rank_score, reverse=True)
        for cat, c in remaining_pool[:leftover_slots]:
            result[cat].append(c)

    return result


def write_candidates_json(serialized: str) -> None:
    """Persist candidates to a stable cache path and the legacy /tmp path.

    Cron jobs used to hand off data through /tmp/candidates.json only. Keeping
    /tmp for compatibility while adding an atomic repo-local cache makes the
    pipeline much less likely to read stale or half-written data.
    """
    for path in (STABLE_CANDIDATES_PATH, LEGACY_CANDIDATES_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(serialized, encoding="utf-8")
        tmp.replace(path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch RSS candidates for Daily Briefing")
    parser.add_argument(
        "--repo-dir",
        type=Path,
        default=Path(os.environ.get("HOME", "/root")) / "Daily-briefing",
        help="Path to Daily-briefing repo (default: ~/Daily-briefing)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print human-readable summary to stderr, no JSON stdout",
    )
    args = parser.parse_args()

    config_path = args.repo_dir / "config" / "sources.yaml"
    issues_dir = args.repo_dir / "data" / "issues"

    try:
        config = load_config(config_path)
    except (FileNotFoundError, ValueError) as e:
        log.error("Config error: %s", e)
        return 1

    dedup = build_dedup_urls(issues_dir)
    candidates_by_category, report = fetch_all_feeds(config, dedup)
    candidates_by_category = cap_per_category(
        candidates_by_category,
        soft_caps=config.get("soft_caps", {}),
    )

    report.after_category_cap = sum(len(v) for v in candidates_by_category.values())
    candidates_by_category = apply_global_cap(candidates_by_category)
    report.final_count = sum(len(v) for v in candidates_by_category.values())

    # Summary to stderr
    log.info("=" * 50)
    log.info("FETCH SUMMARY")
    log.info("  Total feeds:       %d", report.total_feeds)
    log.info("  Successful:        %d", report.successful_feeds)
    log.info("  Failed:            %d", len(report.failed_feeds))
    log.info("  Raw entries:       %d", report.total_entries_raw)
    log.info("  After dedup:       %d", report.after_dedup)
    log.info("  After category cap:%d", report.after_category_cap)
    log.info("  Final candidates:  %d", report.final_count)
    log.info("=" * 50)

    for cat, cands in candidates_by_category.items():
        log.info("  %s: %d candidates", cat, len(cands))

    if report.failed_feeds:
        log.warning("Failed feeds:")
        for f in report.failed_feeds:
            log.warning("  - [%s] %s: %s", f["category"], f["name"], f["error"][:80])

    # Sanity checks
    if report.final_count < 5:
        log.error("Too few candidates (%d); aborting", report.final_count)
        return 1

    # Build and persist JSON before the dry-run exit so diagnostics exercise the
    # same final candidate pool (including semantic dedup) that gen_briefing_v2.py reads.
    output = {
        "generated_at": datetime.now(TAIPEI_TZ).isoformat(),
        "date_taipei": datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d"),
        "report": {
            "total_feeds": report.total_feeds,
            "successful": report.successful_feeds,
            "failed": len(report.failed_feeds),
            "final_candidates": report.final_count,
            "failed_feeds": report.failed_feeds,
        },
        "candidates_by_category": {
            cat: [c.to_dict() for c in cands]
            for cat, cands in candidates_by_category.items()
        },
    }

    try:
        output["weather_report"] = fetch_issue_weather(output["date_taipei"])
    except Exception as e:
        log.error("Weather fetch failed: %s", e)
        return 1

    # Use ensure_ascii=False so the agent sees real Chinese chars in titles
    serialized = json.dumps(output, ensure_ascii=False, indent=2)
    if not args.dry_run:
        print(serialized)

    # Also persist to /tmp/candidates.json so gen_briefing_v2.py reads
    # TODAY's data, not whatever stale file was left from a previous run.
    # Without this, an agent that skips the manual fetch step in the cron
    # prompt will silently regenerate yesterday's briefing.
    try:
        write_candidates_json(serialized)
        log.info("Wrote candidates to %s and %s", STABLE_CANDIDATES_PATH, LEGACY_CANDIDATES_PATH)
    except OSError as e:
        log.error("Failed to write candidate JSON: %s", e)
        return 1

    # --- Run semantic dedup (within-batch + cross-history 7d rolling) -----
    # Failures here are non-fatal: if dedup crashes we still ship the raw
    # candidates rather than break the briefing pipeline.
    try:
        import subprocess
        dedup_script = SCRIPT_DIR / "dedup_candidates.py"
        # Prefer the project venv (has sentence-transformers); fall back to
        # the current interpreter if the venv is missing.
        venv_py = SCRIPT_DIR.parent / ".venv/bin/python"
        py_exec = str(venv_py) if venv_py.exists() else sys.executable
        if dedup_script.exists():
            log.info("Running semantic dedup with %s ...", py_exec)
            result = subprocess.run(
                [py_exec, str(dedup_script)],
                capture_output=True, text=True, timeout=180,
            )
            if result.returncode != 0:
                log.error("dedup failed (rc=%d): %s", result.returncode, result.stderr[-500:])
            else:
                tail = result.stderr.strip().split("\n")[-1] if result.stderr else "done"
                log.info("dedup ok: %s", tail)
        else:
            log.warning("dedup_candidates.py not found, skipping dedup")
    except Exception as e:
        log.error("dedup invocation error: %s", e)
    if args.dry_run:
        log.info("--dry-run: candidates persisted and deduped; not printing JSON to stdout")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
