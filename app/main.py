"""The web server: serve the frontend, validate inputs, run the graph, return JSON."""

import asyncio
import copy
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import ROOT, settings
from app.models import AnalyzeRequest
from app.sources import DataUnavailable
from app.workflow import create_workflow

app = FastAPI(title="Market Brief", docs_url="/docs", redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
graph = create_workflow(settings)
report_cache = {}
report_lock = asyncio.Lock()
visits = defaultdict(deque)
all_visits = deque()


def enforce_limit(address: str):
    now = time.monotonic()
    for queue in [all_visits, visits[address]]:
        while queue and queue[0] < now - 3600:
            queue.popleft()
    if len(all_visits) >= settings.global_requests_per_hour or len(visits[address]) >= settings.requests_per_hour:
        raise HTTPException(429, "The research request limit has been reached. Try again later.", headers={"Retry-After": "3600"})
    # Prevent a flood of one-off IP addresses leaving an unbounded dictionary.
    stale = [key for key, queue in visits.items() if not queue or queue[-1] < now - 3600]
    for key in stale:
        del visits[key]
    visits[address].append(now)
    all_visits.append(now)


@app.middleware("http")
async def browser_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'"
    )
    return response


@app.get("/")
def home():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(ROOT / "static" / "favicon.svg", media_type="image/svg+xml")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def public_config():
    return {"live_ready": settings.live_ready, "ai_ready": bool(settings.groq_api_key),
            "demo_tickers": ["NVDA", "AAPL", "MSFT"]}


@app.post("/api/analyze")
async def analyze(payload: AnalyzeRequest, request: Request):
    if payload.mode == "live" and not settings.live_ready:
        raise HTTPException(503, "Live research needs ALPHA_VANTAGE_API_KEY and SEC_USER_AGENT with a contact email on the server. The sample demo is available now.")
    cache_key = (payload.ticker, payload.mode)
    started = time.monotonic()
    # Single-process, small-app cache: no database, no hidden persistence.
    # Coalesce work so repeated requests don't burn daily free API allowances.
    async with report_lock:
        report_cache_keys = [key for key, item in report_cache.items() if item[0] <= time.monotonic()]
        for key in report_cache_keys:
            del report_cache[key]
        if cache_key in report_cache:
            result = copy.deepcopy(report_cache[cache_key][1])
            result["cached"] = True
            return result
        if payload.mode == "live":
            enforce_limit(request.client.host if request.client else "unknown")
        try:
            state = await asyncio.wait_for(graph.ainvoke({"ticker": payload.ticker, "mode": payload.mode}), timeout=140)
        except (DataUnavailable, ValueError) as exc:
            raise HTTPException(422, str(exc)) from None
        except TimeoutError:
            raise HTTPException(504, "Research took too long. Try again or use the sample demo.") from None
        except Exception:
            raise HTTPException(502, "Research could not complete. Check server configuration and provider availability.") from None
        bundle = state["bundle"]
        result = {
            "company": bundle["company"], "mode": payload.mode,
            "generation": state["generation"], "market": state["market"],
            "prices": bundle["prices"], "facts": bundle["facts"], "net_margin_pct": state["margin"],
            "brief": state["brief"].model_dump(), "sources": bundle["sources"],
            "filings": bundle["filings"], "warnings": state["warnings"], "trace": state["trace"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": round((time.monotonic() - started) * 1000), "cached": False,
            "audit": "Citation identifiers and output format checked. Interpretations are not independently fact-verified.",
        }
        report_cache[cache_key] = (time.monotonic() + settings.cache_seconds, result)
        return result
