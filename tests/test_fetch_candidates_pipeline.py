import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import fetch_candidates


class FetchCandidatesPipelineTests(unittest.TestCase):
    def test_build_dedup_urls_reads_dual_edition_issue_filenames(self):
        with tempfile.TemporaryDirectory() as tmp:
            issues = Path(tmp)
            today = datetime.now(fetch_candidates.TAIPEI_TZ).strftime("%Y-%m-%d")
            issue = {
                "articles": [
                    {"source": {"url": "https://example.com/story?utm_source=x"}},
                    {"source": {"url": "https://example.com/other#comments"}},
                ]
            }
            (issues / f"{today}-morning.json").write_text(json.dumps(issue), encoding="utf-8")

            dedup = fetch_candidates.build_dedup_urls(issues, days=7)

            self.assertIn("https://example.com/story", dedup)
            self.assertIn("https://example.com/other", dedup)

    def test_enterprise_cases_requires_ai_relevance(self):
        weak_score, weak_signals = fetch_candidates.score_candidate(
            title="AWS announces new billing dashboard for cloud customers",
            summary="The dashboard helps administrators review invoices and cost allocation reports.",
            category="enterprise-cases",
            source_name="AWS News Blog",
            source_tier="tertiary",
            published=datetime.now(timezone.utc),
        )
        strong_score, strong_signals = fetch_candidates.score_candidate(
            title="AWS launches Bedrock agents for enterprise AI workflows",
            summary="The update adds LLM orchestration, model evaluation, and generative AI automation for cloud teams.",
            category="enterprise-cases",
            source_name="AWS News Blog",
            source_tier="tertiary",
            published=datetime.now(timezone.utc),
        )

        self.assertIn("weak_enterprise_ai_match", weak_signals)
        self.assertFalse(fetch_candidates.passes_quality_gate("enterprise-cases", weak_score, weak_signals))
        self.assertNotIn("weak_enterprise_ai_match", strong_signals)
        self.assertTrue(fetch_candidates.passes_quality_gate("enterprise-cases", strong_score, strong_signals))

    def test_global_cap_honors_category_floors(self):
        def cand(cat, idx, score):
            return fetch_candidates.Candidate(
                title=f"{cat} {idx}",
                url=f"https://example.com/{cat}/{idx}",
                summary="AI model release",
                published_at=datetime.now(timezone.utc).isoformat(),
                source_name="Example",
                source_tier="primary",
                category=cat,
                rank_score=score,
            )

        candidates = {
            "ai-ml": [cand("ai-ml", i, 10 - i) for i in range(10)],
            "taiwan": [cand("taiwan", i, 1 + i / 10) for i in range(5)],
            "enterprise-cases": [cand("enterprise-cases", i, 1 + i / 10) for i in range(3)],
        }

        capped = fetch_candidates.apply_global_cap(
            candidates,
            total_cap=8,
            min_category_floors={"taiwan": 4, "enterprise-cases": 2},
        )

        self.assertGreaterEqual(len(capped["taiwan"]), 4)
        self.assertGreaterEqual(len(capped["enterprise-cases"]), 2)
        self.assertEqual(sum(len(v) for v in capped.values()), 8)


if __name__ == "__main__":
    unittest.main()
