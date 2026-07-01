import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np


@unittest.skipUnless(importlib.util.find_spec("sentence_transformers"), "sentence_transformers unavailable")
class DedupCandidatesTests(unittest.TestCase):
    def test_load_history_reads_recent_published_dual_edition_issues(self):
        from scripts import dedup_candidates

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            issues = repo / "data" / "issues"
            issues.mkdir(parents=True)
            issue = {
                "date": "2099-01-02",
                "articles": [
                    {
                        "title_zh": "Mistral OCR 4 發布，強化文件理解能力",
                        "lede_zh": "Mistral 推出新的 OCR 模型，主打企業文件解析與多模態工作流程。",
                        "source": {"url": "https://example.com/mistral-ocr-4"},
                    }
                ],
            }
            (issues / "2099-01-02-morning.json").write_text(json.dumps(issue), encoding="utf-8")

            class FakeModel:
                def encode(self, texts, **kwargs):
                    return np.array([[1.0, 0.0]], dtype=np.float32)

            with mock.patch.object(dedup_candidates, "REPO", repo), \
                 mock.patch.object(dedup_candidates, "SentenceTransformer", return_value=FakeModel()):
                history = dedup_candidates.load_history_from_issues(history_days=7)

            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["title"], "Mistral OCR 4 發布，強化文件理解能力")
            self.assertEqual(history[0]["recorded_at"], "2099-01-02T00:00:00+00:00")
            self.assertEqual(history[0]["embedding"], [1.0, 0.0])


if __name__ == "__main__":
    unittest.main()
