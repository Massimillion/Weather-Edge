"""
weather_signals.py
------------------
Pulls real forecast data from Open-Meteo and NWS, runs the same
probability models as the WeatherEdge dashboard, and returns
trading signals ready to be acted on by bot.py.
"""

import requests
import json
from datetime import date, timedelta
from dataclasses import dataclass
from typing import Optional

# ── City definitions (lat, lon, NWS grid) ───────────────────────────────────
CITIES = {
    "new-york":    {"name": "New York",    "lat": 40.7128, "lon": -74.0060, "nws": ("OKX", 33, 35)},
    "chicago":     {"name": "Chicago",     "lat": 41.8781, "lon": -87.6298, "nws": ("LOT", 76, 73)},
    "miami":       {"name": "Miami",       "lat": 25.7617, "lon": -80.1918, "nws": ("MFL", 110, 50)},
    "seattle":     {"name": "Seattle",     "lat": 47.6062, "lon": -122.3321,"nws": ("SEW", 125, 68)},
    "denver":      {"name": "Denver",      "lat": 39.7392, "lon": -104.9903,"nws": ("BOU", 63, 62)},
    "los-angeles": {"name": "Los Angeles", "lat": 34.0522, "lon": -118.2437,"nws": ("LOX", 155, 45)},
    "houston":     {"name": "Houston",     "lat": 29.7604, "lon": -95.3698, "nws": ("HGX", 63, 95)},
    "phoenix":     {"name": "Phoenix",     "lat": 33.4484, "lon": -112.074, "nws": ("PSR", 159, 58)},
    "boston":      {"name": "Boston",      "lat": 42.3601, "lon": -71.0589, "nws": ("BOX", 71, 90)},
    "las-vegas":   {"name": "Las Vegas",   "lat": 36.1699, "lon": -115.1398,"nws": ("VEF", 123, 98)},
    "dallas":      {"name": "Dallas",      "lat": 32.7767, "lon": -96.797,  "nws": ("FWD", 89, 104)},
    "atlanta":     {"name": "Atlanta",     "lat": 33.749,  "lon": -84.388,  "nws": ("FFC", 51, 87)},
    "minneapolis": {"name": "Minneapolis", "lat": 44.9778, "lon": -93.265,  "nws": ("MPX", 108, 72)},
    "portland":    {"name": "Portland",    "lat": 45.5231, "lon": -122.6765,"nws": ("PQR", 113, 104)},
    "kansas-city": {"name": "Kansas City", "lat": 39.0997, "lon": -94.5786, "nws": ("EAX", 44, 51)},
}

NWS_HEADERS = {"User-Agent": "WeatherEdgeBot/1.0 (contact@example.com)"}


@dataclass
class WeatherSignal:
    city_key: str
    city_name: str
    event_type: str          # "rain" | "snow" | "storm" | "heat"
    target_date: str         # "YYYY-MM-DD"
    model_prob: float        # 0-1, our estimated probability
    nws_prob: Optional[float]# 0-1, official NWS PoP (None if unavailable)
    consensus_prob: float    # 0-1, weighted average of all models
    raw: dict                # raw forecast data for logging


# ── Fetch Open-Meteo forecast ────────────────────────────────────────────────
def fetch_open_meteo(lat: float, lon: float) -> dict:
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,"
        f"snowfall_sum,windspeed_10m_max,windgusts_10m_max,weathercode,"
        f"precipitation_probability_max,rain_sum,showers_sum,sunshine_duration"
        f"&timezone=auto&forecast_days=7"
    )
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


# ── Fetch NWS forecast ───────────────────────────────────────────────────────
def fetch_nws(grid_id: str, grid_x: int, grid_y: int) -> Optional[list]:
    """Returns list of forecast periods, or None on failure."""
    url = f"https://api.weather.gov/gridpoints/{grid_id}/{grid_x},{grid_y}/forecast"
    try:
        r = requests.get(url, headers=NWS_HEADERS, timeout=10)
        r.raise_for_status()
        return r.json()["properties"]["periods"]
    except Exception as e:
        print(f"  [NWS] Warning: fetch failed ({e})")
        return None


# ── NWS probability extraction ────────────────────────────────────────────────
def nws_probability(periods: list, event_type: str, tomorrow_str: str) -> Optional[float]:
    """
    Finds tomorrow's daytime NWS period and extracts an event probability.
    NWS gives PoP% directly for rain/snow; storm requires text matching.
    """
    if not periods:
        return None

    # Find tomorrow's daytime period (NWS labels it with the weekday name)
    tomorrow = date.fromisoformat(tomorrow_str)
    tomorrow_name = tomorrow.strftime("%A")  # e.g. "Friday"
    daytime = None
    for p in periods:
        if p.get("isDaytime") and tomorrow_name in p.get("name", ""):
            daytime = p
            break
    # Fallback: just use the first future daytime period
    if not daytime:
        for p in periods:
            if p.get("isDaytime"):
                daytime = p
                break

    if not daytime:
        return None

    pop = (daytime.get("probabilityOfPrecipitation") or {}).get("value") or 0
    temp_f = daytime.get("temperature", 60)
    forecast_text = (daytime.get("shortForecast") or "").lower()
    wind_str = (daytime.get("windSpeed") or "0 mph")
    # Parse max wind from "10 to 25 mph" or "15 mph"
    wind_nums = [int(s) for s in wind_str.split() if s.isdigit()]
    wind_mph = max(wind_nums) if wind_nums else 0

    if event_type == "rain":
        return min(pop / 100.0, 0.97)
    elif event_type == "snow":
        if temp_f > 40:
            return min(pop / 100.0 * 0.3, 0.30)  # too warm for snow
        return min(pop / 100.0, 0.97)
    elif event_type == "storm":
        prob = 0.0
        if any(w in forecast_text for w in ["thunder", "storm", "t-storm", "severe"]):
            prob += 0.55
        if wind_mph > 30:
            prob += 0.15
        if wind_mph > 45:
            prob += 0.15
        prob += (pop / 100.0) * 0.15
        return min(prob, 0.97)
    elif event_type == "heat":
        # Heat index — NWS PoP irrelevant
        if temp_f >= 100:
            return 0.85
        elif temp_f >= 95:
            return 0.65
        elif temp_f >= 90:
            return 0.40
        elif temp_f >= 85:
            return 0.15
        return 0.02
    return None


# ── Core event probability model (Open-Meteo data) ──────────────────────────
def forecast_model_prob(d: dict, event_type: str) -> float:
    """
    d: dict of tomorrow's daily values
    Returns probability 0-1
    """
    temp_max = d["temp_max"]
    temp_min = d["temp_min"]
    precip = d["precip"]
    snowfall = d["snowfall"]
    wind_max = d["wind_max"]
    wind_gusts = d["wind_gusts"]
    weather_code = d["weather_code"]
    pop_max = d["pop_max"]          # precipitation_probability_max (0-100)
    rain_sum = d["rain_sum"]
    showers_sum = d["showers_sum"]
    sunshine = d["sunshine"]        # seconds

    if event_type == "rain":
        prob = 0.0
        prob += (pop_max / 100.0) * 0.45          # NWS-style PoP as primary signal
        if precip > 0:   prob += 0.08
        if precip > 5:   prob += 0.08
        if rain_sum > 0: prob += 0.06
        if weather_code in [51,53,55,61,63,65,66,67,80,81,82]: prob += 0.12
        if sunshine == 0 and weather_code >= 2: prob += 0.05
        return min(prob, 0.97)

    elif event_type == "snow":
        prob = 0.0
        if snowfall > 0:  prob += 0.35
        if snowfall > 2:  prob += 0.15
        if snowfall > 10: prob += 0.10
        if temp_max < 2:  prob += 0.15
        if temp_min < -2: prob += 0.10
        if weather_code in [71,73,75,77,85,86]: prob += 0.25
        prob += (pop_max / 100.0) * 0.20
        if temp_min > 5: prob = min(prob, 0.05)  # too warm
        return min(prob, 0.97)

    elif event_type == "storm":
        prob = 0.0
        if weather_code in [95,96,99]: prob += 0.55
        prob += min(wind_gusts / 100.0, 0.20)
        if wind_gusts > 60: prob += 0.10
        if precip > 10 and showers_sum > 2: prob += 0.10
        if pop_max > 60 and wind_gusts > 40: prob += 0.08
        return min(prob, 0.97)

    elif event_type == "heat":
        prob = 0.0
        if temp_max > 40:   prob = 0.90
        elif temp_max > 37: prob = 0.70
        elif temp_max > 35: prob = 0.50
        elif temp_max > 33: prob = 0.30
        elif temp_max > 30: prob = 0.10
        if temp_min > 22: prob += 0.08
        return min(prob, 0.97)

    return 0.0


def composite_model_prob(d: dict, event_type: str) -> float:
    """Feature-weighted composite model."""
    temp_max = d["temp_max"]
    temp_min = d["temp_min"]
    precip = d["precip"]
    snowfall = d["snowfall"]
    wind_max = d["wind_max"]
    wind_gusts = d["wind_gusts"]
    weather_code = d["weather_code"]
    pop_max = d["pop_max"]

    if event_type == "rain":
        score = min(precip / 15.0, 1) * 0.35
        score += (pop_max / 100.0) * 0.30
        if weather_code in [61,63,65,80,81,82]: score += 0.25
        elif weather_code in [51,53,55,66,67]:  score += 0.15
        elif weather_code in [2,3,45,48]:        score += 0.04
        spread = temp_max - temp_min
        if spread < 6: score += 0.06
        return min(score, 0.97)

    elif event_type == "snow":
        score = min(snowfall / 8.0, 1) * 0.35
        if weather_code in [71,73,75,77,85,86]: score += 0.30
        if temp_max < 0:  score += 0.20
        elif temp_max < 3: score += 0.12
        elif temp_max < 6: score += 0.05
        if temp_min > 4: score *= 0.15
        return min(score, 0.97)

    elif event_type == "storm":
        score = 0.0
        if weather_code in [95,96,99]: score += 0.50
        score += min(wind_gusts / 80.0, 0.25)
        score += min(precip / 30.0, 0.15)
        if (temp_max - temp_min) > 12 and temp_max > 20: score += 0.10
        return min(score, 0.97)

    elif event_type == "heat":
        x = (temp_max - 33) / 3.0
        import math
        score = 1 / (1 + math.exp(-x)) * 0.75
        if temp_min > 22: score += 0.10
        if weather_code in [0, 1]: score += 0.05
        if temp_max < 28: score = 0
        return min(score, 0.97)

    return 0.0


# ── Main signal generator ────────────────────────────────────────────────────
def get_signal(city_key: str, event_type: str, day_offset: int = 1) -> WeatherSignal:
    """
    Fetches weather data and returns a WeatherSignal for the given city/event.
    day_offset=1 means tomorrow, 2 = day after, etc.
    """
    city = CITIES[city_key]
    print(f"  Fetching Open-Meteo for {city['name']}...")
    data = fetch_open_meteo(city["lat"], city["lon"])

    daily = data["daily"]
    idx = day_offset  # index into 7-day arrays

    target_date = daily["time"][idx]

    d = {
        "temp_max":    daily["temperature_2m_max"][idx],
        "temp_min":    daily["temperature_2m_min"][idx],
        "precip":      daily["precipitation_sum"][idx],
        "snowfall":    daily["snowfall_sum"][idx],
        "wind_max":    daily["windspeed_10m_max"][idx],
        "wind_gusts":  daily.get("windgusts_10m_max", [0]*7)[idx],
        "weather_code":daily["weathercode"][idx],
        "pop_max":     daily.get("precipitation_probability_max", [0]*7)[idx] or 0,
        "rain_sum":    daily.get("rain_sum", [0]*7)[idx] or 0,
        "showers_sum": daily.get("showers_sum", [0]*7)[idx] or 0,
        "sunshine":    daily.get("sunshine_duration", [0]*7)[idx] or 0,
    }

    # Three model probabilities
    p_forecast  = forecast_model_prob(d, event_type)
    p_composite = composite_model_prob(d, event_type)

    # NWS model
    grid_id, grid_x, grid_y = city["nws"]
    nws_periods = fetch_nws(grid_id, grid_x, grid_y)
    p_nws = nws_probability(nws_periods, event_type, target_date)

    # Consensus: weighted average
    if p_nws is not None:
        p_consensus = p_forecast * 0.35 + p_composite * 0.30 + p_nws * 0.35
    else:
        p_consensus = p_forecast * 0.50 + p_composite * 0.50

    return WeatherSignal(
        city_key=city_key,
        city_name=city["name"],
        event_type=event_type,
        target_date=target_date,
        model_prob=p_forecast,
        nws_prob=p_nws,
        consensus_prob=round(p_consensus, 4),
        raw=d,
    )


if __name__ == "__main__":
    # Quick test
    sig = get_signal("new-york", "rain", day_offset=1)
    print(f"\nSignal: {sig.city_name} — {sig.event_type} on {sig.target_date}")
    print(f"  Forecast model: {sig.model_prob:.1%}")
    print(f"  NWS/NOAA:       {sig.nws_prob:.1%}" if sig.nws_prob else "  NWS/NOAA:       unavailable")
    print(f"  Consensus:      {sig.consensus_prob:.1%}")
