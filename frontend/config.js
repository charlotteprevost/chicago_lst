export default {
  // Optional: TiTiler base URL (Render). Used for high-resolution ECOSTRESS tiles.
  // If you deploy the backend service name as "chicago-lst-tiles", this URL matches by default.
  titilerBaseUrl: "https://chicago-lst-tiles.onrender.com",

  gibs: {
    // NASA GIBS WMTS REST template.
    // Docs: https://nasa-gibs.github.io/gibs-api-docs/
    urlTemplate:
      "https://gibs.earthdata.nasa.gov/wmts/epsg3857/{service}/{layer}/default/{time}/{tileMatrixSet}/{z}/{y}/{x}.png",

    // "Best of both worlds":
    // - Global daily night LST (always available)
    // - Regional high-cadence geostationary IR (10-min) as an hourly-ish thermal proxy
    //
    // NOTE: TileMatrixSet can vary by layer; these defaults work for many epsg3857 layers.
    datasets: {
      ecostress_il_highres: {
        label: "Illinois high‑res • ECOSTRESS LST (70m)",
        cadence: "static",
        type: "titiler_cog",
        // Loads a JSON containing a COG URL; see data/ecostress_highres_latest.json
        cogMetaUrl: "../data/ecostress_highres_latest.json",
        // Chicago (initial view)
        defaultView: { center: [41.8781, -87.6298], zoom: 10 },
      },
    },

    defaultDatasetId: "ecostress_il_highres",
  },

  overlays: {
    riskAoi: {
      label: "AOI risk",
      // Expected output from analysis/03_export_geojson.py
      url: "../data/aoi_risk_latest.geojson",
      field: "risk_score",
    },
    dataCenters: {
      label: "Data centers",
      url: "../data/chicago_data_centers_183.geojson",
    },
    dcEffect: {
      label: "DC effect (cumulative • 500m)",
      // Expected output from analysis/06_export_dc_effect_geojson.py
      url: "../data/dc_effect_cumulative.geojson",
      field: "delta_mean_c",
    },
  },
};

