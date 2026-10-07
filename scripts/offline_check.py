"""Verify financial/report helpers without network requests or provider keys."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.demo import demo_bundle
from app.finance import extract_annual_facts, market_metrics, net_margin
from app.workflow import citation_errors, fallback_brief


class OfflineChecks(unittest.TestCase):
    def test_three_samples_and_reference_checks(self):
        for ticker in ["NVDA", "AAPL", "MSFT"]:
            bundle = demo_bundle(ticker)
            self.assertEqual(len(bundle["prices"]), 90)
            brief = fallback_brief(bundle)
            self.assertEqual(citation_errors(brief, {"D1"}), [])
            brief.overview[0].text = "Made up 999%."
            brief.overview[0].source_ids = ["FAKE"]
            self.assertGreaterEqual(len(citation_errors(brief, {"D1"})), 2)

    def test_financial_alignment_amendments_and_negative_bases(self):
        def record(start, end, val, filed="2026-02-01", form="10-K"):
            return dict(start=start,end=end,val=val,filed=filed,form=form,fp="FY")
        revenue = [record("2025-01-01","2025-12-31",120),
                   record("2024-01-01","2024-12-31",100),
                   record("2025-10-01","2025-12-31",40),
                   record("2025-01-01","2025-12-31",125,"2026-03-01","10-K/A")]
        income = [record("2025-01-01","2025-12-31",25)]
        payload = {"facts":{"us-gaap":{"Revenues":{"units":{"USD":revenue}},
                    "NetIncomeLoss":{"units":{"USD":income}}}}}
        facts = extract_annual_facts(payload)
        self.assertEqual(facts["revenue"]["value"], 125)
        self.assertEqual(facts["revenue"]["yoy_pct"], 25)
        self.assertEqual(net_margin(facts), 20)
        self.assertIsNone(facts["eps"])
        facts["net_income"]["end"] = "2024-12-31"
        self.assertIsNone(net_margin(facts))
        revenue[1]["val"] = -100
        self.assertIsNone(extract_annual_facts(payload)["revenue"]["yoy_pct"])

    def test_price_math_and_missing_history(self):
        result = market_metrics([dict(date="2026-01-01",close=100), dict(date="2026-01-02",close=110)])
        self.assertEqual(result["daily_change_pct"], 10)
        self.assertIsNone(result["month_change_pct"])
        self.assertEqual(market_metrics([]), {})


if __name__ == "__main__":
    unittest.main()
