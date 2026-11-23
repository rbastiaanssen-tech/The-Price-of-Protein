from __future__ import annotations
import json, urllib.request
import duckdb, pandas as pd

con = duckdb.connect("protein_atlas.duckdb")

# Remove any partial rows from prior attempt
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'BLS_APU_REGIONAL';")

US_REGIONS = {
    "0100": ("US_CENSUS_NE", "Northeast Census Region (BLS Urban)", 40.713, -74.006, 0.09),
    "0200": ("US_CENSUS_MW", "Midwest Census Region (BLS Urban)",   41.878, -87.630, 0.85),
    "0300": ("US_CENSUS_SO", "South Census Region (BLS Urban)",     33.749, -84.388, 0.42),
    "0400": ("US_CENSUS_WE", "West Census Region (BLS Urban)",      34.052, -118.244, 0.38),
}

comm_df = con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").df()
eff_map = dict(zip(comm_df["commodity_code"], comm_df["eff_protein_g_kg"]))

# BLS APU item codes & exact metric mass divisors (kg per BLS unit)
BLS_ITEMS = [
    ("703112", "BEEF_BONELESS", 0.45359237), # Ground beef, 100% beef, per lb
    ("706111", "CHICKEN_WHOLE", 0.45359237), # Chicken, fresh, whole, per lb
    ("708111", "EGGS_FRESH",    0.68038855), # Eggs, grade A, large, per doz
    ("709112", "MILK_FRESH",    3.89900000), # Milk, fresh, whole, fortified, per gal
    ("701111", "WHEAT_FLOUR",   0.45359237), # Flour, white, all purpose, per lb
]

series_meta = {}
all_series_ids = ["CES0500000008", "CUUR0000SA0"]
for reg_code in US_REGIONS:
    for suffix, comm_code, kg_div in BLS_ITEMS:
        if comm_code in eff_map:
            sid = f"APU{reg_code}{suffix}"
            all_series_ids.append(sid)
            series_meta[sid] = (reg_code, comm_code, kg_div)

def fetch_bls_chunk(series_ids: list[str], start_yr: int, end_yr: int) -> dict[str, list[dict]]:
    url = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
    payload = json.dumps({
        "seriesid": series_ids,
        "startyear": str(start_yr),
        "endyear": str(end_yr),
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        res = json.loads(resp.read().decode("utf-8"))
    if res.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS API error: {res.get('message')}")
    return {s["seriesID"]: s["data"] for s in res["Results"]["series"]}

print(f"[PHASE 2] Querying official BLS Public API v2 ({len(all_series_ids)} series, 1987–2026)...")
raw_data = {sid: [] for sid in all_series_ids}
for y0, y1 in [(1987, 1996), (1997, 2006), (2007, 2016), (2017, 2026)]:
    chunk = fetch_bls_chunk(all_series_ids, y0, y1)
    for sid, rows in chunk.items():
        raw_data[sid].extend(rows)
    print(f"  -> Fetched {y0}–{y1}")

# Parse annual BLS wages (CES0500000008) & CPI-U (CUUR0000SA0)
def to_annual_map(rows: list[dict]) -> dict[int, float]:
    recs = []
    for r in rows:
        p = r.get("period", "")
        if p.startswith("M") and p != "M13":
            try:
                recs.append((int(r["year"]), float(r["value"])))
            except ValueError:
                pass
    df = pd.DataFrame(recs, columns=["yr", "val"])
    return df.groupby("yr")["val"].mean().to_dict()

ann_wage = to_annual_map(raw_data["CES0500000008"])
ann_cpi = to_annual_map(raw_data["CUUR0000SA0"])
cpi_latest = ann_cpi[max(ann_cpi.keys())]
cpi_mult = {yr: cpi_latest / val for yr, val in ann_cpi.items()}

for reg_code, (mkt_id, mkt_name, lat, lon, arable) in US_REGIONS.items():
    con.execute("""
        INSERT OR REPLACE INTO dim_markets VALUES (?, ?, 'USA', 'NORTH_AMERICA', ?, ?, ?, 0.11);
    """, (mkt_id, mkt_name, lat, lon, arable))

for yr, w_hr in ann_wage.items():
    if yr in cpi_mult:
        con.execute("""
            INSERT OR REPLACE INTO dim_macro_wages (
                country_iso3, year, currency_code, cpi_to_2026, ppp_to_2026_usd, unskilled_wage_hr, wage_source
            ) VALUES ('USA', ?, 'USD', ?, 1.00, ?, 'BLS CES0500000008 Prod & Nonsupervisory');
        """, (yr, cpi_mult[yr], w_hr))

max_id = con.execute("SELECT COALESCE(MAX(obs_id), 0) FROM fact_protein_observations;").fetchone()[0]
obs_rows = []
for sid, (reg_code, comm_code, kg_div) in series_meta.items():
    mkt_id = US_REGIONS[reg_code][0]
    eff_g = eff_map[comm_code]
    for r in raw_data[sid]:
        p = r.get("period", "")
        if not (p.startswith("M") and p != "M13"):
            continue
        yr = int(r["year"])
        mo = int(p[1:])
        if yr not in ann_wage or yr not in cpi_mult:
            continue
        try:
            val = float(r["value"])
        except ValueError:
            continue
        nom_kg = val / kg_div
        real_usd_kg = nom_kg * cpi_mult[yr]
        usd_50g = real_usd_kg * (50.0 / eff_g)
        lmin_50g = 60.0 * (nom_kg / ann_wage[yr]) * (50.0 / eff_g)
        max_id += 1
        obs_rows.append((
            max_id, "BLS_APU_REGIONAL", "OBSERVED_MARKET", mkt_id, comm_code,
            f"{yr:04d}-{mo:02d}-15", nom_kg, "USD", real_usd_kg, usd_50g, lmin_50g, False
        ))

con.executemany("""
    INSERT INTO fact_protein_observations (
        obs_id, source_dataset, price_basis, market_id, commodity_code,
        obs_date, nominal_price_kg, currency_code,
        real_usd_per_kg, usd_per_50g_diaas, labor_min_50g_diaas, is_outlier_mad
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
""", obs_rows)

print(f"\n[PHASE 2 COMPLETE] Inserted {len(obs_rows):,} primary monthly BLS observations across 4 U.S. Census Regions (1987–2026).")
print(con.execute("""
    SELECT market_id, COUNT(*) AS n_obs, MIN(obs_year) AS y0, MAX(obs_year) AS y1,
           ROUND(MEDIAN(usd_per_50g_diaas), 3) AS med_usd_50g,
           ROUND(MEDIAN(labor_min_50g_diaas), 2) AS med_lmin
    FROM fact_protein_observations
    WHERE source_dataset = 'BLS_APU_REGIONAL'
    GROUP BY 1 ORDER BY 1;
""").df().to_string(index=False))
con.close()
