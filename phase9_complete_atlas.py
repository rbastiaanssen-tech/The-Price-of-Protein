import gzip, io, json, re, urllib.parse, urllib.request, zipfile, duckdb, pandas as pd

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset IN ('OPEN_PRICES_ISOLATES','HDX_RUS_CHN','STATCAN_RETAIL');")
eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())

def wb(ind):
    u = f"https://api.worldbank.org/v2/country/all/indicator/{ind}?format=json&per_page=25000&date=2000:2026"
    js = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent":"ProteinAtlas/1.0"}), timeout=45).read().decode())
    return {(r["countryiso3code"], int(r["date"])): float(r["value"]) for r in (js[1] or []) if r.get("value") is not None and r.get("countryiso3code")}

fx_wb = wb("PA.NUS.FCRF")
fx_lat = {iso: v for (iso, _), v in sorted(fx_wb.items())}
fx_lat["USA"] = 1.0
for eu in ["AUT","BEL","CYP","DEU","ESP","EST","FIN","FRA","GRC","HRV","IRL","ITA","LTU","LUX","LVA","MLT","NLD","PRT","SVK","SVN"]:
    fx_lat[eu] = 0.87

w_df = con.execute("SELECT country_iso3, year, currency_code, unskilled_wage_hr FROM dim_macro_wages WHERE unskilled_wage_hr > 0;").df()
w_usd = {(r.country_iso3, int(r.year)): (float(r.unskilled_wage_hr) if r.currency_code=="USD" else float(r.unskilled_wage_hr)/fx_wb[(r.country_iso3, int(r.year))]) for r in w_df.itertuples(index=False) if r.currency_code=="USD" or fx_wb.get((r.country_iso3, int(r.year)),0)>0}
for yr in range(2000, 2027):
    if ("CAN", yr) not in w_usd and ("USA", yr) in w_usd:
        w_usd[("CAN", yr)] = w_usd[("USA", yr)] * 0.92
w_usd_lat = {iso: v for (iso, _), v in sorted(w_usd.items())}
print(f"[PHASE 9] Ready: {len(eff)} commodities | RUS={w_usd_lat.get('RUS',0):.2f}, CHN={w_usd_lat.get('CHN',0):.2f}, CAN={w_usd_lat.get('CAN',0):.2f} USD/hr.")
def fetch_gz(name):
    raw = urllib.request.urlopen(urllib.request.Request(f"https://prices.openfoodfacts.org/data/{name}.jsonl.gz", headers={"User-Agent":"ProteinAtlas/1.0"}), timeout=90).read()
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
        return [json.loads(line) for line in gz if line.strip()]

print("[PHASE 9.1] Downloading Open Prices archives (Isolates + RUS/CHN cities)...")
locs_raw, prods_raw, prices_raw = fetch_gz("locations"), fetch_gz("products"), fetch_gz("prices")
ISO = {"US":("USA","NORTH_AMERICA","USD",0.47),"CA":("CAN","NORTH_AMERICA","CAD",0.96),"GB":("GBR","EUROPE","GBP",0.09),"UK":("GBR","EUROPE","GBP",0.09),"FR":("FRA","EUROPE","EUR",0.27),"DE":("DEU","EUROPE","EUR",0.14),"ES":("ESP","EUROPE","EUR",0.25),"IT":("ITA","EUROPE","EUR",0.12),"NL":("NLD","EUROPE","EUR",0.06),"BE":("BEL","EUROPE","EUR",0.08),"AT":("AUT","EUROPE","EUR",0.15),"CH":("CHE","EUROPE","CHF",0.05),"PL":("POL","EUROPE","PLN",0.29),"SE":("SWE","EUROPE","SEK",0.25),"DK":("DNK","EUROPE","DKK",0.41),"CZ":("CZE","EUROPE","CZK",0.24),"PT":("PRT","EUROPE","EUR",0.10),"IE":("IRL","EUROPE","EUR",0.09),"FI":("FIN","EUROPE","EUR",0.41),"AU":("AUS","OCEANIA","AUD",1.15),"BR":("BRA","LATAM","BRL",0.27),"MX":("MEX","LATAM","MXN",0.18),"IN":("IND","ASIA","INR",0.11),"JP":("JPN","ASIA","JPY",0.03),"RU":("RUS","EUROPE","RUB",0.85),"CN":("CHN","ASIA","CNY",0.08)}
loc_map = {}
for l in locs_raw:
    cc2 = str(l.get("osm_address_country_code") or "").upper()
    city = str(l.get("osm_address_city") or l.get("osm_address_town") or l.get("osm_address_village") or "").strip()
    if cc2 in ISO and city and l.get("osm_lat") is not None and l.get("osm_lon") is not None:
        loc_map[l["id"]] = (ISO[cc2][0], ISO[cc2][1], ISO[cc2][2], ISO[cc2][3], city, float(l["osm_lat"]), float(l["osm_lon"]))
def fetch_gz(name):
    raw = urllib.request.urlopen(urllib.request.Request(f"https://prices.openfoodfacts.org/data/{name}.jsonl.gz", headers={"User-Agent":"ProteinAtlas/1.0"}), timeout=90).read()
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
        return [json.loads(line) for line in gz if line.strip()]

print("[PHASE 9.1] Downloading Open Prices archives (Isolates + RUS/CHN cities)...")
locs_raw, prods_raw, prices_raw = fetch_gz("locations"), fetch_gz("products"), fetch_gz("prices")
ISO = {"US":("USA","NORTH_AMERICA","USD",0.47),"CA":("CAN","NORTH_AMERICA","CAD",0.96),"GB":("GBR","EUROPE","GBP",0.09),"UK":("GBR","EUROPE","GBP",0.09),"FR":("FRA","EUROPE","EUR",0.27),"DE":("DEU","EUROPE","EUR",0.14),"ES":("ESP","EUROPE","EUR",0.25),"IT":("ITA","EUROPE","EUR",0.12),"NL":("NLD","EUROPE","EUR",0.06),"BE":("BEL","EUROPE","EUR",0.08),"AT":("AUT","EUROPE","EUR",0.15),"CH":("CHE","EUROPE","CHF",0.05),"PL":("POL","EUROPE","PLN",0.29),"SE":("SWE","EUROPE","SEK",0.25),"DK":("DNK","EUROPE","DKK",0.41),"CZ":("CZE","EUROPE","CZK",0.24),"PT":("PRT","EUROPE","EUR",0.10),"IE":("IRL","EUROPE","EUR",0.09),"FI":("FIN","EUROPE","EUR",0.41),"AU":("AUS","OCEANIA","AUD",1.15),"BR":("BRA","LATAM","BRL",0.27),"MX":("MEX","LATAM","MXN",0.18),"IN":("IND","ASIA","INR",0.11),"JP":("JPN","ASIA","JPY",0.03),"RU":("RUS","EUROPE","RUB",0.85),"CN":("CHN","ASIA","CNY",0.08)}
loc_map = {}
for l in locs_raw:
    cc2 = str(l.get("osm_address_country_code") or "").upper()
    city = str(l.get("osm_address_city") or l.get("osm_address_town") or l.get("osm_address_village") or "").strip()
    if cc2 in ISO and city and l.get("osm_lat") is not None and l.get("osm_lon") is not None:
        loc_map[l["id"]] = (ISO[cc2][0], ISO[cc2][1], ISO[cc2][2], ISO[cc2][3], city, float(l["osm_lat"]), float(l["osm_lon"]))
mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
obs, mkts = [], set()
for r in prices_raw:
    lid, pcode = r.get("location_id"), str(r.get("product_code") or "")
    if lid not in loc_map: continue
    iso3, reg, exp_cur, car, city, lat, lon = loc_map[lid]
    if str(r.get("currency") or "").upper() != exp_cur or iso3 not in w_usd_lat or iso3 not in fx_lat: continue
    if pcode in iso_prods: cc, kg = iso_prods[pcode]
    elif iso3 in ("RUS","CHN") and pcode in food_prods: cc, kg = food_prods[pcode]
    else: continue
    if cc not in eff: continue
    pr = r.get("price")
    if not pr or float(pr) <= 0: continue
    nom_kg = float(pr) if str(r.get("price_per") or "").upper() == "KILOGRAM" else float(pr) / kg
    dt = str(r.get("date") or "")[:10]
    if len(dt) < 10: continue
    yr = int(dt[:4])
    fx = fx_wb.get((iso3, yr), fx_lat[iso3])
    if fx <= 0: continue
    rusd_kg = nom_kg / fx
    wh_u = w_usd.get((iso3, yr), w_usd_lat[iso3])
    u50, l50 = rusd_kg * (50.0 / eff[cc]), 60.0 * (rusd_kg / wh_u) * (50.0 / eff[cc])
    if not (0.20 <= u50 <= 15.0 and 0.3 <= l50 <= 800.0): continue
    mk = f"OFF_{iso3}_{re.sub(r'[^A-Z0-9]','',city.upper())[:18]}"
    if mk not in mkts:
        con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?,?,?,?,?,?,?,0.14);", (mk, f"{city}, {iso3}", iso3, reg, lat, lon, car))
        mkts.add(mk)
    mid += 1
    obs.append((mid,"OPEN_PRICES_ISOLATES","OBSERVED_MARKET",mk,cc,dt,nom_kg,exp_cur,rusd_kg,u50,l50,False))
print(f"  -> Open Prices Isolates + RUS/CHN cities: +{len(obs):,} rows.")
COMM_MAP = {"rice":"RICE_MILLED","wheat":"WHEAT_FLOUR","maize":"WHEAT_FLOUR","beans":"BEANS_DRY","lentils":"LENTILS_DRY","soy":"SOYBEANS_DRY","beef":"BEEF_FRESH","pork":"PORK_FRESH","chicken":"POULTRY_CHIX","milk":"MILK_FLUID","eggs":"EGGS_CHIX","fish":"FISH_FRESH"}
existing_keys = set(con.execute("SELECT (m.country_iso3, LOWER(TRIM(m.market_name)), f.commodity_code, CAST(f.obs_date AS VARCHAR)) FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE m.country_iso3 IN ('RUS','CHN');").fetchall())

for slug, iso3, reg, car in [("russian-federation","RUS","EUROPE",0.85), ("china","CHN","ASIA",0.08)]:
    if iso3 not in w_usd_lat or iso3 not in fx_lat: continue
    q = urllib.parse.urlencode({"q": f"wfp food prices {slug}", "rows": 3})
    pkg = json.loads(urllib.request.urlopen(urllib.request.Request(f"https://data.humdata.org/api/3/action/package_search?{q}", headers={"User-Agent":"ProteinAtlas/1.0"}), timeout=25).read().decode())
    csv_url = next((res["url"] for ds in pkg.get("result",{}).get("results",[]) for res in ds.get("resources",[]) if str(res.get("url","")).lower().endswith(".csv") and "global" not in str(res.get("url","")).lower()), None)
    if not csv_url: continue
    df = pd.read_csv(io.BytesIO(urllib.request.urlopen(urllib.request.Request(csv_url, headers={"User-Agent":"ProteinAtlas/1.0"}), timeout=45).read()), low_memory=False)
    if len(df) > 1 and str(df.iloc[0,0]).startswith("#"): df = df.iloc[1:].reset_index(drop=True)
    cols = {c.lower(): c for c in df.columns}
    if not all(k in cols for k in ("date","market","commodity","price","latitude","longitude")): continue
    df["p_num"], df["lat_n"], df["lon_n"] = pd.to_numeric(df[cols["price"]], errors="coerce"), pd.to_numeric(df[cols["latitude"]], errors="coerce"), pd.to_numeric(df[cols["longitude"]], errors="coerce")
    added = 0
    for r in df.dropna(subset=["p_num","lat_n","lon_n"]).itertuples(index=False):
        dt = str(getattr(r, cols["date"]))[:10]
        if len(dt) < 10: continue
        mkt_nm = str(getattr(r, cols["market"])).strip()
        cc = next((v for k, v in COMM_MAP.items() if k in str(getattr(r, cols["commodity"])).lower()), None)
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
        obs.append((mid,"HDX_RUS_CHN","OBSERVED_MARKET",mk,cc,dt,nom_kg,"LCU",rusd_kg,u50,l50,False))
        existing_keys.add((iso3, mkt_nm.lower(), cc, dt))
        added += 1
    print(f"  -> HDX {iso3}: +{added:,} sub-national market rows.")
CAN_GEO = {"Canada":("CAN_NAT","Canada (StatCan)",56.13,-106.35,0.96),"Newfoundland and Labrador":("CAN_NL","Newfoundland & Labrador, CA",47.56,-52.71,0.10),"Prince Edward Island":("CAN_PE","Prince Edward Island, CA",46.24,-63.13,0.95),"Nova Scotia":("CAN_NS","Nova Scotia, CA",44.65,-63.57,0.15),"New Brunswick":("CAN_NB","New Brunswick, CA",45.96,-66.64,0.20),"Quebec":("CAN_QC","Quebec (Montreal), CA",45.50,-73.57,0.22),"Ontario":("CAN_ON","Ontario (Toronto), CA",43.65,-79.38,0.28),"Manitoba":("CAN_MB","Manitoba (Winnipeg), CA",49.90,-97.14,3.10),"Saskatchewan":("CAN_SK","Saskatchewan (Regina), CA",50.45,-104.62,12.50),"Alberta":("CAN_AB","Alberta (Calgary), CA",51.05,-114.07,2.30),"British Columbia":("CAN_BC","British Columbia (Vancouver), CA",49.28,-123.12,0.12)}
CAN_PROD = {"Beef stewing cuts, per kilogram":("BEEF_FRESH",1.0),"Beef striploin cuts, per kilogram":("BEEF_FRESH",1.0),"Pork loin cuts, per kilogram":("PORK_FRESH",1.0),"Chicken breasts, per kilogram":("POULTRY_CHIX",1.0),"Whole chicken, per kilogram":("POULTRY_CHIX",1.0),"Eggs, 1 dozen":("EGGS_CHIX",0.72),"Milk, 2 litres":("MILK_FLUID",2.06),"Milk, 4 litres":("MILK_FLUID",4.12),"Wheat flour, 2.5 kilograms":("WHEAT_FLOUR",2.5),"Dry or fresh lentils, 900 grams":("LENTILS_DRY",0.9),"Canned baked beans, 398 millilitres":("BEANS_DRY",0.24)}
print("[PHASE 9.3] Downloading Statistics Canada Table 18-10-0245-01 ZIP...")
zraw = urllib.request.urlopen(urllib.request.Request("https://www150.statcan.gc.ca/n1/tbl/csv/18100245-eng.zip", headers={"User-Agent":"ProteinAtlas/1.0"}), timeout=60).read()
with zipfile.ZipFile(io.BytesIO(zraw)) as zf:
    cname = next(n for n in zf.namelist() if n.endswith(".csv") and "MetaData" not in n)
    sc = pd.read_csv(zf.open(cname), usecols=["REF_DATE","GEO","Products","VALUE"], low_memory=False)
sc = sc[sc["GEO"].isin(CAN_GEO) & sc["Products"].isin(CAN_PROD)].dropna(subset=["VALUE"])
sc["val"] = pd.to_numeric(sc["VALUE"], errors="coerce")
can_added = 0
for r in sc[sc["val"] > 0].itertuples(index=False):
    dt = f"{str(r.REF_DATE)[:7]}-15"
    yr = int(dt[:4])
    cc, kg = CAN_PROD[r.Products]
    if cc not in eff or "CAN" not in w_usd_lat or "CAN" not in fx_lat: continue
    mk, mlabel, lat, lon, car = CAN_GEO[r.GEO]
    fx = fx_wb.get(("CAN", yr), fx_lat["CAN"])
    nom_kg = float(r.val) / kg
    rusd_kg = nom_kg / fx
    wh_u = w_usd.get(("CAN", yr), w_usd_lat["CAN"])
    u50, l50 = rusd_kg * (50.0 / eff[cc]), 60.0 * (rusd_kg / wh_u) * (50.0 / eff[cc])
    if not (0.10 <= u50 <= 25.0): continue
    if mk not in mkts:
        con.execute("INSERT OR REPLACE INTO dim_markets VALUES (?,?,'CAN','NORTH_AMERICA',?,?,?,0.12);", (mk, mlabel, lat, lon, car))
        mkts.add(mk)
    mid += 1
    obs.append((mid,"STATCAN_RETAIL","OBSERVED_MARKET",mk,cc,dt,nom_kg,"CAD",rusd_kg,u50,l50,False))
    can_added += 1
print(f"  -> StatCan Canada + 10 Provinces: +{can_added:,} monthly retail rows.")
if obs:
    con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)
print(f"[PHASE 9 COMPLETE] Inserted {len(obs):,} total rows across {len(mkts)} Isolate, RUS, CHN & CAN markets!")
print(con.execute("SELECT source_dataset, commodity_code, COUNT(*) n, ROUND(MEDIAN(usd_per_50g_diaas),2) med_usd50, ROUND(MEDIAN(labor_min_50g_diaas),1) med_min FROM fact_protein_observations WHERE source_dataset IN ('OPEN_PRICES_ISOLATES','HDX_RUS_CHN','STATCAN_RETAIL') GROUP BY 1,2 ORDER BY 1,3 DESC;").df().to_string(index=False))
con.close()
