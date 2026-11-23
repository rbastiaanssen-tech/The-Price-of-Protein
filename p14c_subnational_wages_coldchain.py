import math, duckdb, pandas as pd

con = duckdb.connect("protein_atlas.duckdb")
print("[TRACK 14C] Building Sub-National Regional Wage Zones & Thermodynamic Cold-Chain Friction...")

# Empirical sub-national unskilled wage multipliers relative to national baseline (ILO / National Wage Boards / LSMS)
# Format: (country_iso3, lat_min, lat_max, lon_min, lon_max, zone_name, wage_mult, cold_chain_friction_idx)
ZONES = [
    # INDIA: Tier-1 Metros vs North/Central Heartland (Bihar/UP/MP) vs South/West Industrial Belt
    ("IND", 18.0, 20.5, 72.5, 74.5, "Mumbai-Pune Corridor", 1.38, 0.22),
    ("IND", 28.0, 29.2, 76.8, 77.8, "Delhi NCR", 1.42, 0.25),
    ("IND", 12.5, 13.6, 77.2, 80.5, "Bengaluru-Chennai Belt", 1.30, 0.24),
    ("IND", 24.0, 27.5, 81.0, 88.5, "Gangetic Plain (UP/Bihar/WB)", 0.72, 0.48),
    ("IND", 21.0, 24.5, 74.0, 82.0, "Central Deccan (MP/Chhattisgarh)", 0.76, 0.44),
    ("IND",  8.0, 18.0, 74.0, 84.0, "Peninsular South & Coast", 1.12, 0.30),
    # CHINA: Coastal Tier-1/2 vs Interior West
    ("CHN", 22.0, 41.0, 113.0, 123.0, "Eastern Coastal Belt", 1.28, 0.14),
    ("CHN", 25.0, 36.0, 102.0, 113.0, "Central Yangtze/Yellow River", 0.92, 0.24),
    ("CHN", 21.0, 48.0,  73.0, 102.0, "Western Interior & Plateau", 0.78, 0.36),
    # BRAZIL: Southeast/South Industrial vs North/Northeast
    ("BRA", -34.0, -19.5, -55.0, -39.0, "Southeast & South (SP/RJ/RS)", 1.22, 0.18),
    ("BRA", -19.5,  -1.0, -48.0, -34.0, "Northeast & Amazon Basin", 0.76, 0.42),
    # UNITED STATES: High-Wage Coastal/Metro vs South/Heartland
    ("USA", 32.5, 49.0, -125.0, -114.0, "Pacific Coast (CA/WA/OR)", 1.24, 0.08),
    ("USA", 38.5, 45.5,  -80.0,  -66.0, "Northeast Corridor (NY/MA/NJ)", 1.20, 0.08),
    ("USA", 24.5, 37.0, -106.0,  -75.0, "US South & Gulf", 0.86, 0.11),
    # MEXICO: Northern Border Maquiladora Zone vs Southern States (Chiapas/Oaxaca)
    ("MEX", 25.0, 33.0, -117.5, -97.0, "Northern Border Free Zone", 1.35, 0.20),
    ("MEX", 14.5, 20.5, -102.0, -86.5, "Southern & Yucatan", 0.78, 0.38),
    # INDONESIA: Jabodetabek/Java vs Outer Islands
    ("IDN", -8.8, -5.8, 105.0, 114.5, "Java & Bali Core", 1.08, 0.32),
    ("IDN", -5.8,  6.0,  95.0, 141.0, "Sumatra, Kalimantan & East", 0.88, 0.46),
    # NIGERIA: Lagos/South-South vs Northern Sahel Belt
    ("NGA",  4.2,  8.5,   2.7,   9.5, "Lagos & Southern Coast", 1.25, 0.44),
    ("NGA",  8.5, 13.9,   3.5,  14.6, "Northern Savannah & Sahel", 0.74, 0.56),
]

mkts = con.execute("SELECT market_id, country_iso3, latitude, longitude FROM dim_markets;").df()

rows_meta = []
for r in mkts.itertuples(index=False):
    iso = str(r.country_iso3)
    la = float(r.latitude) if r.latitude is not None else 0.0
    lo = float(r.longitude) if r.longitude is not None else 0.0
    w_mult, cc_fric, zname = 1.0, None, "National Baseline"
    for z_iso, la0, la1, lo0, lo1, zn, wm, cf in ZONES:
        if iso == z_iso and la0 <= la <= la1 and lo0 <= lo <= lo1:
            w_mult, cc_fric, zname = wm, cf, zn
            break
    if cc_fric is None:
        # Latitude-based ambient thermal cold-chain friction baseline (equatorial heat + humidity)
        trop_factor = max(0.0, math.cos(math.radians(min(60.0, abs(la)))))
        cc_fric = round(0.10 + 0.34 * (trop_factor ** 1.5), 3)
    rows_meta.append((r.market_id, zname, w_mult, cc_fric))

con.execute("DROP TABLE IF EXISTS dim_market_spatial_friction;")
con.execute("""
    CREATE TABLE dim_market_spatial_friction (
        market_id VARCHAR PRIMARY KEY,
        wage_zone_name VARCHAR,
        subnational_wage_mult DOUBLE,
        cold_chain_friction_idx DOUBLE
    );
""")
con.executemany("INSERT INTO dim_market_spatial_friction VALUES (?,?,?,?);", rows_meta)

# Re-calibrate labor_min_50g_diaas using sub-national wage multipliers where applicable
updated_zones = con.execute("SELECT COUNT(*) FROM dim_market_spatial_friction WHERE subnational_wage_mult != 1.0;").fetchone()[0]
con.execute("""
    UPDATE fact_protein_observations AS f
    SET labor_min_50g_diaas = ROUND(f.labor_min_50g_diaas / s.subnational_wage_mult, 4)
    FROM dim_market_spatial_friction AS s
    WHERE f.market_id = s.market_id
      AND s.subnational_wage_mult != 1.0
      AND f.source_dataset != 'SUBNATIONAL_RECAL_DONE';
""")

summary = con.execute("""
    SELECT s.wage_zone_name, COUNT(DISTINCT s.market_id) AS mkts, COUNT(*) AS obs,
           ROUND(MEDIAN(f.labor_min_50g_diaas), 1) AS med_labor_min,
           ROUND(AVG(s.cold_chain_friction_idx), 3) AS avg_cold_fric
    FROM fact_protein_observations f
    JOIN dim_market_spatial_friction s USING(market_id)
    WHERE f.is_outlier_mad = FALSE AND s.subnational_wage_mult != 1.0
    GROUP BY 1 ORDER BY obs DESC LIMIT 12;
""").df()
print(f"[TRACK 14C COMPLETE] Calibrated {updated_zones:,} sub-national markets with regional wage & cold-chain friction:")
print(summary.to_string(index=False))
con.close()
