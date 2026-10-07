import asyncio
import copy

from fastapi.testclient import TestClient
from app.config import Settings
from app.demo import demo_bundle
from app.finance import extract_annual_facts, market_metrics, net_margin
from app.main import app
from app.workflow import citation_errors, create_workflow, fallback_brief


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
    bad.overview[0].text = "Revenue grew 999%."
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
