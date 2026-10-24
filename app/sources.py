"""Only these fixed provider URLs are fetched. The user supplies a ticker, not a URL."""

import asyncio
import copy
import math
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

    def _fetch_yfinance(self, ticker: str):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import yfinance as yf
            t = yf.Ticker(ticker)
            hist = t.history(period="6mo", interval="1d", auto_adjust=False, timeout=15)
            prices = []
            if not hist.empty:
                for idx, row in hist.iterrows():
                    d_str = idx.strftime("%Y-%m-%d")
                    c = float(row["Close"])
                    v = int(row["Volume"]) if math.isfinite(row["Volume"]) else 0
                    if math.isfinite(c) and c > 0:
                        prices.append({"date": d_str, "close": round(c, 2), "volume": v})
            profile = {}
            try:
                info = t.info or {}
                profile = {
                    "name": info.get("shortName") or info.get("longName"),
                    "summary": info.get("longBusinessSummary"),
                    "sector": info.get("sector"),
                    "industry": info.get("industry"),
                    "market_cap": info.get("marketCap"),
                    "trailing_pe": info.get("trailingPE"),
                }
                price, stamp = info.get("regularMarketPrice"), info.get("regularMarketTime")
                if (isinstance(price, (int, float)) and math.isfinite(price) and price > 0
                        and isinstance(stamp, (int, float)) and math.isfinite(stamp)):
                    profile["quote"] = {
                        "price": price,
                        "as_of": datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                        "currency": info.get("currency") or "USD",
                        "provider": "Yahoo Finance",
                        "session": "regular",
                    }
            except Exception:
                pass
            return prices, profile

    async def market_data(self, ticker: str) -> list[dict]:
        # Discard an older quote before attempting a new provider request.
        if hasattr(self, "company_profiles"):
            self.company_profiles.pop(ticker, None)
        # Try Yahoo first; availability and rate limits depend on the provider.
        try:
            prices, profile = await asyncio.to_thread(self._fetch_yfinance, ticker)
            if prices or profile.get("quote"):
                if not hasattr(self, "company_profiles"):
                    self.company_profiles = {}
                self.company_profiles[ticker] = profile
                return prices
        except Exception:
            pass

        # 2. Fallback to Alpha Vantage if key is configured
        if self.config.alpha_vantage_api_key:
            try:
                payload = await self._json("https://www.alphavantage.co/query", params={
                    "function": "TIME_SERIES_DAILY", "symbol": ticker,
                    "outputsize": "compact", "apikey": self.config.alpha_vantage_api_key.strip(),
                })
                series = payload.get("Time Series (Daily)", {})
                if series:
                    return [{"date": day, "close": float(row["4. close"]), "volume": int(row["5. volume"])}
                            for day, row in sorted(series.items())]
            except Exception:
                pass

        raise DataUnavailable("No daily prices were returned for this ticker.")

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
        quote = getattr(self, "company_profiles", {}).get(ticker, {}).get("quote")
        if isinstance(company_result, Exception):
            if not isinstance(company_result, DataUnavailable):
                raise DataUnavailable("Company financial records could not be parsed. Try the sample demo.") from None
            warnings.append(str(company_result))
            company_result = {"company": {"ticker": ticker, "name": ticker, "industry": "Financials unavailable"},
                              "facts": {}, "filings": [], "sources": []}
        if isinstance(prices_result, Exception):
            warnings.append(str(prices_result) if isinstance(prices_result, DataUnavailable) else "Daily prices could not be parsed.")
            prices_result = []
        if quote:
            company_result["sources"].append(Source(id="Q1", title="Yahoo Finance latest regular-session quote",
                url=f"https://finance.yahoo.com/quote/{ticker}", as_of=quote["as_of"], kind="market",
                excerpt=f"Latest reported regular-session price: {quote['price']} {quote['currency']}. May be delayed; not a real-time feed.").model_dump())
        else:
            warnings.append("Latest quote unavailable; displaying the daily close when available.")
        if not prices_result and not company_result["sources"]:
            raise DataUnavailable("No live data could be retrieved. " + " ".join(warnings))
        if prices_result:
            company_result["sources"].append(Source(id="P1", title="Market daily close series",
                url=f"https://finance.yahoo.com/quote/{ticker}", as_of=prices_result[-1]["date"], kind="market",
                excerpt=f"Latest available close: USD {prices_result[-1]['close']}. {len(prices_result)} daily observations covering recent trading sessions.").model_dump())

        # Enrich with company business profile from yfinance if available
        profile = getattr(self, "company_profiles", {}).get(ticker, {})
        summary = profile.get("summary")
        if summary:
            company_result["company"]["summary"] = summary[:500]
            if profile.get("sector") and profile.get("industry"):
                company_result["company"]["industry"] = f"{profile['sector']} · {profile['industry']}"
            company_result["sources"].append(Source(
                id="B1",
                title=f"{company_result['company']['name']} Business Profile",
                url=f"https://finance.yahoo.com/quote/{ticker}",
                as_of=datetime.now(timezone.utc).date().isoformat(),
                kind="filing",
                excerpt=summary[:600]
            ).model_dump())

        warnings.append("Prices are end-of-day, unadjusted for splits and dividends; price changes are not total investment returns.")
        if any(company_result["facts"].get(k) is None for k in ["revenue", "net_income", "eps", "operating_cash_flow"]):
            warnings.append("Some standard U.S.-GAAP annual metrics are unavailable; custom tags and IFRS-only filings are not handled in V1.")
        return {**company_result, "prices": prices_result, "quote": quote, "warnings": warnings}
