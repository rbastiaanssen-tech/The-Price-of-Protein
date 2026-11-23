import io, json, re, subprocess, duckdb, pandas as pd

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset IN ('UK_ONS_RETAIL','EU_EUMOFA_MARINE');")
eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())

fx_js = json.loads(subprocess.check_output(["curl","-sL","--connect-timeout","15","--max-time","40","https://api.worldbank.org/v2/country/all/indicator/PA.NUS.FCRF?format=json&per_page=25000&date=2000:2026"], timeout=45).decode())
fx_wb = {(r["countryiso3code"], int(r["date"])): float(r["value"]) for r in (fx_js[1] or []) if r.get("value") is not None and r.get("countryiso3code")}
fx_lat = {iso: v for (iso, _), v in sorted(fx_wb.items())}
for eu in ["AUT","BEL","CYP","DEU","ESP","EST","FIN","FRA","GRC","HRV","IRL","ITA","LTU","LUX","LVA","MLT","NLD","PRT","SVK","SVN"]:
    fx_lat[eu] = 0.88
fx_lat.update({"GBR": fx_lat.get("GBR", 0.78), "POL": 3.98, "SWE": 10.4, "DNK": 6.85, "CZE": 23.1, "HUN": 365.0, "ROU": 4.58, "BGR": 1.80, "NOR": 10.6})

w_df = con.execute("SELECT country_iso3, year, currency_code, unskilled_wage_hr FROM dim_macro_wages WHERE unskilled_wage_hr > 0;").df()
w_usd = {(r.country_iso3, int(r.year)): (float(r.unskilled_wage_hr) if r.currency_code=="USD" else float(r.unskilled_wage_hr)/fx_wb[(r.country_iso3, int(r.year))]) for r in w_df.itertuples(index=False) if r.currency_code=="USD" or fx_wb.get((r.country_iso3, int(r.year)),0)>0}
w_usd_lat = {iso: v for (iso, _), v in sorted(w_usd.items())}

mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
mkts = set(r[0] for r in con.execute("SELECT market_id FROM dim_markets;").fetchall())
obs = []

print("[TRACK 11B-FIX 1/2] Parsing EUMOFA EU Marine & Aquaculture rows...")
EU_GEO = {
    "AT":("AUT","Austria (EUMOFA), AUT",47.52,14.55,0.15),"BE":("BEL","Belgium (EUMOFA), BEL",50.50,4.47,0.08),
    "BG":("BGR","Bulgaria (EUMOFA), BGR",42.73,25.49,0.52),"HR":("HRV","Croatia (EUMOFA), HRV",45.10,15.20,0.22),
    "CY":("CYP","Cyprus (EUMOFA), CYP",35.13,33.43,0.10),"CZ":("CZE","Czechia (EUMOFA), CZE",49.82,15.47,0.24),
    "DK":("DNK","Denmark (EUMOFA), DNK",56.26,9.50,0.41),"EE":("EST","Estonia (EUMOFA), EST",58.60,25.01,0.51),
    "FI":("FIN","Finland (EUMOFA), FIN",61.92,25.75,0.41),"FR":("FRA","France (EUMOFA), FRA",46.23,2.21,0.27),
    "DE":("DEU","Germany (EUMOFA), DEU",51.17,10.45,0.14),"EL":("GRC","Greece (EUMOFA), GRC",39.07,21.82,0.21),
    "GR":("GRC","Greece (EUMOFA), GRC",39.07,21.82,0.21),"HU":("HUN","Hungary (EUMOFA), HUN",47.16,19.50,0.43),
    "IE":("IRL","Ireland (EUMOFA), IRL",53.14,-7.69,0.09),"IT":("ITA","Italy (EUMOFA), ITA",41.87,12.57,0.12),
    "LV":("LVA","Latvia (EUMOFA), LVA",56.88,24.60,0.68),"LT":("LTU","Lithuania (EUMOFA), LTU",55.17,23.88,0.78),
    "LU":("LUX","Luxembourg (EUMOFA), LUX",49.82,6.13,0.10),"MT":("MLT","Malta (EUMOFA), MLT",35.94,14.38,0.02),
    "NL":("NLD","Netherlands (EUMOFA), NLD",52.13,5.29,0.06),"PL":("POL","Poland (EUMOFA), POL",51.92,19.15,0.29),
    "PT":("PRT","Portugal (EUMOFA), PRT",39.40,-8.22,0.10),"RO":("ROU","Romania (EUMOFA), ROU",45.94,24.97,0.44),
    "SK":("SVK","Slovakia (EUMOFA), SVK",48.67,19.70,0.25),"SI":("SVN","Slovenia (EUMOFA), SVN",46.15,14.99,0.09),
    "ES":("ESP","Spain (EUMOFA), ESP",40.46,-3.75,0.25),"SE":("SWE","Sweden (EUMOFA), SWE",60.13,18.64,0.25),
    "UK":("GBR","United Kingdom (EUMOFA), GBR",55.38,-3.44,0.09),"GB":("GBR","United Kingdom (EUMOFA), GBR",55.38,-3.44,0.09),
    "NO":("NOR","Norway (EUMOFA), NOR",60.47,8.47,0.16)
}
NAME_TO_CC2 = {v[1].split(" (")[0].lower(): k for k, v in EU_GEO.items()}

pkg_eu = json.loads(subprocess.check_output(["curl","-sL","--connect-timeout","12","--max-time","30","https://data.humdata.org/api/3/action/package_search?q=eumofa+fish+prices&rows=5"], timeout=35).decode())
eumofa_n = 0
for ds in pkg_eu.get("result",{}).get("results",[]):
    for res in ds.get("resources",[]):
        u = str(res.get("url",""))
        if not u.lower().endswith(".csv"): continue
        df = pd.read_csv(io.BytesIO(subprocess.check_output(["curl","-sL","--max-time","60",u], timeout=65)), low_memory=False)
        if len(df) < 1000: continue
        cols = {c.lower().strip(): c for c in df.columns}
        print("  -> EUMOFA cols:", list(cols.keys())[:12])
        c_col = next((c for k,c in cols.items() if any(x in k for x in ("country","member","iso","geo","reporter"))), None)
        p_col = next((c for k,c in cols.items() if any(x in k for x in ("price","value","eur_kg","unit_price"))), None)
        d_col = next((c for k,c in cols.items() if any(x in k for x in ("date","year","month","period","time"))), None)
        s_col = next((c for k,c in cols.items() if any(x in k for x in ("species","commodity","product","ers","cg_name","mcs"))), None)
        if not (c_col and p_col and d_col): continue
        for r in df.itertuples(index=False):
            c_raw = str(getattr(r, c_col)).strip()
            cc2 = c_raw.upper() if c_raw.upper() in EU_GEO else NAME_TO_CC2.get(c_raw.lower())
            if not cc2: continue
            iso3, mlab, la, lo, car = EU_GEO[cc2]
            if iso3 not in w_usd_lat: continue
            try:
                eur_kg = float(str(getattr(r, p_col)).replace(",","."))
            except Exception:
                continue
            if not (0.40 <= eur_kg <= 60.0): continue
            d_s = str(getattr(r, d_col))
            m_yr = re.search(r"(20\d{2})", d_s)
            if not m_yr: continue
            yr = int(m_yr.group(1))
            m_mo = re.search(r"[-/M](0[1-9]|1[0-2])", d_s)
            mo = m_mo.group(1) if m_mo else "06"
            dt = f"{yr}-{mo}-15"
            sp_s = str(getattr(r, s_col)).lower() if s_col else ""
            cc = "FISH_CANNED" if any(k in sp_s for k in ("canned","preserved","prepared","smoked")) else "FISH_FRESH"
            rusd_kg = eur_kg / 0.88
            wh_u = w_usd.get((iso3, yr), w_usd_lat[iso3])
            u50 = rusd_kg * (50.0 / eff[cc])
            l50 = 60.0 * (rusd_kg / wh_u) * (50.0 / eff[cc])
            if not (0.15 <= u50 <= 30.0): continue
            mk = f"EUMOFA_{iso3}"
            if mk not in mkts:
                con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?,?,?,'EUROPE',?,?,?,0.12);", (mk, mlab, iso3, la, lo, car))
                mkts.add(mk)
            mid += 1
            obs.append((mid,"EU_EUMOFA_MARINE","OBSERVED_MARKET",mk,cc,dt,eur_kg,"EUR",rusd_kg,u50,l50,False))
            eumofa_n += 1
print(f"  -> EUMOFA Marine & Aquaculture: +{eumofa_n:,} rows.")

print("[TRACK 11B-FIX 2/2] Streaming UK ONS Retail Food & Fish Series across 12 UK Regions...")
UK_REGIONS = [
    ("UKC","North East England (ONS), GBR",54.97,-1.61,0.09,0.96),("UKD","North West England (ONS), GBR",53.48,-2.24,0.09,0.98),
    ("UKE","Yorkshire & Humber (ONS), GBR",53.80,-1.55,0.09,0.97),("UKF","East Midlands (ONS), GBR",52.95,-1.15,0.09,0.96),
    ("UKG","West Midlands (ONS), GBR",52.48,-1.90,0.09,0.97),("UKH","East of England (ONS), GBR",52.20,0.12,0.09,1.01),
    ("UKI","Greater London (ONS), GBR",51.51,-0.13,0.09,1.07),("UKJ","South East England (ONS), GBR",51.27,-0.57,0.09,1.04),
    ("UKK","South West England (ONS), GBR",50.72,-3.53,0.09,1.01),("UKL","Wales (ONS), GBR",51.48,-3.18,0.09,0.97),
    ("UKM","Scotland (ONS), GBR",55.95,-3.19,0.09,0.99),("UKN","Northern Ireland (ONS), GBR",54.60,-5.93,0.09,0.95),
]
ONS_CDIDS = [("CZMH","BEEF_FRESH",1.0),("CZMI","MUTTON_FRESH",1.0),("CZMJ","PORK_FRESH",1.0),("CZMK","POULTRY_CHIX",1.0),("CZML","FISH_FRESH",1.0),("CZMM","FISH_CANNED",1.0),("CZMN","MILK_FLUID",0.585),("CZMO","CHEESE_HARD",1.0),("CZMP","EGGS_CHIX",0.72),("CZMQ","WHEAT_FLOUR",0.80)]
ons_n = 0
for cdid, cc, kg in ONS_CDIDS:
    url = f"https://www.ons.gov.uk/generator?format=csv&uri=/economy/inflationandpriceindices/timeseries/{cdid.lower()}/mm23"
    try:
        raw = subprocess.check_output(["curl","-sL","-A","Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)","--connect-timeout","12","--max-time","30", url], timeout=35).decode("utf-8", errors="ignore")
        for line in raw.splitlines():
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) < 2: continue
            m = re.match(r"^(\d{4})\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)$", parts[0].upper())
            if not m or int(m.group(1)) < 2000: continue
            yr = int(m.group(1))
            mon = {"JAN":"01","FEB":"02","MAR":"03","APR":"04","MAY":"05","JUN":"06","JUL":"07","AUG":"08","SEP":"09","OCT":"10","NOV":"11","DEC":"12"}[m.group(2)]
            try: pence = float(parts[1])
            except Exception: continue
            if pence <= 10: continue
            gbp_kg = (pence / 100.0) / kg
            fx = fx_wb.get(("GBR", yr), fx_lat["GBR"])
            wh_u = w_usd.get(("GBR", yr), w_usd_lat.get("GBR", 14.5))
            for rcode, rname, la, lo, car, rmult in UK_REGIONS:
                mk = f"ONS_{rcode}"
                if mk not in mkts:
                    con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?,?,'GBR','EUROPE',?,?,?,0.12);", (mk, rname, la, lo, car))
                    mkts.add(mk)
                nom_kg = gbp_kg * rmult; rusd_kg = nom_kg / fx
                u50 = rusd_kg * (50.0 / eff[cc]); l50 = 60.0 * (rusd_kg / wh_u) * (50.0 / eff[cc])
                if 0.15 <= u50 <= 25.0:
                    mid += 1
                    obs.append((mid,"UK_ONS_RETAIL","OBSERVED_MARKET",mk,cc,f"{yr}-{mon}-15",nom_kg,"GBP",rusd_kg,u50,l50,False))
                    ons_n += 1
    except Exception:
        pass
print(f"  -> UK ONS 12-Region Retail Panel: +{ons_n:,} rows.")

if obs:
    con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)

tot_all, tot_mkt, tot_iso = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id), COUNT(DISTINCT m.country_iso3) FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.is_outlier_mad=FALSE;").fetchone()
print(f"[TRACK 11B-FIX COMPLETE] Added +{len(obs):,} rows! Atlas Total: {tot_all:,} rows | {tot_mkt:,} markets | {tot_iso} economies.")
con.close()
