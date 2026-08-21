export default {
  // Optional: TiTiler base URL (Render). Used for high-resolution ECOSTRESS tiles.
  // If you deploy the backend service name as "chicago-lst-tiles", this URL matches by default.
  titilerBaseUrl: "https://chicago-lst-tiles.onrender.com",

  aoiUrl: "../data/chicago_dc_aoi.json",
  coverageUrl: "../data/coverage_latest.json",

  gibs: {
    datasets: {
      ecostress_il_highres: {
        label: "Chicago high-res • ECOSTRESS LST (70m)",
        cadence: "static",
        type: "titiler_cog",
        cogMetaUrl: "../data/ecostress_highres_latest.json",
        defaultView: { center: [41.8781, -87.6298], zoom: 10 },
      },
    },
    defaultDatasetId: "ecostress_il_highres",
  },

  overlays: {
    riskAoi: {
      label: "AOI risk",
      url: "../data/aoi_risk_latest.geojson",
      field: "risk_score",
    },
    dataCenters: {
      label: "Data centers",
      url: "../data/chicago_data_centers_183.geojson",
    },
    dcEffect: {
      label: "DC effect (cumulative • 500m)",
      url: "../data/dc_effect_cumulative.geojson",
      field: "delta_mean_c",
    },
  },
};
