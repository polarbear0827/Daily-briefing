#!/usr/bin/env python3
"""
Daily Briefing — RSS pre-fetch script for Hermes cron.

This script is designed to be attached to a Hermes cron job via the --script
parameter. When the cron fires, Hermes runs this script first, captures its
stdout, and injects it as context into the agent prompt.

Responsibilities:
1. Load config/sources.yaml
2. Fetch all RSS feeds (with graceful failure handling)
3. Deduplicate against last 3 days of issues
4. Rank by source credibility tier
5. Output top N candidates as JSON to stdout

The agent then:
- Reads the JSON candidates from stdout context
- Uses Qwen 3.6 Plus to summarize and translate
- Assembles the final issue JSON
- Commits and pushes to GitHub

Usage:
    python fetch_candidates.py
    python fetch_candidates.py --dry-run  # print summary to stderr, no stdout JSON
    python fetch_candidates.py --repo-dir /path/to/Daily-briefing
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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
LOOKBACK_HOURS = 24
DEDUP_DAYS = 3
MAX_ENTRIES_PER_FEED = 20
MAX_CANDIDATES_PER_CATEGORY = 8
MAX_TOTAL_CANDIDATES = 20  # cap total to keep agent prompt size reasonable
TIER_WEIGHTS = {"primary": 1.5, "secondary": 1.0, "tertiary": 0.5}

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
    """Cheap HTML tag stripper — we don't need bs4 for our purposes."""
    if not text:
        return ""
    # Remove tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


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


def build_dedup_urls(issues_dir: Path, days: int = DEDUP_DAYS) -> set[str]:
    """Read recent issues and return set of normalized URLs."""
    dedup: set[str] = set()
    if not issues_dir.exists():
        log.info("No issues directory yet; dedup set empty")
        return dedup

    cutoff_date = (datetime.now(TAIPEI_TZ) - timedelta(days=days)).date()
    issue_files = sorted(issues_dir.glob("*.json"), reverse=True)

    for issue_file in issue_files[: days + 2]:  # read a few more for safety
        try:
            file_date = datetime.strptime(issue_file.stem, "%Y-%m-%d").date()
            if file_date < cutoff_date:
                break
        except ValueError:
            continue

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

    try:
        parsed = feedparser.parse(
            url,
            request_headers={"User-Agent": "DailyBriefingBot/1.0 (Hermes cron)"},
        )
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"

    # feedparser sets bozo=1 for various reasons (soft errors). We accept
    # partial data if there are entries.
    if not parsed.entries:
        err = str(parsed.bozo_exception) if parsed.bozo else "No entries"
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

        candidates.append(Candidate(
            title=title,
            url=link,
            summary=summary,
            published_at=published.isoformat(),
            source_name=name,
            source_tier=tier,
            category=category,
            rank_score=TIER_WEIGHTS.get(tier, 0.5),
        ))

    return candidates, None


def fetch_all_feeds(config: dict, dedup: set[str]) -> tuple[dict[str, list[Candidate]], FetchReport]:
    """Fetch all feeds, return (candidates_by_category, report)."""
    report = FetchReport()
    candidates_by_category: dict[str, list[Candidate]] = {}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    rss_feeds = config.get("rss_feeds", {})
    total_feeds = sum(len(feeds) for feeds in rss_feeds.values())
    report.total_feeds = total_feeds
    log.info("Fetching %d feeds across %d categories", total_feeds, len(rss_feeds))

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

    report.after_time_filter = sum(len(v) for v in candidates_by_category.values())
    report.after_dedup = report.after_time_filter  # dedup is already applied

    return candidates_by_category, report


def cap_per_category(
    candidates_by_category: dict[str, list[Candidate]],
    cap: int = MAX_CANDIDATES_PER_CATEGORY,
) -> dict[str, list[Candidate]]:
    """Keep top N per category by rank_score."""
    capped: dict[str, list[Candidate]] = {}
    for cat, cands in candidates_by_category.items():
        sorted_cands = sorted(cands, key=lambda c: c.rank_score, reverse=True)
        capped[cat] = sorted_cands[:cap]
    return capped


def apply_global_cap(
    candidates_by_category: dict[str, list[Candidate]],
    total_cap: int = MAX_TOTAL_CANDIDATES,
    min_per_category: int = 3,
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

    # Phase 1: Give each category its minimum quota (sorted by rank_score within cat)
    quota_used = 0
    remaining_pool: list[tuple[str, Candidate]] = []
    for cat, cands in candidates_by_category.items():
        sorted_cands = sorted(cands, key=lambda c: c.rank_score, reverse=True)
        quota = min(min_per_category, len(sorted_cands))
        result[cat] = sorted_cands[:quota]
        quota_used += quota
        # Remaining candidates compete for leftover slots
        remaining_pool.extend((cat, c) for c in sorted_cands[quota:])

    # Phase 2: Distribute remaining slots by global rank_score
    leftover_slots = total_cap - quota_used
    if leftover_slots > 0 and remaining_pool:
        remaining_pool.sort(key=lambda x: x[1].rank_score, reverse=True)
        for cat, c in remaining_pool[:leftover_slots]:
            result[cat].append(c)

    return result


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
    candidates_by_category = cap_per_category(candidates_by_category)

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

    if args.dry_run:
        log.info("--dry-run: not printing JSON to stdout")
        return 0

    # Output JSON to stdout (this goes into Hermes agent prompt)
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
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())