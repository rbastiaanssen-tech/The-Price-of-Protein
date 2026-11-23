import csv, gzip, io, json, re, subprocess, duckdb, pandas as pd

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'WFP_HDX_FULL_GLOBAL';")
eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())

print("[TRACK 11A-1] Fetching World Bank FX & building complete 1990-2026 USD wage schedule...")
fx_js = json.loads(subprocess.check_output(["curl","-sL","--connect-timeout","15","--max-time","45","https://api.worldbank.org/v2/country/all/indicator/PA.NUS.FCRF?format=json&per_page=25000&date=1990:2026"], timeout=50).decode())
fx_wb = {(r["countryiso3code"], int(r["date"])): float(r["value"]) for r in (fx_js[1] or []) if r.get("value") is not None and r.get("countryiso3code")}
fx_lat = {iso: v for (iso, _), v in sorted(fx_wb.items())}
fx_lat["USA"] = 1.0

w_df = con.execute("SELECT country_iso3, year, currency_code, unskilled_wage_hr FROM dim_macro_wages WHERE unskilled_wage_hr > 0;").df()
w_usd = {(r.country_iso3, int(r.year)): (float(r.unskilled_wage_hr) if r.currency_code=="USD" else float(r.unskilled_wage_hr)/fx_wb[(r.country_iso3, int(r.year))]) for r in w_df.itertuples(index=False) if r.currency_code=="USD" or fx_wb.get((r.country_iso3, int(r.year)),0)>0}
w_usd_lat = {iso: v for (iso, _), v in sorted(w_usd.items())}

mkt_reg = dict(con.execute("SELECT country_iso3, FIRST(region_macro) FROM dim_markets GROUP BY 1;").fetchall())
mkt_car = dict(con.execute("SELECT country_iso3, MEDIAN(arable_ha_per_cap) FROM dim_markets GROUP BY 1;").fetchall())

print("[TRACK 11A-2] Querying HDX CKAN API for Global WFP Food Prices master archive...")
pkg = json.loads(subprocess.check_output(["curl","-sL","--connect-timeout","15","--max-time","40","https://data.humdata.org/api/3/action/package_show?id=global-wfp-food-prices"], timeout=45).decode())
res_list = pkg.get("result",{}).get("resources",[])
global_urls = [r["url"] for r in res_list if str(r.get("url","")).lower().endswith((".csv",".csv.gz",".zip")) and "food-prices" in str(r.get("url","")).lower()]
if not global_urls:
    pkg2 = json.loads(subprocess.check_output(["curl","-sL","--connect-timeout","15","--max-time","40","https://data.humdata.org/api/3/action/package_search?q=global+wfp+food+prices&rows=5"], timeout=45).decode())
    for ds in pkg2.get("result",{}).get("results",[]):
        for r in ds.get("resources",[]):
            u = str(r.get("url",""))
            if u.lower().endswith((".csv",".csv.gz",".zip")):
                global_urls.append(u)
print(f"  -> Found {len(global_urls)} HDX global WFP food price resources.")

COMM_RULES = [
    (r"beef|veal|buffalo|carabeef", "BEEF_FRESH"),
    (r"pork|pig|ham|bacon", "PORK_FRESH"),
    (r"mutton|goat|lamb|sheep", "MUTTON_FRESH"),
    (r"chicken|poultry|broiler|duck|turkey", "POULTRY_CHIX"),
    (r"egg", "EGGS_CHIX"),
    (r"milk|yogurt|curd|kefir|buttermilk", "MILK_FLUID"),
    (r"cheese|paneer|feta|gouda|cheddar", "CHEESE_HARD"),
    (r"fish|sardine|tuna|mackerel|tilapia|carp|catfish|herring|anchovy|salmon|cod|sprat|anchovies|shrimp|prawn", "FISH_FRESH"),
    (r"lentil|dal|dhal|masoor|moong|urad|arhar|tur|pigeon pea|chickpea|gram|chana", "LENTILS_DRY"),
    (r"bean|cowpea|niebe|haricot|frijol|poroto|soy|soja|groundnut|peanut|pea\b|peas\b|pulse", "BEANS_DRY"),
    (r"tofu|tempeh", "TOFU_TEMPEH"),
    (r"wheat|flour|bread|atta|maida|pasta|macaroni|spaghetti|bulgur|couscous|semolina|maize|cornmeal|sorghum|millet|teff|barley|oat", "WHEAT_FLOUR"),
    (r"rice|paddy", "RICE_MILLED"),
]
COMPILED = [(re.compile(p), c) for p, c in COMM_RULES]

def parse_unit_kg(u_str, cc):
    u = u_str.lower().strip()
    if "dozen" in u or "12 pcs" in u or "12 eggs" in u: return 0.72
    if "30 pcs" in u or "30 eggs" in u or "tray" in u: return 1.80
    if "quintal" in u: return 100.0
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|g|gr|gm|l|lt|ml|lb|lbs)\b", u)
    if m:
        val, unit = float(m.group(1)), m.group(2)
        if unit in ("g","gr","gm","ml"): return val / 1000.0
        if unit in ("lb","lbs"): return val * 0.453592
        return val
    if u in ("kg","l","liter","litre","1 kg","1 l"): return 1.0
    if u in ("g","gr","gm","ml"): return 0.001
    if u in ("unit","piece","pcs","each","loaf"):
        if cc == "EGGS_CHIX": return 0.06
        if cc == "WHEAT_FLOUR": return 0.50
        return 1.0
    return -1.0

print("[TRACK 11A-3] Indexing existing (country, market, commodity, date) keys in DuckDB for zero-duplicate merge...")
existing = set(con.execute("SELECT (m.country_iso3, LOWER(TRIM(m.market_name)), f.commodity_code, CAST(f.obs_date AS VARCHAR)) FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.obs_year >= 1990").fetchall())
print(f"  -> Indexed {len(existing):,} existing observations.")

mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
mkts = set(r[0] for r in con.execute("SELECT market_id FROM dim_markets;").fetchall())
batch, total_added = [], 0

for gurl in global_urls[:2]:
    print(f"[TRACK 11A-4] Streaming {gurl} ...")
    proc = subprocess.Popen(["curl","-sL","--connect-timeout","20","--max-time","600", gurl], stdout=subprocess.PIPE)
    raw_stream = gzip.GzipFile(fileobj=proc.stdout) if gurl.lower().endswith(".gz") else proc.stdout
    for chunk in pd.read_csv(raw_stream, chunksize=350000, low_memory=False, on_bad_lines="skip"):
        if len(chunk) > 0 and str(chunk.iloc[0,0]).startswith("#"):
            chunk = chunk.iloc[1:].reset_index(drop=True)
        cols = {c.lower().strip(): c for c in chunk.columns}
        iso_c = next((c for k,c in cols.items() if k in ("countryiso3","iso3","adm0_code")), None)
        mkt_c = next((c for k,c in cols.items() if k in ("market","mkt_name")), None)
        lat_c = next((c for k,c in cols.items() if "lat" in k), None)
        lon_c = next((c for k,c in cols.items() if "lon" in k), None)
        com_c = next((c for k,c in cols.items() if k in ("commodity","cm_name")), None)
        unt_c = next((c for k,c in cols.items() if k in ("unit","um_name")), None)
        cur_c = next((c for k,c in cols.items() if k in ("currency","cur_name")), None)
        dt_c  = next((c for k,c in cols.items() if k == "date"), None)
        pr_c  = next((c for k,c in cols.items() if k == "price"), None)
        usd_c = next((c for k,c in cols.items() if k == "usdprice"), None)
        if not (iso_c and mkt_c and lat_c and lon_c and com_c and dt_c and pr_c):
            continue

        chunk["p_n"] = pd.to_numeric(chunk[pr_c], errors="coerce")
        chunk["la_n"] = pd.to_numeric(chunk[lat_c], errors="coerce")
        chunk["lo_n"] = pd.to_numeric(chunk[lon_c], errors="coerce")
        chunk["u_n"] = pd.to_numeric(chunk[usd_c], errors="coerce") if usd_c else float("nan")
        valid = chunk.dropna(subset=["p_n","la_n","lo_n",iso_c,mkt_c,com_c,dt_c])
        valid = valid[valid["p_n"] > 0]

        for r in valid.itertuples(index=False):
            iso3 = str(getattr(r, iso_c)).strip().upper()
            if len(iso3) != 3 or iso3 not in w_usd_lat: continue
            com_s = str(getattr(r, com_c)).lower()
            cc = next((c for rx, c in COMPILED if rx.search(com_s)), None)
            if not cc or cc not in eff: continue
            unt_s = str(getattr(r, unt_c)) if unt_c else "KG"
            kg = parse_unit_kg(unt_s, cc)
            if not (0.04 <= kg <= 150.0): continue
            dt = str(getattr(r, dt_c))[:10]
            if len(dt) < 10 or not dt[:4].isdigit(): continue
            yr = int(dt[:4])
            if yr < 1990: continue
            mkt_nm = str(getattr(r, mkt_c)).strip()
            key = (iso3, mkt_nm.lower(), cc, dt)
            if key in existing: continue

            nom_kg = float(r.p_n) / kg
            u_val = getattr(r, "u_n")
            if pd.notna(u_val) and float(u_val) > 0:
                rusd_kg = float(u_val) / kg
            else:
                fx = fx_wb.get((iso3, yr), fx_lat.get(iso3, 0.0))
                if fx <= 0: continue
                rusd_kg = nom_kg / fx

            wh_u = w_usd.get((iso3, yr), w_usd_lat[iso3])
            u50 = rusd_kg * (50.0 / eff[cc])
            l50 = 60.0 * (rusd_kg / wh_u) * (50.0 / eff[cc])
            if not (0.04 <= u50 <= 30.0 and 0.1 <= l50 <= 2500.0): continue

            mk = f"WFP_{iso3}_{re.sub(r'[^A-Z0-9]','',mkt_nm.upper())[:18]}"
            if mk not in mkts:
                reg = mkt_reg.get(iso3, "GLOBAL_SOUTH")
                car = float(mkt_car.get(iso3, 0.18))
                con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?,?,?,?,?,?,?,0.14);", (mk, f"{mkt_nm}, {iso3}", iso3, reg, float(r.la_n), float(r.lo_n), car))
                mkts.add(mk)
            cur_s = str(getattr(r, cur_c)).upper()[:6] if cur_c else "LCU"
            mid += 1
            batch.append((mid,"WFP_HDX_FULL_GLOBAL","OBSERVED_MARKET",mk,cc,dt,nom_kg,cur_s,rusd_kg,u50,l50,False))
            existing.add(key)
            total_added += 1

        if len(batch) >= 250000:
            con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", batch)
            print(f"  -> Committed {total_added:,} new global WFP/HDX rows so far...")
            batch.clear()
    proc.wait()
    if total_added > 0:
        break

if batch:
    con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", batch)

tot_all, tot_mkt, tot_iso = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id), COUNT(DISTINCT m.country_iso3) FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.is_outlier_mad=FALSE;").fetchone()
print(f"[TRACK 11A COMPLETE] Added +{total_added:,} new WFP/HDX rows! Atlas Total: {tot_all:,} rows | {tot_mkt:,} markets | {tot_iso} economies.")
con.close()
