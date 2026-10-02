"""Weather service for weather-aware outfit recommendations.

Uses Open-Meteo APIs (no API key required):
- Geocoding API for city → lat/lon
- Forecast API for current weather

When location (city or lat/lon) is provided in the chat request, the backend
fetches weather and injects it into the stylist prompt.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# ── Data models ─────────────────────────────────────────────────────────────

@dataclass
class GeoLocation:
    """Resolved location from geocoding (city name → coordinates)."""

    name: str
    latitude: float
    longitude: float
    country: str | None = None


@dataclass
class WeatherInfo:
    """Current weather at a location for styling context."""

    temp_c: float
    feels_like_c: float
    description: str
    wind_speed_kmh: float
    city: str | None = None
    weather_code: int | None = None
    is_day: bool | None = None

    @property
    def is_hot(self) -> bool:
        return self.temp_c >= 25

    @property
    def is_moderate(self) -> bool:
        return 18 <= self.temp_c < 25

    @property
    def is_cold(self) -> bool:
        return self.temp_c < 15

    @property
    def is_rain(self) -> bool:
        if self.weather_code is None:
            return "rain" in self.description.lower() or "drizzle" in self.description.lower()
        return self.weather_code in (51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96, 99)


# ── Open-Meteo weather code → human-readable description ─────────────────────
# WMO codes: https://open-meteo.com/en/docs

_WEATHER_CODES_EL: dict[int, str] = {
    0: "Καθαρός ουρανός",
    1: "Κατά κύριο λόγο καθαρός",
    2: "Λίγες νεφώσεις",
    3: "Συννεφιά",
    45: "Ομίχλη",
    48: "Ομίχλη με πάγο",
    51: "Ψιχάλα (ασθενής)",
    53: "Ψιχάλα (μέτρια)",
    55: "Ψιχάλα (πυκνή)",
    56: "Ψιχάλα με πάγο (ασθενής)",
    57: "Ψιχάλα με πάγο (πυκνή)",
    61: "Βροχή (ασθενής)",
    63: "Βροχή (μέτρια)",
    65: "Βροχή (ισχυρή)",
    66: "Βροχή με πάγο (ασθενής)",
    67: "Βροχή με πάγο (ισχυρή)",
    71: "Χιόνι (ασθενές)",
    73: "Χιόνι (μέτριο)",
    75: "Χιόνι (ισχυρό)",
    77: "Χιονόνερο",
    80: "Τοπική βροχόπτωση (ασθενής)",
    81: "Τοπική βροχόπτωση (μέτρια)",
    82: "Τοπική βροχόπτωση (ισχυρή)",
    85: "Τοπική χιονόπτωση (ασθενής)",
    86: "Τοπική χιονόπτωση (ισχυρή)",
    95: "Καταιγίδα",
    96: "Καταιγίδα με ασθενή χαλάζι",
    99: "Καταιγίδα με ισχυρό χαλάζι",
}

_WEATHER_CODES_EN: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def map_openmeteo_weather_code(code: int, is_day: bool | None, lang: str = "el") -> str:
    """Map Open-Meteo WMO weather code to human-readable description.

    Supports Greek (el) and English (en). is_day is optional (for future
    day/night phrasing if needed).
    """
    d = _WEATHER_CODES_EL if lang == "el" else _WEATHER_CODES_EN
    if code in d:
        return d[code]
    # Fallback by range
    if code <= 3:
        return d.get(0, "Clear") if lang == "en" else "Καθαρός ουρανός"
    if code in (45, 48):
        return d[45]
    if 51 <= code <= 57:
        return d.get(55, d[51])
    if 61 <= code <= 67:
        return d.get(63, d[61])
    if 71 <= code <= 77:
        return d.get(73, d[71])
    if 80 <= code <= 82:
        return d.get(81, d[80])
    if 85 <= code <= 86:
        return d[85]
    if 95 <= code <= 99:
        return d[95]
    return d.get(0, "Unknown") if lang == "en" else "Άγνωστο"


# ── Geocoding ───────────────────────────────────────────────────────────────

def geocode_city(city: str) -> GeoLocation | None:
    """Resolve city name to coordinates using Open-Meteo Geocoding API.

    Returns None if city is empty, request fails, or no results.
    """
    city = (city or "").strip()
    if not city:
        logger.debug("Geocoding skipped: empty city")
        return None

    settings = get_settings()
    base = (settings.OPENMETEO_GEOCODING_URL or "https://geocoding-api.open-meteo.com/v1").rstrip("/")
    url = f"{base}/search"
    timeout = settings.OPENMETEO_TIMEOUT or 10.0
    params = {"name": city, "count": 1, "language": "el", "format": "json"}

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        logger.warning("Geocoding request failed: %s", e)
        return None
    except Exception as e:
        logger.warning("Geocoding error: %s", e)
        return None

    results = data.get("results")
    if not results or not isinstance(results, list):
        logger.debug("Geocoding: no results for city=%r", city)
        return None

    first = results[0]
    if not isinstance(first, dict):
        return None
    try:
        return GeoLocation(
            name=first.get("name") or city,
            latitude=float(first.get("latitude", 0)),
            longitude=float(first.get("longitude", 0)),
            country=first.get("country"),
        )
    except (TypeError, ValueError):
        logger.warning("Geocoding: malformed result for city=%r", city)
        return None


# ── Weather fetch ───────────────────────────────────────────────────────────

def _fetch_weather(lat: float, lon: float, city_name: str | None = None) -> WeatherInfo | None:
    """Fetch current weather from Open-Meteo Forecast API for given coordinates."""
    settings = get_settings()
    base = (settings.OPENMETEO_BASE_URL or "https://api.open-meteo.com/v1").rstrip("/")
    url = f"{base}/forecast"
    timeout = settings.OPENMETEO_TIMEOUT or 10.0
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,is_day",
        "timezone": "auto",
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        logger.warning("Open-Meteo weather request failed: %s", e)
        return None
    except Exception as e:
        logger.warning("Weather fetch error: %s", e)
        return None

    current = data.get("current")
    if not current or not isinstance(current, dict):
        logger.warning("Open-Meteo: missing or malformed 'current' in response")
        return None

    try:
        temp = float(current.get("temperature_2m", 0))
        feels = float(current.get("apparent_temperature", temp))
        code = current.get("weather_code")
        weather_code = int(code) if code is not None else None
        wind_kmh = float(current.get("wind_speed_10m", 0))
        is_day_val = current.get("is_day")
        is_day = bool(is_day_val) if is_day_val is not None else None
    except (TypeError, ValueError) as e:
        logger.warning("Open-Meteo: malformed current values: %s", e)
        return None

    description = map_openmeteo_weather_code(weather_code or 0, is_day, "en")

    return WeatherInfo(
        temp_c=temp,
        feels_like_c=feels,
        description=description,
        wind_speed_kmh=wind_kmh,
        city=city_name,
        weather_code=weather_code,
        is_day=is_day,
    )


def get_weather_by_coords(lat: float, lon: float) -> WeatherInfo | None:
    """Get current weather for the given coordinates (Open-Meteo)."""
    try:
        return _fetch_weather(lat, lon, city_name=None)
    except Exception as e:
        logger.warning("get_weather_by_coords failed: %s", e)
        return None


def get_weather_by_city(city: str) -> WeatherInfo | None:
    """Get current weather for the given city (geocode then fetch). Returns None on failure."""
    loc = geocode_city(city)
    if not loc:
        return None
    try:
        return _fetch_weather(loc.latitude, loc.longitude, city_name=loc.name)
    except Exception as e:
        logger.warning("get_weather_by_city failed: %s", e)
        return None
