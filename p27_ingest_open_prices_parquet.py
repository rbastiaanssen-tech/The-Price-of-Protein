import duckdb, time

t0 = time.time()
con = duckdb.connect("protein_atlas.duckdb")
con.execute("INSTALL httpfs; LOAD httpfs;")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'OPEN_PRICES_PARQUET_27';")

# Ensure fallback market & SOY_TOFU commodity exist for foreign-key integrity
con.execute("INSERT OR IGNORE INTO dim_markets VALUES ('OP27_GLOBAL_FALLBACK', 'OpenPrices Global', 'FRA', 'OPEN_PRICES', 48.85, 2.35, 0.15, 0.15);")
try:
    con.execute("INSERT OR IGNORE INTO dim_commodities (commodity_code, commodity_name) VALUES ('SOY_TOFU', 'Soybean Tofu / Tempeh');")
except Exception:
    pass

PARQUET_URL = "data_raw/open_prices.parquet"
cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{PARQUET_URL}');").fetchall()]

con.execute("""
    CREATE OR REPLACE TEMP TABLE country_rates AS
    SELECT m.country_iso3,
           MEDIAN(f.usd_per_50g_diaas / NULLIF(f.labor_min_50g_diaas / 60.0, 0)) AS wage_hr_usd
    FROM fact_protein_observations f
    JOIN dim_markets m USING(market_id)
    WHERE f.is_outlier_mad = FALSE AND f.real_usd_per_kg > 0 AND f.labor_min_50g_diaas > 0
    GROUP BY 1;
""")

FX_SQL = """
    CASE UPPER(currency)
        WHEN 'USD' THEN 1.0 WHEN 'EUR' THEN 1.09 WHEN 'GBP' THEN 1.28
        WHEN 'CAD' THEN 0.73 WHEN 'AUD' THEN 0.66 WHEN 'CHF' THEN 1.14
        WHEN 'SEK' THEN 0.095 WHEN 'NOK' THEN 0.092 WHEN 'DKK' THEN 0.146
        WHEN 'PLN' THEN 0.25 WHEN 'BRL' THEN 0.18 WHEN 'MXN' THEN 0.052
        WHEN 'INR' THEN 0.0118 WHEN 'CNY' THEN 0.138 WHEN 'JPY' THEN 0.0066
        WHEN 'RUB' THEN 0.0108 WHEN 'MAD' THEN 0.10 WHEN 'DZD' THEN 0.0074
        WHEN 'TRY' THEN 0.029 WHEN 'ZAR' THEN 0.055 ELSE NULL
    END
"""

CLASSIFIER_SQL = """
    CASE
        WHEN REGEXP_MATCHES(txt, '(egg|oeuf|œuf|huevo|uovo|ei\\b|eier|яйц)') THEN 'EGGS_CHIX'
        WHEN REGEXP_MATCHES(txt, '(milk|lait|leche|latte|milch|melk|leite|молоко)') THEN 'MILK_FLUID'
        WHEN REGEXP_MATCHES(txt, '(cheese|fromage|queso|formaggio|käse|kaas|queijo|сыр)') THEN 'CHEESE_HARD'
        WHEN REGEXP_MATCHES(txt, '(chicken|poulet|pollo|hähnchen|kip|frango|куриц)') THEN 'POULTRY_CHIX'
        WHEN REGEXP_MATCHES(txt, '(beef|boeuf|bœuf|vacuno|ternera|rind|rund|carne bovina|говядин)') THEN 'BEEF_FRESH'
        WHEN REGEXP_MATCHES(txt, '(pork|porc|cerdo|schwein|varkens|suíno|свинин)') THEN 'PORK_FRESH'
        WHEN REGEXP_MATCHES(txt, '(tuna|thon|atún|atun|tonno|thunfisch|sardine|sardina|herring|hareng)') THEN 'FISH_PELAGIC_SMALL'
        WHEN REGEXP_MATCHES(txt, '(salmon|saumon|salmón|cod|cabillaud|bacalao)') THEN 'FISH_PREDATOR_HIGH'
        WHEN REGEXP_MATCHES(txt, '(lentil|lentille|lenteja|lenticchie|linsen|linzen|lentilha|чечевиц)') THEN 'LENTILS_DRY'
        WHEN REGEXP_MATCHES(txt, '(bean|haricot|frijol|alubia|fagioli|bohnen|bonen|feijão|chickpea|pois chiche|garbanzo)') THEN 'BEANS_DRY'
        ELSE NULL
    END
"""

EFF_G_SQL = """
    CASE commodity_code
        WHEN 'EGGS_CHIX' THEN 124.0 WHEN 'MILK_FLUID' THEN 33.0 WHEN 'CHEESE_HARD' THEN 245.0
        WHEN 'POULTRY_CHIX' THEN 195.0 WHEN 'BEEF_FRESH' THEN 215.0 WHEN 'PORK_FRESH' THEN 190.0
        WHEN 'FISH_PELAGIC_SMALL' THEN 180.0 WHEN 'FISH_PREDATOR_HIGH' THEN 176.0
        WHEN 'LENTILS_DRY' THEN 148.0 WHEN 'BEANS_DRY' THEN 138.0
    END
"""

txt_parts = [f"COALESCE(CAST({c} AS VARCHAR), '')" for c in ("product_product_name", "product_name", "category_tag", "product_categories_tags", "location_osm_display_name") if c in cols]
txt_expr = "LOWER(" + " || ' ' || ".join(txt_parts) + ")"
qty_col = "product_quantity" if "product_quantity" in cols else "NULL"
city_expr = "COALESCE(NULLIF(CAST(location_osm_address_city AS VARCHAR), ''), 'OpenPrices Store')" if "location_osm_address_city" in cols else "'OpenPrices Store'"

print("[TRACK 27] Streaming & classifying Open Prices Parquet...")
con.execute(f"""
    CREATE OR REPLACE TEMP TABLE staged_op AS
    WITH raw_p AS (
        SELECT
            {txt_expr} AS txt,
            CAST(price AS DOUBLE) AS price,
            CAST(currency AS VARCHAR) AS currency,
            UPPER(COALESCE(CAST(price_per AS VARCHAR), 'UNIT')) AS price_per,
            TRY_CAST({qty_col} AS DOUBLE) AS p_qty,
            COALESCE(TRY_CAST(date AS DATE), DATE '2026-01-01') AS obs_date,
            {city_expr} AS city_name
        FROM read_parquet('{PARQUET_URL}')
        WHERE price > 0.05 AND price < 250
    ),
    classified AS (
        SELECT *,
               {CLASSIFIER_SQL} AS commodity_code,
               CASE
                   WHEN price_per = 'KILOGRAM' THEN price
                   WHEN p_qty IS NOT NULL AND p_qty BETWEEN 30 AND 5000 THEN price / (p_qty / 1000.0)
                   ELSE price / 0.50
               END AS nominal_price_kg
        FROM raw_p
    )
    SELECT
        commodity_code,
        obs_date,
        UPPER(currency) AS currency_code,
        nominal_price_kg,
        nominal_price_kg * {FX_SQL} AS real_usd_per_kg,
        city_name
    FROM classified
    WHERE commodity_code IS NOT NULL
      AND {FX_SQL} IS NOT NULL;
""")

con.execute("""
    CREATE OR REPLACE TEMP TABLE city_geo AS
    SELECT LOWER(TRIM(market_name)) AS cname,
           FIRST(market_id) AS market_id,
           FIRST(country_iso3) AS country_iso3
    FROM dim_markets
    GROUP BY 1;
""")

max_id = con.execute("SELECT COALESCE(MAX(obs_id), 0) FROM fact_protein_observations;").fetchone()[0]

con.execute(f"""
    INSERT INTO fact_protein_observations (
        obs_id, source_dataset, price_basis, market_id, commodity_code, obs_date,
        nominal_price_kg, currency_code, real_usd_per_kg, usd_per_50g_diaas, labor_min_50g_diaas, is_outlier_mad
    )
    SELECT
        {max_id} + ROW_NUMBER() OVER () AS obs_id,
        'OPEN_PRICES_PARQUET_27' AS source_dataset,
        'OBSERVED_MARKET' AS price_basis,
        COALESCE(cg.market_id, 'OP27_GLOBAL_FALLBACK') AS market_id,
        s.commodity_code,
        CAST(s.obs_date AS VARCHAR),
        ROUND(s.nominal_price_kg, 3),
        s.currency_code,
        ROUND(s.real_usd_per_kg, 3),
        ROUND((s.real_usd_per_kg / {EFF_G_SQL}) * 50.0, 4),
        ROUND(((s.real_usd_per_kg / {EFF_G_SQL}) * 50.0) / COALESCE(cr.wage_hr_usd, 22.0) * 60.0, 2),
        FALSE
    FROM staged_op s
    LEFT JOIN city_geo cg ON LOWER(TRIM(s.city_name)) = cg.cname
    LEFT JOIN country_rates cr ON cr.country_iso3 = COALESCE(cg.country_iso3, 'FRA')
    WHERE s.real_usd_per_kg BETWEEN 0.25 AND 65.0
      AND ((s.real_usd_per_kg / {EFF_G_SQL}) * 50.0) BETWEEN 0.04 AND 8.50;
""")

added = con.execute("SELECT COUNT(*) FROM fact_protein_observations WHERE source_dataset = 'OPEN_PRICES_PARQUET_27';").fetchone()[0]
tot_rows, tot_mkts = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id) FROM fact_protein_observations WHERE is_outlier_mad = FALSE;").fetchone()
print(f"[TRACK 27 COMPLETE] Added +{added:,} Open Prices receipt rows! Total DB: {tot_rows:,} rows | {tot_mkts:,} markets ({time.time()-t0:.1f}s)")
con.close()
