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

function formatDateUTC(d) {
  const yyyy = d.getUTCFullYear();
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(d.getUTCDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

function formatTimeHHMMUTC(d) {
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

function formatTimestampUTC(d) {
  // ISO8601 with Z, to the minute (seconds fixed at 00).
  return `${formatDateUTC(d)}T${formatTimeHHMMUTC(d)}:00Z`;
}

function parseDateInput(value) {
  // HTML date input gives YYYY-MM-DD (local), interpret as UTC day for GIBS.
  const [y, m, d] = (value || "").split("-").map((v) => Number(v));
  if (!y || !m || !d) return null;
  return new Date(Date.UTC(y, m - 1, d, 0, 0, 0));
}

function parseTimeInput(value) {
  const [h, m] = (value || "").split(":").map((v) => Number(v));
  if (Number.isNaN(h) || Number.isNaN(m)) return null;
  if (h < 0 || h > 23 || m < 0 || m > 59) return null;
  return { h, m };
}

function addDaysUTC(date, deltaDays) {
  const t = Date.UTC(
    date.getUTCFullYear(),
    date.getUTCMonth(),
    date.getUTCDate() + deltaDays,
    0,
    0,
    0
  );
  return new Date(t);
}

function addMinutesUTC(date, deltaMinutes) {
  return new Date(date.getTime() + deltaMinutes * 60 * 1000);
}

function floorToStepMinutesUTC(date, stepMinutes) {
  const stepMs = stepMinutes * 60 * 1000;
  return new Date(Math.floor(date.getTime() / stepMs) * stepMs);
}

class GibsTimeLayer extends L.TileLayer {
  constructor(options) {
    super(options.urlTemplate, {
      ...options,
      attribution:
        'Imagery: <a href="https://earthdata.nasa.gov/gibs" target="_blank" rel="noopener noreferrer">NASA GIBS</a>',
      maxZoom: options.maxZoom ?? 7,
    });
    this._time = options.time;
    this._layerId = options.layerId;
    this._tileMatrixSet = options.tileMatrixSet;
    this._service = options.service ?? "best";
  }

  setTime(isoTime) {
    this._time = isoTime;
    this.redraw();
  }

  setDataset({ layerId, tileMatrixSet, maxZoom }) {
    this._layerId = layerId;
    this._tileMatrixSet = tileMatrixSet;
    if (typeof maxZoom === "number") this.options.maxZoom = maxZoom;
    this.redraw();
  }

  setService(service) {
    this._service = service || "best";
    this.redraw();
  }

  getTileUrl(coords) {
    // Leaflet supplies {x,y,z}; GIBS requires {layer,time,tileMatrixSet} too.
    const url = L.Util.template(this._url, {
      ...coords,
      service: this._service,
      layer: this._layerId,
      time: this._time,
      tileMatrixSet: this._tileMatrixSet,
    });
    return url;
  }
}

async function fetchJson(url) {
  const res = await fetch(url, { cache: "no-store" });
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

async function makeTitilerLayer(ds) {
  const meta = await fetchJson(ds.cogMetaUrl);
  const cogUrl = meta?.cog_url;
  const tms = meta?.tms ?? "WebMercatorQuad";
  const render = meta?.render ?? {};
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
    maxZoom: typeof maxzoom === "number" ? maxzoom : 17,
    opacity: 0.85,
    attribution:
      'Tiles: <a href="https://developmentseed.org/titiler/" target="_blank" rel="noopener noreferrer">TiTiler</a>',
    crossOrigin: true,
  });
}

const els = {
  toggleHelp: document.getElementById("toggleHelp"),
  helpPanel: document.getElementById("helpPanel"),
  dataset: document.getElementById("dataset"),
  overlayRisk: document.getElementById("overlayRisk"),
  overlayDC: document.getElementById("overlayDC"),
  overlayEffect: document.getElementById("overlayEffect"),
  date: document.getElementById("date"),
  time: document.getElementById("time"),
  timeWrap: document.getElementById("timeWrap"),
  prev: document.getElementById("prev"),
  next: document.getElementById("next"),
  play: document.getElementById("play"),
  status: document.getElementById("status"),
};

function setStatus(msg) {
  if (els.status) els.status.textContent = msg || "";
}

function setHelpPanelOpen(open) {
  if (!els.helpPanel || !els.toggleHelp) return;
  els.helpPanel.hidden = !open;
  els.toggleHelp.setAttribute("aria-expanded", open ? "true" : "false");
  els.toggleHelp.textContent = open ? "Hide guide" : "How this works";
}

els.toggleHelp?.addEventListener("click", () => {
  const isOpen = !els.helpPanel?.hidden;
  setHelpPanelOpen(!isOpen);
});

const datasets = config.gibs.datasets;
let datasetId = config.gibs.defaultDatasetId;
const fallbackDatasetId = config.gibs.fallbackDatasetId || "viirs_night_global";

function getDataset() {
  return datasets[datasetId] || datasets[config.gibs.defaultDatasetId];
}

function setDatasetChoice(nextId) {
  if (!datasets[nextId]) return false;
  datasetId = nextId;
  if (els.dataset) els.dataset.value = nextId;
  return true;
}

function isoTimeForDataset(ds, dateObj) {
  return ds.cadence === "daily" ? formatDateUTC(dateObj) : formatTimestampUTC(dateObj);
}

function currentDefaultTimeForDataset(ds) {
  const now = new Date();
  if (ds.cadence === "daily") {
    // Default to "yesterday" UTC to avoid partial same-day coverage.
    return addDaysUTC(
      new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 0, 0, 0)),
      -1
    );
  }
  // For 10-min cadence, stay a bit behind "now" and snap to the cadence step.
  return floorToStepMinutesUTC(addMinutesUTC(now, -60), 10);
}

function syncControlsFromCurrent(ds, current) {
  // High-res TiTiler layer is effectively "static" until we wire scene selection.
  if (ds.cadence === "static" || ds.type === "titiler_cog") {
    if (els.timeWrap) els.timeWrap.hidden = true;
    if (els.date) els.date.disabled = true;
    if (els.time) els.time.disabled = true;
    if (els.prev) els.prev.disabled = true;
    if (els.next) els.next.disabled = true;
    if (els.play) els.play.disabled = true;
    return;
  }

  if (els.date) els.date.disabled = false;
  if (els.time) els.time.disabled = false;
  if (els.prev) els.prev.disabled = false;
  if (els.next) els.next.disabled = false;
  if (els.play) els.play.disabled = false;
  if (els.date) els.date.value = formatDateUTC(current);
  if (ds.cadence === "daily") {
    if (els.timeWrap) els.timeWrap.hidden = true;
  } else {
    if (els.timeWrap) els.timeWrap.hidden = false;
    if (els.time) els.time.value = formatTimeHHMMUTC(current);
  }
}

function ensureDatasetSelect() {
  if (!els.dataset) return;
  els.dataset.innerHTML = "";
  for (const [id, ds] of Object.entries(datasets)) {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = ds.label;
    els.dataset.appendChild(opt);
  }
  els.dataset.value = datasetId;
}

ensureDatasetSelect();

let current = currentDefaultTimeForDataset(getDataset());
let playing = false;
let timer = null;

const map = L.map("map", { worldCopyJump: true });

// Illinois-only focus (don’t let the map drift to global view).
const IL_BOUNDS = L.latLngBounds(
  [36.97, -91.52], // SW
  [42.51, -87.0] // NE
);
map.setMaxBounds(IL_BOUNDS);
map.on("drag", () => {
  map.panInsideBounds(IL_BOUNDS, { animate: false });
});

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution:
    '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>',
}).addTo(map);

const ds0 = getDataset();
map.setView(ds0.defaultView.center, ds0.defaultView.zoom);
syncControlsFromCurrent(ds0, current);

let baseLayer = null;
async function setBaseLayerForDataset(ds) {
  if (baseLayer) map.removeLayer(baseLayer);
  if (ds.type === "titiler_cog") {
    setStatus("Loading ECOSTRESS high‑res tiles…");
    baseLayer = await makeTitilerLayer(ds);
    baseLayer.addTo(map);
    setStatus("ECOSTRESS high‑res tiles loaded.");
    return;
  }

  baseLayer = new GibsTimeLayer({
    urlTemplate: config.gibs.urlTemplate,
    layerId: ds.layer,
    tileMatrixSet: ds.tileMatrixSet,
    time: isoTimeForDataset(ds, current),
    maxZoom: ds.maxZoom ?? 7,
    service: ds.service ?? "best",
  });
  baseLayer.addTo(map);
  baseLayer.on("tileerror", (e) => {
    console.warn("GIBS tileerror", { coords: e?.coords ?? null, url: e?.tile?.src ?? null });
  });
}

async function switchToFallback(reason) {
  if (!fallbackDatasetId || fallbackDatasetId === datasetId || !datasets[fallbackDatasetId]) {
    setStatus(reason);
    return;
  }
  const fallback = datasets[fallbackDatasetId];
  setDatasetChoice(fallbackDatasetId);
  current = currentDefaultTimeForDataset(fallback);
  syncControlsFromCurrent(fallback, current);
  map.setView(fallback.defaultView.center, fallback.defaultView.zoom);
  await setBaseLayerForDataset(fallback);
  setStatus(`${reason} Falling back to ${fallback.label}.`);
}

// init base layer
setBaseLayerForDataset(ds0).catch((e) => {
  console.warn("Base layer init failed", e);
  switchToFallback(`High-res layer unavailable (${e?.message ?? e}).`).catch((err) => {
    console.warn("Fallback layer init failed", err);
    setStatus(String(err?.message ?? err));
  });
});

// --- AOI risk overlay (GeoJSON) ---
const riskCfg = config.overlays?.riskAoi ?? null;
let riskLayer = null;
const dcCfg = config.overlays?.dataCenters ?? null;
let dcLayer = null;
const effectCfg = config.overlays?.dcEffect ?? null;
let effectLayer = null;

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

async function loadRiskLayer() {
  if (!riskCfg?.url) return null;
  let res = await fetch(riskCfg.url, { cache: "no-store" });
  // If latest isn't present (common when outputs are gitignored), fall back to a sample file.
  if (!res.ok && String(riskCfg.url).includes("aoi_risk_latest.geojson")) {
    res = await fetch("../data/aoi_risk_sample.geojson", { cache: "no-store" });
  }
  if (!res.ok) throw new Error(`Overlay fetch failed: ${res.status} ${res.statusText}`);
  const gj = await res.json();
  if (!gj?.features?.length) {
    setStatus("AOI risk overlay loaded (0 features). Run analysis export to populate it.");
  }

  const layer = L.geoJSON(gj, {
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
  const res = await fetch(dcCfg.url, { cache: "no-store" });
  if (!res.ok) throw new Error(`Overlay fetch failed: ${res.status} ${res.statusText}`);
  const gj = await res.json();
  const layer = L.geoJSON(gj, {
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
  const res = await fetch(effectCfg.url, { cache: "no-store" });
  if (!res.ok) throw new Error(`Overlay fetch failed: ${res.status} ${res.statusText}`);
  const gj = await res.json();
  if (!gj?.features?.length) {
    setStatus("DC effect overlay loaded (0 features). Run analysis export to populate it.");
  }
  const layer = L.geoJSON(gj, {
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

function ensureLegend() {
  if (!riskCfg) return;
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
}

ensureLegend();

async function syncRiskOverlay() {
  const enabled = Boolean(els.overlayRisk?.checked);
  if (!enabled) {
    if (riskLayer) map.removeLayer(riskLayer);
    return;
  }
  if (!riskCfg?.url) return;
  if (!riskLayer) {
    try {
      setStatus("Loading AOI risk overlay…");
      riskLayer = await loadRiskLayer();
    } catch (e) {
      console.warn("Risk overlay load failed", e);
      setStatus("AOI risk overlay not found (run analysis to generate GeoJSON).");
      return;
    }
  }
  riskLayer.addTo(map);
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
  if (!dcLayer) {
    try {
      setStatus("Loading data center points…");
      dcLayer = await loadDataCentersLayer();
    } catch (e) {
      console.warn("Data center overlay load failed", e);
      setStatus("Data center overlay not found.");
      return;
    }
  }
  dcLayer.addTo(map);
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
  if (!effectLayer) {
    try {
      setStatus("Loading DC effect overlay…");
      effectLayer = await loadEffectLayer();
    } catch (e) {
      console.warn("Effect overlay load failed", e);
      setStatus("DC effect overlay not found (run analysis export).");
      return;
    }
  }
  effectLayer.addTo(map);
}

els.overlayEffect?.addEventListener("change", () => {
  syncEffectOverlay();
});

function stop() {
  playing = false;
  if (els.play) els.play.textContent = "Play";
  if (timer) window.clearInterval(timer);
  timer = null;
}

function updateTime(dateObj, { keepPlaying = true } = {}) {
  current = dateObj;
  const ds = getDataset();
  const iso = isoTimeForDataset(ds, current);
  syncControlsFromCurrent(ds, current);
  if (baseLayer instanceof GibsTimeLayer) {
    baseLayer.setTime(iso);
    setStatus(`Layer: ${ds.layer} • Time: ${iso}`);
  } else {
    setStatus(`Layer: ${ds.label}`);
  }
  if (!keepPlaying) stop();
}

els.dataset?.addEventListener("change", () => {
  datasetId = els.dataset.value;
  const ds = getDataset();
  current = currentDefaultTimeForDataset(ds);
  syncControlsFromCurrent(ds, current);
  setBaseLayerForDataset(ds).catch((e) => {
    console.warn("Base layer switch failed", e);
    if (ds.type === "titiler_cog") {
      switchToFallback(`High-res layer unavailable (${e?.message ?? e}).`).catch((err) => {
        console.warn("Fallback layer switch failed", err);
        setStatus(String(err?.message ?? err));
      });
      return;
    }
    setStatus(String(e?.message ?? e));
  });
  map.setView(ds.defaultView.center, ds.defaultView.zoom);
  updateTime(current, { keepPlaying: false });
});

els.date?.addEventListener("change", () => {
  const d = parseDateInput(els.date.value);
  if (!d) return;
  const ds = getDataset();
  if (ds.cadence === "daily") {
    updateTime(d, { keepPlaying: false });
    return;
  }
  const t = parseTimeInput(els.time?.value);
  const merged = new Date(d.getTime());
  merged.setUTCHours(t?.h ?? current.getUTCHours(), t?.m ?? current.getUTCMinutes(), 0, 0);

  updateTime(floorToStepMinutesUTC(merged, 10), { keepPlaying: false });
});

els.time?.addEventListener("change", () => {
  const ds = getDataset();
  if (ds.cadence === "daily") return;
  const t = parseTimeInput(els.time.value);
  if (!t) return;
  const merged = new Date(current.getTime());
  merged.setUTCHours(t.h, t.m, 0, 0);

  updateTime(floorToStepMinutesUTC(merged, 10), { keepPlaying: false });
});

els.prev?.addEventListener("click", () => {
  const ds = getDataset();
  if (ds.cadence === "daily") updateTime(addDaysUTC(current, -1));
  else updateTime(addMinutesUTC(current, -10));
});

els.next?.addEventListener("click", () => {
  const ds = getDataset();
  if (ds.cadence === "daily") updateTime(addDaysUTC(current, 1));
  else updateTime(addMinutesUTC(current, 10));
});

function start() {
  playing = true;
  if (els.play) els.play.textContent = "Pause";
  timer = window.setInterval(() => {
    const ds = getDataset();
    if (ds.cadence === "daily") updateTime(addDaysUTC(current, 1));
    else updateTime(addMinutesUTC(current, 10));
  }, 700);
}

els.play?.addEventListener("click", () => {
  if (playing) stop();
  else start();
});

// Init
setHelpPanelOpen(true);
updateTime(current, { keepPlaying: false });
syncRiskOverlay();
syncDcOverlay();
syncEffectOverlay();

