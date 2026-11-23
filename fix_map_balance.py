from __future__ import annotations
import json
from pathlib import Path
import duckdb, numpy as np, pandas as pd

con = duckdb.connect("protein_atlas.duckdb", read_only=True)

# 1. Quick audit of industrial / G20 coverage in 2020-2026
g20_check = con.execute("""
    SELECT m.region_macro, COUNT(DISTINCT m.country_iso3) AS countries, COUNT(*) AS obs_2020_26
    FROM fact_protein_observations f
    JOIN dim_markets m ON f.market_id = m.market_id
    WHERE f.obs_year >= 2020 AND f.price_basis = 'OBSERVED_MARKET' AND f.is_outlier_mad = FALSE
    GROUP BY 1 ORDER BY 3 DESC;
""").df()
print("=== 2020–2026 OBSERVED MARKET COVERAGE BY REGION ===")
print(g20_check.to_string(index=False))

# 2. Build balanced spatiotemporal_markets.json:
#    - Historical decades (1700..2010): exact market-decade medians
#    - Modern slice (2026): pools 2020-2026 so OECD/FAOSTAT/IMF/BLS (2020-2025) & WFP (2020-2026) appear together
#    - Spatial declustering: countries with >15 sub-national markets aggregate onto 1.5-deg cells
#    - Ethnographic layer: all 390 non-agrarian societies preserved (0 dropped)
df_mkt = con.execute("""
    WITH tagged AS (
        SELECT
            mk.market_id,
            mk.market_name,
            mk.country_iso3,
            mk.region_macro,
            mk.latitude AS lat,
            mk.longitude AS lon,
            COALESCE(mk.arable_ha_per_cap, 0.15) AS arable_ha,
            CASE WHEN f.obs_year >= 2020 THEN 2026 ELSE f.obs_decade END AS decade,
            c.regime_class AS regime,
            f.usd_per_50g_diaas,
            f.labor_min_50g_diaas
        FROM fact_protein_observations f
        JOIN dim_markets mk ON f.market_id = mk.market_id
        JOIN dim_commodities c ON f.commodity_code = c.commodity_code
        WHERE f.price_basis = 'OBSERVED_MARKET'
          AND f.is_outlier_mad = FALSE
          AND f.obs_year >= 1700
          AND (mk.latitude <> 0 OR mk.longitude <> 0)
    ),
    ctry_counts AS (
        SELECT country_iso3, decade, COUNT(DISTINCT market_id) AS n_mkts
        FROM tagged GROUP BY 1, 2
    ),
    grouped AS (
        SELECT
            CASE WHEN cc.n_mkts > 15
                 THEN t.country_iso3 || '_GRID_' || CAST(ROUND(t.lat / 1.5) * 1.5 AS VARCHAR) || '_' || CAST(ROUND(t.lon / 1.5) * 1.5 AS VARCHAR)
                 ELSE t.market_id END AS market_id,
            CASE WHEN MAX(cc.n_mkts) > 15
                 THEN t.country_iso3 || ' Regional Cluster (' || CAST(COUNT(DISTINCT t.market_id) AS VARCHAR) || ' mkts)'
                 ELSE MAX(t.market_name) END AS market_name,
            t.country_iso3,
            ROUND(MEDIAN(t.lat), 3) AS lat,
            ROUND(MEDIAN(t.lon), 3) AS lon,
            ROUND(MEDIAN(t.arable_ha), 4) AS arable_ha,
            t.decade,
            t.regime,
            ROUND(MEDIAN(t.usd_per_50g_diaas), 3) AS usd_50g,
            ROUND(MEDIAN(t.labor_min_50g_diaas), 2) AS labor_min,
            COUNT(*) AS n_series
        FROM tagged t
        JOIN ctry_counts cc ON t.country_iso3 = cc.country_iso3 AND t.decade = cc.decade
        GROUP BY 1, 3, 7, 8
    )
    SELECT * FROM grouped
    UNION ALL
    SELECT
        mk.market_id,
        mk.market_name,
        mk.country_iso3,
        ROUND(mk.latitude, 3) AS lat,
        ROUND(mk.longitude, 3) AS lon,
        ROUND(COALESCE(mk.arable_ha_per_cap, 0.15), 4) AS arable_ha,
        f.obs_decade AS decade,
        c.regime_class AS regime,
        NULL AS usd_50g,
        ROUND(MEDIAN(f.labor_min_50g_diaas), 2) AS labor_min,
        COUNT(*) AS n_series
    FROM fact_protein_observations f
    JOIN dim_markets mk ON f.market_id = mk.market_id
    JOIN dim_commodities c ON f.commodity_code = c.commodity_code
    WHERE f.price_basis = 'ETHNOGRAPHIC_TIME_ALLOCATION'
      AND (mk.latitude <> 0 OR mk.longitude <> 0)
    GROUP BY 1, 2, 3, 4, 5, 6, 7, 8;
""").df()

payload = {
    "count": int(len(df_mkt)),
    "columns": {
        "market_id": df_mkt["market_id"].tolist(),
        "market_name": df_mkt["market_name"].tolist(),
        "country_iso3": df_mkt["country_iso3"].tolist(),
        "lat": df_mkt["lat"].tolist(),
        "lon": df_mkt["lon"].tolist(),
        "arable_ha": df_mkt["arable_ha"].tolist(),
        "decade": df_mkt["decade"].astype(int).tolist(),
        "regime": df_mkt["regime"].tolist(),
        "usd_50g": [None if (v is None or pd.isna(v) or v <= 0) else float(v) for v in df_mkt["usd_50g"]],
        "labor_min": [float(v) for v in df_mkt["labor_min"]],
        "n_series": df_mkt["n_series"].astype(int).tolist(),
    },
}

out_file = Path("public/data/spatiotemporal_markets.json")
out_file.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8")

m26 = df_mkt[df_mkt["decade"] == 2026]
eth_n = sum(1 for m in df_mkt["market_id"] if str(m).startswith("ETH_"))
print(f"\n[EXPORTED] {out_file} ({out_file.stat().st_size / 1024:.1f} KB)")
print(f"Total map nodes: {len(df_mkt):,} | 2026 Modern countries covered: {m26['country_iso3'].nunique()} | Ethnographic nodes: {eth_n}")
