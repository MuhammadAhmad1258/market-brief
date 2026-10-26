"""Financial-period alignment and market metrics."""

import math
import statistics
from datetime import date


TAGS = {
    "revenue": ("USD", ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]),
    "net_income": ("USD", ["NetIncomeLoss", "ProfitLoss"]),
    "eps": ("USD/shares", ["EarningsPerShareDiluted"]),
    "operating_cash_flow": ("USD", ["NetCashProvidedByUsedInOperatingActivities"]),
}


def _duration(record: dict) -> int:
    try:
        return (date.fromisoformat(record["end"]) - date.fromisoformat(record["start"])).days
    except (KeyError, ValueError):
        return 0


def extract_annual_facts(companyfacts: dict) -> dict:
    """Use actual year-long periods, NOT fiscal labels on comparative records.

    A 10-K often repeats prior years, and an FY label can sit on a quarterly
    comparative. Keep the most recently filed version of each exact period.
    Align every metric with revenue's start/end so margins don't mix periods.
    """
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    metrics = {}
    revenue_period = None
    for metric, (unit, tags) in TAGS.items():
        candidates = []
        for priority, tag in enumerate(tags):
            for record in gaap.get(tag, {}).get("units", {}).get(unit, []):
                if record.get("form") not in {"10-K", "10-K/A"} or not 330 <= _duration(record) <= 380:
                    continue
                value = record.get("val")
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    continue
                candidates.append({**record, "tag": tag, "priority": priority})
        if not candidates:
            metrics[metric] = None
            continue
        periods = {}
        for record in candidates:
            key = (record["start"], record["end"])
            old = periods.get(key)
            if old is None or (record.get("filed", ""), -record["priority"]) > (old.get("filed", ""), -old["priority"]):
                periods[key] = record
        ordered = sorted(periods.values(), key=lambda r: (r["end"], r["start"]), reverse=True)
        current = ordered[0] if metric == "revenue" else next(
            (r for r in ordered if revenue_period and (r["start"], r["end"]) == revenue_period), None
        )
        if current is None:
            metrics[metric] = None
            continue
        if metric == "revenue":
            revenue_period = (current["start"], current["end"])
        # Same-length prior year, ending approximately a year before current.
        previous = next((r for r in ordered if 330 <= (
            date.fromisoformat(current["end"]) - date.fromisoformat(r["end"])
        ).days <= 400 and abs(_duration(r) - _duration(current)) <= 15), None)
        growth = None
        # Percentage growth from zero or a negative base is misleading; omit it.
        if previous and previous["val"] > 0:
            growth = (current["val"] / previous["val"] - 1) * 100
        metrics[metric] = {
            "value": current["val"], "unit": unit,
            "start": current["start"], "end": current["end"],
            "filed": current.get("filed", ""), "accession": current.get("accn", ""),
            "previous_value": previous["val"] if previous else None,
            "previous_start": previous["start"] if previous else None,
            "previous_end": previous["end"] if previous else None,
            "previous_filed": previous.get("filed", "") if previous else None,
            "previous_accession": previous.get("accn", "") if previous else None,
            "yoy_pct": round(growth, 2) if growth is not None else None,
        }
    return metrics


def market_metrics(prices: list[dict]) -> dict:
    valid = [r for r in prices if isinstance(r.get("close"), (int, float))
             and math.isfinite(r["close"]) and r["close"] > 0]
    ordered = sorted(valid, key=lambda r: r["date"])
    if not ordered:
        return {}
    closes = [r["close"] for r in ordered]
    latest = ordered[-1]
    daily = [(b / a - 1) for a, b in zip(closes, closes[1:])]
    move = (closes[-1] / closes[-22] - 1) * 100 if len(closes) >= 22 else None
    volatility = statistics.stdev(daily[-63:]) * math.sqrt(252) * 100 if len(daily) >= 20 else None
    return {
        "close": latest["close"], "as_of": latest["date"],
        "daily_change_pct": round(daily[-1] * 100, 2) if daily else None,
        "month_change_pct": round(move, 2) if move is not None else None,
        "annualized_volatility_pct": round(volatility, 2) if volatility is not None else None,
        "volume": latest.get("volume"),
        "observations": len(closes), "currency": "USD",
        "adjusted": False,
    }


def net_margin(facts: dict) -> float | None:
    revenue, income = facts.get("revenue"), facts.get("net_income")
    if not revenue or not income or revenue["value"] <= 0:
        return None
    if (revenue["start"], revenue["end"]) != (income["start"], income["end"]):
        return None
    return round(income["value"] / revenue["value"] * 100, 2)
