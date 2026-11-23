import io, json, re, urllib.parse, urllib.request, duckdb, pandas as pd

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset IN ('HDX_SUBNATIONAL', 'GUS_POLAND_REGIONAL');")
eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())

def wb(ind):
    u = f"https://api.worldbank.org/v2/country/all/indicator/{ind}?format=json&per_page=25000&date=2010:2026"
    js = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent":"ProteinAtlas/1.0"}), timeout=45).read().decode())
    return {(r["countryiso3code"], int(r["date"])): float(r["value"]) for r in (js[1] or []) if r.get("value") is not None and r.get("countryiso3code")}

fx_wb = wb("PA.NUS.FCRF")
fx_lat = {iso: v for (iso, _), v in sorted(fx_wb.items())}
w_df = con.execute("SELECT country_iso3, year, currency_code, unskilled_wage_hr FROM dim_macro_wages WHERE unskilled_wage_hr > 0;").df()
w_usd = {(r.country_iso3, int(r.year)): (float(r.unskilled_wage_hr) if r.currency_code=="USD" else float(r.unskilled_wage_hr)/fx_wb[(r.country_iso3, int(r.year))]) for r in w_df.itertuples(index=False) if r.currency_code=="USD" or fx_wb.get((r.country_iso3, int(r.year)),0)>0}
w_usd_lat = {iso: v for (iso, _), v in sorted(w_usd.items())}
VOIV = {
    "011200000000":("Malopolskie (Krakow), PL",50.06,19.94),
    "012400000000":("Slaskie (Katowice), PL",50.26,19.02),
    "020800000000":("Lubuskie (Zielona Gora), PL",51.94,15.51),
    "023000000000":("Wielkopolskie (Poznan), PL",52.41,16.93),
    "023200000000":("Zachodniopomorskie (Szczecin), PL",53.43,14.55),
    "030200000000":("Dolnoslaskie (Wroclaw), PL",51.11,17.04),
    "031600000000":("Opolskie (Opole), PL",50.67,17.92),
    "040400000000":("Kujawsko-Pomorskie (Bydgoszcz), PL",53.12,18.01),
    "042200000000":("Pomorskie (Gdansk), PL",54.35,18.65),
    "042800000000":("Warminsko-Mazurskie (Olsztyn), PL",53.78,20.48),
    "051000000000":("Lodzkie (Lodz), PL",51.76,19.46),
    "052600000000":("Swietokrzyskie (Kielce), PL",50.87,20.63),
    "060600000000":("Lubelskie (Lublin), PL",51.25,22.57),
    "061800000000":("Podkarpackie (Rzeszow), PL",50.04,22.00),
    "062000000000":("Podlaskie (Bialystok), PL",53.13,23.16),
    "071400000000":("Mazowieckie (Warsaw), PL",52.23,21.01),
}
GUS_VARS = [("4621","BEEF_BONELESS",1.0),("4623","PORK_FRESH",1.0),("4626","CHICKEN_WHOLE",1.0),("4634","MILK_FRESH",1.03),("4637","EGGS_FRESH",0.06),("4613","WHEAT_FLOUR",1.0),("4646","POTATOES",1.0)]
print("[PHASE 8B] Querying Statistics Poland (GUS BDL API) across all 16 Voivodeships...")
mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
obs, mkts = [], set()
for vid, cc, kg_div in GUS_VARS:
    if cc not in eff: continue
    u = f"https://bdl.stat.gov.pl/api/v1/data/by-variable/{vid}?unit-level=2&page-size=100&format=json"
    try:
        js = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent":"ProteinAtlas/1.0","Accept":"application/json"}), timeout=30).read().decode())
    except Exception as e:
        print(f"  -> GUS {vid} skipped: {e}"); continue
    for unit in js.get("results", []):
        uid = str(unit.get("id",""))
        if uid not in VOIV: continue
        vname, lat, lon = VOIV[uid]
        mk = f"GUS_PL_{uid[:4]}"
        for vr in unit.get("values", []):
            yr, val = int(vr["year"]), vr.get("val")
            if not val or float(val) <= 0 or "POL" not in w_usd_lat: continue
            fx = fx_wb.get(("POL", yr), fx_lat.get("POL", 4.0))
            nom_kg = float(val) / kg_div
            rusd_kg = nom_kg / fx
            wh_u = w_usd.get(("POL", yr), w_usd_lat["POL"])
            u50, l50 = rusd_kg * (50.0 / eff[cc]), 60.0 * (rusd_kg / wh_u) * (50.0 / eff[cc])
            if not (0.08 <= u50 <= 25.0 and 0.2 <= l50 <= 1500.0): continue
            if mk not in mkts:
                con.execute("INSERT OR REPLACE INTO dim_markets VALUES (?,?,'POL','EUROPE',?,?,0.29,0.16);", (mk, vname, lat, lon))
                mkts.add(mk)
            mid += 1
            obs.append((mid,"GUS_POLAND_REGIONAL","OBSERVED_MARKET",mk,cc,f"{yr:04d}-06-15",nom_kg,"PLN",rusd_kg,u50,l50,False))
COMM_MAP = {"rice":"RICE_MILLED","wheat":"WHEAT_FLOUR","maize":"MAIZE_GRAIN","beans":"DRY_BEANS","lentils":"LENTILS_DRY","chickpeas":"CHICKPEAS_DRY","beef":"BEEF_BONELESS","pork":"PORK_FRESH","chicken":"CHICKEN_WHOLE","milk":"MILK_FRESH","eggs":"EGGS_FRESH","fish":"FISH_FRESH","potatoes":"POTATOES"}
HDX_TARGETS = [("ukraine","UKR","EUROPE",0.79),("brazil","BRA","LATAM",0.27),("mexico","MEX","LATAM",0.18),("argentina","ARG","LATAM",0.92),("india","IND","ASIA",0.11),("indonesia","IDN","ASIA",0.09),("philippines","PHL","ASIA",0.05),("nigeria","NGA","AFRICA",0.16),("kenya","KEN","AFRICA",0.11),("south-africa","ZAF","AFRICA",0.20),("egypt","EGY","MENA",0.03)]
existing_keys = set(con.execute("SELECT (m.country_iso3, LOWER(TRIM(m.market_name)), f.commodity_code, CAST(f.obs_date AS VARCHAR)) FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.obs_year >= 2018;").fetchall())
print(f"[PHASE 8B] Querying HDX CKAN API for sub-national market CSVs (deduping vs {len(existing_keys):,} keys)...")
for slug, iso3, reg, car in HDX_TARGETS:
    if iso3 not in w_usd_lat or iso3 not in fx_lat: continue
    q = urllib.parse.urlencode({"q": f"wfp food prices {slug}", "rows": 3})
    try:
        pkg = json.loads(urllib.request.urlopen(urllib.request.Request(f"https://data.humdata.org/api/3/action/package_search?{q}", headers={"User-Agent":"ProteinAtlas/1.0"}), timeout=25).read().decode())
        results = pkg.get("result", {}).get("results", [])
    except Exception: continue
    csv_url = next((res["url"] for ds in results for res in ds.get("resources", []) if str(res.get("url","")).lower().endswith(".csv") and "global" not in str(res.get("url","")).lower()), None)
    if not csv_url: continue
    try:
        df = pd.read_csv(io.BytesIO(urllib.request.urlopen(urllib.request.Request(csv_url, headers={"User-Agent":"ProteinAtlas/1.0"}), timeout=45).read()), low_memory=False)
    except Exception: continue
    if len(df) > 1 and str(df.iloc[0,0]).startswith("#"): df = df.iloc[1:].reset_index(drop=True)
    cols = {c.lower(): c for c in df.columns}
    if not all(k in cols for k in ("date","market","commodity","price","latitude","longitude")): continue
    df["p_num"] = pd.to_numeric(df[cols["price"]], errors="coerce")
    df["lat_n"] = pd.to_numeric(df[cols["latitude"]], errors="coerce")
    df["lon_n"] = pd.to_numeric(df[cols["longitude"]], errors="coerce")
    added_c = 0
    for r in df.dropna(subset=["p_num","lat_n","lon_n"]).itertuples(index=False):
        dt = str(getattr(r, cols["date"]))[:10]
        if len(dt) < 10 or int(dt[:4]) < 2015: continue
        mkt_nm = str(getattr(r, cols["market"])).strip()
        comm_raw = str(getattr(r, cols["commodity"])).lower().strip()
        cc = next((v for k, v in COMM_MAP.items() if k in comm_raw), None)
        if not cc or cc not in eff: continue
        unit_s = str(getattr(r, cols["unit"])) if "unit" in cols else "KG"
        kg = 0.72 if "dozen" in unit_s.lower() else (float(re.findall(r"[0-9.]+", unit_s)[0]) if re.findall(r"^[0-9.]+", unit_s) else 1.0)
        if "g" in unit_s.lower() and "kg" not in unit_s.lower() and kg >= 50: kg /= 1000.0
        if not (0.05 <= kg <= 100.0) or (iso3, mkt_nm.lower(), cc, dt) in existing_keys: continue
        nom_kg, yr = float(r.p_num) / kg, int(dt[:4])
        fx = fx_wb.get((iso3, yr), fx_lat[iso3])
        if fx <= 0: continue
        rusd_kg = nom_kg / fx
        wh_u = w_usd.get((iso3, yr), w_usd_lat[iso3])
        u50, l50 = rusd_kg * (50.0 / eff[cc]), 60.0 * (rusd_kg / wh_u) * (50.0 / eff[cc])
        if not (0.05 <= u50 <= 25.0 and 0.2 <= l50 <= 1500.0): continue
        mk = f"HDX_{iso3}_{re.sub(r'[^A-Z0-9]','',mkt_nm.upper())[:16]}"
        if mk not in mkts:
            con.execute("INSERT OR REPLACE INTO dim_markets VALUES (?,?,?,?,?,?,?,0.14);", (mk, f"{mkt_nm}, {iso3}", iso3, reg, float(r.lat_n), float(r.lon_n), car))
            mkts.add(mk)
        mid += 1
        obs.append((mid,"HDX_SUBNATIONAL","OBSERVED_MARKET",mk,cc,dt,nom_kg,"LCU",rusd_kg,u50,l50,False))
        existing_keys.add((iso3, mkt_nm.lower(), cc, dt))
        added_c += 1
    print(f"  -> HDX {iso3}: +{added_c:,} new sub-national market rows.")
if obs:
    con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)
print(f"[PHASE 8B COMPLETE] Inserted {len(obs):,} regional & sub-national market observations across {len(mkts)} hubs!")
print(con.execute("SELECT m.region_macro, m.country_iso3, COUNT(DISTINCT m.market_id) AS markets, COUNT(*) AS n, ROUND(MEDIAN(f.usd_per_50g_diaas),2) AS med_usd, ROUND(MEDIAN(f.labor_min_50g_diaas),1) AS med_min FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.source_dataset IN ('GUS_POLAND_REGIONAL','HDX_SUBNATIONAL') GROUP BY 1,2 ORDER BY 1,4 DESC;").df().to_string(index=False))
con.close()
