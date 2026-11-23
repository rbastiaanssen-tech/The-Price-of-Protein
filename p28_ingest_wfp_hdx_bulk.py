import duckdb, time
from pathlib import Path

t0 = time.time()
raw_dir = Path("data_raw/wfp_hdx")
con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'WFP_HDX_BULK_28';")

con.execute("""
    CREATE OR REPLACE TEMP TABLE country_rates AS
    SELECT m.country_iso3,
           MEDIAN(f.usd_per_50g_diaas / NULLIF(f.labor_min_50g_diaas / 60.0, 0)) AS wage_hr_usd
    FROM fact_protein_observations f
    JOIN dim_markets m USING(market_id)
    WHERE f.is_outlier_mad = FALSE AND f.real_usd_per_kg > 0 AND f.labor_min_50g_diaas > 0
    GROUP BY 1;
""")

EFF_G_SQL = """
    CASE commodity_code
        WHEN 'EGGS_CHIX' THEN 124.0 WHEN 'MILK_FLUID' THEN 33.0 WHEN 'CHEESE_HARD' THEN 245.0
        WHEN 'POULTRY_CHIX' THEN 195.0 WHEN 'BEEF_FRESH' THEN 215.0 WHEN 'PORK_FRESH' THEN 190.0
        WHEN 'MUTTON_FRESH' THEN 195.0 WHEN 'FISH_PELAGIC_SMALL' THEN 180.0
        WHEN 'FISH_AQUACULTURE' THEN 146.0 WHEN 'FISH_PREDATOR_HIGH' THEN 176.0
        WHEN 'LENTILS_DRY' THEN 148.0 WHEN 'BEANS_DRY' THEN 138.0
        WHEN 'WHEAT_FLOUR' THEN 95.0 WHEN 'RICE_MILLED' THEN 68.0
        ELSE 130.0
    END
"""

f26 = raw_dir / "wfp_2026.csv"
if f26.exists():
    print("[TRACK 28A] Ingesting wfp_2026.csv...")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE staged_w26 AS
        WITH raw_w AS (
            SELECT
                UPPER(TRIM(CAST(countryiso3 AS VARCHAR))) AS iso3,
                TRIM(CAST(admin1 AS VARCHAR)) AS admin1,
                TRIM(CAST(market AS VARCHAR)) AS market,
                TRY_CAST(latitude AS DOUBLE) AS lat,
                TRY_CAST(longitude AS DOUBLE) AS lon,
                LOWER(TRIM(CAST(commodity AS VARCHAR))) AS comm,
                LOWER(TRIM(CAST(unit AS VARCHAR))) AS unit,
                CAST(currency AS VARCHAR) AS cur,
                TRY_CAST(price AS DOUBLE) AS price,
                TRY_CAST(usdprice AS DOUBLE) AS usdprice,
                CAST(date AS VARCHAR) AS obs_date
            FROM read_csv_auto('{f26}', ignore_errors=true)
            WHERE TRY_CAST(usdprice AS DOUBLE) > 0.05
              AND LOWER(CAST(pricetype AS VARCHAR)) IN ('retail', 'wholesale')
        ),
        mapped AS (
            SELECT *,
                CASE
                    WHEN REGEXP_MATCHES(comm, '(egg)') THEN 'EGGS_CHIX'
                    WHEN REGEXP_MATCHES(comm, '(milk|yogurt|curd)') THEN 'MILK_FLUID'
                    WHEN REGEXP_MATCHES(comm, '(cheese|paneer)') THEN 'CHEESE_HARD'
                    WHEN REGEXP_MATCHES(comm, '(chicken|poultry)') THEN 'POULTRY_CHIX'
                    WHEN REGEXP_MATCHES(comm, '(beef|cattle|veal)') THEN 'BEEF_FRESH'
                    WHEN REGEXP_MATCHES(comm, '(pork|pig)') THEN 'PORK_FRESH'
                    WHEN REGEXP_MATCHES(comm, '(mutton|goat|sheep|lamb)') THEN 'MUTTON_FRESH'
                    WHEN REGEXP_MATCHES(comm, '(sardine|anchov|herring|sprat|mackerel|fish \\(dry|fish \\(smoked)') THEN 'FISH_PELAGIC_SMALL'
                    WHEN REGEXP_MATCHES(comm, '(tilapia|carp|catfish|pangasius|fish \\(fresh)') THEN 'FISH_AQUACULTURE'
                    WHEN REGEXP_MATCHES(comm, '(tuna|salmon|cod)') THEN 'FISH_PREDATOR_HIGH'
                    WHEN REGEXP_MATCHES(comm, '(lentil|masoor|moong|urad|dal|dhal)') THEN 'LENTILS_DRY'
                    WHEN REGEXP_MATCHES(comm, '(bean|cowpea|chickpea|pea|groundnut|peanut|pulse)') THEN 'BEANS_DRY'
                    WHEN REGEXP_MATCHES(comm, '(wheat|flour|bread)') THEN 'WHEAT_FLOUR'
                    WHEN REGEXP_MATCHES(comm, '(rice)') THEN 'RICE_MILLED'
                    ELSE NULL
                END AS commodity_code,
                CASE
                    WHEN unit = 'kg' OR unit = 'l' THEN 1.0
                    WHEN REGEXP_MATCHES(unit, '^[0-9]+\\s*(g|ml)$') THEN TRY_CAST(REGEXP_EXTRACT(unit, '^([0-9]+)', 1) AS DOUBLE) / 1000.0
                    WHEN REGEXP_MATCHES(unit, '^[0-9]+\\s*(kg|l)$') THEN TRY_CAST(REGEXP_EXTRACT(unit, '^([0-9]+)', 1) AS DOUBLE)
                    WHEN unit LIKE '%10 pcs%' OR unit LIKE '%dozen%' OR unit LIKE '%12 pcs%' THEN 0.60
                    WHEN unit = 'unit' OR unit = 'piece' THEN 0.055
                    ELSE 1.0
                END AS kg_factor
            FROM raw_w
        )
        SELECT iso3, admin1, market, lat, lon, commodity_code, obs_date, cur,
               price / GREATEST(0.04, kg_factor) AS nominal_price_kg,
               usdprice / GREATEST(0.04, kg_factor) AS real_usd_per_kg
        FROM mapped
        WHERE commodity_code IN (SELECT commodity_code FROM dim_commodities)
          AND lat BETWEEN -60 AND 75 AND lon BETWEEN -180 AND 180;
    """)

    con.execute("""
        INSERT OR IGNORE INTO dim_markets
        SELECT DISTINCT
            'WFP28_' || iso3 || '_' || REGEXP_REPLACE(UPPER(market), '[^A-Z0-9]', '', 'g') AS market_id,
            market, iso3, COALESCE(NULLIF(admin1, ''), 'WFP_ADMIN1'), ROUND(lat, 4), ROUND(lon, 4), 0.20, 0.20
        FROM staged_w26 WHERE LENGTH(iso3) = 3;
    """)

    max_id = con.execute("SELECT COALESCE(MAX(obs_id), 0) FROM fact_protein_observations;").fetchone()[0]
    con.execute(f"""
        INSERT INTO fact_protein_observations (
            obs_id, source_dataset, price_basis, market_id, commodity_code, obs_date,
            nominal_price_kg, currency_code, real_usd_per_kg, usd_per_50g_diaas, labor_min_50g_diaas, is_outlier_mad
        )
        SELECT
            {max_id} + ROW_NUMBER() OVER (),
            'WFP_HDX_BULK_28', 'OBSERVED_MARKET',
            'WFP28_' || s.iso3 || '_' || REGEXP_REPLACE(UPPER(s.market), '[^A-Z0-9]', '', 'g'),
            s.commodity_code, s.obs_date,
            ROUND(s.nominal_price_kg, 3), s.cur, ROUND(s.real_usd_per_kg, 3),
            ROUND((s.real_usd_per_kg / {EFF_G_SQL}) * 50.0, 4),
            ROUND(((s.real_usd_per_kg / {EFF_G_SQL}) * 50.0) / COALESCE(cr.wage_hr_usd, 3.5) * 60.0, 2),
            FALSE
        FROM staged_w26 s
        LEFT JOIN country_rates cr ON cr.country_iso3 = s.iso3
        WHERE LENGTH(s.iso3) = 3
          AND s.real_usd_per_kg BETWEEN 0.12 AND 55.0
          AND ((s.real_usd_per_kg / {EFF_G_SQL}) * 50.0) BETWEEN 0.04 AND 8.50;
    """)

f_hist = raw_dir / "wfp_1990_2021.csv"
if f_hist.exists():
    print("[TRACK 28B] Ingesting wfp_1990_2021.csv (225.8 MB archive)...")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE mkt_geo_lookup AS
        SELECT LOWER(TRIM(market_name)) AS mname, FIRST(market_id) AS market_id, FIRST(country_iso3) AS iso3,
               MEDIAN(nominal_price_kg / NULLIF(real_usd_per_kg, 0)) AS fx
        FROM fact_protein_observations JOIN dim_markets USING(market_id)
        WHERE real_usd_per_kg > 0
        GROUP BY 1;
    """)
    max_id = con.execute("SELECT COALESCE(MAX(obs_id), 0) FROM fact_protein_observations;").fetchone()[0]
    con.execute(f"""
        INSERT INTO fact_protein_observations (
            obs_id, source_dataset, price_basis, market_id, commodity_code, obs_date,
            nominal_price_kg, currency_code, real_usd_per_kg, usd_per_50g_diaas, labor_min_50g_diaas, is_outlier_mad
        )
        WITH raw_h AS (
            SELECT
                LOWER(TRIM(CAST(mkt_name AS VARCHAR))) AS mname,
                LOWER(TRIM(CAST(cm_name AS VARCHAR))) AS comm,
                LOWER(TRIM(CAST(um_name AS VARCHAR))) AS unit,
                CAST(cur_name AS VARCHAR) AS cur,
                TRY_CAST(mp_price AS DOUBLE) AS price,
                LPAD(CAST(mp_year AS VARCHAR), 4, '0') || '-' || LPAD(CAST(mp_month AS VARCHAR), 2, '0') || '-15' AS obs_date
            FROM read_csv_auto('{f_hist}', ignore_errors=true)
            WHERE TRY_CAST(mp_price AS DOUBLE) > 0
              AND LOWER(CAST(pt_name AS VARCHAR)) IN ('retail', 'wholesale')
        ),
        mapped_h AS (
            SELECT r.*, g.market_id, g.iso3, COALESCE(NULLIF(g.fx, 0), 1.0) AS fx,
                CASE
                    WHEN REGEXP_MATCHES(comm, '(egg)') THEN 'EGGS_CHIX'
                    WHEN REGEXP_MATCHES(comm, '(milk|yogurt|curd)') THEN 'MILK_FLUID'
                    WHEN REGEXP_MATCHES(comm, '(cheese|paneer)') THEN 'CHEESE_HARD'
                    WHEN REGEXP_MATCHES(comm, '(chicken|poultry)') THEN 'POULTRY_CHIX'
                    WHEN REGEXP_MATCHES(comm, '(beef|cattle|veal)') THEN 'BEEF_FRESH'
                    WHEN REGEXP_MATCHES(comm, '(pork|pig)') THEN 'PORK_FRESH'
                    WHEN REGEXP_MATCHES(comm, '(mutton|goat|sheep|lamb)') THEN 'MUTTON_FRESH'
                    WHEN REGEXP_MATCHES(comm, '(sardine|anchov|herring|sprat|mackerel|fish \\(dry|fish \\(smoked)') THEN 'FISH_PELAGIC_SMALL'
                    WHEN REGEXP_MATCHES(comm, '(tilapia|carp|catfish|pangasius|fish \\(fresh)') THEN 'FISH_AQUACULTURE'
                    WHEN REGEXP_MATCHES(comm, '(tuna|salmon|cod)') THEN 'FISH_PREDATOR_HIGH'
                    WHEN REGEXP_MATCHES(comm, '(lentil|masoor|moong|urad|dal|dhal)') THEN 'LENTILS_DRY'
                    WHEN REGEXP_MATCHES(comm, '(bean|cowpea|chickpea|pea|groundnut|peanut|pulse)') THEN 'BEANS_DRY'
                    WHEN REGEXP_MATCHES(comm, '(wheat|flour|bread)') THEN 'WHEAT_FLOUR'
                    WHEN REGEXP_MATCHES(comm, '(rice)') THEN 'RICE_MILLED'
                    ELSE NULL
                END AS commodity_code,
                CASE
                    WHEN unit = 'kg' OR unit = 'l' THEN 1.0
                    WHEN REGEXP_MATCHES(unit, '^[0-9]+\\s*(g|ml)$') THEN TRY_CAST(REGEXP_EXTRACT(unit, '^([0-9]+)', 1) AS DOUBLE) / 1000.0
                    WHEN unit LIKE '%10 pcs%' OR unit LIKE '%dozen%' OR unit LIKE '%12 pcs%' THEN 0.60
                    WHEN unit = 'unit' OR unit = 'piece' THEN 0.055
                    ELSE 1.0
                END AS kg_factor
            FROM raw_h r
            JOIN mkt_geo_lookup g ON r.mname = g.mname
        )
        SELECT
            {max_id} + ROW_NUMBER() OVER (),
            'WFP_HDX_BULK_28', 'OBSERVED_MARKET',
            m.market_id, m.commodity_code, m.obs_date,
            ROUND(m.price / GREATEST(0.04, m.kg_factor), 3),
            m.cur,
            ROUND((m.price / GREATEST(0.04, m.kg_factor)) / m.fx, 3),
            ROUND((((m.price / GREATEST(0.04, m.kg_factor)) / m.fx) / {EFF_G_SQL}) * 50.0, 4),
            ROUND(((((m.price / GREATEST(0.04, m.kg_factor)) / m.fx) / {EFF_G_SQL}) * 50.0) / COALESCE(cr.wage_hr_usd, 3.5) * 60.0, 2),
            FALSE
        FROM mapped_h m
        LEFT JOIN country_rates cr ON cr.country_iso3 = m.iso3
        WHERE m.commodity_code IN (SELECT commodity_code FROM dim_commodities)
          AND ((m.price / GREATEST(0.04, m.kg_factor)) / m.fx) BETWEEN 0.12 AND 55.0
          AND ((((m.price / GREATEST(0.04, m.kg_factor)) / m.fx) / {EFF_G_SQL}) * 50.0) BETWEEN 0.04 AND 8.50;
    """)

added = con.execute("SELECT COUNT(*) FROM fact_protein_observations WHERE source_dataset = 'WFP_HDX_BULK_28';").fetchone()[0]
tot_rows, tot_mkts = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id) FROM fact_protein_observations WHERE is_outlier_mad = FALSE;").fetchone()
print(f"[TRACK 28 COMPLETE] Added +{added:,} WFP HDX rows! Total DB: {tot_rows:,} rows | {tot_mkts:,} markets ({time.time()-t0:.1f}s)")
con.close()
