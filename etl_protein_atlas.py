from __future__ import annotations
"""
etl_protein_atlas.py
End-to-end ETL pipeline building the standardized Protein Price Atlas (1750–2026)
in DuckDB + Parquet.

Dependencies:
    pip install duckdb pandas numpy pyarrow
"""

import os
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd


class ProteinAtlasETL:
    def __init__(self, db_path: str = "protein_atlas.duckdb", schema_path: str = "schema.sql"):
        self.db_path = db_path
        self.schema_path = schema_path
        self.con = duckdb.connect(self.db_path)

    def init_schema(self) -> None:
        """Executes the DuckDB DDL schema."""
        with open(self.schema_path, "r", encoding="utf-8") as f:
            self.con.execute(f.read())
        print("[1/6] DuckDB schema initialized.")

    def seed_reference_tables(self) -> None:
        """
        Populates objective biochemical attributes (FAO DIAAS, USDA edible yields,
        shelf life) and historical metrological unit conversions.
        """
        # Objective Biochemical & Physical Lookup Table
        commodities = pd.DataFrame([
            # code, name, regime, is_animal, edible_frac, crude_prot_g_kg, retention, diaas, kcal_kg, shelf_days, dry_frac, ref
            ("BEEF_FRESH",   "Beef (fresh carcass/cuts)",     "ANIMAL_MEAT",            True,  0.82, 200.0, 0.95, 1.12, 2500,    5.0, 0.38, "USDA FDC / FAO 2013"),
            ("PORK_FRESH",   "Pork (fresh cuts)",             "ANIMAL_MEAT",            True,  0.80, 195.0, 0.95, 1.14, 2420,    5.0, 0.39, "USDA FDC / FAO 2013"),
            ("POULTRY_CHIX", "Chicken (broiler/whole)",       "ANIMAL_MEAT",            True,  0.72, 210.0, 0.95, 1.08, 2150,    4.0, 0.34, "USDA FDC / FAO 2013"),
            ("COD_SALTED",   "Salted/Dried Cod (Stockfish)",  "MARINE",                 True,  0.88, 620.0, 0.98, 1.09, 2900,  365.0, 0.84, "USDA FDC 15018"),
            ("MILK_FLUID",   "Cow Milk (whole fluid)",        "ANIMAL_DAIRY_EGG",       True,  1.00,  33.0, 1.00, 1.18,  610,    7.0, 0.13, "FAO 2013 DIAAS=1.18"),
            ("CHEESE_HARD",  "Hard Cheese (Cheddar/Gouda)",   "ANIMAL_DAIRY_EGG",       True,  1.00, 250.0, 1.00, 1.18, 4030,  180.0, 0.63, "USDA FDC / FAO 2013"),
            ("EGGS_CHIX",    "Chicken Eggs (shell)",          "ANIMAL_DAIRY_EGG",       True,  0.89, 126.0, 0.98, 1.13, 1430,   28.0, 0.24, "FAO 2013 DIAAS=1.13"),
            ("LENTILS_DRY",  "Lentils (dried)",               "PLANT_WHOLE",            False, 1.00, 246.0, 0.92, 0.54, 3520,  730.0, 0.90, "FAO 2013 / Marinangeli"),
            ("BEANS_DRY",    "Common Beans / Peas (dried)",   "PLANT_WHOLE",            False, 1.00, 225.0, 0.92, 0.59, 3410,  730.0, 0.89, "FAO 2013 DIAAS=0.59"),
            ("SOYBEANS_DRY", "Soybeans (dried whole)",        "PLANT_WHOLE",            False, 1.00, 365.0, 0.95, 0.91, 4460,  730.0, 0.91, "FAO 2013 DIAAS=0.91"),
            ("SOY_ISOLATE",  "Soy Protein Isolate (90%)",     "PLANT_ISOLATE",          False, 1.00, 880.0, 1.00, 0.90, 3380,  730.0, 0.95, "Rutherfurd et al. 2015"),
            ("PEA_ISOLATE",  "Pea Protein Isolate (85%)",     "PLANT_ISOLATE",          False, 1.00, 820.0, 1.00, 0.82, 3400,  730.0, 0.94, "Rutherfurd et al. 2015"),
            ("MYCOPROTEIN",  "Fermented Mycoprotein (dry)",   "MICROBIAL_BIOMASS",      False, 1.00, 540.0, 1.00, 0.91, 3100,  540.0, 0.94, "Dunlop et al. 2017"),
            ("PF_WHEY_BLG",  "Precision-Fermented Whey/BLG",  "PRECISION_FERMENTATION", True,  1.00, 900.0, 1.00, 1.15, 3600,  730.0, 0.96, "GFI / Perfect Day Specs"),
            ("PF_CASEIN",    "Precision-Fermented Casein",    "PRECISION_FERMENTATION", True,  1.00, 880.0, 1.00, 1.18, 3580,  730.0, 0.95, "CE Delft / GFI TEA"),
            ("CULT_CHICKEN", "Cultivated Chicken Biomass",    "CELLULAR_CULTIVATED",    True,  1.00, 205.0, 0.95, 1.08, 1900,    5.0, 0.32, "CE Delft / Humbird TEA"),
        ], columns=[
            "commodity_code", "commodity_name", "regime_class", "is_animal_source",
            "edible_fraction", "crude_protein_g_kg", "prep_retention", "diaas_score",
            "kcal_per_kg", "shelf_life_days", "dry_matter_fraction", "reference_source"
        ])

        self.con.register("df_comm", commodities)
        self.con.execute("""
            INSERT OR REPLACE INTO dim_commodities (
                commodity_code, commodity_name, regime_class, is_animal_source,
                edible_fraction, crude_protein_g_kg, prep_retention, diaas_score,
                kcal_per_kg, shelf_life_days, dry_matter_fraction, reference_source
            )
            SELECT * FROM df_comm;
        """)

        # Historical & Modern Unit Conversions -> Kilograms
        units = pd.DataFrame([
            ("kg", 1.0, "Metric kilogram"),
            ("100 kg", 100.0, "Quintal / 100 kg"),
            ("mt", 1000.0, "Metric ton"),
            ("g", 0.001, "Gram"),
            ("500 g", 0.5, "Half kilogram"),
            ("lb", 0.45359237, "Imperial / US pound"),
            ("livre", 0.4895, "Pre-metric French livre (Paris)"),
            ("pood", 16.3807, "Imperial Russian pood (40 funt)"),
            ("stone", 6.35029, "British stone (14 lb)"),
            ("arroba", 11.502, "Spanish/Portuguese arroba"),
            ("bushel_peas", 27.2155, "Standard 60-lb pulse bushel"),
            ("liter_milk", 1.03, "Fluid cow milk density ~1.03 kg/L"),
            ("dozen_eggs", 0.68, "12 large eggs (~56.7g each)"),
            ("unit_egg", 0.0567, "Single shell egg"),
        ], columns=["raw_unit", "kg_multiplier", "notes"])

        self.con.register("df_units", units)
        self.con.execute("INSERT OR REPLACE INTO dim_units SELECT * FROM df_units;")
        print("[2/6] Seeded dim_commodities and dim_units.")

    def ingest_markets_and_macro(self, markets_df: pd.DataFrame, macro_df: pd.DataFrame) -> None:
        """Loads geocoded market metadata and country-year wage/CPI/PPP tables."""
        self.con.register("df_mkts", markets_df)
        self.con.execute("INSERT OR REPLACE INTO dim_markets SELECT * FROM df_mkts;")

        self.con.register("df_macro", macro_df)
        self.con.execute("INSERT OR REPLACE INTO dim_macro_wages SELECT * FROM df_macro;")
        print(f"[3/6] Loaded {len(markets_df):,} markets and {len(macro_df):,} macro-wage rows.")

    def ingest_price_stream(self, raw_prices_source: str | pd.DataFrame) -> None:
        """
        Standardizes raw prices (from CSV/Parquet path or DataFrame) into
        fact_protein_observations using vectorized SQL joins inside DuckDB.

        Expected input columns:
            source_dataset, price_basis, market_id, commodity_code,
            obs_date, raw_price, raw_unit, currency_code
        """
        if isinstance(raw_prices_source, str):
            source_sql = f"read_parquet('{raw_prices_source}')" if raw_prices_source.endswith(".parquet") \
                         else f"read_csv_auto('{raw_prices_source}')"
        else:
            self.con.register("df_raw_prices", raw_prices_source)
            source_sql = "df_raw_prices"

        self.con.execute(f"""
            INSERT INTO fact_protein_observations (
                obs_id, source_dataset, price_basis, market_id, commodity_code,
                obs_date, nominal_price_kg, currency_code,
                real_usd_per_kg, usd_per_50g_diaas, labor_min_50g_diaas
            )
            WITH standardized AS (
                SELECT
                    ROW_NUMBER() OVER () + COALESCE(
                        (SELECT MAX(obs_id) FROM fact_protein_observations), 0
                    ) AS obs_id,
                    r.source_dataset,
                    r.price_basis,
                    r.market_id,
                    r.commodity_code,
                    CAST(r.obs_date AS DATE) AS obs_date,
                    (r.raw_price / u.kg_multiplier) AS nominal_price_kg,
                    r.currency_code,
                    c.eff_protein_g_kg,
                    m.cpi_to_2026,
                    m.ppp_to_2026_usd,
                    m.unskilled_wage_hr
                FROM {source_sql} r
                JOIN dim_units u
                  ON LOWER(TRIM(r.raw_unit)) = u.raw_unit
                JOIN dim_commodities c
                  ON r.commodity_code = c.commodity_code
                JOIN dim_markets mk
                  ON r.market_id = mk.market_id
                JOIN dim_macro_wages m
                  ON mk.country_iso3 = m.country_iso3
                 AND EXTRACT(YEAR FROM CAST(r.obs_date AS DATE)) = m.year
                 AND r.currency_code = m.currency_code
                WHERE r.raw_price > 0
            )
            SELECT
                obs_id,
                source_dataset,
                price_basis,
                market_id,
                commodity_code,
                obs_date,
                nominal_price_kg,
                currency_code,
                -- Constant 2026 Int'l PPP USD per kg food
                (nominal_price_kg * cpi_to_2026) / ppp_to_2026_usd AS real_usd_per_kg,
                -- Specific Cost 1: 2026 PPP USD per 50g DIAAS-adjusted protein
                (50.0 / eff_protein_g_kg) * ((nominal_price_kg * cpi_to_2026) / ppp_to_2026_usd) AS usd_per_50g_diaas,
                -- Specific Cost 2: Labor minutes per 50g DIAAS-adjusted protein
                60.0 * (50.0 / eff_protein_g_kg) * (nominal_price_kg / unskilled_wage_hr) AS labor_min_50g_diaas
            FROM standardized;
        """)
        total = self.con.execute("SELECT COUNT(*) FROM fact_protein_observations;").fetchone()[0]
        print(f"[4/6] Standardized fact table now holds {total:,} observations.")

    def flag_mad_outliers(self, z_threshold: float = 6.0) -> None:
        """
        Flags extreme unit-transcription errors within each (market_id, commodity_code, decade)
        using robust Median Absolute Deviation (MAD) in log-price space.
        """
        self.con.execute(f"""
            WITH stats AS (
                SELECT
                    obs_id,
                    market_id,
                    commodity_code,
                    obs_decade,
                    LN(usd_per_50g_diaas) AS log_p,
                    MEDIAN(LN(usd_per_50g_diaas)) OVER (
                        PARTITION BY market_id, commodity_code, obs_decade
                    ) AS med_log_p
                FROM fact_protein_observations
                WHERE price_basis = 'OBSERVED_MARKET'
            ),
            mad_calc AS (
                SELECT
                    obs_id,
                    log_p,
                    med_log_p,
                    MEDIAN(ABS(log_p - med_log_p)) OVER (
                        PARTITION BY market_id, commodity_code, obs_decade
                    ) AS mad
                FROM stats
            )
            UPDATE fact_protein_observations f
            SET is_outlier_mad = TRUE
            FROM mad_calc m
            WHERE f.obs_id = m.obs_id
              AND m.mad > 0
              AND (0.6745 * ABS(m.log_p - m.med_log_p) / m.mad) > {z_threshold};
        """)
        outliers = self.con.execute(
            "SELECT COUNT(*) FROM fact_protein_observations WHERE is_outlier_mad = TRUE;"
        ).fetchone()[0]
        print(f"[5/6] MAD outlier filter flagged {outliers:,} extreme transcription anomalies.")

    def export_analytical_parquet(self, out_path: str = "protein_atlas_clean.parquet") -> None:
        """Exports the denormalized, analysis-ready table to ZSTD-compressed Parquet."""
        self.con.execute(f"""
            COPY (
                SELECT
                    f.obs_id,
                    f.source_dataset,
                    f.price_basis,
                    f.obs_date,
                    f.obs_year,
                    f.obs_decade,
                    f.obs_month,
                    mk.market_id,
                    mk.market_name,
                    mk.country_iso3,
                    mk.region_macro,
                    mk.latitude,
                    mk.longitude,
                    mk.arable_ha_per_cap,
                    mk.industrial_elec_kwh,
                    c.commodity_code,
                    c.commodity_name,
                    c.regime_class,
                    c.is_animal_source,
                    c.diaas_capped,
                    c.eff_protein_g_kg,
                    c.kcal_per_kg,
                    c.shelf_life_days,
                    c.dry_matter_fraction,
                    f.real_usd_per_kg,
                    f.usd_per_50g_diaas,
                    f.labor_min_50g_diaas
                FROM fact_protein_observations f
                JOIN dim_markets mk ON f.market_id = mk.market_id
                JOIN dim_commodities c ON f.commodity_code = c.commodity_code
                WHERE f.is_outlier_mad = FALSE
            ) TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD);
        """)
        print(f"[6/6] Exported clean analytical dataset to {out_path}.")


# ============================================================================
# SMOKE TEST WITH REPRESENTATIVE HISTORICAL, WFP, AND 2026 LAB DATA
# ============================================================================
if __name__ == "__main__":
    etl = ProteinAtlasETL(db_path="protein_atlas.duckdb", schema_path="schema.sql")
    etl.init_schema()
    etl.seed_reference_tables()

    # Sample geocoded markets
    markets = pd.DataFrame([
        ("HIST_LONDON",  "London",    "GBR", "EUROPE",             51.507,  -0.127, 0.09, 0.24),
        ("HIST_CHICAGO", "Chicago",   "USA", "NORTH_AMERICA",      41.878, -87.629, 0.47, 0.08),
        ("WFP_NAIROBI",  "Nairobi",   "KEN", "SUB_SAHARAN_AFRICA", -1.286,  36.817, 0.11, 0.14),
        ("HUB_SGP",      "Singapore", "SGP", "ASIA",                1.352, 103.819, 0.001, 0.16),
    ], columns=[
        "market_id", "market_name", "country_iso3", "region_macro",
        "latitude", "longitude", "arable_ha_per_cap", "industrial_elec_kwh"
    ])

    # Sample macro deflators & unskilled hourly wages
    macro = pd.DataFrame([
        ("GBR", 1780, "GBP", 165.0, 0.78, 0.012, "Allen-Unger / Clark"),
        ("GBR", 2026, "GBP",   1.0, 0.78, 13.50, "ONS"),
        ("USA", 1880, "USD",  31.5, 1.00, 0.140, "BLS Historical"),
        ("USA", 2026, "USD",   1.0, 1.00, 18.50, "BLS"),
        ("KEN", 2026, "KES",   1.0, 48.5, 165.0, "ILO / World Bank"),
        ("SGP", 2026, "SGD",   1.0, 1.34, 14.00, "MOM Singapore"),
    ], columns=[
        "country_iso3", "year", "currency_code", "cpi_to_2026",
        "ppp_to_2026_usd", "unskilled_wage_hr", "wage_source"
    ])

    etl.ingest_markets_and_macro(markets, macro)

    # Sample multi-era price observations (Observed Markets + Ex-Ante TEA)
    sample_prices = pd.DataFrame([
        ("ALLEN_UNGER",   "OBSERVED_MARKET",  "HIST_LONDON",  "BEEF_FRESH",   "1780-06-15",  0.018, "lb",          "GBP"),
        ("ALLEN_UNGER",   "OBSERVED_MARKET",  "HIST_LONDON",  "BEANS_DRY",    "1780-06-15",  0.160, "bushel_peas", "GBP"),
        ("BLS_USDA",      "OBSERVED_MARKET",  "HIST_CHICAGO", "BEEF_FRESH",   "1880-07-01",  0.110, "lb",          "USD"),
        ("BLS_USDA",      "OBSERVED_MARKET",  "HIST_CHICAGO", "POULTRY_CHIX", "2026-06-01",  2.450, "lb",          "USD"),
        ("WFP_MARKET",    "OBSERVED_MARKET",  "WFP_NAIROBI",  "BEANS_DRY",    "2026-06-01", 185.00, "kg",          "KES"),
        ("WFP_MARKET",    "OBSERVED_MARKET",  "WFP_NAIROBI",  "BEEF_FRESH",   "2026-06-01", 680.00, "kg",          "KES"),
        ("TEA_BENCHMARK", "OBSERVED_MARKET",  "HUB_SGP",      "SOY_ISOLATE",  "2026-06-01",   3.80, "kg",          "SGD"),
        ("TEA_BENCHMARK", "EX_ANTE_TEA_COGS", "HUB_SGP",      "PF_CASEIN",    "2026-06-01",  32.00, "kg",          "SGD"),
        ("TEA_BENCHMARK", "EX_ANTE_TEA_COGS", "HUB_SGP",      "CULT_CHICKEN", "2026-06-01",  16.50, "kg",          "SGD"),
    ], columns=[
        "source_dataset", "price_basis", "market_id", "commodity_code",
        "obs_date", "raw_price", "raw_unit", "currency_code"
    ])

    etl.ingest_price_stream(sample_prices)
    etl.flag_mad_outliers()
    etl.export_analytical_parquet("protein_atlas_clean.parquet")

    # Preview standardized GvK coordinates
    preview = etl.con.execute("""
        SELECT obs_year, market_id, commodity_code, price_basis,
               ROUND(usd_per_50g_diaas, 3) AS usd_50g_rda,
               ROUND(labor_min_50g_diaas, 1) AS labor_min_rda
        FROM fact_protein_observations
        ORDER BY obs_year, usd_per_50g_diaas;
    """).df()
    print("\nStandardized GvK Preview:")
    print(preview.to_string(index=False))