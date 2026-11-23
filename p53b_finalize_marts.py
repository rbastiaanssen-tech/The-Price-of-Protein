import time
import duckdb

t0 = time.time()
con = duckdb.connect("protein_atlas.duckdb")

# Expand canonical DIAAS & physical bounds across ALL 28 commodities in the database
con.execute("""
CREATE OR REPLACE TEMP TABLE diaas_full (
    commodity_code VARCHAR,
    protein_g_per_kg DOUBLE,
    diaas_score DOUBLE,
    eff_diaas_g_per_kg DOUBLE,
    min_valid_usd_kg DOUBLE,
    max_valid_usd_kg DOUBLE
);
INSERT INTO diaas_full VALUES
    ('EGGS_CHIX',          124.0, 1.00, 124.0, 0.45, 22.0),
    ('MILK_FLUID',          33.0, 1.00,  33.0, 0.20,  6.5),
    ('CHEESE_HARD',        245.0, 1.00, 245.0, 2.00, 55.0),
    ('POULTRY_CHIX',       195.0, 1.00, 195.0, 0.70, 32.0),
    ('BEEF_FRESH',         215.0, 1.00, 215.0, 1.20, 65.0),
    ('PORK_FRESH',         190.0, 1.00, 190.0, 0.90, 42.0),
    ('MUTTON_FRESH',       200.0, 1.00, 200.0, 1.00, 55.0),
    ('FISH_PELAGIC_SMALL', 180.0, 1.00, 180.0, 0.60, 38.0),
    ('FISH_PREDATOR_HIGH', 200.0, 1.00, 200.0, 1.00, 60.0),
    ('FISH_AQUACULTURE',   180.0, 1.00, 180.0, 0.70, 35.0),
    ('COD_SALTED',         380.0, 1.00, 380.0, 1.50, 55.0),
    ('LENTILS_DRY',        246.0, 0.60, 147.6, 0.30, 18.0),
    ('BEANS_DRY',          230.0, 0.60, 138.0, 0.30, 18.0),
    ('SOYBEANS_DRY',       365.0, 0.90, 328.5, 0.30, 16.0),
    ('GROUNDNUTS',         258.0, 0.46, 118.7, 0.40, 22.0),
    ('TOFU_TEMPEH',        130.0, 0.90, 117.0, 0.60, 28.0),
    ('RICE_MILLED',         71.0, 0.59,  41.9, 0.20,  8.0),
    ('WHEAT_FLOUR',        103.0, 0.45,  46.4, 0.15,  7.0),
    ('WHEY_ISOLATE',       850.0, 1.00, 850.0, 5.00, 95.0),
    ('PEA_ISOLATE',        800.0, 0.82, 656.0, 4.00, 75.0),
    ('SOY_ISOLATE',        850.0, 0.91, 773.5, 3.00, 65.0),
    ('TVP',                500.0, 0.90, 450.0, 1.50, 35.0);
""")

con.execute("""
UPDATE fact_protein_observations AS f
SET usd_per_50g_diaas = ROUND((f.real_usd_per_kg / r.eff_diaas_g_per_kg) * 50.0, 4)
FROM diaas_full AS r
WHERE f.commodity_code = r.commodity_code
  AND f.real_usd_per_kg > 0;

UPDATE fact_protein_observations AS f
SET is_outlier_mad = TRUE
FROM diaas_full AS r
WHERE f.commodity_code = r.commodity_code
  AND (f.real_usd_per_kg < r.min_valid_usd_kg OR f.real_usd_per_kg > r.max_valid_usd_kg);
""")

con.execute("""
CREATE OR REPLACE VIEW v_clean_protein_observations AS
SELECT
    f.obs_id,
    f.source_dataset,
    f.price_basis,
    f.market_id,
    m.market_name,
    m.country_iso3,
    m.latitude,
    m.longitude,
    m.arable_ha_per_cap,
    f.commodity_code,
    f.obs_date,
    f.nominal_price_kg,
    f.currency_code,
    f.real_usd_per_kg,
    f.usd_per_50g_diaas,
    f.labor_min_50g_diaas
FROM fact_protein_observations f
JOIN dim_markets m ON f.market_id = m.market_id
WHERE f.is_outlier_mad = FALSE
  AND f.real_usd_per_kg > 0
  AND f.usd_per_50g_diaas > 0;

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
WHERE n_obs >= 3;
""")

clean_cnt, flagged_cnt = con.execute("""
    SELECT
        SUM(CASE WHEN is_outlier_mad = FALSE THEN 1 ELSE 0 END),
        SUM(CASE WHEN is_outlier_mad = TRUE THEN 1 ELSE 0 END)
    FROM fact_protein_observations;
""").fetchone()

print(f"Clean Valid Observations   : {clean_cnt:,} ({clean_cnt/(clean_cnt+flagged_cnt)*100:.2f}%)")
print(f"Flagged Outliers (MAD/Phys): {flagged_cnt:,} ({flagged_cnt/(clean_cnt+flagged_cnt)*100:.2f}%)")

print("\n=== POST-CLEAN GLOBAL COMMODITY BENCHMARKS (ALL CLEAN OBSERVATIONS) ===")
for comm, cnt, med_kg, med_50g, iqr_50g in con.execute("""
    SELECT commodity_code, COUNT(*),
           ROUND(MEDIAN(real_usd_per_kg), 2),
           ROUND(MEDIAN(usd_per_50g_diaas), 3),
           ROUND(QUANTILE_CONT(usd_per_50g_diaas, 0.75) - QUANTILE_CONT(usd_per_50g_diaas, 0.25), 3)
    FROM v_clean_protein_observations
    GROUP BY 1 ORDER BY 4 ASC;
""").fetchall():
    print(f" {str(comm):20s} | {cnt:9,d} clean rows | Med $/kg: ${med_kg:6.2f} | Med $/50g DIAAS: ${med_50g:5.3f} (IQR: ${iqr_50g:5.3f})")

print("\n=== SAMPLE COUNTRY PARITY: CHEAPEST 50g DIAAS PROTEIN (OBSERVED_RETAIL) ===")
for iso3, comm, n_obs, n_mkt, med_50g, lab_min in con.execute("""
    SELECT country_iso3, commodity_code, n_obs, n_markets, med_usd_50g_diaas, med_labor_min_50g
    FROM mart_country_protein_parity
    WHERE price_basis = 'OBSERVED_RETAIL' AND affordability_rank = 1
      AND country_iso3 IN ('USA','GBR','DEU','ESP','NOR','CHE','BRA','MEX','ZAF','HKG','SGP','IND')
    ORDER BY med_usd_50g_diaas ASC;
""").fetchall():
    print(f" {iso3} | Least-Cost: {comm:16s} | {n_obs:6,d} obs ({n_mkt:4d} mkts) | ${med_50g:5.3f} / 50g DIAAS | {lab_min:5.1f} labor min")

print(f"\n[TRACK 53B COMPLETE] Full 28-commodity DIAAS standardization & marts built in {time.time()-t0:.1f}s")
con.close()
