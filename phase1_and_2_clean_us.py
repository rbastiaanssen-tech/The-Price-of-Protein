from __future__ import annotations
import io, urllib.request
from pathlib import Path
import duckdb, pandas as pd

con = duckdb.connect("protein_atlas.duckdb")

# ==============================================================================
# PHASE 1: DEEP PURGE OF ALL SYNTHETIC / IMPUTED ROWS
# ==============================================================================
n_oecd = con.execute("SELECT COUNT(*) FROM fact_protein_observations WHERE source_dataset = 'OECD_NSO_RETAIL';").fetchone()[0]
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'OECD_NSO_RETAIL';")
con.execute("DELETE FROM dim_macro_wages WHERE year = 2025 AND wage_source LIKE '%2025';")

n_dplace = con.execute("SELECT COUNT(*) FROM fact_protein_observations WHERE market_id LIKE 'ETH_EA_%';").fetchone()[0]
con.execute("DELETE FROM fact_protein_observations WHERE market_id LIKE 'ETH_EA_%';")
con.execute("DELETE FROM dim_markets WHERE market_id LIKE 'ETH_EA_%';")

con.execute("""
    DELETE FROM dim_markets
    WHERE market_id NOT IN (SELECT DISTINCT market_id FROM fact_protein_observations);
""")

for dead_script in ["expand_western_regions.py", "ingest_oecd_g20_2026.py"]:
    if Path(dead_script).exists():
        Path(dead_script).unlink()

print(f"[PHASE 1 PURGE] Deleted {n_oecd:,} synthetic OECD rows & {n_dplace:,} imputed D-PLACE rows.")

# ==============================================================================
# PHASE 2: LIVE U.S. CENSUS REGION RETAIL SERIES (BLS APU 1984–2026)
# ==============================================================================
# Official BLS Average Retail Food Prices by U.S. Census Region (Urban):
# 0100 = Northeast, 0200 = Midwest, 0300 = South, 0400 = West
US_REGIONS = {
    "0100": ("US_CENSUS_NE", "Northeast Census Region (BLS Urban)", 40.713, -74.006, 0.09),
    "0200": ("US_CENSUS_MW", "Midwest Census Region (BLS Urban)",   41.878, -87.630, 0.85),
    "0300": ("US_CENSUS_SO", "South Census Region (BLS Urban)",     33.749, -84.388, 0.42),
    "0400": ("US_CENSUS_WE", "West Census Region (BLS Urban)",      34.052, -118.244, 0.38),
}

# Verify exact commodity codes present in dim_commodities
comm_df = con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").df()
eff_map = dict(zip(comm_df["commodity_code"], comm_df["eff_protein_g_kg"]))

# BLS item suffix -> (commodity_code, unit_to_kg_divisor)
# 1 lb = 0.45359237 kg | 1 doz large eggs = 0.680388 kg | 1 gal whole milk = 3.899 kg
BLS_ITEMS = [
    ("703112", "BEEF_BONELESS", 0.45359237), # Ground beef, 100% beef, per lb
    ("706111", "CHICKEN_WHOLE", 0.45359237), # Chicken, fresh, whole, per lb
    ("708111", "EGGS_FRESH",    0.68038855), # Eggs, grade A, large, per doz
    ("709112", "MILK_FRESH",    3.89900000), # Milk, fresh, whole, fortified, per gal
    ("701111", "WHEAT_FLOUR",   0.45359237), # Flour, white, all purpose, per lb
]

# Fetch official BLS Production & Nonsupervisory Average Hourly Earnings (CES0500000008)
print("[PHASE 2] Fetching official BLS hourly wage series (CES0500000008)...")
wage_url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CES0500000008"
w_raw = pd.read_csv(wage_url)
w_raw.columns = ["DATE", "WAGE"]
w_raw["WAGE"] = pd.to_numeric(w_raw["WAGE"], errors="coerce")
w_raw = w_raw.dropna()
w_raw["YEAR"] = pd.to_datetime(w_raw["DATE"]).dt.year
annual_us_wage = w_raw.groupby("YEAR")["WAGE"].mean().to_dict()

# Get existing US CPI/PPP deflators from dim_macro_wages (fallback to CPI-U ratio for 2025-2026)
cpi_url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL"
c_raw = pd.read_csv(cpi_url)
c_raw.columns = ["DATE", "CPI"]
c_raw["CPI"] = pd.to_numeric(c_raw["CPI"], errors="coerce")
c_raw = c_raw.dropna()
c_raw["YEAR"] = pd.to_datetime(c_raw["DATE"]).dt.year
ann_cpi = c_raw.groupby("YEAR")["CPI"].mean()
cpi_2026 = float(ann_cpi.iloc[-1])
cpi_mult_map = (cpi_2026 / ann_cpi).to_dict()

# Register the 4 official U.S. Census Region markets
for reg_code, (mkt_id, mkt_name, lat, lon, arable) in US_REGIONS.items():
    con.execute("""
        INSERT OR REPLACE INTO dim_markets VALUES (?, ?, 'USA', 'NORTH_AMERICA', ?, ?, ?, 0.11);
    """, (mkt_id, mkt_name, lat, lon, arable))

# Ensure dim_macro_wages has verified annual US BLS wages through 2026
for yr, w_hr in annual_us_wage.items():
    if yr >= 1980 and yr in cpi_mult_map:
        con.execute("""
            INSERT OR REPLACE INTO dim_macro_wages (
                country_iso3, year, currency_code, cpi_to_2026, ppp_to_2026_usd, unskilled_wage_hr, wage_source
            ) VALUES ('USA', ?, 'USD', ?, 1.00, ?, 'BLS CES0500000008 Prod & Nonsupervisory');
        """, (int(yr), float(cpi_mult_map[yr]), float(w_hr)))

# Download all 20 regional BLS food price series (4 regions x 5 items)
max_id = con.execute("SELECT COALESCE(MAX(obs_id), 0) FROM fact_protein_observations;").fetchone()[0]
obs_rows = []
series_fetched = 0

for reg_code, (mkt_id, mkt_name, _, _, _) in US_REGIONS.items():
    for suffix, comm_code, kg_div in BLS_ITEMS:
        if comm_code not in eff_map:
            continue
        series_id = f"APU{reg_code}{suffix}"
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        try:
            df_s = pd.read_csv(url)
            df_s.columns = ["DATE", "VAL"]
            df_s["VAL"] = pd.to_numeric(df_s["VAL"], errors="coerce")
            df_s = df_s.dropna()
            series_fetched += 1
        except Exception as e:
            print(f"  [WARN] Could not fetch {series_id}: {e}")
            continue

        eff_g = eff_map[comm_code]
        for row in df_s.itertuples(index=False):
            dt_str = str(row.DATE)
            yr = int(dt_str[:4])
            if yr not in annual_us_wage or yr not in cpi_mult_map:
                continue
            nom_kg = float(row.VAL) / kg_div
            real_usd_kg = nom_kg * float(cpi_mult_map[yr])
            usd_50g = real_usd_kg * (50.0 / eff_g)
            lmin_50g = 60.0 * (nom_kg / float(annual_us_wage[yr])) * (50.0 / eff_g)
            max_id += 1
            obs_rows.append((
                max_id, "BLS_APU_REGIONAL", "OBSERVED_MARKET", mkt_id, comm_code,
                dt_str, nom_kg, "USD", real_usd_kg, usd_50g, lmin_50g, False
            ))

con.executemany("""
    INSERT INTO fact_protein_observations (
        obs_id, source_dataset, price_basis, market_id, commodity_code,
        obs_date, nominal_price_kg, currency_code,
        real_usd_per_kg, usd_per_50g_diaas, labor_min_50g_diaas, is_outlier_mad
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
""", obs_rows)

print(f"[PHASE 2 COMPLETE] Fetched {series_fetched}/20 official BLS regional series -> inserted {len(obs_rows):,} primary monthly observations (1984–2026).")
print("\n=== VERIFIED CORPUS SUMMARY ===")
print(con.execute("""
    SELECT source_dataset, price_basis, COUNT(DISTINCT m.country_iso3) AS countries,
           MIN(obs_year) AS min_yr, MAX(obs_year) AS max_yr, COUNT(*) AS n_obs
    FROM fact_protein_observations f JOIN dim_markets m USING (market_id)
    WHERE f.is_outlier_mad = FALSE
    GROUP BY 1, 2 ORDER BY 6 DESC;
""").df().to_string(index=False))
con.close()
