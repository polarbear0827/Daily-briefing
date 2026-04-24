import unittest

from scripts.weather_utils import build_weather_entry, describe_weather_code


CITY_SPEC = {
    "location_id": "banqiao",
    "location_zh": "板橋",
    "location_en": "Banqiao",
    "latitude": 25.0119,
    "longitude": 121.4628,
}


class WeatherUtilsTests(unittest.TestCase):
    def test_describe_weather_code_maps_known_code(self):
        description = describe_weather_code(80)

        self.assertEqual(description, {"condition_zh": "陣雨", "condition_en": "Showers"})

    def test_build_weather_entry_uses_api_payload(self):
        payload = {
            "current": {
                "temperature_2m": 24.8,
                "weather_code": 80,
                "time": "2026-04-23T16:30",
            }
        }

        entry = build_weather_entry(CITY_SPEC, payload)

        self.assertEqual(entry["location_id"], "banqiao")
        self.assertEqual(entry["location_zh"], "板橋")
        self.assertEqual(entry["location_en"], "Banqiao")
        self.assertEqual(entry["temp_c"], 25)
        self.assertEqual(entry["condition_zh"], "陣雨")
        self.assertEqual(entry["condition_en"], "Showers")


if __name__ == "__main__":
    unittest.main()
