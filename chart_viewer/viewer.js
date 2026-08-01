// ─────────────────────────────────────────────────────────────────────────
// Flag & Pole Chart Review — viewer logic
// Inlined into the exported HTML by chart_export.py at build time (same
// pattern as vendor/lightweight-charts.standalone.production.js) — the
// SHIPPED file is still a single self-contained .html with no external
// requests; this is split out purely so it's maintainable as source.
// ─────────────────────────────────────────────────────────────────────────

const flatSymbols = DATA.symbols;

// ── State ──
let currentSymbolIdx = -1;
let currentTF = "1D";
let currentBars = [];
let currentIndicators = {};

let chart = null, candleSeries = null, volumeSeries = null, volMaSeries = null;
let poleLineSeries = null, flagUpperSeries = null, flagLowerSeries = null;

const MA_PERIODS_JS = [5, 10, 20, 30, 50];
const DEFAULT_MA_COLORS = { 5: "#e6edf3", 10: "#79c0ff", 20: "#ffa657", 30: "#f778ba", 50: "#56d4dd" };
let maSettings = {};
MA_PERIODS_JS.forEach(p => { maSettings[p] = { period: p, color: DEFAULT_MA_COLORS[p] }; });
let maSeriesMap = {};   // period -> series

const activeIndicators = {
  ma5: false, ma10: false, ma20: true, ma30: false, ma50: true,
  volume: true, volMa: true, rsi: false, macd: false,
};

let rsiChart = null, rsiSeries = null, rsiSyncHandler = null;
let rsiColor = "#bc8cff";
let macdChart = null, macdLineSeries = null, macdSignalSeries = null, macdHistSeries = null, macdSyncHandler = null;
let macdLineColor = "#58a6ff", macdSignalColor = "#ffa657";

let sidebarCollapsed = false;
let infoCollapsed = false;
let gridMode = false;
let gridChartInstances = {};   // { "1D": {chart, candles, ...}, ... }

// ─────────────────────────────────────────────────────────────────────────
// Sidebar
// ─────────────────────────────────────────────────────────────────────────
function qualityBadgeClass(score) {
  if (score >= 70) return "badge-high";
  if (score >= 40) return "badge-med";
  return "badge-low";
}

function renderSidebar(filterText) {
  const list = document.getElementById("symbol-list");
  list.innerHTML = "";
  const ft = (filterText || "").toUpperCase();

  Object.keys(DATA.tab_labels).forEach(tabKey => {
    const label = DATA.tab_labels[tabKey];
    const items = flatSymbols.filter(s => s.tabs.includes(tabKey));
    const shown = items.filter(s => !ft || s.symbol.toUpperCase().includes(ft) ||
                                       (s.company_name || "").toUpperCase().includes(ft));
    if (shown.length === 0) return;

    const gh = document.createElement("div");
    gh.className = "group-header";
    gh.textContent = `${label} (${shown.length})`;
    list.appendChild(gh);
    shown.forEach(s => list.appendChild(buildSymbolItem(s)));
  });
}

function buildSymbolItem(s) {
  const idx = flatSymbols.indexOf(s);
  const el = document.createElement("div");
  el.className = "sym-item" + (idx === currentSymbolIdx ? " active" : "");
  el.dataset.idx = idx;
  const tabChips = s.tabs.map(t => DATA.tab_labels[t].replace(/^\S+\s/, ""));
  el.innerHTML = `
    <div>
      <div class="sym-name">${esc(s.symbol.replace('.NS', ''))}</div>
      <div class="sym-sub">${esc(s.signal_type || s.status || "")}</div>
      <div class="sym-tabs">${tabChips.map(t => `<span>${esc(t)}</span>`).join("")}</div>
    </div>
    <span class="badge ${qualityBadgeClass(s.quality_score)}">${Math.round(s.quality_score)}</span>
  `;
  el.addEventListener("click", () => selectSymbol(idx));
  return el;
}

// ─────────────────────────────────────────────────────────────────────────
// Main chart setup (created once)
// ─────────────────────────────────────────────────────────────────────────
function initChart() {
  const container = document.getElementById("chart");
  chart = LightweightCharts.createChart(container, {
    layout: { background: { color: "#0d1117" }, textColor: "#e6edf3" },
    grid: { vertLines: { color: "#1c2128" }, horzLines: { color: "#1c2128" } },
    rightPriceScale: { borderColor: "#30363d" },
    timeScale: { borderColor: "#30363d", timeVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });

  candleSeries = chart.addCandlestickSeries({
    upColor: "#3fb950", downColor: "#f85149",
    borderUpColor: "#3fb950", borderDownColor: "#f85149",
    wickUpColor: "#3fb950", wickDownColor: "#f85149",
    priceScaleId: "right",
  });
  candleSeries.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.28 } });

  volumeSeries = chart.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "vol",
  });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });

  volMaSeries = chart.addLineSeries({
    color: "#e6edf3", lineWidth: 1, priceScaleId: "vol",
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    visible: activeIndicators.volMa,
  });

  MA_PERIODS_JS.forEach(period => {
    maSeriesMap[period] = chart.addLineSeries({
      color: maSettings[period].color, lineWidth: 1,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      visible: activeIndicators[`ma${period}`],
    });
  });

  poleLineSeries = chart.addLineSeries({
    color: "#bc8cff", lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
  });
  flagUpperSeries = chart.addLineSeries({
    color: "#d29922", lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
  });
  flagLowerSeries = chart.addLineSeries({
    color: "#d29922", lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
  });

  setupCrosshairTooltip();

  new ResizeObserver(resizeAllCharts).observe(document.getElementById("chart-container"));
}

function resizeAllCharts() {
  if (chart && !gridMode) {
    const c = document.getElementById("chart");
    if (c.clientWidth > 0 && c.clientHeight > 0) chart.resize(c.clientWidth, c.clientHeight);
  }
  if (rsiChart) {
    const c = document.getElementById("rsi-pane");
    if (c.clientWidth > 0 && c.clientHeight > 0) rsiChart.resize(c.clientWidth, c.clientHeight);
  }
  if (macdChart) {
    const c = document.getElementById("macd-pane");
    if (c.clientWidth > 0 && c.clientHeight > 0) macdChart.resize(c.clientWidth, c.clientHeight);
  }
  if (gridMode) {
    Object.keys(gridChartInstances).forEach(tf => {
      const inst = gridChartInstances[tf];
      const c = document.getElementById(`grid-chart-${tf}`);
      if (inst && c && c.clientWidth > 0 && c.clientHeight > 0) inst.chart.resize(c.clientWidth, c.clientHeight);
    });
  }
}

function clearPriceLines() {
  if (!candleSeries) return;
  (candleSeries._priceLines || []).forEach(pl => candleSeries.removePriceLine(pl));
  candleSeries._priceLines = [];
}

function addPriceLine(price, color, title) {
  if (price === null || price === undefined) return;
  const pl = candleSeries.createPriceLine({
    price: price, color: color, lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dashed,
    axisLabelVisible: true, title: title,
  });
  candleSeries._priceLines = candleSeries._priceLines || [];
  candleSeries._priceLines.push(pl);
}

// ─────────────────────────────────────────────────────────────────────────
// Hover OHLCV tooltip (TradingView-style fixed corner readout)
// ─────────────────────────────────────────────────────────────────────────
function setupCrosshairTooltip() {
  const tip = document.getElementById("ohlc-tooltip");
  chart.subscribeCrosshairMove(param => {
    if (!param.time || !param.seriesData || !param.seriesData.has(candleSeries)) {
      tip.style.display = "none";
      return;
    }
    const bar = param.seriesData.get(candleSeries);
    const volData = param.seriesData.get(volumeSeries);
    const vol = volData ? volData.value : null;
    const changeColor = bar.close >= bar.open ? "#3fb950" : "#f85149";
    tip.innerHTML = `
      <span>${esc(param.time)}</span>
      <span>O <b style="color:${changeColor}">${bar.open.toFixed(2)}</b></span>
      <span>H <b style="color:${changeColor}">${bar.high.toFixed(2)}</b></span>
      <span>L <b style="color:${changeColor}">${bar.low.toFixed(2)}</b></span>
      <span>C <b style="color:${changeColor}">${bar.close.toFixed(2)}</b></span>
      ${vol != null ? `<span>Vol <b>${formatVolume(vol)}</b></span>` : ""}
    `;
    tip.style.display = "flex";
  });
}

function formatVolume(v) {
  if (v >= 1e7) return (v / 1e7).toFixed(2) + "Cr";
  if (v >= 1e5) return (v / 1e5).toFixed(2) + "L";
  if (v >= 1e3) return (v / 1e3).toFixed(1) + "K";
  return String(Math.round(v));
}

// ─────────────────────────────────────────────────────────────────────────
// Selecting a symbol / timeframe
// ─────────────────────────────────────────────────────────────────────────
function selectSymbol(idx) {
  currentSymbolIdx = idx;
  const s = flatSymbols[idx];

  document.getElementById("empty-state").style.display = "none";
  document.getElementById("hdr-symbol").textContent = s.symbol.replace(".NS", "");
  document.getElementById("hdr-symbol").title = s.symbol.replace(".NS", "");
  document.getElementById("hdr-company").textContent =
    `${s.company_name || ""}${s.sector ? " · " + s.sector : ""}`;
  document.getElementById("hdr-company").title = document.getElementById("hdr-company").textContent;

  renderStatsRow(s);
  renderReasonsPanel(s);
  renderSidebar(document.getElementById("search").value);

  if (gridMode) {
    renderGridView(s);
  } else {
    loadTimeframe(currentTF);
  }
}

function renderStatsRow(s) {
  const row = document.getElementById("stats-row");
  const stat = (label, value, color) =>
    `<div class="stat"><span class="label">${label}</span>
     <span class="value" style="${color ? 'color:'+color : ''}">${value}</span></div>`;
  row.innerHTML = [
    stat("Signal", esc(s.signal_type || s.status || "—")),
    stat("Quality", Math.round(s.quality_score)),
    stat("Readiness", s.breakout_readiness_pct != null ? Math.round(s.breakout_readiness_pct) + "%" : "—"),
    stat("Pole Move", s.pole_pct_move != null ? s.pole_pct_move.toFixed(1) + "%" : "—"),
    stat("Flag Retrace", s.flag_retracement_pct != null ? s.flag_retracement_pct.toFixed(1) + "%" : "—"),
    stat("Flag Range", s.flag_range_pct != null ? s.flag_range_pct.toFixed(1) + "%" : "—"),
    stat("Flag Days", s.flag_duration_bars != null ? Math.round(s.flag_duration_bars) + "d" : "—"),
    stat("Pivot", s.pivot_point != null ? "₹" + s.pivot_point.toFixed(2) : "—"),
    stat("Entry", s.entry_price != null ? "₹" + s.entry_price.toFixed(2) : "—"),
    stat("Stop", s.stop_loss_price != null ? "₹" + s.stop_loss_price.toFixed(2) : "—", "#f85149"),
    stat("Target 1", s.target1 != null ? "₹" + s.target1.toFixed(2) : "—", "#3fb950"),
    stat("Target 2", s.target2 != null ? "₹" + s.target2.toFixed(2) : "—", "#3fb950"),
    stat("R:R (T2)", s.rr_t2 != null ? s.rr_t2.toFixed(2) : "—"),
    stat("RS Rating", s.rs_rating != null ? Math.round(s.rs_rating) : "—"),
  ].join("");
}

function esc(str) {
  const d = document.createElement("div");
  d.textContent = str == null ? "" : String(str);
  return d.innerHTML;
}

function renderReasonsPanel(s) {
  const panel = document.getElementById("reasons-panel");
  panel.classList.add("visible");

  document.getElementById("reasons-thesis").textContent = s.buy_thesis || "No summary available.";

  const detailLines = [];
  if (s.flag_retracement_pct != null || s.flag_range_pct != null) {
    const bits = [];
    if (s.flag_range_pct != null) bits.push(`${s.flag_range_pct.toFixed(1)}% high-low range`);
    if (s.flag_retracement_pct != null) bits.push(`${s.flag_retracement_pct.toFixed(1)}% retracement`);
    if (s.flag_duration_bars != null) bits.push(`${Math.round(s.flag_duration_bars)} trading days`);
    detailLines.push(`<b>Tightness:</b> ${bits.join(" · ")}`);
  }
  if (s.readiness_reasons) detailLines.push(`<b>Readiness:</b> ${esc(s.readiness_reasons)}`);
  if (s.scan_date) {
    const isStale = s.scan_date !== DATA.scan_date;
    detailLines.push(`<b>Detected:</b> ${esc(s.scan_date)}${isStale ? " (not today — still active from an earlier scan)" : ""}`);
  }
  if (s.exit_date) {
    detailLines.push(`<b>Closed:</b> ${esc(s.exit_date)} at ₹${s.exit_price != null ? s.exit_price.toFixed(2) : "—"} (${esc(s.exit_type || "")})`);
  }
  document.getElementById("reasons-detail").innerHTML = detailLines.join(" &nbsp;·&nbsp; ");

  const tags = (s.remarks || "").split(",").map(t => t.trim()).filter(Boolean);
  document.getElementById("reasons-tags").innerHTML =
    tags.map(t => `<span class="tag">${esc(t)}</span>`).join("");
}

function loadTimeframe(tf) {
  currentTF = tf;
  document.querySelectorAll("#tf-toggle button[data-tf]").forEach(b =>
    b.classList.toggle("active", b.dataset.tf === tf));

  const s = flatSymbols[currentSymbolIdx];
  const tfData = s.timeframes[tf] || { bars: [], indicators: {} };
  currentBars = tfData.bars;
  currentIndicators = tfData.indicators || {};

  if (currentBars.length === 0) {
    candleSeries.setData([]);
    volumeSeries.setData([]);
    return;
  }

  clearPriceLines();
  addPriceLine(s.pivot_point, "#58a6ff", "Pivot");
  addPriceLine(s.stop_loss_price, "#f85149", "Stop");
  addPriceLine(s.target1, "#3fb950", "T1");
  addPriceLine(s.target2, "#2ea043", "T2");

  renderFullChart(s);
}

// ─────────────────────────────────────────────────────────────────────────
// Full chart rendering — no replay, everything shown at once, fit to screen
// ─────────────────────────────────────────────────────────────────────────
function renderFullChart(s) {
  candleSeries.setData(currentBars.map(b => ({ time: b.time, open: b.o, high: b.h, low: b.l, close: b.c })));
  volumeSeries.setData(currentBars.map(b => ({
    time: b.time, value: b.v,
    color: b.c >= b.o ? "rgba(63,185,80,0.5)" : "rgba(248,81,73,0.5)",
  })));
  volMaSeries.setData(computeVolumeSma(currentBars, 20));

  candleSeries.setMarkers(buildMarkers(s));
  updatePatternOverlay(s);
  updateMaSeriesData();

  if (rsiChart) updateRsiPaneData();
  if (macdChart) updateMacdPaneData();

  chart.timeScale().fitContent();
}

function buildMarkers(s) {
  const markers = [];
  const add = (date, position, color, shape, text) => {
    if (date) markers.push({ time: date, position, color, shape, text });
  };
  add(s.pole_start_date, "belowBar", "#58a6ff", "arrowUp", "Pole");
  add(s.flag_start_date, "aboveBar", "#d29922", "circle", "Flag");
  add(s.breakout_date, "belowBar", "#3fb950", "arrowUp", "Breakout");
  return markers;
}

function updatePatternOverlay(s) {
  if (s.pole_start_date && s.pole_end_date) {
    const startPrice = findBarClose(currentBars, s.pole_start_date);
    const endPrice = findBarClose(currentBars, s.pole_end_date);
    poleLineSeries.setData([
      { time: s.pole_start_date, value: startPrice },
      { time: s.pole_end_date, value: endPrice },
    ]);
  } else {
    poleLineSeries.setData([]);
  }

  if (s.flag_start_date && s.flag_end_date && s.flag_high != null && s.flag_low != null) {
    flagUpperSeries.setData([
      { time: s.flag_start_date, value: s.flag_high },
      { time: s.flag_end_date, value: s.flag_high },
    ]);
    flagLowerSeries.setData([
      { time: s.flag_start_date, value: s.flag_low },
      { time: s.flag_end_date, value: s.flag_low },
    ]);
  } else {
    flagUpperSeries.setData([]);
    flagLowerSeries.setData([]);
  }
}

function findBarClose(bars, dateStr) {
  if (!dateStr || bars.length === 0) return null;
  let best = bars[0];
  for (const b of bars) {
    if (b.time <= dateStr) best = b; else break;
  }
  return best.c;
}

// ─────────────────────────────────────────────────────────────────────────
// Moving averages — server-computed for the 5 default periods (accurate
// from the first visible bar, uses full history before trimming); a
// custom period typed into Settings is computed client-side on demand
// from whatever bars are currently loaded, which may have a short
// warm-up gap at the left edge if it needs more history than is loaded.
// ─────────────────────────────────────────────────────────────────────────
function computeSMA(bars, period) {
  const out = [];
  for (let i = period - 1; i < bars.length; i++) {
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += bars[j].c;
    out.push({ time: bars[i].time, value: sum / period });
  }
  return out;
}

function computeVolumeSma(bars, period) {
  const out = [];
  for (let i = period - 1; i < bars.length; i++) {
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += bars[j].v;
    out.push({ time: bars[i].time, value: sum / period });
  }
  return out;
}

function updateMaSeriesData() {
  MA_PERIODS_JS.forEach(defaultPeriod => {
    const series = maSeriesMap[defaultPeriod];
    if (!series) return;
    const effectivePeriod = maSettings[defaultPeriod].period;
    let points;
    if (effectivePeriod === defaultPeriod && currentIndicators[`sma${defaultPeriod}`]) {
      // Unmodified default period -> use the server-computed series
      // (correct from the first visible bar, uses full pre-trim history).
      points = currentIndicators[`sma${defaultPeriod}`].map(p => ({ time: p.time, value: p.value }));
    } else {
      // Custom period -> compute client-side from the currently loaded bars.
      points = computeSMA(currentBars, effectivePeriod);
    }
    series.setData(points);
  });
}

// ─────────────────────────────────────────────────────────────────────────
// RSI sub-pane
// ─────────────────────────────────────────────────────────────────────────
function ensureRsiPane() {
  if (rsiChart) return;
  document.getElementById("rsi-pane-wrap").style.display = "block";
  const container = document.getElementById("rsi-pane");
  rsiChart = LightweightCharts.createChart(container, {
    layout: { background: { color: "#0d1117" }, textColor: "#e6edf3" },
    grid: { vertLines: { color: "#1c2128" }, horzLines: { color: "#1c2128" } },
    rightPriceScale: { borderColor: "#30363d" },
    timeScale: { borderColor: "#30363d" },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });
  rsiSeries = rsiChart.addLineSeries({ color: rsiColor, lineWidth: 1.5, priceLineVisible: false });
  rsiSeries.createPriceLine({ price: 70, color: "#f85149", lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: false });
  rsiSeries.createPriceLine({ price: 30, color: "#3fb950", lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: false });

  rsiSyncHandler = range => { if (range && rsiChart) rsiChart.timeScale().setVisibleLogicalRange(range); };
  chart.timeScale().subscribeVisibleLogicalRangeChange(rsiSyncHandler);
  const curRange = chart.timeScale().getVisibleLogicalRange();
  if (curRange) rsiChart.timeScale().setVisibleLogicalRange(curRange);

  resizeAllCharts();
}

function teardownRsiPane() {
  if (rsiSyncHandler) { chart.timeScale().unsubscribeVisibleLogicalRangeChange(rsiSyncHandler); rsiSyncHandler = null; }
  if (rsiChart) { rsiChart.remove(); rsiChart = null; rsiSeries = null; }
  document.getElementById("rsi-pane-wrap").style.display = "none";
}

function updateRsiPaneData() {
  if (!rsiSeries) return;
  rsiSeries.setData((currentIndicators.rsi14 || []).map(p => ({ time: p.time, value: p.value })));
}

// ─────────────────────────────────────────────────────────────────────────
// MACD sub-pane
// ─────────────────────────────────────────────────────────────────────────
function ensureMacdPane() {
  if (macdChart) return;
  document.getElementById("macd-pane-wrap").style.display = "block";
  const container = document.getElementById("macd-pane");
  macdChart = LightweightCharts.createChart(container, {
    layout: { background: { color: "#0d1117" }, textColor: "#e6edf3" },
    grid: { vertLines: { color: "#1c2128" }, horzLines: { color: "#1c2128" } },
    rightPriceScale: { borderColor: "#30363d" },
    timeScale: { borderColor: "#30363d" },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });
  macdHistSeries = macdChart.addHistogramSeries({ priceLineVisible: false });
  macdLineSeries = macdChart.addLineSeries({ color: macdLineColor, lineWidth: 1.5, priceLineVisible: false });
  macdSignalSeries = macdChart.addLineSeries({ color: macdSignalColor, lineWidth: 1.5, priceLineVisible: false });

  macdSyncHandler = range => { if (range && macdChart) macdChart.timeScale().setVisibleLogicalRange(range); };
  chart.timeScale().subscribeVisibleLogicalRangeChange(macdSyncHandler);
  const curRange = chart.timeScale().getVisibleLogicalRange();
  if (curRange) macdChart.timeScale().setVisibleLogicalRange(curRange);

  resizeAllCharts();
}

function teardownMacdPane() {
  if (macdSyncHandler) { chart.timeScale().unsubscribeVisibleLogicalRangeChange(macdSyncHandler); macdSyncHandler = null; }
  if (macdChart) { macdChart.remove(); macdChart = null; macdLineSeries = null; macdSignalSeries = null; macdHistSeries = null; }
  document.getElementById("macd-pane-wrap").style.display = "none";
}

function updateMacdPaneData() {
  if (!macdLineSeries) return;
  macdLineSeries.setData((currentIndicators.macd || []).map(p => ({ time: p.time, value: p.value })));
  macdSignalSeries.setData((currentIndicators.macd_signal || []).map(p => ({ time: p.time, value: p.value })));
  macdHistSeries.setData((currentIndicators.macd_hist || []).map(p => ({
    time: p.time, value: p.value,
    color: p.value >= 0 ? "rgba(63,185,80,0.6)" : "rgba(248,81,73,0.6)",
  })));
}

// ─────────────────────────────────────────────────────────────────────────
// Indicator toggling
// ─────────────────────────────────────────────────────────────────────────
function toggleIndicator(key) {
  activeIndicators[key] = !activeIndicators[key];
  const btn = document.querySelector(`.ind-toggle[data-ind="${key}"]`);
  if (btn) btn.classList.toggle("active", activeIndicators[key]);

  // Exact-match checks first: "macd" also starts with "ma", so it
  // must never fall into the generic MA-period branch below.
  if (key === "volume") {
    volumeSeries.applyOptions({ visible: activeIndicators.volume });
  } else if (key === "volMa") {
    volMaSeries.applyOptions({ visible: activeIndicators.volMa });
  } else if (key === "rsi") {
    if (activeIndicators.rsi) { ensureRsiPane(); updateRsiPaneData(); } else { teardownRsiPane(); }
  } else if (key === "macd") {
    if (activeIndicators.macd) { ensureMacdPane(); updateMacdPaneData(); } else { teardownMacdPane(); }
  } else if (key.startsWith("ma")) {
    const period = parseInt(key.slice(2), 10);
    if (maSeriesMap[period]) maSeriesMap[period].applyOptions({ visible: activeIndicators[key] });
  }
}

// ─────────────────────────────────────────────────────────────────────────
// Settings panel (periods + colors)
// ─────────────────────────────────────────────────────────────────────────
function openSettingsPanel() {
  const rows = document.getElementById("settings-rows");
  rows.innerHTML = "";

  MA_PERIODS_JS.forEach(defaultPeriod => {
    const row = document.createElement("div");
    row.className = "settings-row";
    row.innerHTML = `
      <label>MA (default ${defaultPeriod})</label>
      <input type="number" min="2" max="400" value="${maSettings[defaultPeriod].period}" data-ma="${defaultPeriod}">
      <input type="color" value="${maSettings[defaultPeriod].color}" data-ma-color="${defaultPeriod}">
    `;
    rows.appendChild(row);
  });

  const addSimpleColorRow = (label, currentColor, dataAttr) => {
    const row = document.createElement("div");
    row.className = "settings-row";
    row.innerHTML = `<label>${label}</label><span></span><input type="color" value="${currentColor}" data-simple-color="${dataAttr}">`;
    rows.appendChild(row);
  };
  addSimpleColorRow("RSI line", rsiColor, "rsi");
  addSimpleColorRow("MACD line", macdLineColor, "macd_line");
  addSimpleColorRow("MACD signal", macdSignalColor, "macd_signal");

  document.getElementById("settings-overlay").classList.add("visible");
}

function closeSettingsPanel() {
  document.getElementById("settings-overlay").classList.remove("visible");
  applySettingsFromPanel();
}

function applySettingsFromPanel() {
  document.querySelectorAll("#settings-rows input[data-ma]").forEach(inp => {
    const key = parseInt(inp.dataset.ma, 10);
    const val = parseInt(inp.value, 10);
    if (val >= 2 && val <= 400) maSettings[key].period = val;
  });
  document.querySelectorAll("#settings-rows input[data-ma-color]").forEach(inp => {
    const key = parseInt(inp.dataset.maColor, 10);
    maSettings[key].color = inp.value;
    if (maSeriesMap[key]) maSeriesMap[key].applyOptions({ color: inp.value });
  });
  document.querySelectorAll("#settings-rows input[data-simple-color]").forEach(inp => {
    const which = inp.dataset.simpleColor;
    if (which === "rsi") { rsiColor = inp.value; if (rsiSeries) rsiSeries.applyOptions({ color: rsiColor }); }
    if (which === "macd_line") { macdLineColor = inp.value; if (macdLineSeries) macdLineSeries.applyOptions({ color: macdLineColor }); }
    if (which === "macd_signal") { macdSignalColor = inp.value; if (macdSignalSeries) macdSignalSeries.applyOptions({ color: macdSignalColor }); }
  });

  // Update the MA toggle chips' labels/dots and refresh chart data in
  // case any period value changed.
  MA_PERIODS_JS.forEach(defaultPeriod => {
    const btn = document.querySelector(`.ind-toggle[data-ind="ma${defaultPeriod}"]`);
    if (btn) {
      const dot = btn.querySelector("i");
      if (dot) dot.style.background = maSettings[defaultPeriod].color;
      const label = maSettings[defaultPeriod].period === defaultPeriod
        ? String(defaultPeriod) : `${defaultPeriod}→${maSettings[defaultPeriod].period}`;
      btn.lastChild.textContent = label;
    }
  });
  if (currentSymbolIdx >= 0 && !gridMode) updateMaSeriesData();
}

// ─────────────────────────────────────────────────────────────────────────
// Grid view — 1D + 1W + 1M side by side, in-page (not a popup window)
// ─────────────────────────────────────────────────────────────────────────
function toggleGridView() {
  gridMode = !gridMode;
  document.getElementById("btn-grid-view").classList.toggle("active", gridMode);
  document.getElementById("single-view").style.display = gridMode ? "none" : "flex";
  document.getElementById("grid-view").classList.toggle("visible", gridMode);
  document.getElementById("tf-toggle").querySelectorAll("button[data-tf]").forEach(b => {
    b.style.opacity = gridMode ? "0.4" : "1";
    b.style.pointerEvents = gridMode ? "none" : "auto";
  });

  if (gridMode && currentSymbolIdx >= 0) {
    renderGridView(flatSymbols[currentSymbolIdx]);
  } else if (!gridMode) {
    destroyGridCharts();
    if (currentSymbolIdx >= 0) loadTimeframe(currentTF);
  }
}

function destroyGridCharts() {
  Object.values(gridChartInstances).forEach(inst => { if (inst && inst.chart) inst.chart.remove(); });
  gridChartInstances = {};
}

function renderGridView(s) {
  destroyGridCharts();
  ["1D", "1W", "1M"].forEach(tf => {
    const container = document.getElementById(`grid-chart-${tf}`);
    const tfData = s.timeframes[tf] || { bars: [], indicators: {} };
    if (!container || tfData.bars.length === 0) return;

    const gChart = LightweightCharts.createChart(container, {
      layout: { background: { color: "#0d1117" }, textColor: "#e6edf3" },
      grid: { vertLines: { color: "#1c2128" }, horzLines: { color: "#1c2128" } },
      rightPriceScale: { borderColor: "#30363d" },
      timeScale: { borderColor: "#30363d" },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    });
    const candles = gChart.addCandlestickSeries({
      upColor: "#3fb950", downColor: "#f85149",
      borderUpColor: "#3fb950", borderDownColor: "#f85149",
      wickUpColor: "#3fb950", wickDownColor: "#f85149",
    });
    candles.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.22 } });
    candles.setData(tfData.bars.map(b => ({ time: b.time, open: b.o, high: b.h, low: b.l, close: b.c })));

    const vol = gChart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol" });
    gChart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(tfData.bars.map(b => ({ time: b.time, value: b.v, color: b.c >= b.o ? "rgba(63,185,80,0.5)" : "rgba(248,81,73,0.5)" })));

    const addLine = (price, color, title) => {
      if (price == null) return;
      candles.createPriceLine({ price, color, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, title });
    };
    addLine(s.pivot_point, "#58a6ff", "Pivot");
    addLine(s.stop_loss_price, "#f85149", "Stop");
    addLine(s.target1, "#3fb950", "T1");
    addLine(s.target2, "#2ea043", "T2");

    const markers = [];
    if (s.pole_start_date) markers.push({ time: s.pole_start_date, position: "belowBar", color: "#58a6ff", shape: "arrowUp", text: "Pole" });
    if (s.flag_start_date) markers.push({ time: s.flag_start_date, position: "aboveBar", color: "#d29922", shape: "circle", text: "Flag" });
    if (s.breakout_date) markers.push({ time: s.breakout_date, position: "belowBar", color: "#3fb950", shape: "arrowUp", text: "Breakout" });
    candles.setMarkers(markers);

    if (s.pole_start_date && s.pole_end_date) {
      const startPrice = findBarClose(tfData.bars, s.pole_start_date);
      const endPrice = findBarClose(tfData.bars, s.pole_end_date);
      const poleLine = gChart.addLineSeries({ color: "#bc8cff", lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
      poleLine.setData([{ time: s.pole_start_date, value: startPrice }, { time: s.pole_end_date, value: endPrice }]);
    }
    if (s.flag_start_date && s.flag_end_date && s.flag_high != null && s.flag_low != null) {
      const opts = { color: "#d29922", lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, priceLineVisible: false, lastValueVisible: false };
      const upper = gChart.addLineSeries(opts), lower = gChart.addLineSeries(opts);
      upper.setData([{ time: s.flag_start_date, value: s.flag_high }, { time: s.flag_end_date, value: s.flag_high }]);
      lower.setData([{ time: s.flag_start_date, value: s.flag_low }, { time: s.flag_end_date, value: s.flag_low }]);
    }

    // Mirror whichever default-period MAs are currently active, using
    // the same server-computed series as the main chart.
    MA_PERIODS_JS.forEach(period => {
      if (!activeIndicators[`ma${period}`]) return;
      const ind = (tfData.indicators || {})[`sma${period}`] || [];
      if (ind.length === 0) return;
      const line = gChart.addLineSeries({
        color: maSettings[period].color, lineWidth: 1,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      });
      line.setData(ind.map(p => ({ time: p.time, value: p.value })));
    });

    gChart.timeScale().fitContent();
    gridChartInstances[tf] = { chart: gChart, candles };
  });

  resizeAllCharts();
}

// ─────────────────────────────────────────────────────────────────────────
// Sidebar / info panel collapse
// ─────────────────────────────────────────────────────────────────────────
function toggleSidebar() {
  sidebarCollapsed = !sidebarCollapsed;
  document.getElementById("sidebar").classList.toggle("collapsed", sidebarCollapsed);
  setTimeout(resizeAllCharts, 180);   // after the CSS width transition finishes
}

function toggleInfoPanel() {
  infoCollapsed = !infoCollapsed;
  document.getElementById("info-panel").classList.toggle("collapsed", infoCollapsed);
  resizeAllCharts();
}

// ─────────────────────────────────────────────────────────────────────────
// Navigation
// ─────────────────────────────────────────────────────────────────────────
function stepSymbol(delta) {
  if (flatSymbols.length === 0) return;
  let next = currentSymbolIdx + delta;
  if (next < 0) next = flatSymbols.length - 1;
  if (next >= flatSymbols.length) next = 0;
  selectSymbol(next);
}

// ─────────────────────────────────────────────────────────────────────────
// Wiring
// ─────────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  initChart();
  renderSidebar("");

  document.getElementById("search").addEventListener("input", e => renderSidebar(e.target.value));

  document.querySelectorAll("#tf-toggle button[data-tf]").forEach(b =>
    b.addEventListener("click", () => { if (currentSymbolIdx >= 0 && !gridMode) loadTimeframe(b.dataset.tf); }));

  document.getElementById("btn-prev-symbol").addEventListener("click", () => stepSymbol(-1));
  document.getElementById("btn-next-symbol").addEventListener("click", () => stepSymbol(1));
  document.getElementById("btn-grid-view").addEventListener("click", toggleGridView);
  document.getElementById("btn-toggle-sidebar").addEventListener("click", toggleSidebar);
  document.getElementById("btn-toggle-info").addEventListener("click", toggleInfoPanel);
  document.getElementById("btn-ind-settings").addEventListener("click", openSettingsPanel);
  document.getElementById("btn-settings-close").addEventListener("click", closeSettingsPanel);
  document.getElementById("settings-overlay").addEventListener("click", e => {
    if (e.target.id === "settings-overlay") closeSettingsPanel();
  });

  document.querySelectorAll(".ind-toggle[data-ind]").forEach(btn =>
    btn.addEventListener("click", () => toggleIndicator(btn.dataset.ind)));

  document.addEventListener("keydown", e => {
    if (e.target.tagName === "INPUT") return;
    if (e.code === "ArrowUp") { e.preventDefault(); stepSymbol(-1); }
    else if (e.code === "ArrowDown") { e.preventDefault(); stepSymbol(1); }
  });

  if (flatSymbols.length > 0) selectSymbol(0);
});
