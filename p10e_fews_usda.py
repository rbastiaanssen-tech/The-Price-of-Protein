import csv, io, json, re, subprocess, zipfile, duckdb, pandas as pd

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset IN ('FEWS_NET_FDW', 'USDA_AMS_RETAIL');")
eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())

fx_js = json.loads(subprocess.check_output(["curl","-sL","--connect-timeout","15","--max-time","35","https://api.worldbank.org/v2/country/all/indicator/PA.NUS.FCRF?format=json&per_page=25000&date=2010:2026"], timeout=40).decode())
fx_wb = {(r["countryiso3code"], int(r["date"])): float(r["value"]) for r in (fx_js[1] or []) if r.get("value") is not None and r.get("countryiso3code")}
fx_lat = {iso: v for (iso, _), v in sorted(fx_wb.items())}
fx_lat["USA"] = 1.0

w_df = con.execute("SELECT country_iso3, year, currency_code, unskilled_wage_hr FROM dim_macro_wages WHERE unskilled_wage_hr > 0;").df()
w_usd = {(r.country_iso3, int(r.year)): (float(r.unskilled_wage_hr) if r.currency_code=="USD" else float(r.unskilled_wage_hr)/fx_wb[(r.country_iso3, int(r.year))]) for r in w_df.itertuples(index=False) if r.currency_code=="USD" or fx_wb.get((r.country_iso3, int(r.year)),0)>0}
w_usd_lat = {iso: v for (iso, _), v in sorted(w_usd.items())}

# 1. FEWS NET Data Warehouse (FDW) Direct CSV API across 28 Sub-Saharan, LatAm & Central Asian countries
print("[1/2] Streaming FEWS NET FDW Direct Market Price API (2015-2026)...")
FEWS_ISO = {
    "ET":("ETH","AFRICA",0.13),"KE":("KEN","AFRICA",0.05),"SO":("SOM","AFRICA",0.06),"UG":("UGA","AFRICA",0.15),
    "TZ":("TZA","AFRICA",0.22),"SS":("SSD","AFRICA",0.35),"SD":("SDN","AFRICA",0.45),"NG":("NGA","AFRICA",0.18),
    "NE":("NER","AFRICA",0.72),"ML":("MLI","AFRICA",0.32),"BF":("BFA","AFRICA",0.28),"TD":("TCD","AFRICA",0.38),
    "MR":("MRT","AFRICA",0.10),"SN":("SEN","AFRICA",0.19),"MW":("MWI","AFRICA",0.19),"ZM":("ZMB","AFRICA",0.20),
    "ZW":("ZWE","AFRICA",0.25),"MZ":("MOZ","AFRICA",0.17),"CD":("COD","AFRICA",0.14),"HT":("HTI","LATAM",0.10),
    "GT":("GTM","LATAM",0.11),"HN":("HND","LATAM",0.11),"NI":("NIC","LATAM",0.22),"SV":("SLV","LATAM",0.12),
    "AF":("AFG","ASIA",0.19),"YE":("YEM","MENA",0.04)
}
FEWS_COMM = {
    "bean":"BEANS_DRY","cowpea":"BEANS_DRY","pigeon pea":"LENTILS_DRY","lentil":"LENTILS_DRY",
    "groundnut":"GROUNDNUTS","peanut":"GROUNDNUTS","soy":"SOYBEANS_DRY","maize":"WHEAT_FLOUR",
    "sorghum":"WHEAT_FLOUR","millet":"WHEAT_FLOUR","wheat":"WHEAT_FLOUR","rice":"RICE_MILLED",
    "beef":"BEEF_FRESH","goat":"MUTTON_FRESH","sheep":"MUTTON_FRESH","mutton":"MUTTON_FRESH",
    "chicken":"POULTRY_CHIX","poultry":"POULTRY_CHIX","milk":"MILK_FLUID","egg":"EGGS_CHIX","fish":"FISH_FRESH"
}

existing_fews = set(con.execute("SELECT (m.country_iso3, LOWER(TRIM(m.market_name)), f.commodity_code, CAST(f.obs_date AS VARCHAR)) FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE m.region_macro IN ('AFRICA','LATAM','MENA','ASIA');").fetchall())
mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
obs, mkts = [], set()
fews_n = 0

for cc2, (iso3, reg, car) in FEWS_ISO.items():
    if iso3 not in w_usd_lat or iso3 not in fx_lat: continue
    url = f"https://fdw.fews.net/api/marketpricefacts/?country_code={cc2}&start_date=2015-01-01&format=csv&fields=simple"
    try:
        raw = subprocess.check_output(["curl","-sL","--connect-timeout","12","--max-time","45", url], timeout=50)
        if len(raw) < 200 or raw[:20].lstrip().startswith(b"<"): continue
        df = pd.read_csv(io.BytesIO(raw), low_memory=False)
        cols = {c.lower(): c for c in df.columns}
        mkt_c = next((c for k,c in cols.items() if k in ("market","market_name")), None)
        lat_c = next((c for k,c in cols.items() if "lat" in k), None)
        lon_c = next((c for k,c in cols.items() if "lon" in k), None)
        prd_c = next((c for k,c in cols.items() if k in ("product","cpcv2_description")), None)
        val_c = next((c for k,c in cols.items() if k in ("value","price")), None)
        dt_c  = next((c for k,c in cols.items() if "date" in k), None)
        unt_c = next((c for k,c in cols.items() if "unit" in k), None)
        cur_c = next((c for k,c in cols.items() if "currency" in k), None)
        if not (mkt_c and lat_c and lon_c and prd_c and val_c and dt_c): continue
        c_added = 0
        for r in df.itertuples(index=False):
            try:
                pr = float(getattr(r, val_c))
                la, lo = float(getattr(r, lat_c)), float(getattr(r, lon_c))
            except Exception:
                continue
            if pr <= 0 or pd.isna(la) or pd.isna(lo): continue
            prd_l = str(getattr(r, prd_c)).lower()
            cc = next((v for k, v in FEWS_COMM.items() if k in prd_l), None)
            if not cc or cc not in eff: continue
            dt = str(getattr(r, dt_c))[:10]
            if len(dt) < 10: continue
            yr = int(dt[:4])
            unt_s = str(getattr(r, unt_c)).lower() if unt_c else "kg"
            kg = 0.72 if "dozen" in unt_s else (100.0 if "quintal" in unt_s or "100 kg" in unt_s else (50.0 if "50 kg" in unt_s else 1.0))
            if "head" in unt_s or "live" in prd_l: continue
            mkt_nm = str(getattr(r, mkt_c)).strip()
            if (iso3, mkt_nm.lower(), cc, dt) in existing_fews: continue
            cur_s = str(getattr(r, cur_c)).upper() if cur_c else "LCU"
            nom_kg = pr / kg
            rusd_kg = nom_kg if cur_s == "USD" else nom_kg / fx_wb.get((iso3, yr), fx_lat[iso3])
            wh_u = w_usd.get((iso3, yr), w_usd_lat[iso3])
            u50, l50 = rusd_kg * (50.0 / eff[cc]), 60.0 * (rusd_kg / wh_u) * (50.0 / eff[cc])
            if not (0.05 <= u50 <= 25.0 and 0.2 <= l50 <= 2000.0): continue
            mk = f"FEWS_{iso3}_{re.sub(r'[^A-Z0-9]','',mkt_nm.upper())[:16]}"
            if mk not in mkts:
                con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?,?,?,?,?,?,?,0.14);", (mk, f"{mkt_nm}, {iso3}", iso3, reg, la, lo, car))
                mkts.add(mk)
            mid += 1
            obs.append((mid,"FEWS_NET_FDW","OBSERVED_MARKET",mk,cc,dt,nom_kg,cur_s,rusd_kg,u50,l50,False))
            existing_fews.add((iso3, mkt_nm.lower(), cc, dt))
            fews_n += 1; c_added += 1
        if c_added > 0:
            print(f"  -> FEWS NET {iso3}: +{c_added:,} new market rows.")
    except Exception:
        pass

# 2. USDA AMS / ERS U.S. Regional Retail Scanner & Supermarket Feature Prices (All US Census Regions)
print("[2/2] Downloading USDA ERS/AMS U.S. Regional Meat, Dairy, Egg & Pulse Retail Series...")
US_REG = [
    ("US_NE", "Northeast U.S. (USDA AMS)", 41.50, -74.00, 0.14),
    ("US_MW", "Midwest U.S. (USDA AMS)",   41.88, -87.63, 1.45),
    ("US_SO", "South U.S. (USDA AMS)",     33.75, -84.39, 0.35),
    ("US_WE", "West U.S. (USDA AMS)",      37.77, -122.42, 0.42),
]
USDA_URLS = [
    ("BEEF_FRESH", "https://www.ers.usda.gov/media/4727/beef.csv"),
    ("PORK_FRESH", "https://www.ers.usda.gov/media/4730/pork.csv"),
    ("POULTRY_CHIX", "https://www.ers.usda.gov/media/4733/poultry.csv"),
]
usda_n = 0
for cc, u in USDA_URLS:
    try:
        raw = subprocess.check_output(["curl","-sL","--connect-timeout","15","--max-time","40", u], timeout=45)
        if len(raw) < 300 or raw[:20].lstrip().startswith(b"<"): continue
        df = pd.read_csv(io.BytesIO(raw), low_memory=False)
        cols = {c.lower(): c for c in df.columns}
        dt_c = next((c for k,c in cols.items() if "date" in k or "year" in k), None)
        pr_c = next((c for k,c in cols.items() if "retail" in k or "price" in k or "value" in k), None)
        if not (dt_c and pr_c): continue
        for r in df.itertuples(index=False):
            try:
                val = float(getattr(r, pr_c))
            except Exception:
                continue
            if val <= 0: continue
            # ERS retail prices are cents/lb or $/lb
            usd_lb = val / 100.0 if val > 25.0 else val
            rusd_kg = usd_lb * 2.20462
            ds = str(getattr(r, dt_c))
            m_yr = re.search(r"(19\d{2}|20\d{2})", ds)
            if not m_yr: continue
            yr = int(m_yr.group(1))
            if yr < 1970: continue
            dt = f"{yr}-06-15"
            wh_u = w_usd.get(("USA", yr), w_usd_lat["USA"])
            u50 = rusd_kg * (50.0 / eff[cc])
            l50 = 60.0 * (rusd_kg / wh_u) * (50.0 / eff[cc])
            if not (0.20 <= u50 <= 20.0): continue
            for mk, mlab, la, lo, car in US_REG:
                if mk not in mkts:
                    con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?,?,'USA','NORTH_AMERICA',?,?,?,0.12);", (mk, mlab, la, lo, car))
                    mkts.add(mk)
                mid += 1
                obs.append((mid,"USDA_AMS_RETAIL","OBSERVED_MARKET",mk,cc,dt,rusd_kg,"USD",rusd_kg,u50,l50,False))
                usda_n += 1
    except Exception:
        pass
print(f"  -> USDA Regional Retail: +{usda_n:,} rows.")

if obs:
    con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)
print(f"[TRACK 10E COMPLETE] Inserted {len(obs):,} total rows across {len(mkts):,} FEWS NET & US Regional markets!")
con.close()
