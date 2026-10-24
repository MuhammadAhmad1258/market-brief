import asyncio
import copy
import pytest
import sys
from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient
from app.config import Settings
from app.demo import demo_bundle
from app.finance import extract_annual_facts, market_metrics, net_margin
from app.main import app
from app.workflow import citation_errors, create_workflow, fallback_brief


def test_yfinance_prices_survive_parsing(monkeypatch):
    from app.sources import DataTools

    class History:
        empty = False

        def iterrows(self):
            yield datetime(2026, 1, 2), {"Close": 123.45, "Volume": 1000}
            yield datetime(2026, 1, 5), {"Close": float("nan"), "Volume": 0}
            yield datetime(2026, 1, 6), {"Close": 125.5, "Volume": float("nan")}

    class Ticker:
        info = {"shortName": "Example", "regularMarketPrice": 126.75,
                "regularMarketTime": 1767722400, "currency": "USD"}

        def history(self, **kwargs):
            assert kwargs == {"period": "6mo", "interval": "1d", "auto_adjust": False, "timeout": 15}
            return History()

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=lambda ticker: Ticker()))
    tools = DataTools(Settings(alpha_vantage_api_key=""))
    prices = asyncio.run(tools.market_data("NVDA"))
    assert prices == [
        {"date": "2026-01-02", "close": 123.45, "volume": 1000},
        {"date": "2026-01-06", "close": 125.5, "volume": 0},
    ]
    assert tools.company_profiles["NVDA"]["name"] == "Example"
    quote = tools.company_profiles["NVDA"]["quote"]
    assert quote["price"] == 126.75
    assert quote["as_of"].endswith("+00:00")
    assert quote["currency"] == "USD"


def test_demo_api():
    client = TestClient(app)
    for ticker in ["NVDA", "AAPL", "MSFT"]:
        response = client.post("/api/analyze", json={"ticker": ticker.lower(), "mode": "demo"})
        assert response.status_code == 200
        report = response.json()
        assert report["company"]["ticker"] == ticker
        assert report["generation"] == "demo"
        assert report["sources"][0]["kind"] == "sample"
        assert "SAMPLE DATA" in report["warnings"][0]
        assert len(report["prices"]) == 90
    assert client.post("/api/analyze", json={"ticker":"NVDA"}).json()["cached"] is True
    assert "GROQ_API_KEY" not in client.get("/api/config").text
    assert client.get("/").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}


def test_bad_input_and_unconfigured_live(monkeypatch):
    # Never call real providers even when the owner's .env has keys.
    from app import main
    monkeypatch.setattr(main, "settings", Settings(alpha_vantage_api_key="", sec_user_agent=""))
    client = TestClient(app)
    for ticker in ["../secret", "<script>", "A" * 13, ""]:
        assert client.post("/api/analyze", json={"ticker":ticker}).status_code == 422
    assert client.post("/api/analyze", json={"ticker":"UNKNOWN"}).status_code == 422
    assert client.post("/api/analyze", json={"ticker":"NVDA", "mode":"live"}).status_code == 503


def record(start, end, value, filed="2026-02-01", form="10-K"):
    return {"start":start, "end":end, "val":value, "filed":filed,
            "form":form, "fp":"FY", "fy":2025, "accn":"0000000001-26-000001"}


def test_annual_alignment_and_amendments():
    payload = {"facts":{"us-gaap":{
        "Revenues":{"units":{"USD":[record("2025-01-01","2025-12-31",120),
            record("2024-01-01","2024-12-31",100),
            record("2025-10-01","2025-12-31",40),
            record("2025-01-01","2025-12-31",125,"2026-03-01","10-K/A")]}},
        "NetIncomeLoss":{"units":{"USD":[record("2025-01-01","2025-12-31",25),
            record("2024-01-01","2024-12-31",20)]}}
    }}}
    facts = extract_annual_facts(payload)
    assert facts["revenue"]["value"] == 125
    assert facts["revenue"]["yoy_pct"] == 25
    assert facts["eps"] is None
    assert net_margin(facts) == 20
    facts["net_income"]["end"] = "2024-12-31"
    assert net_margin(facts) is None


def test_negative_base_and_price_math():
    payload = {"facts":{"us-gaap":{"Revenues":{"units":{"USD":[
        record("2025-01-01","2025-12-31",10), record("2024-01-01","2024-12-31",-10)
    ]}}}}}
    assert extract_annual_facts(payload)["revenue"]["yoy_pct"] is None
    result = market_metrics([{"date":"2026-01-01","close":100},{"date":"2026-01-02","close":110}])
    assert result["daily_change_pct"] == 10
    assert result["month_change_pct"] is None
    assert market_metrics([]) == {}


def test_citations_and_number_guard():
    brief = fallback_brief(demo_bundle("NVDA"))
    assert citation_errors(brief, {"D1"}) == []
    bad = copy.deepcopy(brief)
    bad.overview[0].source_ids = ["IMAGINARY"]
    bad.overview[0].text = "Revenue grew at https://fake-url.com."
    assert len(citation_errors(bad, {"D1"})) >= 2


def test_graph_bounded_repair():
    calls = []
    class FakeTools:
        async def collect(self, ticker):
            return demo_bundle(ticker)
    async def invalid_writer(context, system):
        calls.append(system)
        brief = fallback_brief(demo_bundle("NVDA"))
        brief.overview[0].source_ids = ["UNKNOWN"]
        return brief
    graph = create_workflow(Settings(groq_api_key="test-placeholder"), FakeTools(), invalid_writer)
    result = asyncio.run(graph.ainvoke({"ticker":"NVDA","mode":"live"}))
    assert len(calls) == 2
    assert result["generation"] == "facts_only"
    assert citation_errors(result["brief"], {"D1"}) == []


def test_valid_model_shape():
    class FakeTools:
        async def collect(self, ticker):
            return demo_bundle(ticker)
    async def writer(context, system):
        return fallback_brief(demo_bundle("NVDA"))
    graph = create_workflow(Settings(groq_api_key="test-placeholder"), FakeTools(), writer)
    result = asyncio.run(graph.ainvoke({"ticker":"NVDA","mode":"live"}))
    assert result["generation"] == "groq"
    assert len(result["trace"]) == 4


@pytest.mark.parametrize("ticker", ["TSLA", "AMZN", "AMD"])
def test_quote_only_report_and_cache(monkeypatch, ticker):
    from app import main
    from app.sources import DataTools, DataUnavailable

    tools = DataTools(Settings(alpha_vantage_api_key=""))
    quote = {"price": 126.75, "as_of": "2026-01-06T15:20:00+00:00",
             "currency": "USD", "provider": "Yahoo Finance", "session": "regular"}

    async def no_financials(ticker):
        raise DataUnavailable("SEC unavailable")

    def fetch(requested_ticker):
        assert requested_ticker == ticker
        return [], {"quote": quote}

    monkeypatch.setattr(tools, "company_data", no_financials)
    monkeypatch.setattr(tools, "_fetch_yfinance", fetch)
    config = Settings(sec_user_agent="MarketBrief test@example.com", groq_api_key="")
    monkeypatch.setattr(main, "settings", config)
    monkeypatch.setattr(main, "graph", create_workflow(config, tools))
    monkeypatch.setattr(main, "report_cache", {})
    client = TestClient(app)
    response = client.post("/api/analyze", json={"ticker": ticker, "mode": "live"})
    assert response.status_code == 200
    report = response.json()
    assert report["quote"] == quote
    assert report["prices"] == []
    assert report["sources"][0]["id"] == "Q1"
    import time
    assert 0 < main.report_cache[(ticker, "live")][0] - time.monotonic() <= 60
    assert client.post("/api/analyze", json={"ticker": ticker, "mode": "live"}).json()["cached"]
