#!/usr/bin/env python3
"""Deduplicate RSS candidates using local sentence-transformers embeddings.

Two-stage dedup:
1. Within-batch: cluster current candidates; keep one rep per cluster
   (priority: higher source_tier, then earlier published_at).
2. Cross-history: drop candidates similar to anything published in the
   last HISTORY_DAYS (default 7) — across morning AND evening editions.

Pipeline position:
    fetch_candidates.py  → /tmp/candidates.json
    dedup_candidates.py  → /tmp/candidates.json (overwritten, deduped)
    gen_briefing_v2.py   → reads /tmp/candidates.json

Side effects:
    - Updates data/embeddings_history.json (rolling HISTORY_DAYS window).
    - Logs decisions to /tmp/dedup_log.json for tuning.

Defaults (per user 2026-05-03 decision):
    Model: paraphrase-multilingual-MiniLM-L12-v2 (384 dim, ~120MB, multilingual)
    Threshold: 0.85 (drop if cosine >= 0.85)
    Threshold (within-batch fallback): 0.75 if final count < MIN_KEEP
    History window: 7 days
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sentence_transformers import SentenceTransformer

# ---------------------------------------------------------------------------
REPO = Path("/home/hermes/Daily-briefing")
HISTORY_FILE = REPO / "data/embeddings_history.json"
CANDIDATES_FILE = Path("/tmp/candidates.json")
LOG_FILE = Path("/tmp/dedup_log.json")

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
THRESHOLD = 0.85          # drop if similarity >= this
FALLBACK_THRESHOLD = 0.75 # if too few survive, relax to this for batch dedup only
MIN_KEEP_PER_CAT = 2      # if a category has >=2 originally, try to keep >=2
HISTORY_DAYS = 7

TIER_RANK = {"primary": 3, "secondary": 2, "tertiary": 1}

logging.basicConfig(level=logging.INFO, format="[dedup] %(message)s", stream=sys.stderr)
log = logging.getLogger("dedup")


def article_text(a: dict) -> str:
    """Embedding input: title + first 300 chars of summary."""
    title = (a.get("title") or "").strip()
    summary = (a.get("summary") or "").strip()[:300]
    return f"{title}\n\n{summary}".strip()


def article_priority(a: dict) -> tuple:
    """Higher = better. Used to pick cluster representative."""
    tier = TIER_RANK.get(a.get("source_tier", "secondary"), 1)
    # Earlier published_at = better (more original / scoop)
    pub = a.get("published_at") or "9999"
    return (tier, -ord(pub[0]) if pub else 0, pub)


def load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text())
    except Exception as e:
        log.warning(f"history corrupt, resetting: {e}")
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).isoformat()
    return [e for e in data if e.get("recorded_at", "") >= cutoff]


def save_history(entries: list[dict]) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(entries, ensure_ascii=False))


def cosine_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Rows of a vs rows of b. Vectors assumed L2-normalized."""
    return a @ b.T


def within_batch_dedup(
    articles: list[dict],
    embs: np.ndarray,
    threshold: float,
) -> tuple[list[int], list[dict]]:
    """Greedy clustering. Returns (kept_indices, drop_log)."""
    n = len(articles)
    if n == 0:
        return [], []
    sim = cosine_matrix(embs, embs)
    # Sort by priority desc; earlier = preferred representative
    order = sorted(range(n), key=lambda i: article_priority(articles[i]), reverse=True)
    dropped: dict[int, dict] = {}
    kept_set: set[int] = set()
    for i in order:
        if i in dropped:
            continue
        # Compare against all already-kept
        merged = False
        for k in kept_set:
            if sim[i, k] >= threshold:
                dropped[i] = {
                    "dropped": articles[i].get("title", ""),
                    "kept": articles[k].get("title", ""),
                    "similarity": float(sim[i, k]),
                    "reason": "within_batch",
                }
                merged = True
                break
        if not merged:
            kept_set.add(i)
    kept = sorted(kept_set)
    return kept, list(dropped.values())


def cross_history_filter(
    articles: list[dict],
    embs: np.ndarray,
    history: list[dict],
    threshold: float,
) -> tuple[list[int], list[dict]]:
    """Drop articles too similar to history. Returns (kept_indices, drop_log)."""
    if not history or len(articles) == 0:
        return list(range(len(articles))), []
    hist_embs = np.array([h["embedding"] for h in history], dtype=np.float32)
    sim = cosine_matrix(embs, hist_embs)  # (n_articles, n_history)
    kept = []
    dropped = []
    for i, a in enumerate(articles):
        max_j = int(np.argmax(sim[i]))
        max_s = float(sim[i, max_j])
        if max_s >= threshold:
            dropped.append({
                "dropped": a.get("title", ""),
                "matched_history": history[max_j].get("title", ""),
                "matched_date": history[max_j].get("recorded_at", "")[:10],
                "similarity": max_s,
                "reason": "cross_history",
            })
        else:
            kept.append(i)
    return kept, dropped


def main() -> int:
    if not CANDIDATES_FILE.exists():
        log.error("/tmp/candidates.json not found")
        return 1
    data = json.loads(CANDIDATES_FILE.read_text())
    cbc: dict[str, list[dict]] = data.get("candidates_by_category", {})

    # Flatten with category tags so we can rebuild
    flat: list[dict] = []
    flat_cat: list[str] = []
    for cat, arts in cbc.items():
        for a in arts:
            flat.append(a)
            flat_cat.append(cat)

    if not flat:
        log.warning("no candidates to dedup")
        return 0

    log.info(f"loaded {len(flat)} candidates across {len(cbc)} categories")

    # Compute embeddings
    log.info(f"loading model {MODEL_NAME}...")
    model = SentenceTransformer(MODEL_NAME)
    texts = [article_text(a) for a in flat]
    log.info(f"encoding {len(texts)} articles...")
    embs = model.encode(
        texts,
        batch_size=32,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    ).astype(np.float32)

    # Stage 1: within-batch dedup
    kept_idx, batch_drops = within_batch_dedup(flat, embs, THRESHOLD)
    log.info(f"within-batch: {len(flat)} → {len(kept_idx)} (dropped {len(batch_drops)})")

    # Stage 2: cross-history filter
    history = load_history()
    log.info(f"history entries (last {HISTORY_DAYS}d): {len(history)}")
    kept_after_hist_local, hist_drops = cross_history_filter(
        [flat[i] for i in kept_idx],
        embs[kept_idx],
        history,
        THRESHOLD,
    )
    final_idx = [kept_idx[j] for j in kept_after_hist_local]
    log.info(f"cross-history: {len(kept_idx)} → {len(final_idx)} (dropped {len(hist_drops)})")

    # Rebuild candidates_by_category
    new_cbc: dict[str, list[dict]] = {cat: [] for cat in cbc.keys()}
    for j in final_idx:
        new_cbc[flat_cat[j]].append(flat[j])

    # Sanity: warn if any category dropped to 0 from non-empty
    for cat, original in cbc.items():
        if len(original) >= MIN_KEEP_PER_CAT and len(new_cbc[cat]) == 0:
            log.warning(f"category {cat} fully wiped ({len(original)} → 0)")

    data["candidates_by_category"] = new_cbc
    data["dedup"] = {
        "model": MODEL_NAME,
        "threshold": THRESHOLD,
        "original_count": len(flat),
        "after_batch_dedup": len(kept_idx),
        "after_history_dedup": len(final_idx),
        "history_size": len(history),
    }
    CANDIDATES_FILE.write_text(json.dumps(data, ensure_ascii=False))
    log.info(f"wrote {CANDIDATES_FILE}: {len(final_idx)} survivors")

    # Append surviving articles to history
    now_iso = datetime.now(timezone.utc).isoformat()
    new_history_entries = [
        {
            "title": flat[j].get("title", ""),
            "url": flat[j].get("url", ""),
            "recorded_at": now_iso,
            "embedding": embs[j].tolist(),
        }
        for j in final_idx
    ]
    save_history(history + new_history_entries)
    log.info(f"history updated: {len(history)} + {len(new_history_entries)} = {len(history) + len(new_history_entries)}")

    # Log drops for tuning
    LOG_FILE.write_text(json.dumps({
        "timestamp": now_iso,
        "within_batch_drops": batch_drops,
        "cross_history_drops": hist_drops,
    }, ensure_ascii=False, indent=2))
    log.info(f"drop log written to {LOG_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
