from __future__ import annotations
import pandas as pd
from etl_protein_atlas import ProteinAtlasETL

etl = ProteinAtlasETL("protein_atlas.duckdb", "schema.sql")
con = etl.con

con.execute("""
    INSERT OR IGNORE INTO dim_units VALUES
    ('125 g', 0.125, 'Tin 125g'), ('150 g', 0.15, 'Tin 150g'), ('160 g', 0.16, 'Tin 160g'),
    ('170 g', 0.17, 'Tin 170g'), ('200 g', 0.20, 'Pack 200g'), ('500 g', 0.50, 'Pack 500g'), ('1 kg', 1.0, '1 kg');

    INSERT OR REPLACE INTO dim_commodities VALUES
    ('FISH_FRESH',      'Fresh Fish (Tilapia/Carp)',    (SELECT regime_class FROM dim_commodities WHERE commodity_code='COD_SALTED'),  TRUE,  0.60, 190.0, 0.95, 1.05, 1100,    2.0, 0.25, 'FAO / USDA FDC'),
    ('FISH_CANNED',     'Canned Sardines / Tuna',       (SELECT regime_class FROM dim_commodities WHERE commodity_code='COD_SALTED'),  TRUE,  1.00, 230.0, 1.00, 1.05, 2080, 1095.0, 0.40, 'FAO / USDA FDC'),
    ('TOFU_TEMPEH',     'Tofu / Tempeh (Soy Curd)',     (SELECT regime_class FROM dim_commodities WHERE commodity_code='BEANS_DRY'),   FALSE, 1.00, 140.0, 0.98, 0.91, 1450,   14.0, 0.24, 'FAO 2013'),
    ('TVP',             'Textured Vegetable Protein',   (SELECT regime_class FROM dim_commodities WHERE commodity_code='SOY_ISOLATE'), FALSE, 1.00, 510.0, 0.98, 0.91, 3300,  730.0, 0.92, 'USDA FDC'),
    ('WHEAT_FLOUR',     'Wheat Flour (Calorie Anchor)', (SELECT regime_class FROM dim_commodities WHERE commodity_code='BEANS_DRY'),   FALSE, 1.00, 105.0, 0.95, 0.45, 3640,  365.0, 0.88, 'Stigler Anchor'),
    ('RICE_MILLED',     'Milled Rice (Calorie Anchor)', (SELECT regime_class FROM dim_commodities WHERE commodity_code='BEANS_DRY'),   FALSE, 1.00,  71.0, 0.95, 0.59, 3600,  540.0, 0.88, 'Stigler Anchor'),
    ('SINGLE_CELL_GAS', 'Gas-Fermented SCP (Solein)',   (SELECT regime_class FROM dim_commodities WHERE commodity_code='MYCOPROTEIN'), FALSE, 1.00, 700.0, 0.98, 0.95, 3800,  730.0, 0.95, '2026 TEA COGS'),
    ('INSECT_MEAL',     'Industrial Cricket / BSF Meal',(SELECT regime_class FROM dim_commodities WHERE commodity_code='COD_SALTED'),  TRUE,  1.00, 620.0, 0.98, 0.84, 4100,  540.0, 0.94, '2026 TEA COGS');

    DELETE FROM fact_protein_observations WHERE source_dataset = 'WFP_MARKET' AND commodity_code = 'COD_SALTED';
""")

wfp_add = con.execute("""
    SELECT 'WFP_MARKET' AS source_dataset, 'OBSERVED_MARKET' AS price_basis,
           'WFP_' || CAST(market_id AS VARCHAR) AS market_id,
           CASE
               WHEN LOWER(commodity) LIKE '%canned%' OR LOWER(commodity) LIKE '%tin%' OR LOWER(commodity) LIKE '%sardine%' OR LOWER(commodity) LIKE '%tuna%' THEN 'FISH_CANNED'
               WHEN LOWER(commodity) LIKE '%fish%' OR LOWER(commodity) LIKE '%tilapia%' THEN 'FISH_FRESH'
               WHEN LOWER(commodity) LIKE '%wheat%' THEN 'WHEAT_FLOUR'
               WHEN LOWER(commodity) LIKE '%rice%' THEN 'RICE_MILLED'
           END AS commodity_code,
           CAST(date AS DATE) AS obs_date, CAST(price AS DOUBLE) AS raw_price,
           LOWER(TRIM(unit)) AS raw_unit, UPPER(TRIM(currency)) AS currency_code
    FROM read_csv_auto('raw_data/wfp/wfp_food_prices_global_*.csv', union_by_name=True)
    WHERE price > 0 AND usdprice > 0 AND date IS NOT NULL AND commodity_code IS NOT NULL;
""").df()

benchmarks = pd.DataFrame([
    ("TEA_BENCHMARK", "OBSERVED_MARKET",  "HUB_SGP", "TOFU_TEMPEH",     "2026-06-01",  3.20, "kg", "USD"),
    ("TEA_BENCHMARK", "OBSERVED_MARKET",  "HUB_USA", "TVP",             "2026-06-01",  3.80, "kg", "USD"),
    ("TEA_BENCHMARK", "EX_ANTE_TEA_COGS", "HUB_EUR", "SINGLE_CELL_GAS", "2026-06-01",  5.20, "kg", "USD"),
    ("TEA_BENCHMARK", "EX_ANTE_TEA_COGS", "HUB_EUR", "INSECT_MEAL",     "2026-06-01",  7.50, "kg", "USD"),
], columns=["source_dataset","price_basis","market_id","commodity_code","obs_date","raw_price","raw_unit","currency_code"])

etl.ingest_price_stream(pd.concat([wfp_add, benchmarks], ignore_index=True))
etl.flag_mad_outliers(z_threshold=6.0)
etl.export_analytical_parquet("protein_atlas_clean.parquet")
print(con.execute("SELECT commodity_code, COUNT(*) AS rows FROM fact_protein_observations WHERE is_outlier_mad=FALSE GROUP BY 1 ORDER BY 2 DESC").df().to_string(index=False))
