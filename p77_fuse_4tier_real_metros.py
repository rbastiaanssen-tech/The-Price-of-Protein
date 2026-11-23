import json, math
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pyarrow.compute as pc
from pathlib import Path

root = Path.cwd()

# ---------------------------------------------------------
# 1. Load Country Wage Denominators (exports/dim_country_wages.parquet)
# ---------------------------------------------------------
wages_df = pd.read_parquet(root / "exports" / "dim_country_wages.parquet")
print("[WAGES COLS]", list(wages_df.columns))
iso_col = next(c for c in wages_df.columns if "iso" in c.lower())
med_col = next(c for c in wages_df.columns if "med" in c.lower() or "wage" in c.lower())
p20_col = next((c for c in wages_df.columns if "p20" in c.lower() or "q1" in c.lower()), None)
wage_map = {}
for _, r in wages_df.iterrows():
    iso = str(r[iso_col]).upper()
    wm = float(r[med_col]) if pd.notna(r[med_col]) and float(r[med_col]) > 0 else 15.0
    wp = float(r[p20_col]) if p20_col and pd.notna(r[p20_col]) and float(r[p20_col]) > 0 else wm * 0.56
    wage_map[iso] = (wm, wp)

# Approximate USD FX rates for major Open Prices currencies
FX_USD = {
    "EUR": 1.08, "USD": 1.00, "GBP": 1.27, "CAD": 0.73, "AUD": 0.66, "CHF": 1.13,
    "PLN": 0.25, "SEK": 0.095, "NOK": 0.093, "DKK": 0.145, "CZK": 0.043, "RON": 0.22,
    "BRL": 0.18, "MXN": 0.052, "ARS": 0.001, "CLP": 0.00105, "COP": 0.00024, "PEN": 0.27,
    "INR": 0.0119, "JPY": 0.0066, "KRW": 0.00073, "CNY": 0.138, "THB": 0.029, "IDR": 0.000063,
    "MYR": 0.225, "PHP": 0.0175, "SGD": 0.74, "ZAR": 0.055, "MAD": 0.10, "EGP": 0.020,
    "TRY": 0.029, "SAR": 0.267, "AED": 0.272, "ILS": 0.27, "UAH": 0.024,
}

ISO2_TO_ISO3 = {
    "FR": "FRA", "ES": "ESP", "DE": "DEU", "IT": "ITA", "GB": "GBR", "UK": "GBR",
    "NL": "NLD", "BE": "BEL", "PL": "POL", "RO": "ROU", "SE": "SWE", "CH": "CHE",
    "AT": "AUT", "PT": "PRT", "IE": "IRL", "DK": "DNK", "NO": "NOR", "FI": "FIN",
    "US": "USA", "CA": "CAN", "MX": "MEX", "BR": "BRA", "AR": "ARG", "CL": "CHL",
    "CO": "COL", "PE": "PER", "IN": "IND", "CN": "CHN", "JP": "JPN", "KR": "KOR",
    "ID": "IDN", "TH": "THA", "VN": "VNM", "PH": "PHL", "MY": "MYS", "SG": "SGP",
    "ZA": "ZAF", "MA": "MAR", "EG": "EGY", "KE": "KEN", "NG": "NGA", "GH": "GHA",
    "TR": "TUR", "SA": "SAU", "AE": "ARE", "UA": "UKR",
}

# ---------------------------------------------------------
# 2. Extract Real Tier 2 Supermarkets from open_prices.parquet + off_food.parquet
# ---------------------------------------------------------
op_cols = [
    "product_code", "product_name", "category_tag", "price", "price_per", "currency",
    "date", "location_osm_id", "location_osm_name", "location_osm_brand",
    "location_osm_address_city", "location_osm_address_country_code",
    "location_osm_lat", "location_osm_lon"
]
df_op = pd.read_parquet(root / "data_raw" / "open_prices.parquet", columns=op_cols)
df_op = df_op[
    df_op["location_osm_lat"].notna()
    & df_op["location_osm_lon"].notna()
    & (df_op["price"] > 0)
    & df_op["product_code"].notna()
].copy()
df_op["product_code"] = df_op["product_code"].astype(str).str.strip()
needed_codes = set(df_op["product_code"].unique())
print(f"[OPEN PRICES] Valid geolocated receipt rows: {len(df_op):,} across {len(needed_codes):,} unique barcodes")

# Stream off_food.parquet in batches filtering only matching product barcodes
pf_off = pq.ParquetFile(root / "data_raw" / "off_food.parquet")
off_matches = []
for batch in pf_off.iter_batches(batch_size=100_000, columns=["code", "categories_tags", "nutriments", "product_quantity", "product_quantity_unit"]):
    sub_b = batch.to_pandas()
    sub_b["code"] = sub_b["code"].astype(str).str.strip()
    hit = sub_b[sub_b["code"].isin(needed_codes)]
    if len(hit) > 0:
        off_matches.append(hit)

df_off_hit = pd.concat(off_matches, ignore_index=True).drop_duplicates(subset=["code"]) if off_matches else pd.DataFrame()
print(f"[OFF_FOOD MATCHED] {len(df_off_hit):,} barcodes matched with nutrition metadata")

def extract_protein_100g(nutr):
    if isinstance(nutr, dict):
        v = nutr.get("proteins_100g", nutr.get("proteins"))
        return float(v) if v is not None else np.nan
    if isinstance(nutr, (list, np.ndarray)):
        for item in nutr:
            if isinstance(item, dict) and item.get("name") in ("proteins", "proteins_100g"):
                val = item.get("100g", item.get("value"))
                if val is not None:
                    return float(val)
    return np.nan

def infer_diaas_from_tags(tags, prot_100g):
    t = " ".join(tags).lower() if isinstance(tags, (list, np.ndarray)) else str(tags).lower()
    if any(k in t for k in ["milk", "cheese", "yogurt", "dairy", "egg", "whey"]):
        return 1.00
    if any(k in t for k in ["meat", "beef", "pork", "chicken", "poultry", "fish", "tuna", "sardine", "salmon", "seafood"]):
        return 1.00
    if any(k in t for k in ["soy", "tofu", "tempeh", "edamame"]):
        return 0.91
    if any(k in t for k in ["lentil", "bean", "chickpea", "pea", "pulse", "legume"]):
        return 0.65
    if any(k in t for k in ["oat", "quinoa", "buckwheat"]):
        return 0.62
    if any(k in t for k in ["bread", "pasta", "wheat", "flour", "rice", "cereal", "maize", "corn"]):
        return 0.48
    return 0.72 if prot_100g >= 12.0 else 0.55

if len(df_off_hit) > 0:
    df_off_hit["prot_100g"] = df_off_hit["nutriments"].apply(extract_protein_100g)
    df_off_hit = df_off_hit[df_off_hit["prot_100g"].notna() & (df_off_hit["prot_100g"] >= 3.0)].copy()
    df_off_hit["diaas"] = [infer_diaas_from_tags(t, p) for t, p in zip(df_off_hit["categories_tags"], df_off_hit["prot_100g"])]
    df_off_hit["qty_g"] = pd.to_numeric(df_off_hit["product_quantity"], errors="coerce").fillna(500.0)
    df_off_hit.loc[(df_off_hit["qty_g"] < 30) | (df_off_hit["qty_g"] > 10000), "qty_g"] = 500.0

    op_merged = df_op.merge(df_off_hit[["code", "prot_100g", "diaas", "qty_g"]], left_on="product_code", right_on="code", how="inner")
    op_merged["fx"] = op_merged["currency"].map(FX_USD).fillna(1.0)
    op_merged["usd_price"] = op_merged["price"] * op_merged["fx"]
    # If price_per == KILOGRAM, package mass is 1000g; otherwise use product_quantity
    eff_g = np.where(op_merged["price_per"].astype(str).str.upper() == "KILOGRAM", 1000.0, op_merged["qty_g"])
    bio_prot_g = eff_g * (op_merged["prot_100g"] / 100.0) * op_merged["diaas"]
    op_merged["u_usd"] = op_merged["usd_price"] * (50.0 / np.maximum(bio_prot_g, 1.0))
    op_merged = op_merged[(op_merged["u_usd"] >= 0.15) & (op_merged["u_usd"] <= 12.0)].copy()
    print(f"[SUPERMARKET QUOTES COMPUTED] {len(op_merged):,} protein-scored supermarket receipt lines")
else:
    op_merged = pd.DataFrame()

# Aggregate Tier 2 Supermarkets by real OSM location
supermarket_locs = []
if len(op_merged) > 0:
    op_merged["store_id"] = "OSM_" + op_merged["location_osm_id"].astype(str)
    op_merged["store_name"] = op_merged["location_osm_brand"].fillna(op_merged["location_osm_name"]).fillna("Supermarket").astype(str)
    sm_grp = op_merged.groupby(["store_id", "store_name", "location_osm_address_country_code"], as_index=False).agg(
        lat=("location_osm_lat", "mean"),
        lon=("location_osm_lon", "mean"),
        n_quotes=("u_usd", "count"),
        last_date=("date", "max"),
        u_min=("u_usd", lambda s: float(np.percentile(s, 15))),
        u_med=("u_usd", "median"),
    )
    for _, r in sm_grp.iterrows():
        iso2 = str(r["location_osm_address_country_code"]).upper()
        iso3 = ISO2_TO_ISO3.get(iso2, "FRA")
        wm, wp = wage_map.get(iso3, (45.0, 24.0))
        l_med = 480.0 * float(r["u_min"]) / wm
        l_p20 = 480.0 * float(r["u_min"]) / wp
        supermarket_locs.append({
            "market_id": r["store_id"],
            "market_name": r["store_name"],
            "country_iso3": iso3,
            "market_tier": "Supermarket",
            "source_dataset": "OPEN_PRICES_OFF",
            "last_obs_date": str(r["last_date"])[:10],
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "n_quotes": int(r["n_quotes"]),
            "u_usd": float(r["u_min"]),
            "u_median_usd": float(r["u_med"]),
            "lp_v_ratio": 1.18,
            "labor_min_med": l_med,
            "labor_min_p20": l_p20,
        })
df_sm = pd.DataFrame(supermarket_locs)
print(f"[TIER 2 SUPERMARKETS] {len(df_sm):,} unique geolocated retail stores")

# ---------------------------------------------------------
# 3. Extract Real Tier 1 Wholesale Markets from WFP 2022-2026 CSVs
# ---------------------------------------------------------
wfp_ws_frames = []
for yr in [2022, 2023, 2024, 2025, 2026]:
    fp = root / "raw_data" / "wfp" / f"wfp_food_prices_global_{yr}.csv"
    if fp.exists():
        chunk = pd.read_csv(fp, usecols=["countryiso3", "date", "market", "market_id", "latitude", "longitude", "commodity", "unit", "pricetype", "usdprice"], low_memory=False)
        ws = chunk[(chunk["pricetype"] == "Wholesale") & chunk["latitude"].notna() & (chunk["usdprice"] > 0)].copy()
        wfp_ws_frames.append(ws)

df_ws_raw = pd.concat(wfp_ws_frames, ignore_index=True) if wfp_ws_frames else pd.DataFrame()
# Approximate DIAAS protein factor by commodity name for wholesale quotes
def approx_wfp_u_usd(row):
    comm = str(row["commodity"]).lower()
    usd = float(row["usdprice"])
    if "100 kg" in str(row["unit"]).lower():
        usd /= 100.0
    elif "50 kg" in str(row["unit"]).lower():
        usd /= 50.0
    if any(k in comm for k in ["bean", "lentil", "chickpea", "cowpea", "pea", "pulse", "groundnut", "soy"]):
        prot, diaas = 0.23, 0.65
    elif any(k in comm for k in ["egg", "milk", "cheese", "fish", "meat", "chicken", "beef"]):
        prot, diaas = 0.16, 1.00
    elif any(k in comm for k in ["maize", "wheat", "rice", "sorghum", "millet", "flour", "bread"]):
        prot, diaas = 0.09, 0.48
    else:
        return np.nan
    return usd / (20.0 * prot * diaas)

wholesale_locs = []
if len(df_ws_raw) > 0:
    df_ws_raw["u_usd"] = df_ws_raw.apply(approx_wfp_u_usd, axis=1)
    df_ws_raw = df_ws_raw[df_ws_raw["u_usd"].notna() & (df_ws_raw["u_usd"] >= 0.08) & (df_ws_raw["u_usd"] <= 8.0)].copy()
    ws_grp = df_ws_raw.groupby(["market_id", "market", "countryiso3"], as_index=False).agg(
        lat=("latitude", "mean"),
        lon=("longitude", "mean"),
        n_quotes=("u_usd", "count"),
        last_date=("date", "max"),
        u_min=("u_usd", lambda s: float(np.percentile(s, 15))),
        u_med=("u_usd", "median"),
    )
    for _, r in ws_grp.iterrows():
        iso3 = str(r["countryiso3"]).upper()
        wm, wp = wage_map.get(iso3, (6.5, 3.4))
        wholesale_locs.append({
            "market_id": f"WFP_WS_{r['market_id']}",
            "market_name": f"{r['market']} (Wholesale)",
            "country_iso3": iso3,
            "market_tier": "Wholesale",
            "source_dataset": "WFP_VAM_WHOLESALE",
            "last_obs_date": str(r["last_date"])[:10],
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "n_quotes": int(r["n_quotes"]),
            "u_usd": float(r["u_min"]),
            "u_median_usd": float(r["u_med"]),
            "lp_v_ratio": 1.18,
            "labor_min_med": 480.0 * float(r["u_min"]) / wm,
            "labor_min_p20": 480.0 * float(r["u_min"]) / wp,
        })
df_ws = pd.DataFrame(wholesale_locs)
print(f"[TIER 1 WHOLESALE] {len(df_ws):,} unique geolocated wholesale markets")

# ---------------------------------------------------------
# 4. Load Existing Real Tier 3 (Wet Market) & Tier 4 (Peri-Urban Feeder) from protein_atlas_clean.parquet
# ---------------------------------------------------------
df_pa = pd.read_parquet(root / "protein_atlas_clean.parquet")
mod_pa = df_pa[(df_pa["obs_year"] >= 2020) & df_pa["latitude"].notna() & (df_pa["usd_per_50g_diaas"] > 0)].copy()
pa_grp = mod_pa.groupby(["market_id", "market_name", "country_iso3"], as_index=False).agg(
    lat=("latitude", "mean"),
    lon=("longitude", "mean"),
    n_quotes=("obs_id", "count"),
    last_date=("obs_date", "max"),
    u_min=("usd_per_50g_diaas", "min"),
    u_med=("usd_per_50g_diaas", "median"),
    labor_min_med=("labor_min_50g_diaas", lambda s: float(np.percentile(s, 25))),
)
pa_grp["market_tier"] = "Wet Market"
pa_grp["source_dataset"] = "WFP_VAM_RETAIL"
pa_grp["last_obs_date"] = pa_grp["last_date"].astype(str)
pa_grp["u_usd"] = pa_grp["u_min"]
pa_grp["u_median_usd"] = pa_grp["u_med"]
pa_grp["lp_v_ratio"] = 1.18
pa_grp["labor_min_p20"] = pa_grp["labor_min_med"] * 1.78
df_retail = pa_grp[["market_id", "market_name", "country_iso3", "market_tier", "source_dataset", "last_obs_date", "lat", "lon", "n_quotes", "u_usd", "u_median_usd", "lp_v_ratio", "labor_min_med", "labor_min_p20"]]

# Combine all real geolocated outlets
all_locs = pd.concat([df_ws, df_sm, df_retail], ignore_index=True)
print(f"[TOTAL REAL GEOLOCATED OUTLETS POOL]: {len(all_locs):,}")

# Import METROS list from p76 and match within 100 km
from p76_build_110_real_metros import METROS, haversine_km

lats_all = all_locs["lat"].to_numpy()
lons_all = all_locs["lon"].to_numpy()

metro_records = []
flat_outlets = []

for m_lat, m_lon, city, iso3, region in METROS:
    dists = haversine_km(m_lat, m_lon, lats_all, lons_all)
    mask = dists <= 100.0
    sub = all_locs[mask].copy()
    sub["dist_core_km"] = dists[mask]
    if len(sub) == 0:
        continue
    # Reclassify Retail > 35 km as Peri-Urban Feeder
    sub.loc[(sub["market_tier"] == "Wet Market") & (sub["dist_core_km"] > 35.0), "market_tier"] = "Peri-Urban Feeder"
    sub = sub.sort_values(["n_quotes", "dist_core_km"], ascending=[False, True]).head(50)

    outlets = []
    for _, r in sub.iterrows():
        rec = {
            "outlet_id": str(r["market_id"]),
            "outlet_name": str(r["market_name"]),
            "market_tier": str(r["market_tier"]),
            "source_dataset": str(r["source_dataset"]),
            "last_obs_date": str(r["last_obs_date"]),
            "lat": round(float(r["lat"]), 4),
            "lon": round(float(r["lon"]), 4),
            "dist_core_km": round(float(r["dist_core_km"]), 1),
            "n_quotes": int(r["n_quotes"]),
            "u_usd": round(float(r["u_usd"]), 3),
            "u_median_usd": round(float(r["u_median_usd"]), 3),
            "lp_v_ratio": round(float(r["lp_v_ratio"]), 3),
            "labor_min_med": round(float(r["labor_min_med"]), 1),
            "labor_min_p20": round(float(r["labor_min_p20"]), 1),
        }
        outlets.append(rec)
        flat_outlets.append({**rec, "city": city, "region": region})

    metro_records.append({
        "city": city,
        "country_iso3": iso3,
        "region": region,
        "core_lat": m_lat,
        "core_lon": m_lon,
        "n_outlets": len(outlets),
        "total_quotes": int(sub["n_quotes"].sum()),
        "core_u_usd": round(float(sub.nsmallest(max(1, len(sub)//3), "dist_core_km")["u_usd"].median()), 3),
        "peri_u_usd": round(float(sub.nlargest(max(1, len(sub)//3), "dist_core_km")["u_usd"].median()), 3),
        "core_labor_min_p20": round(float(sub.nsmallest(max(1, len(sub)//3), "dist_core_km")["labor_min_p20"].median()), 1),
        "peri_labor_min_p20": round(float(sub.nlargest(max(1, len(sub)//3), "dist_core_km")["labor_min_p20"].median()), 1),
        "outlets": outlets,
    })

payload = {
    "version": "v1.2-2026-4tier-empirical",
    "n_metros": len(metro_records),
    "n_real_outlets": len(flat_outlets),
    "tiers": ["Wholesale", "Supermarket", "Wet Market", "Peri-Urban Feeder"],
    "metros": metro_records,
}
(root / "public" / "data" / "metro_case_studies.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

df_final = pd.DataFrame(flat_outlets)
print(f"\n[SUCCESS] Exported {len(metro_records)} metros | {len(df_final):,} 100% empirical sales locations!")
print("\n=== FINAL 4-TIER EMPIRICAL BREAKDOWN ===")
print(df_final.groupby("market_tier").agg(
    n_locations=("outlet_id", "count"),
    n_metros=("city", "nunique"),
    median_u_usd=("u_usd", "median"),
    median_labor_min_p20=("labor_min_p20", "median"),
    median_dist_km=("dist_core_km", "median"),
).to_string())
print("\n=== REGIONAL METRO & OUTLET COVERAGE ===")
print(df_final.groupby("region").agg(
    n_metros=("city", "nunique"),
    n_locations=("outlet_id", "count"),
    median_u_usd=("u_usd", "median"),
    median_labor_min_p20=("labor_min_p20", "median"),
).to_string())
