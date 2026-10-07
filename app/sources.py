"""Only these fixed provider URLs are fetched. The user supplies a ticker, not a URL."""

import asyncio
import copy
import time
from datetime import datetime, timezone

import httpx

from app.config import Settings
from app.finance import extract_annual_facts
from app.models import Source


class DataUnavailable(Exception):
    """An actionable error that is safe to show without leaking a key."""


class DataTools:
    def __init__(self, config: Settings):
        self.config = config
        self.cache = {}
        self.lock = asyncio.Lock()
        self.alpha_calls = 0
        self.alpha_day = ""

    async def _json(self, url: str, *, params=None, sec=False) -> dict:
        key = (url, tuple(sorted((params or {}).items())))
        # One short lock also coalesces simultaneous requests for the same ticker.
        async with self.lock:
            now = time.monotonic()
            self.cache = {k: v for k, v in self.cache.items() if v[0] > now}
            if key in self.cache:
                return copy.deepcopy(self.cache[key][1])
            if not sec:
                today = datetime.now(timezone.utc).date().isoformat()
                if self.alpha_day != today:
                    self.alpha_day, self.alpha_calls = today, 0
                if self.alpha_calls >= self.config.alpha_daily_budget:
                    raise DataUnavailable("The local daily market-data budget is exhausted. Try a cached ticker or the sample demo.")
                self.alpha_calls += 1
            headers = {"User-Agent": self.config.sec_user_agent} if sec else {}
            try:
                async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
                    response = await client.get(url, params=params, headers=headers)
                    response.raise_for_status()
                    payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Expected JSON object")
            except (httpx.HTTPError, ValueError):
                provider = "SEC" if sec else "Alpha Vantage"
                raise DataUnavailable(f"{provider} could not return data. Check configuration, provider limits and network access.") from None
            if not sec and any(k in payload for k in ["Note", "Information", "Error Message"]):
                raise DataUnavailable("Alpha Vantage rejected the request: the key, ticker, daily limit or endpoint access needs checking.")
            ttl = 86_400 if "company_tickers" in url else self.config.cache_seconds
            self.cache[key] = (now + ttl, payload)
            return copy.deepcopy(payload)

    async def market_data(self, ticker: str) -> list[dict]:
        payload = await self._json("https://www.alphavantage.co/query", params={
            "function": "TIME_SERIES_DAILY", "symbol": ticker,
            "outputsize": "compact", "apikey": self.config.alpha_vantage_api_key,
        })
        series = payload.get("Time Series (Daily)", {})
        if not series:
            raise DataUnavailable("No daily prices were returned for this ticker.")
        return [{"date": day, "close": float(row["4. close"]), "volume": int(row["5. volume"])}
                for day, row in sorted(series.items())]

    async def company_data(self, ticker: str) -> dict:
        registry = await self._json("https://www.sec.gov/files/company_tickers.json", sec=True)
        entry = next((r for r in registry.values() if r.get("ticker", "").upper() == ticker), None)
        if not entry:
            raise DataUnavailable("This ticker was not found in the SEC company registry. V1 supports U.S. reporting companies, not crypto or funds.")
        cik = str(entry["cik_str"]).zfill(10)
        base = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        payload = await self._json(base, sec=True)
        facts = extract_annual_facts(payload)
        submissions = await self._json(f"https://data.sec.gov/submissions/CIK{cik}.json", sec=True)
        recent = submissions.get("filings", {}).get("recent", {})
        filings, sources = [], []
        for index, form in enumerate(recent.get("form", [])):
            if form not in {"10-K", "10-Q", "8-K", "10-K/A", "10-Q/A"}:
                continue
            accn = recent["accessionNumber"][index]
            primary = recent["primaryDocument"][index]
            filed = recent["filingDate"][index]
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}/{primary}"
            filings.append({"form": form, "filed": filed, "url": url})
            if len(filings) == 5:
                break
        for index, (name, record) in enumerate(facts.items(), start=1):
            if not record:
                continue
            sid = f"F{index}"
            record["source_id"] = sid
            sources.append(Source(id=sid, title=f"{name.replace('_', ' ').title()} · SEC XBRL",
                url=base, as_of=record["filed"], kind="filing",
                excerpt=f"{record['start']} to {record['end']}: {record['value']} {record['unit']}. Filed {record['filed']}. Accession {record['accession']}.").model_dump())
            if record["previous_value"] is not None:
                previous_id = f"{sid}P"
                record["previous_source_id"] = previous_id
                sources.append(Source(id=previous_id, title=f"Prior-year {name.replace('_', ' ')} · SEC XBRL",
                    url=base, as_of=record["previous_filed"] or "", kind="filing",
                    excerpt=f"{record['previous_start']} to {record['previous_end']}: {record['previous_value']} {record['unit']}. Accession {record['previous_accession']}.").model_dump())
        return {
            "company": {"ticker": ticker, "name": payload.get("entityName", entry["title"]),
                        "industry": submissions.get("sicDescription", "U.S. reporting company")},
            "facts": facts, "filings": filings, "sources": sources,
        }

    async def collect(self, ticker: str) -> dict:
        company_result, prices_result = await asyncio.gather(
            self.company_data(ticker), self.market_data(ticker), return_exceptions=True
        )
        warnings = []
        if isinstance(company_result, Exception):
            if not isinstance(company_result, DataUnavailable):
                raise DataUnavailable("Company financial records could not be parsed. Try the sample demo.") from None
            warnings.append(str(company_result))
            company_result = {"company": {"ticker": ticker, "name": ticker, "industry": "Financials unavailable"},
                              "facts": {}, "filings": [], "sources": []}
        if isinstance(prices_result, Exception):
            warnings.append(str(prices_result) if isinstance(prices_result, DataUnavailable) else "Daily prices could not be parsed.")
            prices_result = []
        if not prices_result and not company_result["sources"]:
            raise DataUnavailable("No live data could be retrieved. " + " ".join(warnings))
        if prices_result:
            company_result["sources"].append(Source(id="P1", title="Alpha Vantage daily close series",
                url="https://www.alphavantage.co/documentation/#daily", as_of=prices_result[-1]["date"], kind="market",
                excerpt=f"Latest available unadjusted close: USD {prices_result[-1]['close']}. {len(prices_result)} daily observations. The link explains the provider endpoint; the original API response is fetched server-side with a private key.").model_dump())
        warnings.append("Prices are end-of-day, unadjusted for splits and dividends; price changes are not total investment returns.")
        if any(company_result["facts"].get(k) is None for k in ["revenue", "net_income", "eps", "operating_cash_flow"]):
            warnings.append("Some standard U.S.-GAAP annual metrics are unavailable; custom tags and IFRS-only filings are not handled in V1.")
        return {**company_result, "prices": prices_result, "warnings": warnings}
