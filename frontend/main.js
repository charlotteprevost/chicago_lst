import config from "./config.js";

// If Leaflet failed to load, show runtime evidence instead of a hard crash.
if (!window.L) {
  const statusEl = document.getElementById("status");
  if (statusEl) statusEl.textContent = "Leaflet (window.L) is not available.";
  throw new Error(
    "Leaflet (window.L) is not available. Check that leaflet.js loaded successfully."
  );
}

const L = window.L;

function clamp01(x) {
  return Math.max(0, Math.min(1, x));
}

function fmtNum(v, digits = 2) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "n/a";
  return n.toFixed(digits);
}

function fmtMaybeInt(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "n/a";
  return String(Math.round(n));
}

async function fetchJson(url, { cache = "default" } = {}) {
  const res = await fetch(url, { cache });
  if (!res.ok) throw new Error(`Fetch failed: ${res.status} ${res.statusText}`);
  return await res.json();
}

function joinUrl(base, path) {
  if (!base) return path;
  return base.replace(/\/+$/, "") + "/" + String(path || "").replace(/^\/+/, "");
}

async function buildTitilerTileUrlTemplate({ titilerBaseUrl, cogUrl, tms, render }) {
  const base = titilerBaseUrl?.trim();
  if (!base) throw new Error("Missing config.titilerBaseUrl (Render tile server URL).");
  const tilejsonUrl = new URL(joinUrl(base, `/cog/${encodeURIComponent(tms)}/tilejson.json`));
  tilejsonUrl.searchParams.set("url", cogUrl);
  if (render?.colormap_name) tilejsonUrl.searchParams.set("colormap_name", render.colormap_name);
  if (render?.rescale) tilejsonUrl.searchParams.set("rescale", render.rescale);
  if (render?.format) tilejsonUrl.searchParams.set("tile_format", render.format);

  const tj = await fetchJson(tilejsonUrl.toString());
  const tpl = tj?.tiles?.[0];
  if (!tpl) throw new Error("TiTiler tilejson response missing tiles[0].");
  return { template: tpl, minzoom: tj.minzoom, maxzoom: tj.maxzoom, bounds: tj.bounds };
}

const THERMAL_TILE_OPTS = {
  opacity: 0.85,
  updateWhenIdle: true,
  keepBuffer: 2,
  crossOrigin: true,
};

let snapshotTime = null;
let snapshotCoverage = null;
let analysisCoverage = { nNights: null, medianObs: null };
let thermalReady = false;

function formatSnapshotDate(iso) {
  if (!iso) return null;
  const s = String(iso);
  const day = s.includes("T") ? s.slice(0, 10) : s.slice(0, 10);
  return day || null;
}

function snapshotStatusText() {
  const day = formatSnapshotDate(snapshotTime);
  const nights = Number(analysisCoverage.nNights);
  const nightsBit = Number.isFinite(nights)
    ? `${nights} nights in analysis`
    : "multi-night analysis";
  const when = day ? `snapshot ${day} (max coverage)` : "same-pass snapshot (max coverage)";
  return `ECOSTRESS 70 m · ${nightsBit} · ${when}`;
}

function updateColorbarDate() {
  const el = document.getElementById("lstColorbarDate");
  if (!el) return;
  const day = formatSnapshotDate(snapshotTime);
  el.textContent = day ? `Snapshot ${day}` : "Snapshot date loads with tiles";
}

function showReadyStatus() {
  thermalReady = true;
  updateColorbarDate();
  setStatus(snapshotStatusText());
}

function chicagoTileForZoom(z) {
  const lon = -87.6298;
  const lat = 41.8781;
  const n = 2 ** z;
  const x = Math.floor(((lon + 180) / 360) * n);
  const latRad = (lat * Math.PI) / 180;
  const y = Math.floor(
    ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n
  );
  return { z, x, y };
}

async function probeXyzTemplate(template) {
  const { z, x, y } = chicagoTileForZoom(9);
  const url = template
    .replace("{z}", String(z))
    .replace("{x}", String(x))
    .replace("{y}", String(y));
  try {
    const res = await fetch(url, { method: "GET", cache: "default" });
    return res.ok;
  } catch {
    return false;
  }
}

function wakeTitiler() {
  const base = config.titilerBaseUrl?.trim();
  if (!base) return;
  fetch(joinUrl(base, "/"), { mode: "no-cors", cache: "no-store" }).catch(() => {});
}

async function makeTitilerLayer(ds) {
  const meta = await fetchJson(ds.cogMetaUrl);
  snapshotTime = meta?.scene_time || meta?.acquired || snapshotTime;
  const cov = meta?.coverage || {};
  const n = Number(cov.n_dc_with_pixels);
  const m = Number(cov.n_dc_sites);
  if (Number.isFinite(n) && Number.isFinite(m) && m > 0) {
    snapshotCoverage = { n, m };
  }
  const cogUrl = meta?.cog_url;
  const tms = meta?.tms ?? "WebMercatorQuad";
  const render = meta?.render ?? {};
  const tilesUrl = String(meta?.tiles_url || "").trim();
  if (tilesUrl.includes("{z}") && tilesUrl.includes("{x}") && tilesUrl.includes("{y}")) {
    const xyzReady = await probeXyzTemplate(tilesUrl);
    if (xyzReady) {
      return L.tileLayer(tilesUrl, {
        ...THERMAL_TILE_OPTS,
        maxZoom: typeof meta.maxzoom === "number" ? meta.maxzoom : 12,
        minZoom: typeof meta.minzoom === "number" ? meta.minzoom : 9,
        attribution: "ECOSTRESS LST (static tiles)",
      });
    }
  }
  if (!cogUrl) throw new Error("Missing cog_url in data/ecostress_highres_latest.json");
  if (String(cogUrl).includes("example.com")) {
    throw new Error("COG URL is still a placeholder. Set a real public COG in data/ecostress_highres_latest.json.");
  }

  const { template, maxzoom } = await buildTitilerTileUrlTemplate({
    titilerBaseUrl: config.titilerBaseUrl,
    cogUrl,
    tms,
    render,
  });

  return L.tileLayer(template, {
    ...THERMAL_TILE_OPTS,
    maxZoom: typeof maxzoom === "number" ? maxzoom : 17,
    attribution:
      'Tiles: <a href="https://developmentseed.org/titiler/" target="_blank" rel="noopener noreferrer">TiTiler</a>',
  });
}

const els = {
  toggleHelp: document.getElementById("toggleHelp"),
  helpPanel: document.getElementById("helpPanel"),
  toggleEng: document.getElementById("toggleEng"),
  engPanel: document.getElementById("engPanel"),
  engRefreshTs: document.getElementById("engRefreshTs"),
  engApiHealth: document.getElementById("engApiHealth"),
  overlayRisk: document.getElementById("overlayRisk"),
  overlayDC: document.getElementById("overlayDC"),
  overlayEffect: document.getElementById("overlayEffect"),
  siteFilter: document.getElementById("siteFilter"),
  bufferFilter: document.getElementById("bufferFilter"),
  metricCoverage: document.getElementById("metricCoverage"),
  metricVisibleAoIs: document.getElementById("metricVisibleAoIs"),
  metricMeanDelta: document.getElementById("metricMeanDelta"),
  metricMaxRisk: document.getElementById("metricMaxRisk"),
  exportCsv: document.getElementById("exportCsv"),
  exportGeojson: document.getElementById("exportGeojson"),
  status: document.getElementById("status"),
};

function setStatus(msg) {
  if (!els.status) return;
  els.status.textContent = msg || "";
  els.status.classList.toggle("status--loading", /(loading|rendering)/i.test(msg || ""));
}

function setHelpPanelOpen(open) {
  if (!els.helpPanel || !els.toggleHelp) return;
  els.helpPanel.hidden = !open;
  els.toggleHelp.setAttribute("aria-expanded", open ? "true" : "false");
  els.toggleHelp.textContent = open ? "Hide guide" : "How this works";
  if (open && els.engPanel && !els.engPanel.hidden) {
    els.engPanel.hidden = true;
    els.toggleEng?.setAttribute("aria-expanded", "false");
    document.body.classList.remove("eng-open");
  }
}

function setEngPanelOpen(open) {
  if (!els.engPanel || !els.toggleEng) return;
  els.engPanel.hidden = !open;
  els.toggleEng.setAttribute("aria-expanded", open ? "true" : "false");
  document.body.classList.toggle("eng-open", open);
  if (open && els.helpPanel && !els.helpPanel.hidden) {
    els.helpPanel.hidden = true;
    els.toggleHelp?.setAttribute("aria-expanded", "false");
    if (els.toggleHelp) els.toggleHelp.textContent = "How this works";
  }
}

els.toggleHelp?.addEventListener("click", () => {
  const isOpen = !els.helpPanel?.hidden;
  setHelpPanelOpen(!isOpen);
});

els.toggleEng?.addEventListener("click", () => {
  const isOpen = !els.engPanel?.hidden;
  setEngPanelOpen(!isOpen);
});

const datasets = config.gibs.datasets;
let datasetId = config.gibs.defaultDatasetId;

function getDataset() {
  return datasets[datasetId] || datasets[config.gibs.defaultDatasetId];
}

const map = L.map("map", { maxBoundsViscosity: 1.0, minZoom: 9, worldCopyJump: false });

const mapShell = document.getElementById("map-shell");
const mapLoadingEl = document.getElementById("map-loading");

function setMapLoading(on) {
  if (!mapShell) return;
  mapShell.classList.toggle("map-shell--loading", Boolean(on));
  mapShell.setAttribute("aria-busy", on ? "true" : "false");
  if (mapLoadingEl) mapLoadingEl.setAttribute("aria-hidden", on ? "false" : "true");
}

function boundsFromAoi(aoi) {
  if (!aoi || !Number.isFinite(aoi.south)) return null;
  const raw = L.latLngBounds(
    [aoi.south, aoi.west],
    [aoi.north, aoi.east]
  );
  return raw.pad(0.08);
}

async function applyChicagoMapClamp() {
  let aoi = null;
  try {
    aoi = await fetchJson(config.aoiUrl || "../data/chicago_dc_aoi.json");
  } catch (e) {
    console.warn("Chicago AOI JSON missing; using default cluster box", e);
    aoi = { west: -88.38, south: 41.52, east: -87.47, north: 42.43 };
  }
  const box = boundsFromAoi(aoi);
  if (!box) return;
  map.setMaxBounds(box);
  map.setMinZoom(9);
  map.fitBounds(box, { padding: [24, 24], maxZoom: 10 });
}

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution:
    '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>',
}).addTo(map);

const ds0 = getDataset();
map.setView(ds0.defaultView.center, ds0.defaultView.zoom);
applyChicagoMapClamp().catch((e) => console.warn("Map clamp failed", e));
window.setTimeout(() => map.invalidateSize(), 0);
window.addEventListener("resize", () => map.invalidateSize());

let baseLayer = null;
let baseLayerLoadTimer = null;

function attachThermalLoadHandlers(layer, { doneMsg }) {
  let settled = false;
  let tileErrors = 0;
  const finish = (statusMsg) => {
    if (settled) return;
    settled = true;
    if (baseLayerLoadTimer) {
      window.clearTimeout(baseLayerLoadTimer);
      baseLayerLoadTimer = null;
    }
    setMapLoading(false);
    setStatus(statusMsg);
  };
  layer.on("tileerror", (e) => {
    tileErrors += 1;
    console.warn("tileerror", { coords: e?.coords ?? null, url: e?.tile?.src ?? null });
  });
  layer.once("load", () => {
    showReadyStatus();
    if (tileErrors) {
      finish(`${snapshotStatusText()}; some tiles are missing (coverage or server).`);
      return;
    }
    finish(doneMsg);
  });
  baseLayerLoadTimer = window.setTimeout(() => {
    if (settled) return;
    setStatus("Still rendering temperature tiles…");
  }, 15000);
}

async function setBaseLayerForDataset(ds) {
  setMapLoading(true);
  if (baseLayerLoadTimer) {
    window.clearTimeout(baseLayerLoadTimer);
    baseLayerLoadTimer = null;
  }
  try {
    if (baseLayer) map.removeLayer(baseLayer);
    setStatus("Rendering ECOSTRESS tiles…");
    baseLayer = await makeTitilerLayer(ds);
    baseLayer.addTo(map);
    attachThermalLoadHandlers(baseLayer, { doneMsg: snapshotStatusText() });
    updateInsightPanel();
  } catch (e) {
    setMapLoading(false);
    throw e;
  }
}

function markThermalUnavailable(reason) {
  if (baseLayer) {
    map.removeLayer(baseLayer);
    baseLayer = null;
  }
  setMapLoading(false);
  const msg = reason
    ? `${reason} Temperature tiles unavailable.`
    : "Temperature tiles unavailable.";
  setStatus(msg);
}

wakeTitiler();

// init base layer
setBaseLayerForDataset(ds0).catch((e) => {
  console.warn("Base layer init failed", e);
  markThermalUnavailable(`High-res layer unavailable (${e?.message ?? e}).`);
});

// --- AOI risk overlay (GeoJSON) ---
const riskCfg = config.overlays?.riskAoi ?? null;
let riskLayer = null;
let riskData = null;
const dcCfg = config.overlays?.dataCenters ?? null;
let dcLayer = null;
let dcData = null;
const effectCfg = config.overlays?.dcEffect ?? null;
let effectLayer = null;
let effectData = null;
let siteFilterValue = "all";
let bufferFilterValue = "all";

function siteKeyFromProps(p = {}) {
  const candidate = p.site_id ?? p.site_name ?? p.name ?? null;
  if (candidate === null || candidate === undefined) return null;
  const out = String(candidate).trim();
  return out ? out : null;
}

function bufferKeyFromProps(p = {}) {
  const raw = p.buffer_m;
  if (raw === null || raw === undefined || raw === "") return null;
  const n = Number(raw);
  if (!Number.isFinite(n)) return null;
  return String(Math.round(n));
}

function featurePassesFilters(p = {}, { requireBuffer = false } = {}) {
  const s = siteKeyFromProps(p);
  const b = bufferKeyFromProps(p);
  if (siteFilterValue !== "all" && s !== siteFilterValue) return false;
  if (bufferFilterValue !== "all") {
    if (requireBuffer && !b) return false;
    if (b && b !== bufferFilterValue) return false;
  }
  return true;
}

function rebuildFilterControls() {
  const siteKeys = new Set();
  const bufferKeys = new Set();
  const allFeatures = [
    ...(riskData?.features ?? []),
    ...(dcData?.features ?? []),
    ...(effectData?.features ?? []),
  ];
  for (const f of allFeatures) {
    const p = f?.properties ?? {};
    const s = siteKeyFromProps(p);
    const b = bufferKeyFromProps(p);
    if (s) siteKeys.add(s);
    if (b) bufferKeys.add(b);
  }

  if (els.siteFilter) {
    const prev = siteFilterValue;
    els.siteFilter.innerHTML = '<option value="all">All sites</option>';
    [...siteKeys].sort((a, b) => a.localeCompare(b)).forEach((k) => {
      const opt = document.createElement("option");
      opt.value = k;
      opt.textContent = k;
      els.siteFilter.appendChild(opt);
    });
    if ([...siteKeys].includes(prev)) {
      els.siteFilter.value = prev;
      siteFilterValue = prev;
    } else {
      els.siteFilter.value = "all";
      siteFilterValue = "all";
    }
  }

  if (els.bufferFilter) {
    const prev = bufferFilterValue;
    els.bufferFilter.innerHTML = '<option value="all">All buffers</option>';
    [...bufferKeys]
      .map((v) => Number(v))
      .filter((v) => Number.isFinite(v))
      .sort((a, b) => a - b)
      .forEach((n) => {
        const k = String(Math.round(n));
        const opt = document.createElement("option");
        opt.value = k;
        opt.textContent = `${k} m`;
        els.bufferFilter.appendChild(opt);
      });
    if ([...bufferKeys].includes(prev)) {
      els.bufferFilter.value = prev;
      bufferFilterValue = prev;
    } else {
      els.bufferFilter.value = "all";
      bufferFilterValue = "all";
    }
  }
  updateInsightPanel();
}

function getFilteredFeatures(gj, { requireBuffer = false } = {}) {
  return (gj?.features ?? []).filter((f) =>
    featurePassesFilters(f?.properties ?? {}, { requireBuffer })
  );
}

function setMetric(el, value) {
  if (!el) return;
  el.textContent = value;
}

function currentExportContext() {
  if (els.overlayEffect?.checked && effectData) {
    return {
      name: "dc_effect_filtered",
      features: getFilteredFeatures(effectData, { requireBuffer: true }),
    };
  }
  if (els.overlayRisk?.checked && riskData) {
    return {
      name: "aoi_risk_filtered",
      features: getFilteredFeatures(riskData, { requireBuffer: false }),
    };
  }
  if (els.overlayDC?.checked && dcData) {
    return {
      name: "data_centers_filtered",
      features: getFilteredFeatures(dcData, { requireBuffer: false }),
    };
  }
  return { name: "overlay_filtered", features: [] };
}

function updateInsightPanel() {
  const riskFeatures = getFilteredFeatures(riskData, { requireBuffer: false });
  const effectFeatures = getFilteredFeatures(effectData, { requireBuffer: true });
  const visibleAois = riskFeatures.length;
  setMetric(els.metricVisibleAoIs, String(visibleAois || 0));

  if (snapshotCoverage && Number.isFinite(snapshotCoverage.n) && Number.isFinite(snapshotCoverage.m)) {
    setMetric(els.metricCoverage, `${snapshotCoverage.n} of ${snapshotCoverage.m} sites`);
  } else {
    setMetric(els.metricCoverage, "n/a");
  }

  const deltas = effectFeatures
    .map((f) => Number(f?.properties?.delta_mean_c))
    .filter((v) => Number.isFinite(v));
  const meanDelta = deltas.length
    ? deltas.reduce((a, b) => a + b, 0) / deltas.length
    : Number.NaN;
  setMetric(els.metricMeanDelta, Number.isFinite(meanDelta) ? fmtNum(meanDelta, 2) : "n/a");

  const risks = riskFeatures
    .map((f) => Number(f?.properties?.risk_score))
    .filter((v) => Number.isFinite(v));
  const maxRisk = risks.length ? Math.max(...risks) : Number.NaN;
  setMetric(els.metricMaxRisk, Number.isFinite(maxRisk) ? fmtNum(maxRisk, 1) : "n/a");
  updateEngineeringPanel();
}

function updateEngineeringPanel() {
  if (els.engApiHealth) {
    els.engApiHealth.href = joinUrl(config.titilerBaseUrl, "/");
  }
  if (!els.engRefreshTs) return;

  if (snapshotTime) {
    els.engRefreshTs.textContent = snapshotTime;
    return;
  }

  const candidates = [];
  for (const f of riskData?.features ?? []) {
    const dt = f?.properties?.date;
    if (dt) candidates.push(dt);
  }
  for (const f of effectData?.features ?? []) {
    const dt = f?.properties?.last_dt;
    if (dt) candidates.push(dt);
  }
  if (!candidates.length) {
    els.engRefreshTs.textContent = "n/a";
    return;
  }
  const parsed = candidates
    .map((s) => new Date(String(s)))
    .filter((d) => Number.isFinite(d.getTime()))
    .sort((a, b) => b.getTime() - a.getTime());
  els.engRefreshTs.textContent = parsed.length ? parsed[0].toISOString() : "n/a";
}

function triggerDownload(filename, text, mimeType) {
  const blob = new Blob([text], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function toCsv(features) {
  if (!features.length) return "message\nNo filtered features available\n";
  const rows = features.map((f) => f?.properties ?? {});
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  const esc = (v) => {
    const s = String(v ?? "");
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [cols.join(",")];
  rows.forEach((r) => lines.push(cols.map((c) => esc(r[c])).join(",")));
  return `${lines.join("\n")}\n`;
}

function riskColor(score) {
  const s = Number(score);
  if (!Number.isFinite(s)) return "#64748b"; // slate
  if (s >= 80) return "#7f1d1d"; // red-900
  if (s >= 60) return "#b91c1c"; // red-700
  if (s >= 40) return "#f97316"; // orange-500
  if (s >= 20) return "#facc15"; // yellow-400
  return "#22c55e"; // green-500
}

function riskStyle(feature) {
  const score = feature?.properties?.[riskCfg?.field ?? "risk_score"];
  return {
    color: "#0b1020",
    weight: 1,
    opacity: 0.8,
    fillColor: riskColor(score),
    fillOpacity: clamp01(0.15 + (Number(score) || 0) / 120),
  };
}

async function fetchRiskData() {
  if (!riskCfg?.url) return null;
  let res = await fetch(riskCfg.url);
  // If latest isn't present (common when outputs are gitignored), fall back to a sample file.
  if (!res.ok && String(riskCfg.url).includes("aoi_risk_latest.geojson")) {
    res = await fetch("../data/aoi_risk_sample.geojson");
  }
  if (!res.ok) throw new Error(`Overlay fetch failed: ${res.status} ${res.statusText}`);
  const gj = await res.json();
  return gj;
}

function buildRiskLayer(gj) {
  if (!gj?.features?.length) {
    setStatus("AOI risk overlay loaded (0 features). Run analysis export to populate it.");
  }

  const layer = L.geoJSON(gj, {
    filter: (f) => featurePassesFilters(f?.properties ?? {}, { requireBuffer: false }),
    style: riskStyle,
    onEachFeature: (f, l) => {
      const p = f?.properties ?? {};
      const score = p[riskCfg.field ?? "risk_score"];
      const units = p.units ?? "°C";
      const mean = p.mean;
      const anomaly = p.anomaly;
      const z = p.z;
      const hot14 = p.hot_nights_14;
      const trend = p.trend_c_per_year;
      const dt = p.date;

      const buffer = p.buffer_m;
      const isDC = p.is_data_center;
      const siteName = p.site_name;
      const siteId = p.site_id;
      const group = p.group;

      const ctxBits = [
        siteName ? `<div><b>Site</b>: ${siteName}</div>` : "",
        siteId ? `<div><b>Site ID</b>: ${siteId}</div>` : "",
        Number.isFinite(Number(isDC))
          ? `<div><b>AOI type</b>: ${Number(isDC) === 1 ? "data center buffer" : "control buffer"}</div>`
          : "",
        Number.isFinite(Number(buffer)) ? `<div><b>Buffer</b>: ${fmtMaybeInt(buffer)} m</div>` : "",
        group ? `<div><b>Group</b>: ${group}</div>` : "",
      ]
        .filter(Boolean)
        .join("");
      l.bindPopup(
        `<div style="min-width:220px">
          <div style="font-weight:700;margin-bottom:6px">AOI risk + context</div>
          ${ctxBits}
          <hr style="border:0;border-top:1px solid rgba(255,255,255,.15);margin:8px 0" />
          <div><b>Risk score</b>: ${fmtNum(score, 1)} / 100</div>
          <div style="opacity:.85;margin-top:6px">
            Risk is a composite:
            <div style="margin-top:4px">
              - <b>z-score</b> (latest anomaly vs baseline)<br/>
              - <b>hot nights</b> (last 14 obs above baseline p90)<br/>
              - <b>trend</b> (°C/year, positive only)
            </div>
          </div>
          <hr style="border:0;border-top:1px solid rgba(255,255,255,.15);margin:8px 0" />
          <div><b>Mean</b>: ${fmtNum(mean, 2)} ${units}</div>
          <div><b>Anomaly</b>: ${fmtNum(anomaly, 2)} ${units}</div>
          <div><b>z</b>: ${fmtNum(z, 2)}</div>
          <div><b>Hot nights (last 14)</b>: ${fmtMaybeInt(hot14)} / 14</div>
          <div><b>Trend</b>: ${fmtNum(trend, 2)} °C/yr</div>
          <div style="opacity:.75;margin-top:6px"><b>Latest timestamp</b>: ${dt ?? "n/a"}</div>
          <div style="opacity:.75;margin-top:6px">
            If values show <b>n/a</b>, that AOI had no usable ECOSTRESS pixels at the latest timestamp
            (cloud/QC masking, missing coverage, or insufficient baseline).
          </div>
        </div>`
      );
    },
  });
  return layer;
}

function dcPointStyle() {
  return {
    radius: 5,
    color: "#0b1020",
    weight: 1,
    opacity: 0.9,
    fillColor: "#6ee7ff",
    fillOpacity: 0.85,
  };
}

async function loadDataCentersLayer() {
  if (!dcCfg?.url) return null;
  const res = await fetch(dcCfg.url);
  if (!res.ok) throw new Error(`Overlay fetch failed: ${res.status} ${res.statusText}`);
  return await res.json();
}

function buildDataCentersLayer(gj) {
  const layer = L.geoJSON(gj, {
    filter: (f) => featurePassesFilters(f?.properties ?? {}, { requireBuffer: false }),
    pointToLayer: (_f, latlng) => L.circleMarker(latlng, dcPointStyle()),
    onEachFeature: (f, l) => {
      const p = f?.properties ?? {};
      const name = p.name ?? p.site_name ?? "Data center";
      const op = p.operator ?? "";
      const addr = p.full_address ?? "";
      l.bindPopup(
        `<div style="min-width:240px">
          <div style="font-weight:700">${name}</div>
          ${op ? `<div style="opacity:.85">${op}</div>` : ""}
          ${addr ? `<div style="opacity:.75;margin-top:6px">${addr}</div>` : ""}
          <div style="opacity:.75;margin-top:8px">
            Tip: the colored <b>buffer polygons</b> show ΔLST vs controls. Click a polygon for details.
          </div>
        </div>`
      );
    },
  });
  return layer;
}

function effectColor(delta) {
  const d = Number(delta);
  if (!Number.isFinite(d)) return "#64748b";
  if (d >= 4) return "#7f1d1d";
  if (d >= 2) return "#b91c1c";
  if (d >= 1) return "#ef4444";
  if (d >= 0.5) return "#f97316";
  if (d >= 0) return "#facc15";
  if (d >= -0.5) return "#a3e635";
  if (d >= -1) return "#22c55e";
  if (d >= -2) return "#14b8a6";
  return "#0ea5e9";
}

function effectStyle(feature) {
  const delta = feature?.properties?.[effectCfg?.field ?? "delta_mean_c"];
  return {
    color: "#0b1020",
    weight: 1,
    opacity: 0.75,
    fillColor: effectColor(delta),
    fillOpacity: 0.35,
  };
}

async function loadEffectLayer() {
  if (!effectCfg?.url) return null;
  const res = await fetch(effectCfg.url);
  if (!res.ok) throw new Error(`Overlay fetch failed: ${res.status} ${res.statusText}`);
  return await res.json();
}

function buildEffectLayer(gj) {
  if (!gj?.features?.length) {
    setStatus("DC effect overlay loaded (0 features). Run analysis export to populate it.");
  }
  const layer = L.geoJSON(gj, {
    filter: (f) => featurePassesFilters(f?.properties ?? {}, { requireBuffer: true }),
    style: effectStyle,
    onEachFeature: (f, l) => {
      const p = f?.properties ?? {};
      l.bindPopup(
        `<div style="min-width:240px">
          <div style="font-weight:700;margin-bottom:6px">Data center effect (cumulative)</div>
          <div><b>Mean ΔLST (DC − controls)</b>: ${fmtNum(p.delta_mean_c, 2)} °C</div>
          <div><b>Median ΔLST</b>: ${fmtNum(p.delta_median_c, 2)} °C</div>
          <div><b>P90 ΔLST</b>: ${fmtNum(p.delta_p90_c, 2)} °C</div>
          <div style="opacity:.85;margin-top:6px"><b>Observations</b>: ${fmtMaybeInt(p.n_obs)}</div>
          <div style="opacity:.75;margin-top:6px"><b>Buffer</b>: ${p.buffer_m ?? "n/a"} m</div>
          <div style="opacity:.75"><b>Span</b>: ${p.first_dt ?? "n/a"} → ${p.last_dt ?? "n/a"}</div>
          <div style="opacity:.75;margin-top:6px">
            This aggregates per‑timestamp DC−control differences across all available dates
            (controls are timestamp-matched).
          </div>
          <hr style="border:0;border-top:1px solid rgba(255,255,255,.15);margin:8px 0" />
          <div><b>Opening date (if known)</b>: ${p.opening_date ?? "n/a"}</div>
          <div><b>Pre-open observations</b>: ${fmtMaybeInt(p.n_pre_open_obs)}</div>
          <div><b>Post-open observations</b>: ${fmtMaybeInt(p.n_post_open_obs)}</div>
          <div><b>Pre-open window</b>: ${(p.pre_open_first_dt ?? "n/a")} → ${(p.pre_open_last_dt ?? "n/a")}</div>
          <div><b>Post-open window</b>: ${(p.post_open_first_dt ?? "n/a")} → ${(p.post_open_last_dt ?? "n/a")}</div>
          <div><b>Pre-open Δ mean</b>: ${fmtNum(p.delta_pre_open_mean_c, 2)} °C</div>
          <div><b>Post-open Δ mean</b>: ${fmtNum(p.delta_post_open_mean_c, 2)} °C</div>
          <div style="opacity:.75;margin-top:6px">
            If opening date is n/a, the site metadata lacks opening year.
          </div>
        </div>`
      );
    },
  });
  return layer;
}

let riskLegend = null;

function ensureLegend() {
  if (riskLegend || !riskCfg) return;
  // Use top-right so it can't fall off-screen on shorter viewports.
  const legend = L.control({ position: "topright" });
  legend.onAdd = () => {
    const div = L.DomUtil.create("div", "leaflet-control");
    div.style.background = "rgba(11,16,32,0.85)";
    div.style.color = "#fff";
    div.style.padding = "10px 10px";
    div.style.borderRadius = "10px";
    div.style.border = "1px solid rgba(255,255,255,0.18)";
    div.style.font = "12px system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif";
    div.innerHTML = `
      <div style="font-weight:700;margin-bottom:6px">AOI risk</div>
      <div style="display:grid;gap:4px">
        ${[
          { label: "0–19", c: riskColor(0) },
          { label: "20–39", c: riskColor(20) },
          { label: "40–59", c: riskColor(40) },
          { label: "60–79", c: riskColor(60) },
          { label: "80–100", c: riskColor(80) },
        ]
          .map(
            (b) =>
              `<div style="display:flex;align-items:center;gap:8px">
                 <span style="width:12px;height:12px;border-radius:3px;background:${b.c};border:1px solid rgba(255,255,255,.25)"></span>
                 <span>${b.label}</span>
               </div>`
          )
          .join("")}
      </div>
    `;
    return div;
  };
  legend.addTo(map);
  riskLegend = legend;
}

function setRiskLegendVisible(on) {
  if (!on) {
    if (riskLegend) {
      map.removeControl(riskLegend);
      riskLegend = null;
    }
    return;
  }
  ensureLegend();
}

async function syncRiskOverlay() {
  const enabled = Boolean(els.overlayRisk?.checked);
  setRiskLegendVisible(enabled);
  if (!enabled) {
    if (riskLayer) map.removeLayer(riskLayer);
    return;
  }
  if (!riskCfg?.url) return;
  if (!riskData) {
    try {
      setStatus("Loading AOI risk overlay…");
      riskData = await fetchRiskData();
      rebuildFilterControls();
    } catch (e) {
      console.warn("Risk overlay load failed", e);
      setStatus("AOI risk overlay not found (run analysis to generate GeoJSON).");
      return;
    }
  }
  if (riskLayer) map.removeLayer(riskLayer);
  riskLayer = buildRiskLayer(riskData);
  riskLayer.addTo(map);
  updateInsightPanel();
}

els.overlayRisk?.addEventListener("change", () => {
  syncRiskOverlay();
});

async function syncDcOverlay() {
  const enabled = Boolean(els.overlayDC?.checked);
  if (!enabled) {
    if (dcLayer) map.removeLayer(dcLayer);
    return;
  }
  if (!dcCfg?.url) return;
  if (!dcData) {
    try {
      setStatus("Loading data center points…");
      dcData = await loadDataCentersLayer();
      rebuildFilterControls();
    } catch (e) {
      console.warn("Data center overlay load failed", e);
      setStatus("Data center overlay not found.");
      return;
    }
  }
  if (dcLayer) map.removeLayer(dcLayer);
  dcLayer = buildDataCentersLayer(dcData);
  dcLayer.addTo(map);
  updateInsightPanel();
}

els.overlayDC?.addEventListener("change", () => {
  syncDcOverlay();
});

async function syncEffectOverlay() {
  const enabled = Boolean(els.overlayEffect?.checked);
  if (!enabled) {
    if (effectLayer) map.removeLayer(effectLayer);
    return;
  }
  if (!effectCfg?.url) return;
  if (!effectData) {
    try {
      setStatus("Loading DC effect overlay…");
      effectData = await loadEffectLayer();
      rebuildFilterControls();
    } catch (e) {
      console.warn("Effect overlay load failed", e);
      setStatus("DC effect overlay not found (run analysis export).");
      return;
    }
  }
  if (effectLayer) map.removeLayer(effectLayer);
  effectLayer = buildEffectLayer(effectData);
  effectLayer.addTo(map);
  updateInsightPanel();
}

els.overlayEffect?.addEventListener("change", () => {
  syncEffectOverlay();
});

async function applySiteAndBufferFilters() {
  const nextSite = els.siteFilter?.value ?? "all";
  const nextBuffer = els.bufferFilter?.value ?? "all";
  siteFilterValue = nextSite;
  bufferFilterValue = nextBuffer;
  await Promise.all([syncRiskOverlay(), syncDcOverlay(), syncEffectOverlay()]);
}

els.siteFilter?.addEventListener("change", () => {
  applySiteAndBufferFilters().catch((e) => {
    console.warn("Site filter sync failed", e);
  });
});

els.bufferFilter?.addEventListener("change", () => {
  applySiteAndBufferFilters().catch((e) => {
    console.warn("Buffer filter sync failed", e);
  });
});

els.exportGeojson?.addEventListener("click", () => {
  const ctx = currentExportContext();
  const fc = {
    type: "FeatureCollection",
    features: ctx.features,
  };
  triggerDownload(
    `${ctx.name}.geojson`,
    `${JSON.stringify(fc, null, 2)}\n`,
    "application/geo+json;charset=utf-8"
  );
});

els.exportCsv?.addEventListener("click", () => {
  const ctx = currentExportContext();
  triggerDownload(`${ctx.name}.csv`, toCsv(ctx.features), "text/csv;charset=utf-8");
});

// Init
setHelpPanelOpen(false);
setEngPanelOpen(false);
updateEngineeringPanel();
fetchJson(config.coverageUrl || "../data/coverage_latest.json")
  .then((cov) => {
    const snap = cov?.snapshot || cov;
    const n = Number(snap?.n_dc_with_pixels);
    const m = Number(snap?.n_dc_sites || cov?.n_dc_sites);
    const nights = Number(cov?.n_study_nights);
    const medianObs = Number(cov?.median_n_obs);
    if (Number.isFinite(nights)) analysisCoverage.nNights = nights;
    if (Number.isFinite(medianObs)) analysisCoverage.medianObs = medianObs;
    if (snap?.scene_time) {
      snapshotTime = snap.scene_time;
      updateColorbarDate();
    }
    if (!snapshotCoverage && Number.isFinite(n) && Number.isFinite(m) && m > 0) {
      snapshotCoverage = { n, m };
      updateInsightPanel();
    }
    if (thermalReady) showReadyStatus();
  })
  .catch(() => {});
syncDcOverlay();
syncRiskOverlay();
syncEffectOverlay();

