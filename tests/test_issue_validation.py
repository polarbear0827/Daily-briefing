import copy
import unittest

from scripts.issue_validation import validate_issue_data


VALID_ISSUE = {
    "issue_number": 1,
    "date": "2026-04-23",
    "date_display_zh": "2026年4月23日·星期四",
    "date_display_en": "Thursday, April 23, 2026",
    "weather": {
        "location_zh": "台北",
        "location_en": "Taipei",
        "temp_c": 25,
        "condition_zh": "陣雨",
        "condition_en": "Showers",
    },
    "weather_locations": [
        {
            "location_id": "taipei",
            "location_zh": "台北",
            "location_en": "Taipei",
            "temp_c": 25,
            "condition_zh": "陣雨",
            "condition_en": "Showers",
        },
        {
            "location_id": "banqiao",
            "location_zh": "板橋",
            "location_en": "Banqiao",
            "temp_c": 25,
            "condition_zh": "陣雨",
            "condition_en": "Showers",
        },
        {
            "location_id": "zhubei",
            "location_zh": "竹北",
            "location_en": "Zhubei",
            "temp_c": 26,
            "condition_zh": "多雲",
            "condition_en": "Cloudy",
        },
    ],
    "tagline_zh": "由 AI 為你策展，專屬早晨讀物",
    "tagline_en": "AI-curated, your morning read",
    "categories": [
        {"id": "ai-ml", "name_zh": "AI／機器學習", "name_en": "AI / Machine Learning"}
    ],
    "articles": [
        {
            "id": "2026-04-23-001",
            "category": "ai-ml",
            "is_headline": True,
            "title_zh": "測試標題中文足夠長度示意",
            "title_en": "Test English headline with enough words",
            "lede_zh": "這是一段足夠長的中文導言，用於測試驗證器是否正確接受完整欄位內容。",
            "lede_en": "This is a sufficiently long English lede used to verify that the validator accepts complete article content.",
            "bullets_zh": ["第一點測試內容足夠完整", "第二點測試內容足夠完整", "第三點測試內容足夠完整"],
            "bullets_en": [
                "First testing bullet contains enough descriptive detail",
                "Second testing bullet contains enough descriptive detail",
                "Third testing bullet contains enough descriptive detail",
            ],
            "source": {
                "name": "OpenAI Blog",
                "url": "https://example.com/article",
                "published_at": "2026-04-23T08:00:00+08:00",
                "reading_time_min": 3,
                "credibility_tier": "primary",
            },
            "fetched_via": "rss",
        }
    ],
    "meta": {
        "generated_at": "2026-04-23T08:05:00+08:00",
        "total_articles": 1,
        "sources_used": {"rss": 1, "firecrawl": 0},
        "schema_version": "1.0",
    },
}


class IssueValidationTests(unittest.TestCase):
    def test_valid_issue_passes(self):
        errors = validate_issue_data(copy.deepcopy(VALID_ISSUE))
        self.assertEqual(errors, [])

    def test_weather_temp_cannot_be_null(self):
        issue = copy.deepcopy(VALID_ISSUE)
        issue["weather"]["temp_c"] = None

        errors = validate_issue_data(issue)

        self.assertTrue(any("weather.temp_c" in error for error in errors), errors)

    def test_weather_locations_must_include_banqiao_and_zhubei(self):
        issue = copy.deepcopy(VALID_ISSUE)
        issue["weather_locations"] = issue["weather_locations"][:1]

        errors = validate_issue_data(issue)

        self.assertTrue(any("weather_locations" in error and "banqiao" in error.lower() for error in errors), errors)
        self.assertTrue(any("weather_locations" in error and "zhubei" in error.lower() for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
