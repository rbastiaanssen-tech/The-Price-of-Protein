from __future__ import annotations
import glob, re, warnings
from pathlib import Path
import duckdb, numpy as np, pandas as pd
from etl_protein_atlas import ProteinAtlasETL

warnings.filterwarnings("ignore")

CITY_COORDS = {
    "london": ("GBR", "EUROPE", 51.507, -0.127, 0.09, 0.24),
    "amsterdam": ("NLD", "EUROPE", 52.367, 4.904, 0.06, 0.18),
    "antwerp": ("BEL", "EUROPE", 51.219, 4.402, 0.07, 0.19),
    "paris": ("FRA", "EUROPE", 48.856, 2.352, 0.27, 0.15),
    "strasbourg": ("FRA", "EUROPE", 48.573, 7.752, 0.27, 0.15),
    "aix": ("FRA", "EUROPE", 43.529, 5.447, 0.27, 0.15),
    "madrid": ("ESP", "EUROPE", 40.416, -3.703, 0.25, 0.14),
    "barcelona": ("ESP", "EUROPE", 41.387, 2.168, 0.25, 0.14),
    "valencia": ("ESP", "EUROPE", 39.469, -0.376, 0.25, 0.14),
    "lisbon": ("PRT", "EUROPE", 38.722, -9.139, 0.10, 0.15),
    "vienna": ("AUT", "EUROPE", 48.208, 16.373, 0.15, 0.16),
    "munich": ("DEU", "EUROPE", 48.135, 11.582, 0.14, 0.20),
    "leipzig": ("DEU", "EUROPE", 51.339, 12.373, 0.14, 0.20),
    "augsburg": ("DEU", "EUROPE", 48.370, 10.897, 0.14, 0.20),
    "frankfurt": ("DEU", "EUROPE", 50.110, 8.682, 0.14, 0.20),
    "gdansk": ("POL", "EUROPE", 54.352, 18.646, 0.29, 0.14),
    "warsaw": ("POL", "EUROPE", 52.229, 21.012, 0.29, 0.14),
    "krakow": ("POL", "EUROPE", 50.064, 19.945, 0.29, 0.14),
    "milan": ("ITA", "EUROPE", 45.464, 9.190, 0.12, 0.22),
    "florence": ("ITA", "EUROPE", 43.769, 11.255, 0.12, 0.22),
    "naples": ("ITA", "EUROPE", 40.851, 14.268, 0.12, 0.22),
    "stockholm": ("SWE", "EUROPE", 59.329, 18.068, 0.24, 0.07),
    "copenhagen": ("DNK", "EUROPE", 55.676, 12.568, 0.40, 0.11),
    "moscow": ("RUS", "EUROPE", 55.755, 37.617, 0.85, 0.08),
    "istanbul": ("TUR", "MENA", 41.008, 28.978, 0.23, 0.11),
    "cairo": ("EGY", "MENA", 30.044, 31.235, 0.03, 0.07),
    "beijing": ("CHN", "ASIA", 39.904, 116.407, 0.08, 0.09),
    "kyoto": ("JPN", "ASIA", 35.011, 135.768, 0.03, 0.19),
    "osaka": ("JPN", "ASIA", 34.693, 135.502, 0.03, 0.19),
    "edo": ("JPN", "ASIA", 35.676, 139.650, 0.03, 0.19),
    "boston": ("USA", "NORTH_AMERICA", 42.360, -71.058, 0.47, 0.08),
    "philadelphia": ("USA", "NORTH_AMERICA", 39.952, -75.165, 0.47, 0.08),
    "new york": ("USA", "NORTH_AMERICA", 40.712, -74.006, 0.47, 0.08),
    "mexico": ("MEX", "LATAM", 19.432, -99.133, 0.18, 0.11),
    "lima": ("PER", "LATAM", -12.046, -77.042, 0.11, 0.10),
    "potosi": ("BOL", "LATAM", -19.583, -65.753, 0.38, 0.09),
    "buenos aires": ("ARG", "LATAM", -34.603, -58.381, 0.88, 0.08),
    "santiago": ("CHL", "LATAM", -33.448, -70.669, 0.07, 0.11),
}

def main():
    Path("protein_atlas.duckdb").unlink(missing_ok=True)
    etl = ProteinAtlasETL("protein_atlas.duckdb", "schema.sql")
    etl.init_schema()
    etl.seed_reference_tables()
    con = etl.con

    # 1. Extend commodities & units for global WFP + historical coverage
    con.execute("""
        INSERT OR REPLACE INTO dim_commodities (
            commodity_code, commodity_name, regime_class, is_animal_source,
            edible_fraction, crude_protein_g_kg, prep_retention, diaas_score,
            kcal_per_kg, shelf_life_days, dry_matter_fraction, reference_source
        ) VALUES
        ('MUTTON_FRESH', 'Mutton / Goat Meat', 'ANIMAL_MEAT', TRUE, 0.78, 195.0, 0.95, 1.10, 2350, 5.0, 0.37, 'USDA FDC / FAO 2013'),
        ('GROUNDNUTS',   'Groundnuts / Peanuts', 'PLANT_WHOLE', FALSE, 1.00, 258.0, 0.96, 0.46, 5670, 365.0, 0.94, 'FAO 2013 DIAAS=0.46');
    """)
    con.execute("""
        INSERT OR REPLACE INTO dim_units VALUES
        ('100 g', 0.1, '100 grams'), ('250 g', 0.25, '250 grams'), ('400 g', 0.4, '400 grams'),
        ('l', 1.03, 'Liter fluid'), ('500 ml', 0.515, 'Half liter'), ('unit', 0.0567, 'Single egg/unit'),
        ('dozen', 0.68, 'Dozen eggs'), ('10 pcs', 0.567, '10 eggs'), ('12 pcs', 0.68, '12 eggs'),
        ('litre', 1.03, 'Historical/metric litre');
    """)

    # 2. Ingest all WFP Global Food Price CSVs (1990–2026) via DuckDB SQL
    print("[WFP] Scanning & ingesting raw_data/wfp/wfp_food_prices_global_*.csv...")
    con.execute("""
        CREATE TEMP TABLE raw_wfp AS
        SELECT * FROM read_csv_auto('raw_data/wfp/wfp_food_prices_global_*.csv', union_by_name=True)
        WHERE price > 0 AND usdprice > 0 AND date IS NOT NULL;
    """)

    # Populate WFP markets & macro FX/wage bridges
    con.execute("""
        INSERT OR IGNORE INTO dim_markets
        SELECT DISTINCT
            'WFP_' || CAST(market_id AS VARCHAR) AS market_id,
            COALESCE(market, 'Market ' || CAST(market_id AS VARCHAR)) AS market_name,
            UPPER(TRIM(countryiso3)) AS country_iso3,
            'GLOBAL_SOUTH' AS region_macro,
            COALESCE(TRY_CAST(latitude AS DOUBLE), 0.0) AS latitude,
            COALESCE(TRY_CAST(longitude AS DOUBLE), 0.0) AS longitude,
            0.16 AS arable_ha_per_cap,
            0.12 AS industrial_elec_kwh
        FROM raw_wfp
        WHERE market_id IS NOT NULL AND countryiso3 IS NOT NULL;
    """)

    con.execute("""
        INSERT OR IGNORE INTO dim_macro_wages
        SELECT
            UPPER(TRIM(countryiso3)) AS country_iso3,
            EXTRACT(YEAR FROM CAST(date AS DATE)) AS year,
            UPPER(TRIM(currency)) AS currency_code,
            -- Deflate historical USD to 2026 USD (~2.4% avg annual US CPI)
            POWER(1.024, 2026 - EXTRACT(YEAR FROM CAST(date AS DATE))) AS cpi_to_2026,
            MEDIAN(price / usdprice) AS ppp_to_2026_usd,
            -- Implied nominal hourly unskilled wage (~$1.35/hr 2026 PPP baseline scaled by FX & CPI)
            (1.35 / POWER(1.024, 2026 - EXTRACT(YEAR FROM CAST(date AS DATE)))) * MEDIAN(price / usdprice) AS unskilled_wage_hr,
            'WFP_FX_ILO_PROXY' AS wage_source
        FROM raw_wfp
        WHERE countryiso3 IS NOT NULL AND currency IS NOT NULL
        GROUP BY 1, 2, 3;
    """)

    wfp_mapped = con.execute("""
        SELECT
            'WFP_MARKET' AS source_dataset,
            'OBSERVED_MARKET' AS price_basis,
            'WFP_' || CAST(market_id AS VARCHAR) AS market_id,
            CASE
                WHEN LOWER(commodity) LIKE '%beef%' THEN 'BEEF_FRESH'
                WHEN LOWER(commodity) LIKE '%pork%' THEN 'PORK_FRESH'
                WHEN LOWER(commodity) LIKE '%chicken%' OR LOWER(commodity) LIKE '%poultry%' THEN 'POULTRY_CHIX'
                WHEN LOWER(commodity) LIKE '%mutton%' OR LOWER(commodity) LIKE '%goat%' OR LOWER(commodity) LIKE '%lamb%' THEN 'MUTTON_FRESH'
                WHEN LOWER(commodity) LIKE '%fish%' OR LOWER(commodity) LIKE '%tilapia%' OR LOWER(commodity) LIKE '%sardine%' THEN 'COD_SALTED'
                WHEN LOWER(commodity) LIKE '%milk%' THEN 'MILK_FLUID'
                WHEN LOWER(commodity) LIKE '%cheese%' THEN 'CHEESE_HARD'
                WHEN LOWER(commodity) LIKE '%egg%' THEN 'EGGS_CHIX'
                WHEN LOWER(commodity) LIKE '%lentil%' THEN 'LENTILS_DRY'
                WHEN LOWER(commodity) LIKE '%bean%' OR LOWER(commodity) LIKE '%pea%' OR LOWER(commodity) LIKE '%chickpea%' OR LOWER(commodity) LIKE '%cowpea%' THEN 'BEANS_DRY'
                WHEN LOWER(commodity) LIKE '%groundnut%' OR LOWER(commodity) LIKE '%peanut%' THEN 'GROUNDNUTS'
                WHEN LOWER(commodity) LIKE '%soy%' THEN 'SOYBEANS_DRY'
                ELSE NULL
            END AS commodity_code,
            CAST(date AS DATE) AS obs_date,
            CAST(price AS DOUBLE) AS raw_price,
            LOWER(TRIM(unit)) AS raw_unit,
            UPPER(TRIM(currency)) AS currency_code
        FROM raw_wfp
        WHERE commodity_code IS NOT NULL;
    """).df()
    etl.ingest_price_stream(wfp_mapped)

    # 3. Ingest Allen–Unger Historical Archive (all_commodities.csv)
    au_path = Path("raw_data/historical/all_commodities.csv")
    if au_path.exists():
        print("[Allen-Unger] Parsing historical city series...")
        au = pd.read_csv(au_path, low_memory=False)
        au.columns = [c.strip() for c in au.columns]
        au = au[pd.to_numeric(au["Item Year"], errors="coerce").between(1700, 1950)].copy()
        au["year"] = au["Item Year"].astype(int)
        au["val"] = pd.to_numeric(au["Standard Value"], errors="coerce")
        au = au[au["val"] > 0].copy()

        # Map cities & seed silver-gram (XAG_GRAM) macro wages (~1.2g Ag/hr urban unskilled baseline)
        mkt_rows, macro_rows, price_rows = [], [], []
        seen_mkts, seen_macro = set(), set()

        def map_au_comm(c):
            c = str(c).lower()
            if "beef" in c: return "BEEF_FRESH"
            if "pork" in c or "bacon" in c: return "PORK_FRESH"
            if "mutton" in c or "lamb" in c: return "MUTTON_FRESH"
            if "chicken" in c or "poultry" in c or "fowl" in c: return "POULTRY_CHIX"
            if "cod" in c or "herring" in c or "fish" in c: return "COD_SALTED"
            if "cheese" in c: return "CHEESE_HARD"
            if "milk" in c: return "MILK_FLUID"
            if "egg" in c: return "EGGS_CHIX"
            if "lentil" in c: return "LENTILS_DRY"
            if "bean" in c or "pea" in c: return "BEANS_DRY"
            return None

        au["commodity_code"] = au["Commodity"].map(map_au_comm)
        au = au[au["commodity_code"].notna() & au["Standard Measure"].str.lower().isin(["kg", "litre"])].copy()

        for r in au.itertuples(index=False):
            loc_key = str(r.Location).strip().lower()
            matched = next((k for k in CITY_COORDS if k in loc_key), None)
            if not matched:
                continue
            iso3, reg, lat, lon, arable, elec = CITY_COORDS[matched]
            mkt_id = f"HIST_{matched.upper().replace(' ', '_')}"
            if mkt_id not in seen_mkts:
                mkt_rows.append((mkt_id, matched.title(), iso3, reg, lat, lon, arable, elec))
                seen_mkts.add(mkt_id)

            yr = int(r.year)
            if (iso3, yr, "XAG_GRAM") not in seen_macro:
                # 1g silver ≈ $1.15 in 2026 PPP; historical urban unskilled wage ~0.95g Ag/hr (rising post-1850)
                ag_wage_hr = 0.95 if yr < 1850 else 0.95 + 0.025 * (yr - 1850)
                macro_rows.append((iso3, yr, "XAG_GRAM", 1.15, 1.0, ag_wage_hr, "Allen-Unger Silver Series"))
                seen_macro.add((iso3, yr, "XAG_GRAM"))

            unit = "kg" if str(r._6).strip().lower() == "kg" else "litre"
            price_rows.append((
                "ALLEN_UNGER", "OBSERVED_MARKET", mkt_id, r.commodity_code,
                f"{yr}-06-15", float(r.val), unit, "XAG_GRAM"
            ))

        if mkt_rows:
            etl.ingest_markets_and_macro(
                pd.DataFrame(mkt_rows, columns=["market_id","market_name","country_iso3","region_macro","latitude","longitude","arable_ha_per_cap","industrial_elec_kwh"]),
                pd.DataFrame(macro_rows, columns=["country_iso3","year","currency_code","cpi_to_2026","ppp_to_2026_usd","unskilled_wage_hr","wage_source"])
            )
            etl.ingest_price_stream(pd.DataFrame(
                price_rows,
                columns=["source_dataset","price_basis","market_id","commodity_code","obs_date","raw_price","raw_unit","currency_code"]
            ))

    # 4. Add 2026 Lab & Alternative Protein TEA Benchmarks across Global Hubs
    print("[2026 TEA] Adding calibrated lab-protein benchmarks...")
    hubs = pd.DataFrame([
        ("HUB_SGP", "Singapore", "SGP", "ASIA", 1.352, 103.819, 0.001, 0.16),
        ("HUB_USA", "Chicago / Midwest Hub", "USA", "NORTH_AMERICA", 41.878, -87.629, 0.47, 0.08),
        ("HUB_EUR", "Rotterdam / EU Hub", "NLD", "EUROPE", 51.924, 4.477, 0.06, 0.18),
    ], columns=["market_id","market_name","country_iso3","region_macro","latitude","longitude","arable_ha_per_cap","industrial_elec_kwh"])

    hub_macro = pd.DataFrame([
        ("SGP", 2026, "USD", 1.0, 1.0, 11.50, "MOM Singapore PPP"),
        ("USA", 2026, "USD", 1.0, 1.0, 18.50, "BLS 2026"),
        ("NLD", 2026, "USD", 1.0, 1.0, 19.20, "Eurostat PPP"),
    ], columns=["country_iso3","year","currency_code","cpi_to_2026","ppp_to_2026_usd","unskilled_wage_hr","wage_source"])
    etl.ingest_markets_and_macro(hubs, hub_macro)

    tea_rows = pd.DataFrame([
        ("TEA_BENCHMARK", "OBSERVED_MARKET",  "HUB_USA", "SOY_ISOLATE",  "2026-06-01",  2.85, "kg", "USD"),
        ("TEA_BENCHMARK", "OBSERVED_MARKET",  "HUB_EUR", "PEA_ISOLATE",  "2026-06-01",  3.60, "kg", "USD"),
        ("TEA_BENCHMARK", "OBSERVED_MARKET",  "HUB_EUR", "MYCOPROTEIN",  "2026-06-01",  6.50, "kg", "USD"),
        ("TEA_BENCHMARK", "EX_ANTE_TEA_COGS", "HUB_USA", "PF_WHEY_BLG",  "2026-06-01", 18.00, "kg", "USD"),
        ("TEA_BENCHMARK", "EX_ANTE_TEA_COGS", "HUB_SGP", "PF_CASEIN",    "2026-06-01", 24.00, "kg", "USD"),
        ("TEA_BENCHMARK", "EX_ANTE_TEA_COGS", "HUB_SGP", "CULT_CHICKEN", "2026-06-01", 12.50, "kg", "USD"),
    ], columns=["source_dataset","price_basis","market_id","commodity_code","obs_date","raw_price","raw_unit","currency_code"])
    etl.ingest_price_stream(tea_rows)

    # 5. Filter outliers & export Parquet
    etl.flag_mad_outliers(z_threshold=6.0)
    etl.export_analytical_parquet("protein_atlas_clean.parquet")

    summary = con.execute("""
        SELECT source_dataset, COUNT(*) AS rows, MIN(obs_year) AS min_yr, MAX(obs_year) AS max_yr
        FROM fact_protein_observations
        WHERE is_outlier_mad = FALSE
        GROUP BY 1 ORDER BY rows DESC;
    """).df()
    print("\n=== FINAL PROTEIN ATLAS SUMMARY ===")
    print(summary.to_string(index=False))

if __name__ == "__main__":
    main()
