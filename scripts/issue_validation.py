from __future__ import annotations

from numbers import Number
from typing import Any

REQUIRED_TOP_LEVEL_FIELDS = (
    "issue_number",
    "date",
    "date_display_zh",
    "date_display_en",
    "weather",
    "weather_locations",
    "tagline_zh",
    "tagline_en",
    "categories",
    "articles",
    "meta",
)
REQUIRED_WEATHER_LOCATION_IDS = {"taipei", "banqiao", "zhubei"}
VALID_CREDIBILITY_TIERS = {"primary", "secondary", "tertiary"}


def _add_error(errors: list[str], path: str, message: str) -> None:
    errors.append(f"{path}: {message}")


def _require_non_empty_string(errors: list[str], path: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        _add_error(errors, path, "must be a non-empty string")


def _require_number(errors: list[str], path: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, Number):
        _add_error(errors, path, "must be a number")


def _validate_required_fields(errors: list[str], data: dict[str, Any], required_fields: tuple[str, ...], prefix: str = "") -> None:
    for field in required_fields:
        if field not in data:
            _add_error(errors, f"{prefix}{field}", "is required")


def validate_weather_object(weather: Any, path: str = "weather") -> list[str]:
    errors: list[str] = []
    if not isinstance(weather, dict):
        _add_error(errors, path, "must be an object")
        return errors

    _validate_required_fields(errors, weather, ("location_zh", "location_en", "temp_c", "condition_zh", "condition_en"), f"{path}.")
    _require_non_empty_string(errors, f"{path}.location_zh", weather.get("location_zh"))
    _require_non_empty_string(errors, f"{path}.location_en", weather.get("location_en"))
    _require_number(errors, f"{path}.temp_c", weather.get("temp_c"))
    _require_non_empty_string(errors, f"{path}.condition_zh", weather.get("condition_zh"))
    _require_non_empty_string(errors, f"{path}.condition_en", weather.get("condition_en"))
    return errors


def validate_weather_locations(weather_locations: Any) -> list[str]:
    errors: list[str] = []
    path = "weather_locations"
    if not isinstance(weather_locations, list) or not weather_locations:
        _add_error(errors, path, "must be a non-empty array")
        return errors

    seen_ids: set[str] = set()
    for index, location in enumerate(weather_locations):
        item_path = f"{path}[{index}]"
        if not isinstance(location, dict):
            _add_error(errors, item_path, "must be an object")
            continue
        _validate_required_fields(
            errors,
            location,
            ("location_id", "location_zh", "location_en", "temp_c", "condition_zh", "condition_en"),
            f"{item_path}.",
        )
        _require_non_empty_string(errors, f"{item_path}.location_id", location.get("location_id"))
        _require_non_empty_string(errors, f"{item_path}.location_zh", location.get("location_zh"))
        _require_non_empty_string(errors, f"{item_path}.location_en", location.get("location_en"))
        _require_number(errors, f"{item_path}.temp_c", location.get("temp_c"))
        _require_non_empty_string(errors, f"{item_path}.condition_zh", location.get("condition_zh"))
        _require_non_empty_string(errors, f"{item_path}.condition_en", location.get("condition_en"))

        location_id = location.get("location_id")
        if isinstance(location_id, str) and location_id.strip():
            if location_id in seen_ids:
                _add_error(errors, f"{item_path}.location_id", "must be unique")
            seen_ids.add(location_id)

    missing = REQUIRED_WEATHER_LOCATION_IDS - seen_ids
    for location_id in sorted(missing):
        _add_error(errors, path, f"missing required location_id '{location_id}'")

    return errors


def validate_categories(categories: Any) -> tuple[list[str], set[str]]:
    errors: list[str] = []
    ids: set[str] = set()
    path = "categories"
    if not isinstance(categories, list) or not categories:
        _add_error(errors, path, "must be a non-empty array")
        return errors, ids

    for index, category in enumerate(categories):
        item_path = f"{path}[{index}]"
        if not isinstance(category, dict):
            _add_error(errors, item_path, "must be an object")
            continue
        _validate_required_fields(errors, category, ("id", "name_zh", "name_en"), f"{item_path}.")
        category_id = category.get("id")
        _require_non_empty_string(errors, f"{item_path}.id", category_id)
        _require_non_empty_string(errors, f"{item_path}.name_zh", category.get("name_zh"))
        _require_non_empty_string(errors, f"{item_path}.name_en", category.get("name_en"))
        if isinstance(category_id, str) and category_id.strip():
            ids.add(category_id)
    return errors, ids


def validate_articles(articles: Any, category_ids: set[str]) -> list[str]:
    errors: list[str] = []
    path = "articles"
    if not isinstance(articles, list):
        _add_error(errors, path, "must be an array")
        return errors

    headline_count = 0
    for index, article in enumerate(articles):
        item_path = f"{path}[{index}]"
        if not isinstance(article, dict):
            _add_error(errors, item_path, "must be an object")
            continue
        _validate_required_fields(
            errors,
            article,
            (
                "id",
                "category",
                "is_headline",
                "title_zh",
                "title_en",
                "lede_zh",
                "lede_en",
                "source",
                "fetched_via",
            ),
            f"{item_path}.",
        )
        _require_non_empty_string(errors, f"{item_path}.id", article.get("id"))
        category = article.get("category")
        _require_non_empty_string(errors, f"{item_path}.category", category)
        if isinstance(category, str) and category_ids and category not in category_ids:
            _add_error(errors, f"{item_path}.category", "must reference an existing category id")
        if not isinstance(article.get("is_headline"), bool):
            _add_error(errors, f"{item_path}.is_headline", "must be a boolean")
        elif article.get("is_headline"):
            headline_count += 1
        _require_non_empty_string(errors, f"{item_path}.title_zh", article.get("title_zh"))
        _require_non_empty_string(errors, f"{item_path}.title_en", article.get("title_en"))
        _require_non_empty_string(errors, f"{item_path}.lede_zh", article.get("lede_zh"))
        _require_non_empty_string(errors, f"{item_path}.lede_en", article.get("lede_en"))

        for bullets_key in ("bullets_zh", "bullets_en"):
            if bullets_key not in article:
                continue
            bullets = article.get(bullets_key)
            if not isinstance(bullets, list):
                _add_error(errors, f"{item_path}.{bullets_key}", "must be an array if present")
            else:
                for bullet_index, bullet in enumerate(bullets):
                    if not isinstance(bullet, str):
                        _add_error(errors, f"{item_path}.{bullets_key}[{bullet_index}]", "must be a string")

        source = article.get("source")
        source_path = f"{item_path}.source"
        if not isinstance(source, dict):
            _add_error(errors, source_path, "must be an object")
        else:
            _validate_required_fields(
                errors,
                source,
                ("name", "url", "published_at", "reading_time_min", "credibility_tier"),
                f"{source_path}.",
            )
            _require_non_empty_string(errors, f"{source_path}.name", source.get("name"))
            _require_non_empty_string(errors, f"{source_path}.url", source.get("url"))
            _require_non_empty_string(errors, f"{source_path}.published_at", source.get("published_at"))
            _require_number(errors, f"{source_path}.reading_time_min", source.get("reading_time_min"))
            credibility_tier = source.get("credibility_tier")
            _require_non_empty_string(errors, f"{source_path}.credibility_tier", credibility_tier)
            if isinstance(credibility_tier, str) and credibility_tier not in VALID_CREDIBILITY_TIERS:
                _add_error(errors, f"{source_path}.credibility_tier", "must be primary, secondary, or tertiary")

        fetched_via = article.get("fetched_via")
        _require_non_empty_string(errors, f"{item_path}.fetched_via", fetched_via)

    if articles and headline_count != 1:
        _add_error(errors, path, f"must contain exactly 1 headline article, found {headline_count}")

    return errors


def validate_meta(meta: Any, article_count: int) -> list[str]:
    errors: list[str] = []
    path = "meta"
    if not isinstance(meta, dict):
        _add_error(errors, path, "must be an object")
        return errors

    _validate_required_fields(errors, meta, ("generated_at", "total_articles", "sources_used", "schema_version"), f"{path}.")
    _require_non_empty_string(errors, f"{path}.generated_at", meta.get("generated_at"))
    _require_number(errors, f"{path}.total_articles", meta.get("total_articles"))
    _require_non_empty_string(errors, f"{path}.schema_version", meta.get("schema_version"))

    sources_used = meta.get("sources_used")
    if not isinstance(sources_used, dict):
        _add_error(errors, f"{path}.sources_used", "must be an object")
    else:
        _require_number(errors, f"{path}.sources_used.rss", sources_used.get("rss"))
        _require_number(errors, f"{path}.sources_used.firecrawl", sources_used.get("firecrawl"))

    total_articles = meta.get("total_articles")
    if isinstance(total_articles, Number) and int(total_articles) != article_count:
        _add_error(errors, f"{path}.total_articles", f"must equal articles.length ({article_count})")

    return errors


def validate_issue_data(issue: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(issue, dict):
        return ["issue: must be an object"]

    _validate_required_fields(errors, issue, REQUIRED_TOP_LEVEL_FIELDS)

    if not isinstance(issue.get("issue_number"), int):
        _add_error(errors, "issue_number", "must be an integer")
    _require_non_empty_string(errors, "date", issue.get("date"))
    _require_non_empty_string(errors, "date_display_zh", issue.get("date_display_zh"))
    _require_non_empty_string(errors, "date_display_en", issue.get("date_display_en"))
    _require_non_empty_string(errors, "tagline_zh", issue.get("tagline_zh"))
    _require_non_empty_string(errors, "tagline_en", issue.get("tagline_en"))

    errors.extend(validate_weather_object(issue.get("weather"), "weather"))
    errors.extend(validate_weather_locations(issue.get("weather_locations")))
    category_errors, category_ids = validate_categories(issue.get("categories"))
    errors.extend(category_errors)
    errors.extend(validate_articles(issue.get("articles"), category_ids))
    article_count = len(issue.get("articles")) if isinstance(issue.get("articles"), list) else 0
    errors.extend(validate_meta(issue.get("meta"), article_count))

    # Optional: breaking_news must be a list of strings referencing existing article ids.
    if "breaking_news" in issue:
        bn = issue.get("breaking_news")
        if not isinstance(bn, list):
            _add_error(errors, "breaking_news", "must be an array if present")
        else:
            article_ids = set()
            if isinstance(issue.get("articles"), list):
                article_ids = {a.get("id") for a in issue["articles"] if isinstance(a, dict)}
            for i, v in enumerate(bn):
                if not isinstance(v, str) or not v.strip():
                    _add_error(errors, f"breaking_news[{i}]", "must be a non-empty string")
                elif article_ids and v not in article_ids:
                    _add_error(errors, f"breaking_news[{i}]", "must reference an existing article id")

    weather = issue.get("weather")
    weather_locations = issue.get("weather_locations")
    if isinstance(weather, dict) and isinstance(weather_locations, list):
        taipei_entry = next((item for item in weather_locations if isinstance(item, dict) and item.get("location_id") == "taipei"), None)
        if taipei_entry:
            expected_pairs = {
                "location_zh": taipei_entry.get("location_zh"),
                "location_en": taipei_entry.get("location_en"),
                "temp_c": taipei_entry.get("temp_c"),
                "condition_zh": taipei_entry.get("condition_zh"),
                "condition_en": taipei_entry.get("condition_en"),
            }
            for key, expected_value in expected_pairs.items():
                if weather.get(key) != expected_value:
                    _add_error(errors, f"weather.{key}", "must match the taipei entry in weather_locations")

    return errors
