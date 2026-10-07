"""Deterministic synthetic fixtures. These are NOT real market observations."""

import math
from datetime import date, timedelta

from app.models import Source

DEMO_COMPANIES = {
    "NVDA": ("NVIDIA", "Semiconductors", 120, 80_000_000_000, 42_000_000_000, 4.20, 39_000_000_000, 34),
    "AAPL": ("Apple", "Consumer technology", 190, 360_000_000_000, 88_000_000_000, 6.10, 96_000_000_000, 5),
    "MSFT": ("Microsoft", "Software & services", 380, 245_000_000_000, 85_000_000_000, 11.40, 100_000_000_000, 13),
}


def demo_bundle(ticker: str) -> dict:
    if ticker not in DEMO_COMPANIES:
        raise ValueError("The sample demo supports NVDA, AAPL and MSFT. Use live mode for other U.S. tickers.")
    name, sector, start_price, revenue, income, eps, cash, growth = DEMO_COMPANIES[ticker]
    prices = []
    day = date(2026, 4, 1)
    while len(prices) < 90:
        if day.weekday() < 5:
            index = len(prices)
            close = start_price * (1 + index * 0.0012 + math.sin(index / 5) * 0.026 + math.sin(index * 1.8) * 0.008)
            prices.append({"date": day.isoformat(), "close": round(close, 2), "volume": 20_000_000 + index * 53_000})
        day += timedelta(days=1)
    facts = {}
    for key, value in [("revenue", revenue), ("net_income", income), ("eps", eps), ("operating_cash_flow", cash)]:
        facts[key] = {
            "value": value, "unit": "USD/shares" if key == "eps" else "USD",
            "start": "2025-01-01", "end": "2025-12-31", "filed": "2026-02-01",
            "previous_value": round(value / (1 + growth / 100), 2), "yoy_pct": growth,
            "source_id": "D1", "previous_source_id": "D1",
        }
    return {
        "company": {"ticker": ticker, "name": name, "industry": sector},
        "prices": prices, "facts": facts, "filings": [],
        "sources": [Source(id="D1", title="Illustrative demo dataset", url="/static/demo-note.txt",
            as_of="Synthetic example", kind="sample",
            excerpt="All displayed demo prices, financial metrics and commentary are invented examples. They are not real company records or investment research.").model_dump()],
        "warnings": ["SAMPLE DATA: these are illustrative prices and financials, not actual company data. No external API or Groq call is made in demo mode."],
    }
