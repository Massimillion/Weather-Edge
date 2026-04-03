"""
bot.py
------
WeatherEdge → Polymarket Trading Bot

Runs a scan loop:
  1. Pull weather signals from Open-Meteo + NWS
  2. Find matching active Polymarket markets
  3. Compare consensus probability vs. market price
  4. If edge > threshold, place a limit order
  5. Log everything to trades.json

USAGE:
  python bot.py --mode paper       # Simulate only, no real trades
  python bot.py --mode live        # Real trades (requires .env credentials)
  python bot.py --once             # Run one scan then exit
  python bot.py --city new-york --event rain   # Single city/event scan

SETUP:
  See README.md for credential setup instructions.
"""

import os
import sys
import json
import time
import argparse
import logging
from datetime import date, timedelta, datetime
from typing import Optional
from dataclasses import asdict

from dotenv import load_dotenv

from weather_signals import get_signal, CITIES, WeatherSignal
from market_finder import search_weather_markets, get_live_price, PolymarketWeatherMarket

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("weatheredge-bot")

# ── Config ────────────────────────────────────────────────────────────────────
load_dotenv()

CONFIG = {
    # Trading parameters
    "MIN_EDGE":          0.06,   # Minimum edge to bet (6% = model prob vs market price)
    "MIN_CONSENSUS":     0.55,   # Don't bet if consensus < 55% or > 45%
    "MIN_LIQUIDITY":     1000,   # Minimum $1k liquidity in the market
    "BANKROLL":          20.00,  # Total bankroll in USDC
    "MAX_BET":           5.00,   # Maximum single bet
    "MIN_BET":           1.00,   # Polymarket minimum
    "FRACTION_KELLY":    0.25,   # 25% fractional Kelly
    "DIVERGENCE_MAX":    0.08,   # Don't bet if model spread > 8%

    # Scanning
    "SCAN_INTERVAL_MIN": 60,     # How often to scan (minutes)
    "CITIES_TO_SCAN":    list(CITIES.keys()),
    "EVENTS_TO_SCAN":    ["rain", "snow", "storm", "heat"],
    "DAY_OFFSETS":       [1, 2], # Tomorrow and day after

    # Polymarket
    "CLOB_HOST":   "https://clob.polymarket.com",
    "CHAIN_ID":    137,
}

TRADE_LOG_FILE = "trades.json"


# ── Kelly bet sizing ──────────────────────────────────────────────────────────
def kelly_bet(model_prob: float, market_price: float) -> float:
    """Returns recommended bet size in USDC."""
    if market_price <= 0 or market_price >= 1 or model_prob <= market_price:
        return 0.0
    edge = model_prob - market_price
    odds = 1 - market_price
    full_kelly = edge / odds
    bet = full_kelly * CONFIG["FRACTION_KELLY"] * CONFIG["BANKROLL"]
    return round(min(max(bet, 0), CONFIG["MAX_BET"]), 2)


# ── Trade logging ─────────────────────────────────────────────────────────────
def log_trade(record: dict):
    """Appends a trade record to trades.json."""
    trades = []
    if os.path.exists(TRADE_LOG_FILE):
        with open(TRADE_LOG_FILE) as f:
            try:
                trades = json.load(f)
            except Exception:
                trades = []
    trades.append(record)
    with open(TRADE_LOG_FILE, "w") as f:
        json.dump(trades, f, indent=2, default=str)


# ── Polymarket order placement ────────────────────────────────────────────────
def place_order(
    market: PolymarketWeatherMarket,
    side: str,          # "YES" or "NO"
    price: float,       # limit price (0-1)
    size_usdc: float,   # dollar amount to bet
    mode: str,          # "paper" or "live"
) -> dict:
    """
    Places a limit order on Polymarket.
    In paper mode: logs the would-be order without executing.
    In live mode: calls py-clob-client to sign and submit.
    """
    token_id = market.yes_token_id if side == "YES" else market.no_token_id
    # Shares = dollars / price
    size_shares = round(size_usdc / price, 2)

    record = {
        "timestamp": datetime.utcnow().isoformat(),
        "mode": mode,
        "question": market.question,
        "condition_id": market.condition_id,
        "token_id": token_id,
        "side": side,
        "price": price,
        "size_usdc": size_usdc,
        "size_shares": size_shares,
        "status": "paper" if mode == "paper" else "pending",
        "order_id": None,
        "error": None,
    }

    if mode == "paper":
        log.info(f"  [PAPER] Would buy {size_shares:.1f} {side} shares @ {price:.2f} (${size_usdc:.2f})")
        log.info(f"         Market: {market.question}")
        record["status"] = "paper"
        return record

    # ── Live order placement ──
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        private_key   = os.environ["POLYMARKET_PRIVATE_KEY"]
        api_key       = os.environ.get("POLYMARKET_API_KEY")
        api_secret    = os.environ.get("POLYMARKET_API_SECRET")
        api_passphrase= os.environ.get("POLYMARKET_API_PASSPHRASE")
        funder        = os.environ.get("POLYMARKET_FUNDER_ADDRESS", "")
        sig_type      = int(os.environ.get("POLYMARKET_SIGNATURE_TYPE", "1"))

        if api_key and api_secret and api_passphrase:
            from py_clob_client.client import ApiCreds
            creds = ApiCreds(
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
            )
        else:
            creds = None

        client = ClobClient(
            host=CONFIG["CLOB_HOST"],
            key=private_key,
            chain_id=CONFIG["CHAIN_ID"],
            creds=creds,
            signature_type=sig_type,
            funder=funder or None,
        )

        # Derive credentials if not provided
        if not creds:
            log.info("  Deriving API credentials from private key...")
            derived = client.derive_api_key()
            client.set_api_creds(derived)

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size_shares,
            side=BUY,
        )
        resp = client.create_and_post_order(order_args)
        order_id = resp.get("orderID") or resp.get("id") if isinstance(resp, dict) else str(resp)
        record["order_id"] = order_id
        record["status"] = "placed"
        log.info(f"  [LIVE] Order placed: {order_id}")

    except ImportError:
        log.error("  py-clob-client not installed. Run: pip install py-clob-client")
        record["status"] = "error"
        record["error"] = "py-clob-client not installed"
    except KeyError as e:
        log.error(f"  Missing environment variable: {e}")
        record["status"] = "error"
        record["error"] = f"Missing env var: {e}"
    except Exception as e:
        log.error(f"  Order failed: {e}")
        record["status"] = "error"
        record["error"] = str(e)

    return record


# ── Main scan logic ───────────────────────────────────────────────────────────
def scan_once(mode: str, city_filter: Optional[str] = None, event_filter: Optional[str] = None):
    """Runs a full scan and places orders where edge exists."""
    cities = [city_filter] if city_filter else CONFIG["CITIES_TO_SCAN"]
    events = [event_filter] if event_filter else CONFIG["EVENTS_TO_SCAN"]
    opportunities = []

    log.info(f"=== Scanning {len(cities)} cities × {len(events)} events ===")

    for city_key in cities:
        for event_type in events:
            for day_offset in CONFIG["DAY_OFFSETS"]:
                target_date = (date.today() + timedelta(days=day_offset)).isoformat()
                log.info(f"[{city_key}] {event_type} on {target_date}")

                try:
                    signal = get_signal(city_key, event_type, day_offset)
                except Exception as e:
                    log.warning(f"  Signal fetch failed: {e}")
                    continue

                consensus = signal.consensus_prob
                log.info(f"  Consensus: {consensus:.1%}  (NWS: {signal.nws_prob:.1%}" if signal.nws_prob else
                         f"  Consensus: {consensus:.1%}  (NWS: unavailable)")

                # Skip if no meaningful edge direction
                # We bet YES if consensus > 50%, NO if consensus < 50%
                bet_yes = consensus >= 0.50
                bet_no  = not bet_yes
                model_prob = consensus if bet_yes else (1 - consensus)

                if model_prob < CONFIG["MIN_CONSENSUS"]:
                    log.info(f"  Skip — consensus {model_prob:.1%} < min {CONFIG['MIN_CONSENSUS']:.1%}")
                    continue

                # Find matching Polymarket market
                markets = search_weather_markets(
                    city_key, event_type, target_date,
                    min_liquidity=CONFIG["MIN_LIQUIDITY"]
                )

                if not markets:
                    log.info(f"  No matching Polymarket markets found")
                    continue

                market = markets[0]
                # Refresh live price
                live_yes = get_live_price(market.yes_token_id) or market.yes_price
                live_no  = get_live_price(market.no_token_id)  or market.no_price
                market.yes_price = live_yes
                market.no_price  = live_no

                market_price = live_yes if bet_yes else live_no
                edge = model_prob - market_price

                log.info(f"  Market: {market.question[:70]}")
                log.info(f"  YES: {live_yes:.2f}  NO: {live_no:.2f}")
                log.info(f"  Model: {model_prob:.1%}  Market: {market_price:.1%}  Edge: {edge:+.1%}")

                if edge < CONFIG["MIN_EDGE"]:
                    log.info(f"  Skip — edge {edge:.1%} < min {CONFIG['MIN_EDGE']:.1%}")
                    continue

                # Size the bet
                bet_size = kelly_bet(model_prob, market_price)
                if bet_size < CONFIG["MIN_BET"]:
                    log.info(f"  Skip — Kelly bet ${bet_size:.2f} below min ${CONFIG['MIN_BET']:.2f}")
                    continue

                side = "YES" if bet_yes else "NO"
                log.info(f"  *** BET {side} ${bet_size:.2f} (edge={edge:.1%}, Kelly) ***")

                order = place_order(market, side, market_price, bet_size, mode)
                order.update({
                    "city": city_key,
                    "event_type": event_type,
                    "target_date": target_date,
                    "model_prob": consensus,
                    "market_price": market_price,
                    "edge": round(edge, 4),
                    "nws_prob": signal.nws_prob,
                })
                log_trade(order)
                opportunities.append(order)

    log.info(f"=== Scan complete: {len(opportunities)} opportunities acted on ===\n")
    return opportunities


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="WeatherEdge Polymarket Trading Bot")
    parser.add_argument("--mode", choices=["paper", "live"], default="paper",
                        help="paper = simulate only, live = real trades")
    parser.add_argument("--once", action="store_true",
                        help="Run one scan then exit (default: loop)")
    parser.add_argument("--city", help="Only scan this city key (e.g. new-york)")
    parser.add_argument("--event", help="Only scan this event type (rain/snow/storm/heat)")
    parser.add_argument("--interval", type=int, default=CONFIG["SCAN_INTERVAL_MIN"],
                        help="Minutes between scans (default: 60)")
    args = parser.parse_args()

    if args.mode == "live":
        if not os.environ.get("POLYMARKET_PRIVATE_KEY"):
            log.error("POLYMARKET_PRIVATE_KEY not set. See README.md for setup.")
            sys.exit(1)
        log.warning("=== LIVE MODE — real money will be traded ===")
        time.sleep(3)  # Give user a moment to cancel

    log.info(f"WeatherEdge Bot starting  mode={args.mode}")
    log.info(f"Bankroll: ${CONFIG['BANKROLL']}  Max bet: ${CONFIG['MAX_BET']}  Min edge: {CONFIG['MIN_EDGE']:.0%}")

    if args.once:
        scan_once(args.mode, args.city, args.event)
    else:
        interval_sec = args.interval * 60
        while True:
            scan_once(args.mode, args.city, args.event)
            log.info(f"Next scan in {args.interval} minutes. Press Ctrl+C to stop.\n")
            time.sleep(interval_sec)


if __name__ == "__main__":
    main()
