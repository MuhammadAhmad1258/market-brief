"""Four readable LangGraph nodes, with one bounded repair attempt."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from app.config import Settings
from app.demo import demo_bundle
from app.finance import market_metrics, net_margin
from app.models import Brief, Claim


class ResearchState(TypedDict, total=False):
    ticker: str
    mode: str
    bundle: dict
    market: dict
    margin: float | None
    brief: Brief
    generation: str
    warnings: list[str]
    trace: list[str]
    attempts: int
    validation_errors: list[str]


def fallback_brief(bundle: dict) -> Brief:
    """Honest, rule-based commentary for demo mode or an unavailable model."""
    facts = bundle["facts"]
    default_id = bundle["sources"][0]["id"]

    def claim(text, record=None, kind="interpretation"):
        ids = [record["source_id"]] if record else [default_id]
        if record and record.get("previous_source_id"):
            ids.append(record["previous_source_id"])
        return Claim(text=text, source_ids=list(dict.fromkeys(ids)), kind=kind)

    revenue = facts.get("revenue")
    income = facts.get("net_income")
    cash = facts.get("operating_cash_flow")
    has_financials = any(facts.values())
    overview_text = ("This brief summarizes the available annual financial records. Price history, when available, covers a different time window."
                     if has_financials else "Only market-price data was returned. Company financial records are unavailable.")
    overview = [claim(overview_text, kind="fact")]
    strengths, risks = [], []
    if revenue and revenue.get("yoy_pct") is not None:
        if revenue["yoy_pct"] > 0:
            strengths.append(claim("Reported annual revenue increased against the comparable prior-year period. That supports a growth case, but does not establish future demand.", revenue))
        elif revenue["yoy_pct"] < 0:
            risks.append(claim("Reported annual revenue declined against the comparable prior-year period. Investigate whether this reflects a temporary change or a longer-term issue.", revenue))
    if cash:
        if cash["value"] > 0:
            strengths.append(claim("Operating cash flow was positive in the displayed annual period. This is useful evidence of cash generation, separate from accounting profit.", cash))
        else:
            risks.append(claim("Operating cash flow was not positive in the displayed annual period. Review liquidity and the cash-flow statement before forming a view.", cash))
    if income and income["value"] < 0:
        risks.append(claim("The displayed annual period contains an accounting loss. Revenue growth alone would not establish financial strength.", income))
    if not strengths:
        strengths.append(claim("The available evidence does not establish a clear positive business case. More company records are needed.", revenue))
    risks.append(claim("These annual records and historical closes cannot explain every recent event or predict the next price move.", revenue))
    watch = [claim("Do newer quarterly filings support the trends in the displayed annual period?", revenue, "question"),
             claim("Does operating cash generation continue to support reported earnings?", cash or income, "question")]
    return Brief(overview=overview, strengths=strengths, risks=risks, watch=watch)


def citation_errors(brief: Brief, allowed_ids: set[str]) -> list[str]:
    """Check references and shape, NOT whether every interpretation is true."""
    errors = []
    for section in [brief.overview, brief.strengths, brief.risks, brief.watch]:
        if not 1 <= len(section) <= 4:
            errors.append("Each section must contain between one and four points.")
        for claim in section:
            if not claim.source_ids or not set(claim.source_ids).issubset(allowed_ids):
                errors.append("Every point needs existing evidence IDs.")
            if not claim.text.strip() or len(claim.text) > 600:
                errors.append("Point text must be short and nonempty.")
            if re.search(r"https?://", claim.text):
                errors.append("Narrative must not contain model-written URLs.")
    return list(dict.fromkeys(errors))


def create_workflow(config: Settings, data_tools=None, writer=None):
    # Keep pure report helpers importable without constructing the integrations.
    from langchain_groq import ChatGroq
    from langgraph.graph import END, START, StateGraph
    from app.sources import DataTools

    tools = data_tools or DataTools(config)

    async def retrieve(state: ResearchState):
        bundle = demo_bundle(state["ticker"]) if state["mode"] == "demo" else await tools.collect(state["ticker"])
        return {"bundle": bundle, "warnings": list(bundle["warnings"]),
                "trace": ["Loaded illustrative dataset" if state["mode"] == "demo" else "Collected provider data"]}

    def calculate(state: ResearchState):
        return {"market": market_metrics(state["bundle"]["prices"]),
                "margin": net_margin(state["bundle"]["facts"]),
                "trace": state["trace"] + ["Calculated price changes and aligned annual metrics"]}

    async def draft(state: ResearchState):
        attempt = state.get("attempts", 0) + 1
        bundle = state["bundle"]
        warnings = list(state["warnings"])
        generation = "groq"
        if state["mode"] == "demo" or not config.groq_api_key:
            brief = fallback_brief(bundle)
            generation = "demo" if state["mode"] == "demo" else "facts_only"
            if generation == "facts_only":
                warnings.append("Groq is not configured. Commentary is rule-based, not AI-generated.")
        else:
            # Only this compact, normalized evidence goes to the model.
            context = {"company": bundle["company"], "annual_facts": bundle["facts"],
                       "market": state["market"], "net_margin_pct": state["margin"],
                       "evidence": bundle["sources"], "limitations": warnings}
            system = (
                "You are an expert equity research analyst writing a concise, professional executive brief "
                "based ONLY on the provided evidence. "
                "Provide one to three insightful points per section (overview, strengths, risks, watch). "
                "Every point must cite the relevant evidence IDs from the provided evidence in source_ids. "
                "You may cite verified numbers, percentages, dates, and amounts that appear directly in the provided evidence or market metrics. "
                "Synthesize business operations, revenue scale, margin dynamics, cash flow quality, and market moves. "
                "Label observed records 'fact', analytical deductions 'interpretation', and monitoring items 'question'. "
                "Do not invent facts not in the evidence. Never write raw URLs or buy/sell advice. Include uncertainty and data gaps."
            )
            if state.get("validation_errors"):
                system += " Repair these errors in the prior attempt: " + "; ".join(state["validation_errors"])
            try:
                if writer:
                    brief = await writer(context, system)
                else:
                    model = ChatGroq(model=config.groq_model, api_key=config.groq_api_key,
                                     temperature=0, max_tokens=1800, timeout=35, max_retries=0)
                    structured = model.with_structured_output(Brief, method="json_schema", strict=True)
                    brief = await structured.ainvoke([("system", system), ("human", json.dumps(context))])
                if not isinstance(brief, Brief):
                    brief = Brief.model_validate(brief)
            except Exception:
                # Never send provider exceptions to the browser; they can contain URLs or keys.
                brief = fallback_brief(bundle)
                generation = "facts_only"
                warnings.append("Groq generation was unavailable. Showing a rule-based brief; check model access, key and rate limits.")
        label = "Drafted Groq commentary" if generation == "groq" else "Prepared clearly labelled rule-based commentary"
        return {"brief": brief, "generation": generation, "attempts": attempt,
                "warnings": warnings, "trace": state["trace"] + [label]}

    def verify(state: ResearchState):
        allowed = {s["id"] for s in state["bundle"]["sources"]}
        errors = citation_errors(state["brief"], allowed)
        update = {"validation_errors": errors,
                  "trace": state["trace"] + ["Checked citation IDs and narrative format"]}
        if errors and state["attempts"] >= 2:
            update["brief"] = fallback_brief(state["bundle"])
            update["generation"] = "facts_only"
            update["warnings"] = state["warnings"] + ["The model report failed citation/format checks and was replaced with a rule-based brief."]
        return update

    graph = StateGraph(ResearchState)
    for name, action in [("retrieve", retrieve), ("calculate", calculate), ("draft", draft), ("verify", verify)]:
        graph.add_node(name, action)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "calculate")
    graph.add_edge("calculate", "draft")
    graph.add_edge("draft", "verify")
    graph.add_conditional_edges("verify", lambda s: "draft" if s["validation_errors"] and s["attempts"] < 2 else END)
    return graph.compile()
