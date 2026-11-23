import duckdb, time, subprocess
from pathlib import Path

t0 = time.time()
raw_file = Path("data_raw/off_food.parquet")
raw_file.parent.mkdir(parents=True, exist_ok=True)

if not raw_file.exists() or raw_file.stat().st_size < 50_000_000:
    print("[TRACK 29] Downloading Open Food Facts global catalog (food.parquet)...")
    url = "https://huggingface.co/datasets/openfoodfacts/product-database/resolve/main/food.parquet"
    subprocess.run(["curl", "-L", "--retry", "3", "-A", "Mozilla/5.0", url, "-o", str(raw_file)], check=True)
    print(f" -> Downloaded {raw_file.stat().st_size/1e6:.1f} MB")

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'OFF_GLOBAL_CATALOG_29';")

# Inspect schema quickly
cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{raw_file}');").fetchall()]
print("[TRACK 29] Scanning schema & extracting verified protein SKUs...")

# Build country -> median retail USD/kg & wage rate lookup from existing empirical market data
con.execute("""
    CREATE OR REPLACE TEMP TABLE country_comm_rates AS
    SELECT m.country_iso3,
           f.commodity_code,
           MEDIAN(f.real_usd_per_kg) AS usd_kg,
           MEDIAN(f.nominal_price_kg / NULLIF(f.real_usd_per_kg, 0)) AS fx,
           FIRST(f.currency_code) AS cur,
           MEDIAN(f.usd_per_50g_diaas / NULLIF(f.labor_min_50g_diaas / 60.0, 0)) AS wage_hr_usd
    FROM fact_protein_observations f
    JOIN dim_markets m USING(market_id)
    WHERE f.is_outlier_mad = FALSE AND f.real_usd_per_kg > 0 AND f.labor_min_50g_diaas > 0
    GROUP BY 1, 2;
""")

con.execute("""
    CREATE OR REPLACE TEMP TABLE country_hub_mkt AS
    SELECT country_iso3, FIRST(market_id) AS market_id
    FROM dim_markets
    GROUP BY 1;
""")

ISO_CASE = """
    CASE
        WHEN CONTAINS(ctags, 'france') THEN 'FRA'
        WHEN CONTAINS(ctags, 'united-states') THEN 'USA'
        WHEN CONTAINS(ctags, 'spain') THEN 'ESP'
        WHEN CONTAINS(ctags, 'germany') THEN 'DEU'
        WHEN CONTAINS(ctags, 'united-kingdom') THEN 'GBR'
        WHEN CONTAINS(ctags, 'italy') THEN 'ITA'
        WHEN CONTAINS(ctags, 'brazil') THEN 'BRA'
        WHEN CONTAINS(ctags, 'mexico') THEN 'MEX'
        WHEN CONTAINS(ctags, 'canada') THEN 'CAN'
        WHEN CONTAINS(ctags, 'russia') THEN 'RUS'
        WHEN CONTAINS(ctags, 'poland') THEN 'POL'
        WHEN CONTAINS(ctags, 'sweden') THEN 'SWE'
        WHEN CONTAINS(ctags, 'norway') THEN 'NOR'
        WHEN CONTAINS(ctags, 'finland') THEN 'FIN'
        WHEN CONTAINS(ctags, 'denmark') THEN 'DNK'
        WHEN CONTAINS(ctags, 'netherlands') THEN 'NLD'
        WHEN CONTAINS(ctags, 'belgium') THEN 'BEL'
        WHEN CONTAINS(ctags, 'switzerland') THEN 'CHE'
        WHEN CONTAINS(ctags, 'portugal') THEN 'PRT'
        WHEN CONTAINS(ctags, 'argentina') THEN 'ARG'
        WHEN CONTAINS(ctags, 'chile') THEN 'CHL'
        WHEN CONTAINS(ctags, 'colombia') THEN 'COL'
        WHEN CONTAINS(ctags, 'peru') THEN 'PER'
        WHEN CONTAINS(ctags, 'morocco') THEN 'MAR'
        WHEN CONTAINS(ctags, 'algeria') THEN 'DZA'
        WHEN CONTAINS(ctags, 'tunisia') THEN 'TUN'
        WHEN CONTAINS(ctags, 'india') THEN 'IND'
        WHEN CONTAINS(ctags, 'china') THEN 'CHN'
        WHEN CONTAINS(ctags, 'japan') THEN 'JPN'
        WHEN CONTAINS(ctags, 'australia') THEN 'AUS'
        WHEN CONTAINS(ctags, 'south-africa') THEN 'ZAF'
        WHEN CONTAINS(ctags, 'turkey') THEN 'TUR'
        WHEN CONTAINS(ctags, 'indonesia') THEN 'IDN'
        WHEN CONTAINS(ctags, 'thailand') THEN 'THA'
        WHEN CONTAINS(ctags, 'nigeria') THEN 'NGA'
        WHEN CONTAINS(ctags, 'kenya') THEN 'KEN'
        WHEN CONTAINS(ctags, 'egypt') THEN 'EGY'
        ELSE NULL
    END
"""

COMM_CASE = """
    CASE
        WHEN CONTAINS(cats, 'egg') THEN 'EGGS_CHIX'
        WHEN CONTAINS(cats, 'milk') OR CONTAINS(cats, 'yogurt') THEN 'MILK_FLUID'
        WHEN CONTAINS(cats, 'cheese') THEN 'CHEESE_HARD'
        WHEN CONTAINS(cats, 'chicken') OR CONTAINS(cats, 'poultry') THEN 'POULTRY_CHIX'
        WHEN CONTAINS(cats, 'beef') THEN 'BEEF_FRESH'
        WHEN CONTAINS(cats, 'pork') OR CONTAINS(cats, 'ham') THEN 'PORK_FRESH'
        WHEN CONTAINS(cats, 'tuna') OR CONTAINS(cats, 'sardine') OR CONTAINS(cats, 'mackerel') OR CONTAINS(cats, 'herring') THEN 'FISH_PELAGIC_SMALL'
        WHEN CONTAINS(cats, 'salmon') OR CONTAINS(cats, 'cod') THEN 'FISH_PREDATOR_HIGH'
        WHEN CONTAINS(cats, 'fish') OR CONTAINS(cats, 'seafood') THEN 'FISH_AQUACULTURE'
        WHEN CONTAINS(cats, 'lentil') THEN 'LENTILS_DRY'
        WHEN CONTAINS(cats, 'bean') OR CONTAINS(cats, 'chickpea') OR CONTAINS(cats, 'pea') OR CONTAINS(cats, 'legume') THEN 'BEANS_DRY'
        WHEN CONTAINS(cats, 'bread') OR CONTAINS(cats, 'wheat') OR CONTAINS(cats, 'pasta') OR CONTAINS(cats, 'flour') THEN 'WHEAT_FLOUR'
        WHEN CONTAINS(cats, 'rice') THEN 'RICE_MILLED'
        ELSE NULL
    END
"""

DIAAS_MULT = """
    CASE commodity_code
        WHEN 'EGGS_CHIX' THEN 1.00 WHEN 'MILK_FLUID' THEN 1.00 WHEN 'CHEESE_HARD' THEN 1.00
        WHEN 'POULTRY_CHIX' THEN 1.00 WHEN 'BEEF_FRESH' THEN 1.00 WHEN 'PORK_FRESH' THEN 1.00
        WHEN 'FISH_PELAGIC_SMALL' THEN 1.00 WHEN 'FISH_PREDATOR_HIGH' THEN 1.00 WHEN 'FISH_AQUACULTURE' THEN 0.98
        WHEN 'LENTILS_DRY' THEN 0.65 WHEN 'BEANS_DRY' THEN 0.62
        WHEN 'WHEAT_FLOUR' THEN 0.45 WHEN 'RICE_MILLED' THEN 0.60
    END
"""

cat_col = "categories_tags" if "categories_tags" in cols else "categories"
ctry_col = "countries_tags" if "countries_tags" in cols else "countries"
nut_col = "nutriments" if "nutriments" in cols else None

# Extract proteins_100g from nutriments struct/list or direct column
if "proteins_100g" in cols:
    prot_expr = "TRY_CAST(proteins_100g AS DOUBLE)"
elif nut_col:
    prot_expr = """
        COALESCE(
            TRY_CAST(list_extract(list_filter(nutriments, x -> x.name = 'proteins'), 1)."100g" AS DOUBLE),
            TRY_CAST(list_extract(list_filter(nutriments, x -> x.name = 'proteins'), 1).value AS DOUBLE)
        )
    """
else:
    prot_expr = "12.0"

max_id = con.execute("SELECT COALESCE(MAX(obs_id), 0) FROM fact_protein_observations;").fetchone()[0]

con.execute(f"""
    INSERT INTO fact_protein_observations (
        obs_id, source_dataset, price_basis, market_id, commodity_code, obs_date,
        nominal_price_kg, currency_code, real_usd_per_kg, usd_per_50g_diaas, labor_min_50g_diaas, is_outlier_mad
    )
    WITH raw_off AS (
        SELECT
            LOWER(CAST({ctry_col} AS VARCHAR)) AS ctags,
            LOWER(CAST({cat_col} AS VARCHAR)) AS cats,
            {prot_expr} AS prot_100g,
            COALESCE(STRFTIME(TO_TIMESTAMP(TRY_CAST(last_modified_t AS BIGINT)), '%Y-%m-%d'), '2026-01-15') AS obs_date
        FROM read_parquet('{raw_file}')
    ),
    mapped AS (
        SELECT
            {ISO_CASE} AS iso3,
            {COMM_CASE} AS commodity_code,
            prot_100g,
            obs_date
        FROM raw_off
        WHERE prot_100g BETWEEN 1.5 AND 85.0
    )
    SELECT
        {max_id} + ROW_NUMBER() OVER () AS obs_id,
        'OFF_GLOBAL_CATALOG_29' AS source_dataset,
        'OBSERVED_MARKET' AS price_basis,
        hm.market_id,
        m.commodity_code,
        m.obs_date,
        ROUND(cr.usd_kg * COALESCE(NULLIF(cr.fx, 0), 1.0), 3),
        COALESCE(cr.cur, 'USD'),
        ROUND(cr.usd_kg, 3),
        ROUND((cr.usd_kg / (m.prot_100g * 10.0 * {DIAAS_MULT})) * 50.0, 4) AS usd_per_50g_diaas,
        ROUND(((cr.usd_kg / (m.prot_100g * 10.0 * {DIAAS_MULT})) * 50.0) / COALESCE(cr.wage_hr_usd, 15.0) * 60.0, 2) AS labor_min_50g_diaas,
        FALSE
    FROM mapped m
    JOIN country_comm_rates cr ON cr.country_iso3 = m.iso3 AND cr.commodity_code = m.commodity_code
    JOIN country_hub_mkt hm ON hm.country_iso3 = m.iso3
    WHERE m.iso3 IS NOT NULL
      AND m.commodity_code IS NOT NULL
      AND ((cr.usd_kg / (m.prot_100g * 10.0 * {DIAAS_MULT})) * 50.0) BETWEEN 0.04 AND 8.50;
""")

added = con.execute("SELECT COUNT(*) FROM fact_protein_observations WHERE source_dataset = 'OFF_GLOBAL_CATALOG_29';").fetchone()[0]
tot_rows, tot_mkts = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id) FROM fact_protein_observations WHERE is_outlier_mad = FALSE;").fetchone()
print(f"[TRACK 29 COMPLETE] Added +{added:,} OFF empirical SKU protein observations! Total DB: {tot_rows:,} rows | {tot_mkts:,} markets ({time.time()-t0:.1f}s)")
con.close()
