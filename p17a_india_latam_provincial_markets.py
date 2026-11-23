import csv, io, json, re, subprocess, duckdb, pandas as pd

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset IN ('IND_DISTRICT_MANDI','LATAM_PROVINCIAL_WHOLESALE');")
eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())

fx_js = json.loads(subprocess.check_output(["curl","-sL","--connect-timeout","15","--max-time","40","https://api.worldbank.org/v2/country/all/indicator/PA.NUS.FCRF?format=json&per_page=25000&date=2005:2026"], timeout=45).decode())
fx_wb = {(r["countryiso3code"], int(r["date"])): float(r["value"]) for r in (fx_js[1] or []) if r.get("value") is not None and r.get("countryiso3code")}
fx_lat = {iso: v for (iso, _), v in sorted(fx_wb.items())}
fx_lat.update({"IND": 83.5, "BRA": 5.15, "MEX": 17.5, "COL": 4050.0, "PER": 3.75})

w_df = con.execute("SELECT country_iso3, year, currency_code, unskilled_wage_hr FROM dim_macro_wages WHERE unskilled_wage_hr > 0;").df()
w_usd = {(r.country_iso3, int(r.year)): (float(r.unskilled_wage_hr) if r.currency_code=="USD" else float(r.unskilled_wage_hr)/fx_wb[(r.country_iso3, int(r.year))]) for r in w_df.itertuples(index=False) if r.currency_code=="USD" or fx_wb.get((r.country_iso3, int(r.year)),0)>0}
w_usd_lat = {iso: v for (iso, _), v in sorted(w_usd.items())}

# Build coordinate lookup from existing dim_markets by normalized city/district name
city_coords = {}
for m_id, m_name, iso, reg, la, lo, car in con.execute("SELECT market_id, market_name, country_iso3, region_macro, latitude, longitude, arable_ha_per_cap FROM dim_markets WHERE latitude IS NOT NULL;").fetchall():
    clean = re.sub(r"[^a-z0-9]", "", m_name.split(",")[0].split("(")[0].lower())
    if clean and (iso, clean) not in city_coords:
        city_coords[(iso, clean)] = (reg, float(la), float(lo), float(car or 0.15))

ISO_FALLBACK = {r[0]: (r[1], r[2], r[3], r[4]) for r in con.execute("SELECT country_iso3, FIRST(region_macro), MEDIAN(latitude), MEDIAN(longitude), MEDIAN(arable_ha_per_cap) FROM dim_markets GROUP BY 1;").fetchall()}

RULES = [
    (r"arhar|tur|moong|mung|urad|masoor|lentil|gram|chana|kabuli|chickpea", "LENTILS_DRY"),
    (r"rajma|bean|frijol|feijao|poroto|soy|soja|groundnut|peanut|cowpea|pea", "BEANS_DRY"),
    (r"egg|huevo|ovo|anda", "EGGS_CHIX"),
    (r"milk|leche|leite|dudh|paneer|curd", "MILK_FLUID"),
    (r"cheese|queso|queijo", "CHEESE_HARD"),
    (r"chicken|poultry|pollo|frango|broiler|murga", "POULTRY_CHIX"),
    (r"mutton|goat|sheep|cordero|chivo|bakra", "MUTTON_FRESH"),
    (r"pork|cerdo|suino|porco", "PORK_FRESH"),
    (r"beef|res|bovino|carne|buffalo", "BEEF_FRESH"),
    (r"fish|rohu|catla|hilsa|tilapia|pescado|peixe|sardine|tuna|atun|mackerel", "FISH_FRESH"),
    (r"wheat|atta|maida|trigo|harina|farinha|maize|corn|maiz|milho|sorghum|jowar|bajra|millet", "WHEAT_FLOUR"),
    (r"rice|arroz|chawal|paddy", "RICE_MILLED"),
]
COMPILED = [(re.compile(p), c) for p, c in RULES]

mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
mkts = set(r[0] for r in con.execute("SELECT market_id FROM dim_markets;").fetchall())
obs = []

def add_quote(src, iso, loc_name, raw_item, dt_str, nom_kg, cur, lat=None, lon=None):
    global mid
    cc = next((c for rx, c in COMPILED if rx.search(raw_item.lower())), None)
    if not cc or cc not in eff or iso not in w_usd_lat: return
    yr = int(dt_str[:4]) if len(dt_str) >= 4 and dt_str[:4].isdigit() else 2024
    fx = fx_wb.get((iso, yr), fx_lat.get(iso, 1.0))
    if fx <= 0: return
    rusd_kg = nom_kg / fx
    wh_u = w_usd.get((iso, yr), w_usd_lat[iso])
    u50 = rusd_kg * (50.0 / eff[cc])
    l50 = 60.0 * (rusd_kg / wh_u) * (50.0 / eff[cc])
    if not (0.04 <= u50 <= 25.0 and 0.2 <= l50 <= 2500.0): return

    clean_loc = re.sub(r"[^a-z0-9]", "", loc_name.lower())
    if lat is not None and lon is not None:
        reg, _, _, car = ISO_FALLBACK.get(iso, ("ASIA" if iso=="IND" else "LATAM", lat, lon, 0.15))
        la, lo = float(lat), float(lon)
    elif (iso, clean_loc) in city_coords:
        reg, la, lo, car = city_coords[(iso, clean_loc)]
    else:
        return  # Strict geographic precision: skip rows without verified district/town coordinates

    mk = f"{src[:4]}_{iso}_{clean_loc.upper()[:18]}"
    if mk not in mkts:
        con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?,?,?,?,?,?,?,0.22);", (mk, f"{loc_name.title()}, {iso}", iso, reg, la, lo, car))
        mkts.add(mk)
    mid += 1
    obs.append((mid, src, "OBSERVED_MARKET", mk, cc, dt_str[:10], nom_kg, cur, rusd_kg, u50, l50, False))

# 1. Stream India Open Government Data (data.gov.in) District Wholesale Mandi Feed
print("[TRACK 17A] Fetching India District Mandi & Latin America Provincial Wholesale Feeds...")
OGD_API = "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070?api-key=579b464db66ec23bdd000001cdd3946e44ce4aad7209ff7b23ac571b&format=json&limit=10000"
for offset in range(0, 50000, 10000):
    try:
        raw = subprocess.check_output(["curl","-sL","--connect-timeout","12","--max-time","30", f"{OGD_API}&offset={offset}"], timeout=35).decode("utf-8", errors="ignore")
        recs = json.loads(raw).get("records", [])
        if not recs: break
        for r in recs:
            dist = str(r.get("district") or r.get("market") or "").strip()
            comm = str(r.get("commodity") or "").strip()
            modal = r.get("modal_price")
            dt = str(r.get("arrival_date") or "2025-06-15")
            if "/" in dt and len(dt.split("/")) == 3:
                d, m, y = dt.split("/")
                dt = f"{y}-{m.zfill(2)}-{d.zfill(2)}"
            if dist and comm and modal:
                # Agmarknet quotes INR per Quintal (100 kg) -> divide by 100 for INR/kg
                val_kg = float(modal) / 100.0
                if val_kg > 1.0:
                    add_quote("IND_DISTRICT_MANDI", "IND", dist, comm, dt, val_kg, "INR")
    except Exception:
        break

# 2. Stream HDX / Sub-National Provincial Wholesale Datasets for Mexico, Colombia, Peru & Brazil
HDX_SEARCHES = [
    ("MEX", "https://data.humdata.org/api/3/action/package_search?q=wfp+food+prices+mexico&rows=3"),
    ("COL", "https://data.humdata.org/api/3/action/package_search?q=wfp+food+prices+colombia&rows=3"),
    ("PER", "https://data.humdata.org/api/3/action/package_search?q=wfp+food+prices+peru&rows=3"),
    ("BRA", "https://data.humdata.org/api/3/action/package_search?q=food+prices+brazil+conab&rows=3"),
]
for iso, pkg_url in HDX_SEARCHES:
    try:
        js = json.loads(subprocess.check_output(["curl","-sL","--connect-timeout","12","--max-time","25", pkg_url], timeout=30).decode())
        for pkg in js.get("result",{}).get("results",[]):
            for res in pkg.get("resources",[]):
                u = str(res.get("url",""))
                if u.lower().endswith(".csv") and "qc" not in u.lower():
                    txt = subprocess.check_output(["curl","-sL","--connect-timeout","15","--max-time","45", u], timeout=50).decode("utf-8", errors="ignore")
                    reader = csv.DictReader(io.StringIO(txt))
                    for row in reader:
                        if str(row.get("date","")).startswith("#"): continue
                        p = row.get("price") or row.get("value")
                        cm = row.get("commodity") or row.get("cmname") or ""
                        mk_n = row.get("market") or row.get("admin1") or ""
                        dt = str(row.get("date") or "2024-06-15")
                        la = row.get("latitude")
                        lo = row.get("longitude")
                        cur = str(row.get("currency") or iso)[:6]
                        if p and cm and mk_n:
                            try:
                                add_quote("LATAM_PROVINCIAL_WHOLESALE", iso, mk_n, cm, dt, float(p), cur,
                                          float(la) if la else None, float(lo) if lo else None)
                            except Exception:
                                pass
                    break
    except Exception:
        pass

if obs:
    con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)

tot_all, tot_mkt, tot_iso = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id), COUNT(DISTINCT m.country_iso3) FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.is_outlier_mad=FALSE;").fetchone()
print(f"[TRACK 17A COMPLETE] Added +{len(obs):,} India & LatAm Provincial rows! Atlas Total: {tot_all:,} rows | {tot_mkt:,} markets | {tot_iso} economies.")
con.close()
