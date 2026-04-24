from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

TAIPEI_TZ = timezone(timedelta(hours=8))
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
DEFAULT_TIMEOUT = 20
REQUIRED_LOCATION_IDS = ("taipei", "banqiao", "zhubei")

CITY_SPECS: list[dict[str, Any]] = [
    {
        "location_id": "taipei",
        "location_zh": "台北",
        "location_en": "Taipei",
        "latitude": 25.0375,
        "longitude": 121.5637,
    },
    {
        "location_id": "banqiao",
        "location_zh": "板橋",
        "location_en": "Banqiao",
        "latitude": 25.0119,
        "longitude": 121.4628,
    },
    {
        "location_id": "zhubei",
        "location_zh": "竹北",
        "location_en": "Zhubei",
        "latitude": 24.8387,
        "longitude": 121.0099,
    },
]

WEATHER_CODE_MAP: dict[int, tuple[str, str]] = {
    0: ("晴朗", "Clear sky"),
    1: ("大致晴朗", "Mainly clear"),
    2: ("晴時多雲", "Partly cloudy"),
    3: ("多雲", "Cloudy"),
    45: ("有霧", "Fog"),
    48: ("霧淞", "Depositing rime fog"),
    51: ("毛毛雨", "Light drizzle"),
    53: ("毛毛雨", "Moderate drizzle"),
    55: ("毛毛雨", "Dense drizzle"),
    56: ("凍毛毛雨", "Light freezing drizzle"),
    57: ("凍毛毛雨", "Dense freezing drizzle"),
    61: ("小雨", "Slight rain"),
    63: ("下雨", "Rain"),
    65: ("大雨", "Heavy rain"),
    66: ("凍雨", "Light freezing rain"),
    67: ("凍雨", "Heavy freezing rain"),
    71: ("小雪", "Slight snow fall"),
    73: ("降雪", "Snow fall"),
    75: ("大雪", "Heavy snow fall"),
    77: ("冰粒", "Snow grains"),
    80: ("陣雨", "Showers"),
    81: ("陣雨", "Rain showers"),
    82: ("強陣雨", "Violent rain showers"),
    85: ("陣雪", "Snow showers"),
    86: ("強陣雪", "Heavy snow showers"),
    95: ("雷雨", "Thunderstorm"),
    96: ("雷雨夾冰雹", "Thunderstorm with hail"),
    99: ("強雷雨夾冰雹", "Thunderstorm with heavy hail"),
}


def taipei_today_str() -> str:
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")


def describe_weather_code(code: int | None) -> dict[str, str]:
    zh, en = WEATHER_CODE_MAP.get(code if isinstance(code, int) else -1, ("天氣不明", "Unknown weather"))
    return {"condition_zh": zh, "condition_en": en}


def _request_json(base_url: str, params: dict[str, Any]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    url = f"{base_url}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "DailyBriefing/1.0"})
    with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _round_temp(value: Any) -> int:
    if value is None:
        raise ValueError("temperature_2m is missing")
    numeric_value = float(value)
    if math.isnan(numeric_value):
        raise ValueError("temperature_2m is NaN")
    return int(round(numeric_value))


def build_weather_entry(city_spec: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    current = payload.get("current")
    if not isinstance(current, dict):
        raise ValueError("payload.current must be an object")

    weather_code = current.get("weather_code")
    descriptions = describe_weather_code(int(weather_code) if weather_code is not None else None)

    return {
        "location_id": city_spec["location_id"],
        "location_zh": city_spec["location_zh"],
        "location_en": city_spec["location_en"],
        "temp_c": _round_temp(current.get("temperature_2m")),
        "condition_zh": descriptions["condition_zh"],
        "condition_en": descriptions["condition_en"],
        "weather_code": weather_code,
        "updated_at": current.get("time"),
    }


def _build_historical_entry(city_spec: dict[str, Any], payload: dict[str, Any], target_hour: str = "08:00") -> dict[str, Any]:
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise ValueError("payload.hourly must be an object")

    times = hourly.get("time") or []
    temperatures = hourly.get("temperature_2m") or []
    weather_codes = hourly.get("weather_code") or []
    if not times or not temperatures or not weather_codes:
        raise ValueError("historical hourly payload is incomplete")

    selected_index = None
    for index, timestamp in enumerate(times):
        if timestamp.endswith(target_hour):
            selected_index = index
            break
    if selected_index is None:
        selected_index = 0

    current_payload = {
        "current": {
            "time": times[selected_index],
            "temperature_2m": temperatures[selected_index],
            "weather_code": weather_codes[selected_index],
        }
    }
    return build_weather_entry(city_spec, current_payload)


def fetch_current_weather(city_spec: dict[str, Any]) -> dict[str, Any]:
    payload = _request_json(
        OPEN_METEO_FORECAST_URL,
        {
            "latitude": city_spec["latitude"],
            "longitude": city_spec["longitude"],
            "current": "temperature_2m,weather_code",
            "timezone": "Asia/Taipei",
        },
    )
    return build_weather_entry(city_spec, payload)


def fetch_historical_weather(city_spec: dict[str, Any], date_str: str) -> dict[str, Any]:
    payload = _request_json(
        OPEN_METEO_ARCHIVE_URL,
        {
            "latitude": city_spec["latitude"],
            "longitude": city_spec["longitude"],
            "hourly": "temperature_2m,weather_code",
            "timezone": "Asia/Taipei",
            "start_date": date_str,
            "end_date": date_str,
        },
    )
    return _build_historical_entry(city_spec, payload)


def primary_weather_from_locations(weather_locations: list[dict[str, Any]], primary_id: str = "taipei") -> dict[str, Any]:
    for location in weather_locations:
        if location.get("location_id") == primary_id:
            return {
                "location_zh": location["location_zh"],
                "location_en": location["location_en"],
                "temp_c": location["temp_c"],
                "condition_zh": location["condition_zh"],
                "condition_en": location["condition_en"],
            }
    raise ValueError(f"missing primary weather location: {primary_id}")


def fetch_issue_weather(date_str: str | None = None) -> dict[str, Any]:
    requested_date = date_str or taipei_today_str()
    today = taipei_today_str()
    fetcher = fetch_current_weather if requested_date == today else lambda spec: fetch_historical_weather(spec, requested_date)

    weather_locations = [fetcher(city_spec) for city_spec in CITY_SPECS]
    return {
        "date": requested_date,
        "fetched_at": datetime.now(TAIPEI_TZ).isoformat(),
        "weather": primary_weather_from_locations(weather_locations),
        "weather_locations": weather_locations,
    }
