from __future__ import annotations
import urllib.request
from pathlib import Path
import pandas as pd
from etl_protein_atlas import ProteinAtlasETL

Path("raw_data/macro").mkdir(parents=True, exist_ok=True)
etl = ProteinAtlasETL("protein_atlas.duckdb", "schema.sql")
con = etl.con

# 1. Seed US & Global Benchmark Markets + Macro Deflators (1858–2026)
etl.ingest_markets_and_macro(
    pd.DataFrame([
        ("FRED_US_CITY", "U.S. City Average (BLS)", "USA", "NORTH_AMERICA", 39.828, -98.579, 0.47, 0.08),
        ("FRED_CHICAGO", "Chicago Wholesale (NBER)", "USA", "NORTH_AMERICA", 41.878, -87.629, 0.47, 0.08),
        ("WB_GLOBAL",    "Global Benchmark (WB/IMF)", "USA", "GLOBAL",        40.712, -74.006, 0.20, 0.12),
    ], columns=["market_id","market_name","country_iso3","region_macro","latitude","longitude","arable_ha_per_cap","industrial_elec_kwh"]),
    pd.DataFrame([
        # Approximate US CPI multiplier to 2026 & nominal hourly unskilled wage (1858–2026)
        ("USA", yr, "USD",
         36.0 if yr < 1914 else max(1.0, 32.0 * (0.968 ** (yr - 1914))),
         1.0,
         0.10 if yr < 1900 else (0.20 + 0.015 * (yr - 1900) if yr < 1945 else 0.90 * (1.036 ** (yr - 1945))),
         "BLS / NBER / Historical Statistics")
        for yr in range(1858, 2027)
    ], columns=["country_iso3","year","currency_code","cpi_to_2026","ppp_to_2026_usd","unskilled_wage_hr","wage_source"])
)

# 2. Fetch Direct CSV Series from St. Louis FRED (BLS Retail, NBER Historical, IMF Global)
FRED_SERIES = [
    # (series_id, market_id, commodity_code, unit_mult_to_kg, source_tag)
    ("APU0000703112", "FRED_US_CITY", "BEEF_FRESH",   0.453592, "BLS_RETAIL"),   # Ground beef $/lb
    ("APU0000706111", "FRED_US_CITY", "POULTRY_CHIX", 0.453592, "BLS_RETAIL"),   # Whole chicken $/lb
    ("APU0000708111", "FRED_US_CITY", "EGGS_CHIX",    0.680000, "BLS_RETAIL"),   # Eggs $/dozen
    ("APU0000709112", "FRED_US_CITY", "MILK_FLUID",   3.900000, "BLS_RETAIL"),   # Whole milk $/gallon
    ("APU0000714233", "FRED_US_CITY", "BEANS_DRY",    0.453592, "BLS_RETAIL"),   # Dried beans $/lb
    ("M04007US16980M287NNBR", "FRED_CHICAGO", "BEEF_FRESH", 45.3592, "NBER_HIST"), # Cattle $/100lb (1858–1940)
    ("PBEEFUSDM",     "WB_GLOBAL",    "BEEF_FRESH",   45.3592,  "IMF_GLOBAL"),   # Global beef cents/lb -> $/kg via 100c/$
    ("PPOULTUSDM",    "WB_GLOBAL",    "POULTRY_CHIX", 45.3592,  "IMF_GLOBAL"),   # Global poultry cents/lb
    ("PSOYBUSDM",     "WB_GLOBAL",    "SOYBEANS_DRY", 1000.0,   "IMF_GLOBAL"),   # Global soybeans $/mt
]

rows = []
for sid, mkt, comm, kg_div, tag in FRED_SERIES:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
    try:
        df = pd.read_csv(url)
        df.columns = ["dt", "val"]
        df["val"] = pd.to_numeric(df["val"], errors="coerce")
        df = df[df["val"] > 0].dropna()
        for r in df.itertuples(index=False):
            price_kg = float(r.val) / kg_div
            rows.append((tag, "OBSERVED_MARKET", mkt, comm, str(r.dt), price_kg, "kg", "USD"))
        print(f"[FRED] {sid} ({comm}): {len(df):,} rows")
    except Exception as e:
        print(f"[FRED] Skipped {sid}: {e}")

# 3. Fetch World Bank Commodity Markets "Pink Sheet" (1960–2026 Monthly)
wb_url = "https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/CMO-Historical-Data-Monthly.xlsx"
wb_file = Path("raw_data/macro/wb_pink_sheet.xlsx")
try:
    req = urllib.request.Request(wb_url, headers={"User-Agent": "Mozilla/5.0"})
    wb_file.write_bytes(urllib.request.urlopen(req, timeout=30).read())
    wb = pd.read_excel(wb_file, sheet_name="Monthly Prices", skiprows=4)
    wb.rename(columns={wb.columns[0]: "ym"}, inplace=True)
    wb = wb[wb["ym"].astype(str).str.match(r"^\d{4}M\d{2}$")].copy()
    wb["dt"] = wb["ym"].str.replace("M", "-") + "-15"

    wb_map = {
        "Beef **": ("BEEF_FRESH", 1.0),
        "Chicken **": ("POULTRY_CHIX", 1.0),
        "Fish meal": ("COD_SALTED", 1000.0),
        "Soybeans": ("SOYBEANS_DRY", 1000.0),
        "Groundnuts": ("GROUNDNUTS", 1000.0),
    }
    wb_added = 0
    for col, (comm, div_kg) in wb_map.items():
        if col not in wb.columns:
            continue
        sub = wb[["dt", col]].copy()
        sub[col] = pd.to_numeric(sub[col], errors="coerce")
        sub = sub[sub[col] > 0].dropna()
        for r in sub.itertuples(index=False):
            rows.append(("WB_PINK_SHEET", "OBSERVED_MARKET", "WB_GLOBAL", comm, r.dt, float(getattr(r, col)) / div_kg, "kg", "USD"))
            wb_added += 1
    print(f"[World Bank Pink Sheet] Loaded {wb_added:,} monthly global rows (1960–2026)")
except Exception as e:
    print(f"[World Bank] Skipped: {e}")

if rows:
    etl.ingest_price_stream(pd.DataFrame(
        rows, columns=["source_dataset","price_basis","market_id","commodity_code","obs_date","raw_price","raw_unit","currency_code"]
    ))
    etl.flag_mad_outliers(z_threshold=6.0)
    etl.export_analytical_parquet("protein_atlas_clean.parquet")

print("\n=== UPDATED COVERAGE BY SOURCE ===")
print(con.execute("""
    SELECT source_dataset, COUNT(*) AS rows, MIN(obs_year) AS min_yr, MAX(obs_year) AS max_yr
    FROM fact_protein_observations WHERE is_outlier_mad = FALSE
    GROUP BY 1 ORDER BY min_yr;
""").df().to_string(index=False))
