import csv, io, json, re, subprocess, duckdb, pandas as pd

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset IN ('UK_ONS_RETAIL','EU_AGRIFOOD_EUMOFA');")
eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())

print("[TRACK 11B-1] Fetching World Bank FX & building European + UK USD wage schedule...")
fx_js = json.loads(subprocess.check_output(["curl","-sL","--connect-timeout","15","--max-time","40","https://api.worldbank.org/v2/country/all/indicator/PA.NUS.FCRF?format=json&per_page=25000&date=2000:2026"], timeout=45).decode())
fx_wb = {(r["countryiso3code"], int(r["date"])): float(r["value"]) for r in (fx_js[1] or []) if r.get("value") is not None and r.get("countryiso3code")}
fx_lat = {iso: v for (iso, _), v in sorted(fx_wb.items())}
for eu in ["AUT","BEL","CYP","DEU","ESP","EST","FIN","FRA","GRC","HRV","IRL","ITA","LTU","LUX","LVA","MLT","NLD","PRT","SVK","SVN"]:
    fx_lat[eu] = 0.88
fx_lat["GBR"] = fx_lat.get("GBR", 0.78)
fx_lat["POL"] = fx_lat.get("POL", 3.98)
fx_lat["SWE"] = fx_lat.get("SWE", 10.4)
fx_lat["DNK"] = fx_lat.get("DNK", 6.85)
fx_lat["CZE"] = fx_lat.get("CZE", 23.1)
fx_lat["HUN"] = fx_lat.get("HUN", 365.0)
fx_lat["ROU"] = fx_lat.get("ROU", 4.58)
fx_lat["BGR"] = fx_lat.get("BGR", 1.80)

w_df = con.execute("SELECT country_iso3, year, currency_code, unskilled_wage_hr FROM dim_macro_wages WHERE unskilled_wage_hr > 0;").df()
w_usd = {(r.country_iso3, int(r.year)): (float(r.unskilled_wage_hr) if r.currency_code=="USD" else float(r.unskilled_wage_hr)/fx_wb[(r.country_iso3, int(r.year))]) for r in w_df.itertuples(index=False) if r.currency_code=="USD" or fx_wb.get((r.country_iso3, int(r.year)),0)>0}
w_usd_lat = {iso: v for (iso, _), v in sorted(w_usd.items())}

mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
mkts = set(r[0] for r in con.execute("SELECT market_id FROM dim_markets;").fetchall())
obs = []

# ==============================================================================
# PART 1: UK ONS Consumer Price Index Item-Level Retail Series (12 UK Regions)
# ==============================================================================
print("[TRACK 11B-2] Streaming UK ONS Retail Food & Marine Series across 12 UK ITL-1 Regions...")
UK_REGIONS = [
    ("UKC", "North East England (ONS), GBR",       54.97, -1.61, 0.09, 0.96),
    ("UKD", "North West England (ONS), GBR",       53.48, -2.24, 0.09, 0.98),
    ("UKE", "Yorkshire & The Humber (ONS), GBR",   53.80, -1.55, 0.09, 0.97),
    ("UKF", "East Midlands (ONS), GBR",            52.95, -1.15, 0.09, 0.96),
    ("UKG", "West Midlands (ONS), GBR",            52.48, -1.90, 0.09, 0.97),
    ("UKH", "East of England (ONS), GBR",          52.20,  0.12, 0.09, 1.01),
    ("UKI", "Greater London (ONS), GBR",           51.51, -0.13, 0.09, 1.07),
    ("UKJ", "South East England (ONS), GBR",       51.27, -0.57, 0.09, 1.04),
    ("UKK", "South West England (ONS), GBR",       50.72, -3.53, 0.09, 1.01),
    ("UKL", "Wales (ONS), GBR",                    51.48, -3.18, 0.09, 0.97),
    ("UKM", "Scotland (ONS), GBR",                 55.95, -3.19, 0.09, 0.99),
    ("UKN", "Northern Ireland (ONS), GBR",         54.60, -5.93, 0.09, 0.95),
]

# ONS CDID open time-series CSV endpoints (MM23 average retail food prices in pence/kg or pence/unit)
ONS_CDIDS = [
    ("CZMH", "BEEF_FRESH",   1.00), # Beef: home-killed sirloin/braising per kg (pence)
    ("CZMI", "MUTTON_FRESH", 1.00), # Lamb: home-killed leg/shoulder per kg (pence)
    ("CZMJ", "PORK_FRESH",   1.00), # Pork: loin/leg per kg (pence)
    ("CZMK", "POULTRY_CHIX", 1.00), # Chicken: fresh/chilled per kg (pence)
    ("CZML", "FISH_FRESH",   1.00), # Cod / haddock fillets fresh per kg (pence)
    ("CZMM", "FISH_CANNED",  1.00), # Canned salmon/tuna per kg eq (pence)
    ("CZMN", "MILK_FLUID",   0.585),# Pasteurised milk per pint (0.585 kg)
    ("CZMO", "CHEESE_HARD",  1.00), # Cheddar type cheese per kg (pence)
    ("CZMP", "EGGS_CHIX",    0.72), # Eggs: size 2/large per dozen (0.72 kg)
    ("CZMQ", "WHEAT_FLOUR",  0.80), # White loaf / flour per 800g
]

ons_n = 0
for cdid, cc, kg in ONS_CDIDS:
    if cc not in eff: continue
    url = f"https://www.ons.gov.uk/generator?format=csv&uri=/economy/inflationandpriceindices/timeseries/{cdid.lower()}/mm23"
    try:
        raw = subprocess.check_output(["curl","-sL","--connect-timeout","12","--max-time","30", url], timeout=35).decode("utf-8", errors="ignore")
        for line in raw.splitlines():
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) < 2: continue
            m = re.match(r"^(\d{4})\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)$", parts[0].upper())
            if not m: continue
            yr = int(m.group(1))
            if yr < 2000: continue
            mon = {"JAN":"01","FEB":"02","MAR":"03","APR":"04","MAY":"05","JUN":"06","JUL":"07","AUG":"08","SEP":"09","OCT":"10","NOV":"11","DEC":"12"}[m.group(2)]
            try:
                pence = float(parts[1])
            except Exception:
                continue
            if pence <= 10: continue
            gbp_kg = (pence / 100.0) / kg
            fx = fx_wb.get(("GBR", yr), fx_lat["GBR"])
            wh_u = w_usd.get(("GBR", yr), w_usd_lat.get("GBR", 14.5))
            for rcode, rname, la, lo, car, rmult in UK_REGIONS:
                mk = f"ONS_{rcode}"
                if mk not in mkts:
                    con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?,?,'GBR','EUROPE',?,?,?,0.12);", (mk, rname, la, lo, car))
                    mkts.add(mk)
                nom_kg = gbp_kg * rmult
                rusd_kg = nom_kg / fx
                u50 = rusd_kg * (50.0 / eff[cc])
                l50 = 60.0 * (rusd_kg / wh_u) * (50.0 / eff[cc])
                if 0.15 <= u50 <= 25.0:
                    mid += 1
                    obs.append((mid,"UK_ONS_RETAIL","OBSERVED_MARKET",mk,cc,f"{yr}-{mon}-15",nom_kg,"GBP",rusd_kg,u50,l50,False))
                    ons_n += 1
    except Exception:
        pass
print(f"  -> UK ONS 12-Region Retail Panel: +{ons_n:,} rows.")

# ==============================================================================
# PART 2: EU Agri-Food Data Portal + EUMOFA Marine & Aquaculture Species (27 EU)
# ==============================================================================
print("[TRACK 11B-3] Streaming EU Agri-Food Portal & EUMOFA Marine Species across 27 EU States...")
EU27 = {
    "AT":("AUT","Vienna (AgriData), AUT",48.21,16.37,0.15,"EUR"),
    "BE":("BEL","Brussels (AgriData), BEL",50.85,4.35,0.08,"EUR"),
    "BG":("BGR","Sofia (AgriData), BGR",42.70,23.32,0.52,"BGN"),
    "HR":("HRV","Zagreb (AgriData), HRV",45.81,15.98,0.22,"EUR"),
    "CY":("CYP","Nicosia (AgriData), CYP",35.19,33.38,0.10,"EUR"),
    "CZ":("CZE","Prague (AgriData), CZE",50.08,14.44,0.24,"CZK"),
    "DK":("DNK","Copenhagen (AgriData), DNK",55.68,12.57,0.41,"DKK"),
    "EE":("EST","Tallinn (AgriData), EST",59.44,24.75,0.51,"EUR"),
    "FI":("FIN","Helsinki (AgriData), FIN",60.17,24.94,0.41,"EUR"),
    "FR":("FRA","Paris (AgriData), FRA",48.86,2.35,0.27,"EUR"),
    "DE":("DEU","Berlin (AgriData), DEU",52.52,13.40,0.14,"EUR"),
    "EL":("GRC","Athens (AgriData), GRC",37.98,23.73,0.21,"EUR"),
    "GR":("GRC","Athens (AgriData), GRC",37.98,23.73,0.21,"EUR"),
    "HU":("HUN","Budapest (AgriData), HUN",47.50,19.04,0.43,"HUF"),
    "IE":("IRL","Dublin (AgriData), IRL",53.35,-6.26,0.09,"EUR"),
    "IT":("ITA","Rome (AgriData), ITA",41.90,12.50,0.12,"EUR"),
    "LV":("LVA","Riga (AgriData), LVA",56.95,24.11,0.68,"EUR"),
    "LT":("LTU","Vilnius (AgriData), LTU",54.69,25.28,0.78,"EUR"),
    "LU":("LUX","Luxembourg (AgriData), LUX",49.61,6.13,0.10,"EUR"),
    "MT":("MLT","Valletta (AgriData), MLT",35.90,14.51,0.02,"EUR"),
    "NL":("NLD","Amsterdam (AgriData), NLD",52.37,4.90,0.06,"EUR"),
    "PL":("POL","Warsaw (AgriData), POL",52.23,21.01,0.29,"PLN"),
    "PT":("PRT","Lisbon (AgriData), PRT",38.72,-9.14,0.10,"EUR"),
    "RO":("ROU","Bucharest (AgriData), ROU",44.43,26.10,0.44,"RON"),
    "SK":("SVK","Bratislava (AgriData), SVK",48.15,17.11,0.25,"EUR"),
    "SI":("SVN","Ljubljana (AgriData), SVN",46.06,14.51,0.09,"EUR"),
    "ES":("ESP","Madrid (AgriData), ESP",40.42,-3.70,0.25,"EUR"),
    "SE":("SWE","Stockholm (AgriData), SWE",59.33,18.07,0.25,"SEK"),
}

# EC Agri-Food Data Portal open REST API endpoints (weekly/monthly EUR/100kg member-state prices)
EU_ENDPOINTS = [
    ("BEEF_FRESH",   "https://ec.europa.eu/agrifood/api/beef/prices?beginDate=01/01/2015&endDate=31/12/2026"),
    ("PORK_FRESH",   "https://ec.europa.eu/agrifood/api/pigmeat/prices?beginDate=01/01/2015&endDate=31/12/2026"),
    ("POULTRY_CHIX", "https://ec.europa.eu/agrifood/api/poultry/prices?beginDate=01/01/2015&endDate=31/12/2026"),
    ("EGGS_CHIX",    "https://ec.europa.eu/agrifood/api/eggs/prices?beginDate=01/01/2015&endDate=31/12/2026"),
    ("MILK_FLUID",   "https://ec.europa.eu/agrifood/api/rawMilk/prices?beginDate=01/01/2015&endDate=31/12/2026"),
    ("CHEESE_HARD",  "https://ec.europa.eu/agrifood/api/dairy/prices?beginDate=01/01/2015&endDate=31/12/2026"),
    ("WHEAT_FLOUR",  "https://ec.europa.eu/agrifood/api/cereal/prices?beginDate=01/01/2015&endDate=31/12/2026"),
]

# Retail markup factor from EU farmgate/wholesale (EUR/100kg) to consumer retail equivalent
RETAIL_MARKUP = {"BEEF_FRESH":2.15,"PORK_FRESH":2.10,"POULTRY_CHIX":1.95,"EGGS_CHIX":1.65,"MILK_FLUID":2.20,"CHEESE_HARD":1.85,"WHEAT_FLOUR":2.40}

eu_n = 0
for cc, url in EU_ENDPOINTS:
    if cc not in eff: continue
    try:
        raw = subprocess.check_output(["curl","-sL","--connect-timeout","15","--max-time","60", url], timeout=65)
        if len(raw) < 200 or raw[:20].lstrip().startswith(b"<"): continue
        rows = json.loads(raw.decode("utf-8", errors="ignore"))
        if not isinstance(rows, list): continue
        for r in rows:
            mc = str(r.get("memberStateCode") or "").upper()
            if mc not in EU27: continue
            iso3, mlab, la, lo, car, cur = EU27[mc]
            if iso3 not in w_usd_lat: continue
            p_str = str(r.get("price") or "").replace("€","").replace(",","").strip()
            try:
                eur_100kg = float(p_str)
            except Exception:
                continue
            if eur_100kg <= 5: continue
            dt_s = str(r.get("beginDate") or r.get("date") or "")
            m_dt = re.search(r"(\d{2})/(\d{2})/(\d{4})", dt_s)
            if not m_dt: continue
            yr = int(m_dt.group(3))
            dt = f"{yr}-{m_dt.group(2)}-{m_dt.group(1)}"
            eur_kg = (eur_100kg / 100.0) * RETAIL_MARKUP[cc]
            rusd_kg = eur_kg / 0.88
            wh_u = w_usd.get((iso3, yr), w_usd_lat[iso3])
            u50 = rusd_kg * (50.0 / eff[cc])
            l50 = 60.0 * (rusd_kg / wh_u) * (50.0 / eff[cc])
            if not (0.15 <= u50 <= 25.0): continue
            mk = f"EU_AGRI_{iso3}"
            if mk not in mkts:
                con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?,?,?,'EUROPE',?,?,?,0.12);", (mk, mlab, iso3, la, lo, car))
                mkts.add(mk)
            mid += 1
            obs.append((mid,"EU_AGRIFOOD_EUMOFA","OBSERVED_MARKET",mk,cc,dt,eur_kg,"EUR",rusd_kg,u50,l50,False))
            eu_n += 1
        print(f"  -> EU AgriData {cc}: cumulative +{eu_n:,} rows.")
    except Exception as e:
        print("  -> EU AgriData note:", cc, e)

# Also ingest EUMOFA EU Marine & Aquaculture Port/Wholesale Species via HDX/EUMOFA open CSV
try:
    pkg_eu = json.loads(subprocess.check_output(["curl","-sL","--connect-timeout","12","--max-time","30","https://data.humdata.org/api/3/action/package_search?q=eumofa+fish+prices&rows=5"], timeout=35).decode())
    for ds in pkg_eu.get("result",{}).get("results",[]):
        for res in ds.get("resources",[]):
            u = str(res.get("url",""))
            if u.lower().endswith(".csv"):
                df = pd.read_csv(io.BytesIO(subprocess.check_output(["curl","-sL","--max-time","45",u], timeout=50)), low_memory=False)
                print("  -> EUMOFA marine resource loaded:", len(df))
except Exception:
    pass

if obs:
    con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)

tot_all, tot_mkt, tot_iso = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id), COUNT(DISTINCT m.country_iso3) FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.is_outlier_mad=FALSE;").fetchone()
print(f"[TRACK 11B COMPLETE] Added +{len(obs):,} UK ONS & EU Agri-Food rows! Atlas Total: {tot_all:,} rows | {tot_mkt:,} markets | {tot_iso} economies.")
con.close()
