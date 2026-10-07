// Plain JavaScript: form -> Python API -> render the response. No frontend framework.
const $ = (id) => document.getElementById(id);
let mode = "demo";
let currentReport = null;
let chartRange = 64;
let busy = false;
const money = (value, digits = 2) => Number.isFinite(value)
  ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: digits }).format(value) : "Unavailable";
const compact = (value) => Number.isFinite(value)
  ? new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(value) : "Unavailable";
const pct = (value) => Number.isFinite(value) ? `${value >= 0 ? "+" : ""}${value.toFixed(2)}%` : "Unavailable";

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function safeLink(href) {
  try {
    const url = new URL(href, location.origin);
    if (url.origin === location.origin && url.pathname === "/static/demo-note.txt") return url.href;
    if (url.protocol === "https:" && ["www.sec.gov", "data.sec.gov", "www.alphavantage.co"].includes(url.hostname)) return url.href;
  } catch (_) { /* Invalid provider URL: no link is created. */ }
  return null;
}

function setMode(nextMode) {
  if (busy) return;
  mode = nextMode;
  for (const key of ["demo", "live"]) {
    $(`${key}-mode`).classList.toggle("active", mode === key);
    $(`${key}-mode`).setAttribute("aria-pressed", String(mode === key));
  }
  $("mode-note").textContent = mode === "demo"
    ? "Illustrative demo. Prices and financials are invented examples."
    : "Live research uses end-of-day prices and annual company records. Availability depends on configured providers.";
  $("error-box").hidden = true;
  // Don't leave a sample result visible under a newly selected live-mode label.
  $("results").hidden = true;
  currentReport = null;
}

async function analyze() {
  if (busy) return;
  busy = true;
  const ticker = $("ticker").value.trim().toUpperCase();
  $("ticker").value = ticker;
  $("error-box").hidden = true;
  $("results").hidden = true;
  currentReport = null;
  $("loading").hidden = false;
  $("results").setAttribute("aria-busy", "true");
  $("analyze-button").disabled = true;
  document.querySelectorAll(".ticker-chip,.mode").forEach((button) => button.disabled = true);
  try {
    const response = await fetch("/api/analyze", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker, mode }), signal: AbortSignal.timeout(160000),
    });
    const report = await response.json();
    if (!response.ok) {
      const detail = typeof report.detail === "string" ? report.detail : "Use a valid U.S. company ticker, such as NVDA.";
      throw new Error(detail);
    }
    currentReport = report;
    render(report);
    $("results").hidden = false;
    $("announcement").textContent = `${report.company.name} research brief ready. ${report.mode === "demo" ? "Illustrative sample data." : "Live provider data."}`;
  } catch (error) {
    $("error-box").textContent = error.name === "TimeoutError" ? "The research request timed out. Try again or choose the sample demo." : error.message;
    $("error-box").hidden = false;
  } finally {
    busy = false;
    $("loading").hidden = true;
    $("results").setAttribute("aria-busy", "false");
    $("analyze-button").disabled = false;
    document.querySelectorAll(".ticker-chip,.mode").forEach((button) => button.disabled = false);
  }
}

function renderClaims(id, claims, report) {
  const container = $(id);
  container.replaceChildren();
  const ids = new Set(report.sources.map((source) => source.id));
  for (const claim of claims) {
    const wrapper = node("div", "claim");
    wrapper.append(node("p", "", claim.text));
    const meta = node("div", "claim-meta");
    meta.append(node("span", "claim-kind", claim.kind === "fact" ? "Observation" : claim.kind));
    for (const id of claim.source_ids) {
      if (!ids.has(id)) continue;
      const citation = node("a", "citation", id);
      citation.href = `#source-${id}`;
      citation.setAttribute("aria-label", `Read source ${id}`);
      meta.append(citation);
    }
    wrapper.append(meta);
    container.append(wrapper);
  }
}

function render(report) {
  $("result-ticker").textContent = report.company.ticker;
  $("result-industry").textContent = report.company.industry;
  $("company-name").textContent = report.company.name;
  $("sample-banner").hidden = report.mode !== "demo";
  $("generation-badge").textContent = { demo: "Sample · rule-based", groq: "Groq commentary", facts_only: "Rule-based brief" }[report.generation];
  const market = report.market;
  $("close-price").textContent = money(market.close);
  $("price-date").textContent = market.as_of ? `Close · ${market.as_of}` : "No prices available";
  $("daily-change").textContent = market.daily_change_pct != null ? `${pct(market.daily_change_pct)} daily` : "";
  $("daily-change").classList.toggle("negative", market.daily_change_pct < 0);
  $("month-change").textContent = pct(market.month_change_pct);
  $("month-change").classList.toggle("negative", market.month_change_pct < 0);
  $("volatility").textContent = market.annualized_volatility_pct != null ? `${market.annualized_volatility_pct.toFixed(2)}%` : "Unavailable";
  $("volume").textContent = compact(market.volume);
  renderChart();

  const revenue = report.facts.revenue;
  $("financial-period").textContent = revenue ? `Fiscal year: ${revenue.start} to ${revenue.end}. Filed ${revenue.filed}.` : "Annual metrics unavailable in the supported filing tags.";
  const container = $("financial-metrics");
  container.replaceChildren();
  for (const [key, label] of [["revenue", "Revenue"], ["net_income", "Net income"], ["eps", "Diluted EPS"], ["operating_cash_flow", "Operating cash flow"]]) {
    const fact = report.facts[key];
    const card = node("div", "financial-metric");
    card.append(node("h4", "", label), node("strong", "metric-value", fact ? key === "eps" ? money(fact.value) : `$${compact(fact.value)}` : "Unavailable"));
    const detail = node("div", "metric-detail", fact?.yoy_pct != null ? `${pct(fact.yoy_pct)} vs prior fiscal year` : "Prior-year comparison unavailable");
    if (fact) {
      const citation = node("a", "", fact.source_id);
      citation.href = `#source-${fact.source_id}`;
      detail.append(citation);
    }
    card.append(detail);
    container.append(card);
  }
  $("net-margin").textContent = report.net_margin_pct != null ? `Annual net margin: ${report.net_margin_pct.toFixed(2)}%` : "Net margin unavailable: matching annual revenue and income are required.";

  for (const section of ["overview", "strengths", "risks", "watch"]) renderClaims(`brief-${section}`, report.brief[section], report);
  $("report-timestamp").textContent = `Prepared ${new Date(report.generated_at).toLocaleString()}${report.cached ? " · cached report" : ""}`;
  $("filings").replaceChildren();
  if (!report.filings.length) $("filings").append(node("p", "empty-note", report.mode === "demo" ? "The sample contains no actual company filings. Live research lists recent SEC filings here." : "Recent filings could not be retrieved."));
  for (const filing of report.filings) {
    const row = node("div", "filing-row");
    const url = safeLink(filing.url);
    const title = node(url ? "a" : "span", "", `${filing.form} · company filing`);
    if (url) { title.href = url; title.target = "_blank"; title.rel = "noopener noreferrer"; }
    row.append(title, node("span", "metadata", filing.filed));
    $("filings").append(row);
  }
  $("sources").replaceChildren();
  for (const source of report.sources) {
    const card = node("article", "source-card");
    card.id = `source-${source.id}`;
    const header = node("div", "source-top");
    const url = safeLink(source.url);
    const title = node(url ? "a" : "span", "", source.title);
    if (url) { title.href = url; title.target = "_blank"; title.rel = "noopener noreferrer"; }
    header.append(node("span", "source-id", source.id), title);
    card.append(header, node("p", "", source.excerpt), node("span", "metadata", `As of: ${source.as_of}`));
    $("sources").append(card);
  }
  $("sources-count").textContent = `${report.sources.length} evidence record${report.sources.length === 1 ? "" : "s"}`;
  $("warnings").replaceChildren(...report.warnings.map((warning) => node("li", "", warning)));
  $("audit-note").textContent = report.audit;
}

function renderChart() {
  if (!currentReport) return;
  const data = currentReport.prices.filter((row) => Number.isFinite(row.close) && row.close > 0).slice(-chartRange);
  $("chart").replaceChildren();
  if (!data.length) { $("chart").append(node("p", "empty-note", "No daily price history is available.")); $("chart-window").textContent = ""; $("chart-reading").textContent = ""; return; }
  $("chart-window").textContent = `${data[0].date} — ${data.at(-1).date}`;
  $("chart-reading").textContent = `${data.length} available sessions · ${currentReport.mode === "demo" ? "illustrative" : "daily"} closes`;
  const ns = "http://www.w3.org/2000/svg";
  function svgNode(tag, attrs, text) { const element = document.createElementNS(ns, tag); for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value); if (text !== undefined) element.textContent = text; return element; }
  const width = 540, height = 225, left = 4, right = 55, top = 18, bottom = 27;
  const lows = data.map((r) => r.close), min = Math.min(...lows), max = Math.max(...lows);
  const padding = Math.max((max - min) * .15, max * .01);
  const lower = min - padding, upper = max + padding;
  const x = (i) => left + i / Math.max(1, data.length - 1) * (width - left - right);
  const y = (value) => top + (upper - value) / (upper - lower) * (height - top - bottom);
  const svg = svgNode("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": `${currentReport.company.ticker} daily closing-price chart` });
  svg.append(svgNode("title", {}, `${currentReport.company.ticker}: ${money(data[0].close)} to ${money(data.at(-1).close)}`));
  for (let tick = 0; tick < 4; tick++) {
    const value = lower + (upper - lower) * tick / 3, yy = y(value);
    svg.append(svgNode("line", { x1:left, x2:width-right, y1:yy, y2:yy, stroke:"#e6ece7", "stroke-width":1 }));
    svg.append(svgNode("text", { x:width-right+9, y:yy+4, fill:"#60766a", "font-size":12 }, value.toFixed(0)));
  }
  const points = data.map((row, i) => `${x(i)},${y(row.close)}`).join(" ");
  svg.append(svgNode("polygon", { points: `${left},${height-bottom} ${points} ${x(data.length-1)},${height-bottom}`, fill:"#e4f2e9" }));
  svg.append(svgNode("polyline", { points, fill:"none", stroke:"#127557", "stroke-width":2.5, "stroke-linejoin":"round", "stroke-linecap":"round" }));
  svg.append(svgNode("circle", { cx:x(data.length-1), cy:y(data.at(-1).close), r:3.5, fill:"#127557" }));
  svg.append(svgNode("text", { x:left, y:height-4, fill:"#60766a", "font-size":12 }, data[0].date));
  svg.append(svgNode("text", { x:width-right, y:height-4, "text-anchor":"end", fill:"#60766a", "font-size":12 }, data.at(-1).date));
  svg.addEventListener("pointermove", (event) => {
    const rect = svg.getBoundingClientRect();
    const position = (event.clientX - rect.left) / rect.width * width;
    const i = Math.max(0, Math.min(data.length - 1, Math.round((position-left)/(width-left-right)*(data.length-1))));
    $("chart-reading").textContent = `${data[i].date} · ${money(data[i].close)}${currentReport.mode === "demo" ? " · illustrative" : ""}`;
  });
  $("chart").append(svg);
}

function downloadBrief() {
  if (!currentReport) return;
  const r = currentReport;
  const lines = [`# ${r.company.name} (${r.company.ticker}) — Market Brief`, ``, `Mode: ${r.mode}. Commentary: ${r.generation}.`, `Prepared: ${r.generated_at}`, ""];
  if (r.mode === "demo") lines.push("SAMPLE DATA: invented examples, not actual market data.", "");
  lines.push(`Latest available close: ${money(r.market.close)} (${r.market.as_of || "unavailable"})`, "");
  for (const [key, fact] of Object.entries(r.facts)) if (fact) lines.push(`${key}: ${fact.value} ${fact.unit} · ${fact.start} to ${fact.end}`);
  for (const section of ["overview", "strengths", "risks", "watch"]) {
    lines.push("", `## ${section}`, "");
    for (const claim of r.brief[section]) lines.push(`- [${claim.kind}] ${claim.text} [${claim.source_ids.join(", ")}]`);
  }
  lines.push("", "## Sources", "");
  for (const source of r.sources) lines.push(`- ${source.id}: ${source.title} — ${safeLink(source.url) || "No link"}\n  ${source.excerpt}`);
  lines.push("", "## Data notes", "", ...r.warnings.map((warning) => `- ${warning}`), "", r.audit);
  const url = URL.createObjectURL(new Blob([lines.join("\n")], { type:"text/markdown;charset=utf-8" }));
  const link = document.createElement("a"); link.href = url; link.download = `market-brief-${r.company.ticker}-${r.mode}.md`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  $("announcement").textContent = "Research brief downloaded.";
}

$("research-form").addEventListener("submit", (event) => { event.preventDefault(); analyze(); });
$("demo-mode").addEventListener("click", () => setMode("demo"));
$("live-mode").addEventListener("click", () => setMode("live"));
$("download-button").addEventListener("click", downloadBrief);
document.querySelectorAll(".ticker-chip").forEach((button) => button.addEventListener("click", () => { $("ticker").value = button.dataset.ticker; analyze(); }));
document.querySelectorAll(".range").forEach((button) => button.addEventListener("click", () => {
  chartRange = Number(button.dataset.range);
  document.querySelectorAll(".range").forEach((b) => { const active = b === button; b.classList.toggle("active", active); b.setAttribute("aria-pressed", String(active)); });
  renderChart();
}));
// A useful first screen, without making any external API or model call.
analyze();
