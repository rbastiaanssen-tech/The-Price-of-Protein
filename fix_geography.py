from __future__ import annotations
import duckdb, pandas as pd
from etl_protein_atlas import ProteinAtlasETL

etl = ProteinAtlasETL("protein_atlas.duckdb", "schema.sql")
con = etl.con

# 1. Backfill missing lat/lon from wfp_markets_global.csv
con.execute("""
    CREATE TEMP TABLE mkt_geo AS
    SELECT 'WFP_' || CAST(market_id AS VARCHAR) AS mid,
           TRY_CAST(latitude AS DOUBLE) AS lat,
           TRY_CAST(longitude AS DOUBLE) AS lon
    FROM read_csv_auto('raw_data/wfp/wfp_markets_global.csv')
    WHERE TRY_CAST(latitude AS DOUBLE) IS NOT NULL AND TRY_CAST(latitude AS DOUBLE) <> 0;

    UPDATE dim_markets
    SET latitude = mkt_geo.lat, longitude = mkt_geo.lon
    FROM mkt_geo
    WHERE dim_markets.market_id = mkt_geo.mid AND (dim_markets.latitude = 0 OR dim_markets.longitude = 0);
""")

# 2. Assign true continents based on coordinates & ISO3
con.execute("""
    UPDATE dim_markets SET region_macro = CASE
        WHEN country_iso3 IN ('USA','CAN') THEN 'NORTH_AMERICA'
        WHEN country_iso3 IN ('AUS','NZL','FJI','PNG','WSM','SLB','VUT','TON') THEN 'OCEANIA'
        WHEN country_iso3 IN ('EGY','MAR','DZA','TUN','LBY','JOR','LBN','SYR','IRQ','IRN','SAU','YEM','OMN','ARE','PSE','TUR') THEN 'MENA'
        WHEN longitude BETWEEN -120 AND -30 AND latitude < 33 THEN 'LATAM'
        WHEN longitude BETWEEN -25 AND 60 AND latitude BETWEEN -36 AND 37 THEN 'AFRICA'
        WHEN longitude BETWEEN -25 AND 60 AND latitude > 37 THEN 'EUROPE'
        WHEN longitude > 60 THEN 'ASIA'
        ELSE region_macro
    END;
""")

# 3. Add modern 2026 retail anchors for underrepresented rich-world corners
etl.ingest_markets_and_macro(
    pd.DataFrame([
        ("MOD_TOKYO",  "Tokyo Retail",  "JPN", "ASIA",    35.676, 139.650, 0.03, 0.19),
        ("MOD_LONDON", "London Retail", "GBR", "EUROPE",  51.507,  -0.127, 0.09, 0.24),
        ("MOD_BERLIN", "Berlin Retail", "DEU", "EUROPE",  52.520,  13.405, 0.14, 0.22),
        ("MOD_SYDNEY", "Sydney Retail", "AUS", "OCEANIA",-33.868, 151.209, 1.25, 0.14),
    ], columns=["market_id","market_name","country_iso3","region_macro","latitude","longitude","arable_ha_per_cap","industrial_elec_kwh"]),
    pd.DataFrame([
        ("JPN", 2026, "USD", 1.0, 1.0, 14.50, "OECD 2026"),
        ("GBR", 2026, "USD", 1.0, 1.0, 17.80, "ONS 2026"),
        ("DEU", 2026, "USD", 1.0, 1.0, 18.90, "Destatis 2026"),
        ("AUS", 2026, "USD", 1.0, 1.0, 21.00, "ABS 2026"),
    ], columns=["country_iso3","year","currency_code","cpi_to_2026","ppp_to_2026_usd","unskilled_wage_hr","wage_source"])
)
etl.ingest_price_stream(pd.DataFrame([
    ("OECD_RETAIL", "OBSERVED_MARKET", "MOD_TOKYO",  "BEEF_FRESH",   "2026-06-01", 19.50, "kg", "USD"),
    ("OECD_RETAIL", "OBSERVED_MARKET", "MOD_TOKYO",  "POULTRY_CHIX", "2026-06-01",  7.80, "kg", "USD"),
    ("OECD_RETAIL", "OBSERVED_MARKET", "MOD_LONDON", "BEEF_FRESH",   "2026-06-01", 14.20, "kg", "USD"),
    ("OECD_RETAIL", "OBSERVED_MARKET", "MOD_LONDON", "BEANS_DRY",    "2026-06-01",  2.40, "kg", "USD"),
    ("OECD_RETAIL", "OBSERVED_MARKET", "MOD_BERLIN", "PORK_FRESH",   "2026-06-01",  8.90, "kg", "USD"),
    ("OECD_RETAIL", "OBSERVED_MARKET", "MOD_SYDNEY", "BEEF_FRESH",   "2026-06-01", 13.50, "kg", "USD"),
], columns=["source_dataset","price_basis","market_id","commodity_code","obs_date","raw_price","raw_unit","currency_code"]))

print(con.execute("""
    SELECT m.region_macro, COUNT(DISTINCT m.country_iso3) AS countries, COUNT(*) AS obs
    FROM fact_protein_observations f JOIN dim_markets m USING (market_id)
    WHERE f.is_outlier_mad = FALSE GROUP BY 1 ORDER BY obs DESC;
""").df().to_string(index=False))
