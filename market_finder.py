"""
market_finder.py
----------------
Searches Polymarket for active weather markets matching a city + event type.
Returns the condition ID, token IDs, and current YES/NO prices.

Polymarket weather markets typically have titles like:
  "Will it rain in New York on April 3?"
  "Will New York experience rain on April 3?"
  "NYC rain tomorrow?"
"""

import requests
import json
from datetime import date, timedelta
from dataclasses import dataclass
from typing import Optional
import re

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

# Keywords that map event types to common Polymarket phrasing
EVENT_KEYWORDS = {
    "rain":  ["rain", "rainfall", "precipitation"],
    "snow":  ["snow", "snowfall", "blizzard"],
    "storm": ["storm", "thunder", "thunderstorm", "severe"],
    "heat":  ["heat", "hot", "high temperature", "temp above"],
}

CITY_ALIASES = {
    "new-york":    ["new york", "nyc", "manhattan"],
    "chicago":     ["chicago", "chi"],
    "miami":       ["miami"],
    "seattle":     ["seattle"],
    "denver":      ["denver"],
    "los-angeles": ["los angeles", "la", "lax"],
    "houston":     ["houston"],
    "phoenix":     ["phoenix"],
    "boston":      ["boston"],
    "las-vegas":   ["las vegas", "vegas"],
    "dallas":      ["dallas", "dfw"],
    "atlanta":     ["atlanta", "atl"],
    "minneapolis": ["minneapolis", "minneapolis-st. paul", "twin cities"],
    "portland":    ["portland"],
    "kansas-city": ["kansas city", "kc"],
}


@dataclass
class PolymarketWeatherMarket:
    condition_id: str
    question: str
    yes_token_id: str
    no_token_id: str
    yes_price: float    # current market price for YES (0-1)
    no_price: float     # current market price for NO (0-1)
    volume: float
    end_date: str
    active: bool


def _score_market(question: str, city_key: str, event_type: str, target_date: str) -> int:
    """
    Scores how well a Polymarket market question matches what we're looking for.
    Returns 0 if not a match, higher = better match.
    """
    q = question.lower()
    score = 0

    # City match
    city_names = CITY_ALIASES.get(city_key, [city_key.replace("-", " ")])
    city_match = any(alias in q for alias in city_names)
    if not city_match:
        return 0
    score += 3

    # Event type match
    event_words = EVENT_KEYWORDS.get(event_type, [event_type])
    if any(w in q for w in event_words):
        score += 3

    # Date match — check if target date components appear
    if target_date:
        dt = date.fromisoformat(target_date)
        month_name = dt.strftime("%B").lower()   # "april"
        month_abbr = dt.strftime("%b").lower()   # "apr"
        day_str = str(dt.day)                    # "3"

        if month_name in q or month_abbr in q:
            score += 2
        if day_str in q:
            score += 1
        if "tomorrow" in q:
            score += 1

    return score


def search_weather_markets(
    city_key: str,
    event_type: str,
    target_date: str,
    min_liquidity: float = 500.0,
) -> list[PolymarketWeatherMarket]:
    """
    Searches Polymarket Gamma API for active weather markets.
    Returns a list sorted by match score descending.
    """
    # Build search query
    city_name = CITY_ALIASES.get(city_key, [city_key.replace("-", " ")])[0]
    dt = date.fromisoformat(target_date)
    month = dt.strftime("%B")

    # Try a few search terms
    search_terms = [
        f"{city_name} {event_type}",
        f"{city_name} rain",
        f"{city_name} weather",
    ]

    all_markets = []
    seen_ids = set()

    for term in search_terms:
        try:
            url = f"{GAMMA_API}/events?active=true&closed=false&limit=20"
            params = {"tag": "weather"} if term == search_terms[-1] else {"title": term}
            r = requests.get(url, params=params, timeout=10)
            if not r.ok:
                continue
            events = r.json() if isinstance(r.json(), list) else []
            for event in events:
                for market in event.get("markets", []):
                    cid = market.get("conditionId") or market.get("condition_id")
                    if not cid or cid in seen_ids:
                        continue
                    seen_ids.add(cid)
                    all_markets.append(market)
        except Exception as e:
            print(f"  [search] Warning: {e}")

    # Also try direct CLOB market search
    try:
        city_name_clean = city_name.replace(" ", "%20")
        r = requests.get(f"{CLOB_API}/markets?closed=false&limit=100", timeout=10)
        if r.ok:
            data = r.json()
            for m in (data.get("data") or []):
                cid = m.get("condition_id")
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    all_markets.append(m)
    except Exception:
        pass

    # Score and filter
    results = []
    for m in all_markets:
        question = m.get("question") or ""
        score = _score_market(question, city_key, event_type, target_date)
        if score < 3:
            continue

        # Extract token IDs and prices
        try:
            clob_token_ids = json.loads(m.get("clobTokenIds") or m.get("clob_token_ids") or "[]")
        except Exception:
            clob_token_ids = []

        if len(clob_token_ids) < 2:
            continue

        yes_token = clob_token_ids[0]
        no_token  = clob_token_ids[1]

        # Get live prices
        try:
            outcome_prices = json.loads(m.get("outcomePrices") or "[0.5, 0.5]")
            yes_price = float(outcome_prices[0])
            no_price  = float(outcome_prices[1])
        except Exception:
            yes_price, no_price = 0.50, 0.50

        volume = float(m.get("volume") or m.get("volumeNum") or 0)
        liquidity = float(m.get("liquidity") or m.get("liquidityNum") or 0)

        if liquidity < min_liquidity:
            continue

        results.append((score, PolymarketWeatherMarket(
            condition_id=m.get("conditionId") or m.get("condition_id", ""),
            question=question,
            yes_token_id=yes_token,
            no_token_id=no_token,
            yes_price=yes_price,
            no_price=no_price,
            volume=volume,
            end_date=m.get("endDate") or m.get("end_date_iso") or "",
            active=not m.get("closed", False),
        )))

    # Sort by score descending
    results.sort(key=lambda x: x[0], reverse=True)
    return [r[1] for r in results]


def get_live_price(token_id: str) -> Optional[float]:
    """Fetches the current mid-price for a token from the CLOB order book."""
    try:
        r = requests.get(f"{CLOB_API}/midpoint?token_id={token_id}", timeout=5)
        if r.ok:
            return float(r.json().get("mid") or 0.50)
    except Exception:
        pass
    return None


if __name__ == "__main__":
    print("Searching for New York rain markets...")
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    markets = search_weather_markets("new-york", "rain", tomorrow)
    if markets:
        for m in markets[:3]:
            print(f"\n  Question: {m.question}")
            print(f"  Condition ID: {m.condition_id}")
            print(f"  YES: {m.yes_price:.0%}  NO: {m.no_price:.0%}")
            print(f"  Volume: ${m.volume:,.0f}  Active: {m.active}")
    else:
        print("  No matching markets found.")
