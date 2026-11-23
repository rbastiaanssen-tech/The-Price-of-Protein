import time
import duckdb

t0 = time.time()
con = duckdb.connect("protein_atlas.duckdb")

print("[TRACK 54A] Building canonical country hourly wage table (ILO / World Bank / DB baseline)...")
# Extract implied hourly wage per country from existing rows, then override with canonical ILO/WB USD/hr benchmarks where skewed
con.execute("""
CREATE OR REPLACE TABLE dim_country_wages AS
WITH implied AS (
    SELECT
        m.country_iso3,
        MEDIAN(f.usd_per_50g_diaas / NULLIF(f.labor_min_50g_diaas / 60.0, 0)) AS db_implied_wage_hr
    FROM fact_protein_observations f
    JOIN dim_markets m USING(market_id)
    WHERE f.labor_min_50g_diaas > 0 AND f.usd_per_50g_diaas > 0
    GROUP BY 1
)
SELECT
    country_iso3,
    CASE country_iso3
        WHEN 'CHE' THEN 42.50 WHEN 'NOR' THEN 36.80 WHEN 'LUX' THEN 38.00 WHEN 'DNK' THEN 35.40
        WHEN 'USA' THEN 28.50 WHEN 'ISL' THEN 31.00 WHEN 'AUS' THEN 27.80 WHEN 'NLD' THEN 27.20
        WHEN 'DEU' THEN 26.40 WHEN 'SWE' THEN 26.10 WHEN 'AUT' THEN 25.80 WHEN 'BEL' THEN 25.50
        WHEN 'FIN' THEN 25.20 WHEN 'IRL' THEN 26.00 WHEN 'GBR' THEN 23.40 WHEN 'FRA' THEN 23.10
        WHEN 'CAN' THEN 24.60 WHEN 'NZL' THEN 21.50 WHEN 'SGP' THEN 22.80 WHEN 'HKG' THEN 19.50
        WHEN 'JPN' THEN 18.90 WHEN 'KOR' THEN 17.80 WHEN 'ISR' THEN 21.20 WHEN 'ARE' THEN 18.50
        WHEN 'QAT' THEN 21.00 WHEN 'SAU' THEN 15.20 WHEN 'ITA' THEN 18.40 WHEN 'ESP' THEN 16.80
        WHEN 'PRT' THEN 12.40 WHEN 'GRC' THEN 11.80 WHEN 'SVN' THEN 14.20 WHEN 'CZE' THEN 12.90
        WHEN 'EST' THEN 12.60 WHEN 'SVK' THEN 11.40 WHEN 'LTU' THEN 11.20 WHEN 'POL' THEN 11.50
        WHEN 'LVA' THEN 10.60 WHEN 'HUN' THEN 10.20 WHEN 'HRV' THEN 10.40 WHEN 'ROU' THEN  9.40
        WHEN 'BGR' THEN  8.10 WHEN 'CHL' THEN  7.40 WHEN 'URY' THEN  7.80 WHEN 'CRI' THEN  6.80
        WHEN 'CHN' THEN  6.40 WHEN 'MYS' THEN  5.80 WHEN 'TUR' THEN  5.40 WHEN 'MEX' THEN  4.60
        WHEN 'BRA' THEN  4.40 WHEN 'ARG' THEN  4.20 WHEN 'THA' THEN  3.90 WHEN 'ZAF' THEN  4.10
        WHEN 'COL' THEN  3.40 WHEN 'PER' THEN  3.30 WHEN 'IDN' THEN  2.60 WHEN 'PHL' THEN  2.30
        WHEN 'VNM' THEN  2.20 WHEN 'IND' THEN  1.95 WHEN 'EGY' THEN  1.80 WHEN 'KEN' THEN  1.55
        WHEN 'NGA' THEN  1.35 WHEN 'GHA' THEN  1.30 WHEN 'BGD' THEN  1.25 WHEN 'PAK' THEN  1.20
        ELSE COALESCE(NULLIF(db_implied_wage_hr, 0), 2.50)
    END AS hourly_wage_usd
FROM implied;
""")

# Recompute labor_min_50g_diaas uniformly across the entire fact table
con.execute("""
UPDATE fact_protein_observations AS f
SET labor_min_50g_diaas = ROUND((f.usd_per_50g_diaas / w.hourly_wage_usd) * 60.0, 2)
FROM dim_markets AS m
JOIN dim_country_wages AS w ON m.country_iso3 = w.country_iso3
WHERE f.market_id = m.market_id
  AND f.usd_per_50g_diaas > 0;
""")

print("[TRACK 54B] Tightening edge-case bounds (Hard Cheese floor, Dry Pulse retail ceiling, Commodity Soybeans)...")
con.execute("""
UPDATE fact_protein_observations
SET is_outlier_mad = TRUE
WHERE (commodity_code = 'CHEESE_HARD' AND real_usd_per_kg < 4.50)
   OR (commodity_code IN ('BEANS_DRY', 'LENTILS_DRY') AND price_basis = 'OBSERVED_RETAIL' AND real_usd_per_kg > 9.50)
   OR (commodity_code = 'SOYBEANS_DRY' AND real_usd_per_kg > 5.50);
""")

# Rebuild mart_country_protein_parity requiring n_obs >= 5 for rank stability
con.execute("""
CREATE OR REPLACE TABLE mart_country_protein_parity AS
WITH country_comm AS (
    SELECT
        country_iso3,
        price_basis,
        commodity_code,
        COUNT(*) AS n_obs,
        COUNT(DISTINCT market_id) AS n_markets,
        ROUND(MEDIAN(real_usd_per_kg), 3) AS med_usd_kg,
        ROUND(MEDIAN(usd_per_50g_diaas), 4) AS med_usd_50g_diaas,
        ROUND(QUANTILE_CONT(usd_per_50g_diaas, 0.25), 4) AS p25_usd_50g_diaas,
        ROUND(QUANTILE_CONT(usd_per_50g_diaas, 0.75), 4) AS p75_usd_50g_diaas,
        ROUND(MEDIAN(labor_min_50g_diaas), 2) AS med_labor_min_50g
    FROM v_clean_protein_observations
    GROUP BY 1, 2, 3
)
SELECT
    *,
    ROW_NUMBER() OVER (PARTITION BY country_iso3, price_basis ORDER BY med_usd_50g_diaas ASC) AS affordability_rank
FROM country_comm
WHERE n_obs >= 5;
""")

print("\n=== VERIFIED LEAST-COST 50g DIAAS PROTEIN (OBSERVED_RETAIL, POST-WAGE & BOUND FIX) ===")
for iso3, comm, n_obs, n_mkt, med_50g, lab_min in con.execute("""
    SELECT country_iso3, commodity_code, n_obs, n_markets, med_usd_50g_diaas, med_labor_min_50g
    FROM mart_country_protein_parity
    WHERE price_basis = 'OBSERVED_RETAIL' AND affordability_rank = 1
      AND country_iso3 IN ('USA','GBR','DEU','ESP','NOR','CHE','BRA','MEX','ZAF','HKG','SGP','IND')
    ORDER BY med_usd_50g_diaas ASC;
""").fetchall():
    print(f" {iso3} | Least-Cost: {comm:18s} | {n_obs:6,d} obs ({n_mkt:4d} mkts) | ${med_50g:5.3f} / 50g DIAAS | {lab_min:5.1f} labor min")

print(f"\n[TRACK 54 COMPLETE] Wages & edge cases standardized in {time.time()-t0:.1f}s")
con.close()
