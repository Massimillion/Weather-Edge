# WeatherEdge → Polymarket Trading Bot

Automatically trades Polymarket weather markets based on signals from
the WeatherEdge dashboard (Open-Meteo + NWS/NOAA data).

---

## How it works

```
Open-Meteo API  ──┐
NWS / NOAA API  ──┼──▶  consensus probability
Historical data ──┘
                          │
                          ▼
              Compare vs. Polymarket price
                          │
                     Edge > 6%?
                          │
                    Kelly bet sizing
                          │
                          ▼
              Polymarket CLOB API  ──▶  Trade executed
```

---

## Requirements

- Python 3.10+
- A Polymarket account with USDC on Polygon
- MetaMask (or any EVM wallet) with your private key

---

## Installation

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run setup (generates your .env with API credentials)
python setup_credentials.py
```

> **Security note:** Your private key only exists in the `.env` file on your machine.
> It is never sent to any server. The bot signs transactions locally.

---

## Step-by-step credential setup

### 1. Export your private key from MetaMask

1. Open MetaMask → click the three dots → Account Details
2. Click "Export Private Key"
3. Enter your MetaMask password
4. Copy the 64-char hex key (without or without the `0x` prefix — setup script handles both)

### 2. Deposit USDC to Polygon

Your Polymarket account needs USDC on the **Polygon network**.

1. Go to [polymarket.com](https://polymarket.com) → Profile → Deposit
2. Deposit at least $10 to get started
3. The bot defaults to a $20 bankroll / $5 max bet — adjust in `bot.py`

### 3. Run setup

```bash
python setup_credentials.py
```

This will:
- Verify your private key connects to Polymarket
- Derive L2 API credentials (a separate key used for API calls)
- Check your USDC balance and approve the CLOB contract
- Save everything to `.env`

---

## Running the bot

### Paper mode (test — no real money)

```bash
python bot.py --mode paper
```

Scans all 15 cities for all 4 event types, prints what trades it *would* make.
Check `trades.json` to see the simulated log.

### Paper mode — single scan

```bash
python bot.py --mode paper --once
```

### Paper mode — specific city/event

```bash
python bot.py --mode paper --city new-york --event rain
python bot.py --mode paper --city seattle --event storm
```

### Live mode — real trades

```bash
python bot.py --mode live
```

Scans every 60 minutes. All trades are logged to `trades.json`.

---

## Configuration

Edit the `CONFIG` dict at the top of `bot.py`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MIN_EDGE` | 0.06 | Only bet when model − market ≥ 6% |
| `MIN_CONSENSUS` | 0.55 | Only bet when consensus ≥ 55% (or ≤ 45%) |
| `MIN_LIQUIDITY` | 1000 | Skip markets with < $1k liquidity |
| `BANKROLL` | 20.00 | Total budget in USDC |
| `MAX_BET` | 5.00 | Cap per bet |
| `MIN_BET` | 1.00 | Polymarket minimum |
| `FRACTION_KELLY` | 0.25 | 25% of full Kelly — conservative |
| `SCAN_INTERVAL_MIN` | 60 | Minutes between scans |
| `DAY_OFFSETS` | [1, 2] | Scan tomorrow and day-after-tomorrow |

---

## File structure

```
weatheredge-bot/
├── bot.py                  ← Main bot (entry point)
├── weather_signals.py      ← Weather data + probability models
├── market_finder.py        ← Polymarket market search
├── setup_credentials.py    ← One-time credential setup
├── requirements.txt
├── .env                    ← Your credentials (gitignored)
├── .env.example            ← Template
└── trades.json             ← Auto-generated trade log
```

---

## Trade log format

Every trade attempt is logged to `trades.json`:

```json
{
  "timestamp": "2026-04-03T14:22:00",
  "mode": "live",
  "question": "Will it rain in New York on April 3?",
  "condition_id": "0xabc...",
  "side": "YES",
  "price": 0.68,
  "size_usdc": 3.50,
  "size_shares": 5.15,
  "city": "new-york",
  "event_type": "rain",
  "target_date": "2026-04-03",
  "model_prob": 0.84,
  "market_price": 0.68,
  "edge": 0.16,
  "nws_prob": 0.80,
  "status": "placed",
  "order_id": "0xdef..."
}
```

---

## Important notes

1. **Fees:** Polymarket charges ~1.5% on each fill. This is already accounted for
   in the edge threshold (6% minimum).

2. **Market availability:** Polymarket weather markets are created a day or two
   in advance and close after the event. Run the bot daily, not weeks ahead.

3. **Liquidity:** Small markets may have wide spreads. The bot skips markets
   with less than $1k liquidity by default.
   
   # Arbitrage Weather Betting Tool

An automated arbitrage tool that identifies and analyzes betting 
opportunities across weather-based prediction markets. Built to 
find pricing discrepancies across platforms that can be exploited 
for low-risk returns.

## What it does
- Monitors weather-based betting markets across multiple platforms
- Identifies arbitrage opportunities where odds discrepancies exist
- Calculates optimal stake distribution to lock in guaranteed returns
- Delivers real-time alerts when actionable opportunities are found

## Why I built it
Arbitrage opportunities in prediction markets are time-sensitive and 
hard to spot manually. This tool automates the analysis so opportunities 
are caught and evaluated instantly.

## Tech & Tools
- Built using Perplexity with Claude Code
- Real-time market data integration

5. **Not financial advice:** This is a research tool. Weather prediction has edge
   but is not guaranteed. Never bet more than you can afford to lose.

6. **Rate limits:** Polymarket allows 100 req/min (public) and 60 orders/min.
   The bot scans at most once per hour, well within limits.
