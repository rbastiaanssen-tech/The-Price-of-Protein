import json
import numpy as np
import pandas as pd
from pathlib import Path

root = Path.cwd()

# 1. Country daily wages (hourly_wage_usd * 8h workday)
wages_df = pd.read_parquet(root / "exports" / "dim_country_wages.parquet")
wage_map = {}
for _, r in wages_df.iterrows():
    iso = str(r["country_iso3"]).upper()
    hr = float(r["hourly_wage_usd"]) if pd.notna(r["hourly_wage_usd"]) and float(r["hourly_wage_usd"]) > 0 else 2.0
    wm = hr * 8.0
    wage_map[iso] = (wm, wm * 0.56)

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
    "TR": "TUR", "SA": "SAU", "ARE": "ARE", "UA": "UKR",
}

# 2. Tier 2 Supermarkets directly from open_prices.parquet (79 MB only — zero 7.2 GB parquet!)
op_cols = [
    "product_name", "category_tag", "price", "price_per", "currency", "date",
    "location_osm_id", "location_osm_name", "location_osm_brand",
    "location_osm_address_country_code", "location_osm_lat", "location_osm_lon"
]
df_op = pd.read_parquet(root / "data_raw" / "open_prices.parquet", columns=op_cols)
df_op["price"] = pd.to_numeric(df_op["price"], errors="coerce")
df_op["location_osm_lat"] = pd.to_numeric(df_op["location_osm_lat"], errors="coerce")
df_op["location_osm_lon"] = pd.to_numeric(df_op["location_osm_lon"], errors="coerce")
df_op = df_op[df_op["location_osm_lat"].notna() & df_op["location_osm_lon"].notna() & (df_op["price"] > 0)].copy()

txt = (df_op["category_tag"].fillna("") + " " + df_op["product_name"].fillna("")).str.lower()
prot = np.zeros(len(df_op))
diaas = np.zeros(len(df_op))

# Vectorized protein & DIAAS classification from category_tag + product_name
m_dairy = txt.str.contains("milk|lait|leche|milch|yogurt|yaourt|cheese|fromage|queso|egg|oeuf|huevo")
m_meat  = txt.str.contains("chicken|poulet|pollo|beef|boeuf|pork|porc|fish|poisson|tuna|thon|sardine|salmon|meat|viande")
m_soy   = txt.str.contains("tofu|tempeh|soy|soja|edamame")
m_pulse = txt.str.contains("lentil|lentille|bean|haricot|chickpea|pois chiche|pea|pois")
m_grain = txt.str.contains("oat|flocon|bread|pain|pasta|pate|flour|farine|rice|riz|wheat|ble")

prot[m_grain] = 10.5; diaas[m_grain] = 0.48
prot[m_pulse] = 22.0; diaas[m_pulse] = 0.65
prot[m_soy]   = 16.0; diaas[m_soy]   = 0.91
prot[m_dairy] = 11.0; diaas[m_dairy] = 1.00
prot[m_meat]  = 21.0; diaas[m_meat]  = 1.00

keep = prot > 0
op_p = df_op[keep].copy()
op_p["prot_100g"] = prot[keep]
op_p["diaas"] = diaas[keep]
op_p["usd_price"] = op_p["price"] * op_p["currency"].map(FX_USD).fillna(1.0)
eff_g = np.where(op_p["price_per"].astype(str).str.upper() == "KILOGRAM", 1000.0, 500.0)
op_p["u_usd"] = op_p["usd_price"] * (50.0 / (eff_g * (op_p["prot_100g"] / 100.0) * op_p["diaas"]))
op_p = op_p[(op_p["u_usd"] >= 0.15) & (op_p["u_usd"] <= 10.0)].copy()

op_p["store_id"] = "OSM_" + op_p["location_osm_id"].astype(str)
op_p["store_name"] = op_p["location_osm_brand"].fillna(op_p["location_osm_name"]).fillna("Supermarket").astype(str)
sm_grp = op_p.groupby(["store_id", "store_name", "location_osm_address_country_code"], as_index=False).agg(
    lat=("location_osm_lat", "mean"),
    lon=("location_osm_lon", "mean"),
    n_quotes=("u_usd", "count"),
    last_date=("date", "max"),
    u_min=("u_usd", lambda s: float(np.percentile(s, 15))),
    u_med=("u_usd", "median"),
)

sm_rows = []
for _, r in sm_grp.iterrows():
    iso3 = ISO2_TO_ISO3.get(str(r["location_osm_address_country_code"]).upper(), "FRA")
    wm, wp = wage_map.get(iso3, (120.0, 67.0))
    sm_rows.append({
        "market_id": r["store_id"], "market_name": r["store_name"], "country_iso3": iso3,
        "market_tier": "Supermarket", "source_dataset": "OPEN_PRICES",
        "last_obs_date": str(r["last_date"])[:10],
        "lat": float(r["lat"]), "lon": float(r["lon"]), "n_quotes": int(r["n_quotes"]),
        "u_usd": float(r["u_min"]), "u_median_usd": float(r["u_med"]), "lp_v_ratio": 1.18,
        "labor_min_med": 480.0 * float(r["u_min"]) / wm,
        "labor_min_p20": 480.0 * float(r["u_min"]) / wp,
    })
df_sm = pd.DataFrame(sm_rows)
print(f"[TIER 2 SUPERMARKETS] {len(df_sm):,} geolocated stores from {len(op_p):,} quotes")

# 3. Tier 1 Wholesale Markets from WFP 2022-2026
ws_frames = []
for yr in [2022, 2023, 2024, 2025, 2026]:
    fp = root / "raw_data" / "wfp" / f"wfp_food_prices_global_{yr}.csv"
    if fp.exists():
        c = pd.read_csv(fp, usecols=["countryiso3", "date", "market", "market_id", "latitude", "longitude", "commodity", "unit", "pricetype", "usdprice"], low_memory=False)
        ws_frames.append(c[(c["pricetype"] == "Wholesale") & c["latitude"].notna() & (c["usdprice"] > 0)])
df_ws_raw = pd.concat(ws_frames, ignore_index=True)

w_comm = df_ws_raw["commodity"].fillna("").str.lower()
w_unit = df_ws_raw["unit"].fillna("").str.lower()
usd_kg = df_ws_raw["usdprice"].to_numpy(dtype=float).copy()
usd_kg[w_unit.str.contains("100 kg")] /= 100.0
usd_kg[w_unit.str.contains("50 kg")] /= 50.0

w_fac = np.zeros(len(df_ws_raw))
w_fac[w_comm.str.contains("maize|wheat|rice|sorghum|millet|flour|bread")] = 20.0 * 0.09 * 0.48
w_fac[w_comm.str.contains("bean|lentil|chickpea|cowpea|pea|pulse|groundnut|soy")] = 20.0 * 0.23 * 0.65
w_fac[w_comm.str.contains("egg|milk|cheese|fish|meat|chicken|beef")] = 20.0 * 0.16 * 1.00

wk = w_fac > 0
df_ws_raw = df_ws_raw[wk].copy()
df_ws_raw["u_usd"] = usd_kg[wk] / w_fac[wk]
df_ws_raw = df_ws_raw[(df_ws_raw["u_usd"] >= 0.08) & (df_ws_raw["u_usd"] <= 8.0)]

ws_grp = df_ws_raw.groupby(["market_id", "market", "countryiso3"], as_index=False).agg(
    lat=("latitude", "mean"), lon=("longitude", "mean"), n_quotes=("u_usd", "count"),
    last_date=("date", "max"), u_min=("u_usd", lambda s: float(np.percentile(s, 15))), u_med=("u_usd", "median"),
)
ws_rows = []
for _, r in ws_grp.iterrows():
    iso3 = str(r["countryiso3"]).upper()
    wm, wp = wage_map.get(iso3, (12.0, 6.7))
    ws_rows.append({
        "market_id": f"WFP_WS_{r['market_id']}", "market_name": f"{r['market']} (Wholesale)",
        "country_iso3": iso3, "market_tier": "Wholesale", "source_dataset": "WFP_VAM_WHOLESALE",
        "last_obs_date": str(r["last_date"])[:10], "lat": float(r["lat"]), "lon": float(r["lon"]),
        "n_quotes": int(r["n_quotes"]), "u_usd": float(r["u_min"]), "u_median_usd": float(r["u_med"]),
        "lp_v_ratio": 1.18,
        "labor_min_med": 480.0 * float(r["u_min"]) / wm,
        "labor_min_p20": 480.0 * float(r["u_min"]) / wp,
    })
df_ws = pd.DataFrame(ws_rows)
print(f"[TIER 1 WHOLESALE] {len(df_ws):,} geolocated wholesale markets")

# 4. Tier 3 Wet Markets & Tier 4 Peri-Urban Feeders from protein_atlas_clean.parquet
df_pa = pd.read_parquet(root / "protein_atlas_clean.parquet")
mod_pa = df_pa[(df_pa["obs_year"] >= 2020) & df_pa["latitude"].notna() & (df_pa["usd_per_50g_diaas"] > 0)].copy()
pa_grp = mod_pa.groupby(["market_id", "market_name", "country_iso3"], as_index=False).agg(
    lat=("latitude", "mean"), lon=("longitude", "mean"), n_quotes=("obs_id", "count"),
    last_date=("obs_date", "max"), u_min=("usd_per_50g_diaas", "min"), u_med=("usd_per_50g_diaas", "median"),
    labor_min_med=("labor_min_50g_diaas", lambda s: float(np.percentile(s, 25))),
)
pa_grp["market_tier"] = "Wet Market"
pa_grp["source_dataset"] = "WFP_VAM_RETAIL"
pa_grp["last_obs_date"] = pa_grp["last_date"].astype(str)
pa_grp["u_usd"] = pa_grp["u_min"]
pa_grp["u_median_usd"] = pa_grp["u_med"]
pa_grp["lp_v_ratio"] = 1.18
pa_grp["labor_min_p20"] = pa_grp["labor_min_med"] * 1.78
for _d in (df_ws, df_sm, pa_grp):
    if "country_iso3" not in _d.columns:
        _d["country_iso3"] = _d["iso3"] if "iso3" in _d.columns else ""
df_retail = pa_grp[df_ws.columns]

all_locs = pd.concat([df_ws, df_sm, df_retail], ignore_index=True)

from p76_build_110_real_metros import METROS, haversine_km

lats_all, lons_all = all_locs["lat"].to_numpy(), all_locs["lon"].to_numpy()
metro_records, flat_outlets = [], []

iso3_all = all_locs["country_iso3"].astype(str).str.upper().str.strip().to_numpy()
for m_lat, m_lon, city, iso3, region in METROS:
    dists = haversine_km(m_lat, m_lon, lats_all, lons_all)
    c_target = str(iso3).upper().strip()
    mask = (dists <= 100.0) & ((iso3_all == c_target) | (iso3_all == "") | (iso3_all == "NAN"))
    sub = all_locs[mask].copy()
    sub["dist_core_km"] = dists[mask]
    if len(sub) == 0:
        continue
    sub.loc[(sub["market_tier"] == "Wet Market") & (sub["dist_core_km"] > 35.0), "market_tier"] = "Peri-Urban Feeder"
    sub = sub.sort_values(["n_quotes", "dist_core_km"], ascending=[False, True]).head(50)

    outlets = []
    for _, r in sub.iterrows():
        rec = {
            "outlet_id": str(r["market_id"]), "outlet_name": str(r["market_name"]),
            "market_tier": str(r["market_tier"]), "source_dataset": str(r["source_dataset"]),
            "last_obs_date": str(r["last_obs_date"]),
            "lat": round(float(r["lat"]), 4), "lon": round(float(r["lon"]), 4),
            "dist_core_km": round(float(r["dist_core_km"]), 1), "n_quotes": int(r["n_quotes"]),
            "u_usd": round(float(r["u_usd"]), 3), "u_median_usd": round(float(r["u_median_usd"]), 3),
            "lp_v_ratio": round(float(r["lp_v_ratio"]), 3),
            "labor_min_med": round(float(r["labor_min_med"]), 1),
            "labor_min_p20": round(float(r["labor_min_p20"]), 1),
        }
        outlets.append(rec)
        flat_outlets.append({**rec, "city": city, "region": region})

    metro_records.append({
        "city": city, "country_iso3": iso3, "region": region,
        "core_lat": m_lat, "core_lon": m_lon,
        "n_outlets": len(outlets), "total_quotes": int(sub["n_quotes"].sum()),
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
print(f"\n[SUCCESS] {len(metro_records)} real metros | {len(df_final):,} 100% empirical sales locations!")
print(df_final.groupby("market_tier").agg(
    n_locations=("outlet_id", "count"),
    n_metros=("city", "nunique"),
    median_u_usd=("u_usd", "median"),
    median_labor_min_p20=("labor_min_p20", "median"),
).to_string())
