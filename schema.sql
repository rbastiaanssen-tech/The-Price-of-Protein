-- ============================================================================
-- PROTEIN PRICE ATLAS (1750–2026) — DUCKDB SCHEMA
-- ============================================================================


-- 1. Dimension: Standardized Protein Delivery Vehicles (Foods & Bioreactor Regimes)
CREATE TABLE IF NOT EXISTS dim_commodities (
    commodity_code      VARCHAR PRIMARY KEY,   -- e.g., 'BEEF_FRESH', 'LENTILS_DRY', 'PF_CASEIN'
    commodity_name      VARCHAR NOT NULL,
    regime_class        VARCHAR NOT NULL,      -- 'PLANT_WHOLE', 'ANIMAL_MEAT', 'ANIMAL_DAIRY_EGG',
                                               -- 'MARINE', 'PLANT_ISOLATE', 'MICROBIAL_BIOMASS',
                                               -- 'PRECISION_FERMENTATION', 'CELLULAR_CULTIVATED'
    is_animal_source    BOOLEAN NOT NULL,
    edible_fraction     DOUBLE NOT NULL CHECK (edible_fraction > 0 AND edible_fraction <= 1.0),
    crude_protein_g_kg  DOUBLE NOT NULL CHECK (crude_protein_g_kg > 0), -- g protein per kg edible weight
    prep_retention      DOUBLE NOT NULL DEFAULT 1.0 CHECK (prep_retention > 0 AND prep_retention <= 1.0),
    diaas_score         DOUBLE NOT NULL CHECK (diaas_score > 0),        -- FAO DIAAS ratio (e.g., 1.18 milk, 0.54 lentils)
    diaas_capped        DOUBLE GENERATED ALWAYS AS (LEAST(diaas_score, 1.0)),
    eff_protein_g_kg    DOUBLE GENERATED ALWAYS AS (
                            edible_fraction * crude_protein_g_kg * prep_retention * LEAST(diaas_score, 1.0)
                        ),
    kcal_per_kg         DOUBLE NOT NULL,       -- Needed for Stigler shadow-price LP matrix
    shelf_life_days     DOUBLE NOT NULL,       -- Objective GvK X-axis 1 (ambient or standard cold-chain)
    dry_matter_fraction DOUBLE NOT NULL,       -- Objective GvK X-axis 2 (protein density per kg dry mass)
    reference_source    VARCHAR                -- e.g., 'USDA FDC 171477 / FAO 2013 DIAAS Report'
);

-- 2. Dimension: Geocoded Markets & Agro-Ecological Endowments
CREATE TABLE IF NOT EXISTS dim_markets (
    market_id           VARCHAR PRIMARY KEY,   -- e.g., 'HIST_LONDON_UK', 'WFP_KEN_NAIROBI_102'
    market_name         VARCHAR NOT NULL,
    country_iso3        VARCHAR(3) NOT NULL,
    region_macro        VARCHAR NOT NULL,      -- 'EUROPE', 'NORTH_AMERICA', 'LATAM', 'SUB_SAHARAN_AFRICA', 'ASIA', 'MENA'
    latitude            DOUBLE NOT NULL,
    longitude           DOUBLE NOT NULL,
    arable_ha_per_cap   DOUBLE,                -- FAO GAEZ / World Bank endowment covariate
    industrial_elec_kwh DOUBLE                 -- Local industrial electricity cost ($/kWh) for TEA overlays
);

-- 3. Dimension: Historical & Modern Metrological Unit Conversions -> Kilograms
CREATE TABLE IF NOT EXISTS dim_units (
    raw_unit            VARCHAR PRIMARY KEY,   -- Lowercase trimmed raw unit string
    kg_multiplier       DOUBLE NOT NULL,       -- Multiply raw price by (1 / kg_multiplier) to get price per kg
    notes               VARCHAR
);

-- 4. Bridge: Currency Deflators, PPP Factors & Unskilled Urban Wages
CREATE TABLE IF NOT EXISTS dim_macro_wages (
    country_iso3        VARCHAR(3) NOT NULL,
    year                INTEGER NOT NULL,
    currency_code       VARCHAR(10) NOT NULL,
    cpi_to_2026         DOUBLE NOT NULL,       -- Multiplier converting nominal local currency to 2026 local currency
    ppp_to_2026_usd     DOUBLE NOT NULL,       -- Divisor converting 2026 local currency to 2026 Int'l PPP USD
    unskilled_wage_hr   DOUBLE NOT NULL,       -- Nominal local hourly wage of unskilled urban worker
    wage_source         VARCHAR,               -- 'Allen-Unger', 'Clark', 'BLS', 'ILO'
    PRIMARY KEY (country_iso3, year, currency_code)
);

-- 5. Fact Table: Standardized Protein Price Observations
CREATE TABLE IF NOT EXISTS fact_protein_observations (
    obs_id              BIGINT PRIMARY KEY,
    source_dataset      VARCHAR NOT NULL,      -- 'ALLEN_UNGER', 'BLS_USDA', 'WFP_MARKET', 'TEA_BENCHMARK'
    price_basis         VARCHAR NOT NULL,      -- 'OBSERVED_MARKET' vs 'EX_ANTE_TEA_COGS' (Referee Guardrail #1)
    market_id           VARCHAR NOT NULL REFERENCES dim_markets(market_id),
    commodity_code      VARCHAR NOT NULL REFERENCES dim_commodities(commodity_code),
    obs_date            DATE NOT NULL,
    obs_year            INTEGER GENERATED ALWAYS AS (EXTRACT(YEAR FROM obs_date)),
    obs_decade          INTEGER GENERATED ALWAYS AS ((EXTRACT(YEAR FROM obs_date) // 10) * 10),
    obs_month           INTEGER GENERATED ALWAYS AS (EXTRACT(MONTH FROM obs_date)),
    nominal_price_kg    DOUBLE NOT NULL,       -- Standardized to nominal local currency per kg
    currency_code       VARCHAR(10) NOT NULL,
    real_usd_per_kg     DOUBLE NOT NULL,       -- Constant 2026 PPP USD per kg food
    usd_per_50g_diaas   DOUBLE NOT NULL,       -- GvK Y-axis 1: 2026 PPP USD per 50g bioavailable protein
    labor_min_50g_diaas DOUBLE NOT NULL,       -- GvK Y-axis 2: Unskilled labor minutes per 50g bioavailable protein
    is_outlier_mad      BOOLEAN DEFAULT FALSE  -- Flagged via within-series rolling MAD filter
);

-- Analytical Indexes for Fast GvK & Spatiotemporal Queries
CREATE INDEX IF NOT EXISTS idx_fact_decade_regime ON fact_protein_observations(obs_decade, commodity_code);
CREATE INDEX IF NOT EXISTS idx_fact_market_date   ON fact_protein_observations(market_id, obs_date);
CREATE INDEX IF NOT EXISTS idx_fact_basis_outlier ON fact_protein_observations(price_basis, is_outlier_mad);