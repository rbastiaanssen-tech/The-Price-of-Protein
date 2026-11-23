import time
import duckdb

t0 = time.time()
con = duckdb.connect("protein_atlas.duckdb")

print("=== [TRACK 53A] PRE-CLEANING DIAGNOSTIC AUDIT ===")
tot_rows, tot_mkts = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id) FROM fact_protein_observations;").fetchone()
tot_ctry = con.execute("SELECT COUNT(DISTINCT country_iso3) FROM dim_markets;").fetchone()[0]
print(f"Total Observations: {tot_rows:,} | Markets: {tot_mkts:,} | Countries: {tot_ctry}")

print("\n--- Breakdown by Price Basis ---")
for basis, cnt, med_usd, med_50g in con.execute("""
    SELECT price_basis, COUNT(*),
           ROUND(MEDIAN(real_usd_per_kg), 2),
           ROUND(MEDIAN(usd_per_50g_diaas), 3)
    FROM fact_protein_observations
    GROUP BY 1 ORDER BY 2 DESC;
""").fetchall():
    print(f" {str(basis):22s} | {cnt:10,d} rows | Med $/kg: ${med_usd:6.2f} | Med $/50g DIAAS: ${med_50g:6.3f}")

print("\n--- Breakdown by Commodity Code (Raw Pre-Clean) ---")
for comm, cnt, p05, med, p95 in con.execute("""
    SELECT commodity_code, COUNT(*),
           ROUND(QUANTILE_CONT(usd_per_50g_diaas, 0.05), 3),
           ROUND(MEDIAN(usd_per_50g_diaas), 3),
           ROUND(QUANTILE_CONT(usd_per_50g_diaas, 0.95), 3)
    FROM fact_protein_observations
    GROUP BY 1 ORDER BY 2 DESC;
""").fetchall():
    print(f" {str(comm):20s} | {cnt:9,d} rows | P05: ${p05:5.3f} | Med: ${med:5.3f} | P95: ${p95:6.3f}")

# Step 1: Enforce canonical DIAAS conversion factors & physical plausibility bounds
# Effective digestible protein (g per kg of edible product) = (Protein_g_per_kg * DIAAS_cap_1.0)
print("\n=== [TRACK 53B] ENFORCING CANONICAL DIAAS & PHYSICAL BOUNDS ===")
con.execute("""
CREATE OR REPLACE TEMP TABLE diaas_ref (
    commodity_code VARCHAR,
    protein_g_per_kg DOUBLE,
    diaas_score DOUBLE,
    eff_diaas_g_per_kg DOUBLE,
    min_valid_usd_kg DOUBLE,
    max_valid_usd_kg DOUBLE
);
INSERT INTO diaas_ref VALUES
    ('EGGS_CHIX',          124.0, 1.00, 124.0, 0.45, 22.0),
    ('MILK_FLUID',          33.0, 1.00,  33.0, 0.20,  6.5),
    ('CHEESE_HARD',        245.0, 1.00, 245.0, 2.00, 55.0),
    ('POULTRY_CHIX',       195.0, 1.00, 195.0, 0.70, 32.0),
    ('BEEF_FRESH',         215.0, 1.00, 215.0, 1.20, 65.0),
    ('PORK_FRESH',         190.0, 1.00, 190.0, 0.90, 42.0),
    ('FISH_PELAGIC_SMALL', 180.0, 1.00, 180.0, 0.60, 38.0),
    ('FISH_FRESHWATER',    175.0, 1.00, 175.0, 0.60, 35.0),
    ('LENTILS_DRY',        246.0, 0.60, 147.6, 0.30, 18.0),
    ('BEANS_DRY',          230.0, 0.60, 138.0, 0.30, 18.0),
    ('CHICKPEAS_DRY',      205.0, 0.65, 133.3, 0.30, 18.0),
    ('SOY_TOFU',           120.0, 0.90, 108.0, 0.40, 22.0);
""")

# Re-standardize usd_per_50g_diaas where commodity matches canonical table so all 4.82M rows share identical nutritional math
con.execute("""
UPDATE fact_protein_observations AS f
SET usd_per_50g_diaas = ROUND((f.real_usd_per_kg / r.eff_diaas_g_per_kg) * 50.0, 4)
FROM diaas_ref AS r
WHERE f.commodity_code = r.commodity_code
  AND f.real_usd_per_kg > 0;
""")

# Flag physical bound violations + group-stratified MAD outliers (Modified Z > 3.5 within country x commodity x price_basis)
con.execute("""
CREATE OR REPLACE TEMP TABLE outlier_flags AS
WITH joined AS (
    SELECT
        f.obs_id,
        m.country_iso3,
        f.commodity_code,
        f.price_basis,
        f.real_usd_per_kg,
        r.min_valid_usd_kg,
        r.max_valid_usd_kg
    FROM fact_protein_observations f
    JOIN dim_markets m ON f.market_id = m.market_id
    LEFT JOIN diaas_ref r ON f.commodity_code = r.commodity_code
),
group_med AS (
    SELECT
        country_iso3, commodity_code, price_basis,
        MEDIAN(real_usd_per_kg) AS med_val,
        COUNT(*) AS n_grp
    FROM joined
    WHERE real_usd_per_kg IS NOT NULL
      AND real_usd_per_kg > 0
      AND (min_valid_usd_kg IS NULL OR real_usd_per_kg BETWEEN min_valid_usd_kg AND max_valid_usd_kg)
    GROUP BY 1, 2, 3
),
group_mad AS (
    SELECT
        j.country_iso3, j.commodity_code, j.price_basis,
        g.med_val,
        g.n_grp,
        MEDIAN(ABS(j.real_usd_per_kg - g.med_val)) AS mad_val
    FROM joined j
    JOIN group_med g USING (country_iso3, commodity_code, price_basis)
    WHERE j.real_usd_per_kg BETWEEN COALESCE(j.min_valid_usd_kg, 0.05) AND COALESCE(j.max_valid_usd_kg, 200.0)
    GROUP BY 1, 2, 3, 4, 5
)
SELECT
    j.obs_id,
    CASE
        WHEN j.real_usd_per_kg IS NULL OR j.real_usd_per_kg <= 0 THEN TRUE
        WHEN j.min_valid_usd_kg IS NOT NULL AND (j.real_usd_per_kg < j.min_valid_usd_kg OR j.real_usd_per_kg > j.max_valid_usd_kg) THEN TRUE
        WHEN gm.n_grp >= 8 AND gm.mad_val > 0 AND (0.6745 * ABS(j.real_usd_per_kg - gm.med_val) / gm.mad_val) > 3.5 THEN TRUE
        ELSE FALSE
    END AS new_flag
FROM joined j
LEFT JOIN group_mad gm USING (country_iso3, commodity_code, price_basis);

UPDATE fact_protein_observations AS f
SET is_outlier_mad = o.new_flag
FROM outlier_flags AS o
WHERE f.obs_id = o.obs_id;
""")
print("\n=== [TRACK 53C] BUILDING CLEAN ANALYTICAL VIEWS & COUNTRY MARTS ===")

# 1. Clean analytical view (excludes MAD/physical outliers and non-positive values)
con.execute("""
CREATE OR REPLACE VIEW v_clean_protein_observations AS
SELECT
    f.obs_id,
    f.source_dataset,
    f.price_basis,
    f.market_id,
    m.market_name,
    m.country_iso3,
    m.lat,
    m.lon,
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
""")

# 2. Country-level parity & least-cost protein mart (separating Retail vs Market/Wholesale)
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
),
ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY country_iso3, price_basis ORDER BY med_usd_50g_diaas ASC) AS affordability_rank
    FROM country_comm
    WHERE n_obs >= 3
)
SELECT * FROM ranked;
""")

clean_cnt, flagged_cnt = con.execute("""
    SELECT
        SUM(CASE WHEN is_outlier_mad = FALSE THEN 1 ELSE 0 END),
        SUM(CASE WHEN is_outlier_mad = TRUE THEN 1 ELSE 0 END)
    FROM fact_protein_observations;
""").fetchone()

print(f"Clean Valid Observations : {clean_cnt:,} ({clean_cnt/(clean_cnt+flagged_cnt)*100:.2f}%)")
print(f"Flagged Outliers (MAD/Phys): {flagged_cnt:,} ({flagged_cnt/(clean_cnt+flagged_cnt)*100:.2f}%)")

print("\n=== POST-CLEAN GLOBAL COMMODITY BENCHMARKS (CLEAN OBSERVATIONS) ===")
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

print(f"\n[TRACK 53 COMPLETE] Methodology standardized & data cleaned in {time.time()-t0:.1f}s")
con.close()
