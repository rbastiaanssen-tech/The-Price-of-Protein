import io, json, urllib.request, duckdb, pandas as pd

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'FAO_ILO_PRIMARY';")
eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())
w_map = dict(con.execute("SELECT (country_iso3, year), unskilled_wage_hr FROM dim_macro_wages WHERE unskilled_wage_hr > 0;").fetchall())
print(f"[PHASE 4] Loaded {len(w_map):,} verified LCU wages from dim_macro_wages.")

try:
    req = urllib.request.Request("https://sdmx.ilo.org/rest/data/ILO,DF_EAR_4MMN_CUR_NB_A/all?startPeriod=1990", headers={"Accept": "application/vnd.sdmx.data+csv;version=1.0.0", "User-Agent": "Mozilla/5.0"})
    ilo = pd.read_csv(io.BytesIO(urllib.request.urlopen(req, timeout=45).read()), low_memory=False)
    ilo = ilo[(ilo["CLASSIF1"] == "CUR_TYPE_LCU") & ilo["OBS_VALUE"].notna()]
    for (iso, yr), val in (ilo.groupby(["REF_AREA", "TIME_PERIOD"])["OBS_VALUE"].mean() / 173.33).items():
        w_map[(str(iso), int(yr))] = float(val)
    print(f"  -> Enriched to {len(w_map):,} LCU hourly wage pairs via sdmx.ilo.org.")
except Exception as e:
    print(f"  -> ILO SDMX skipped ({e}); using {len(w_map):,} DB wages.")

def wb(ind):
    u = f"https://api.worldbank.org/v2/country/all/indicator/{ind}?format=json&per_page=25000&date=1990:2026"
    js = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=45).read().decode())
    return {(r["countryiso3code"], int(r["date"])): float(r["value"]) for r in (js[1] or []) if r.get("value") is not None and r.get("countryiso3code")}

ppp, cpi = wb("PA.NUS.PPP"), wb("FP.CPI.TOTL")
c26 = {iso: v for (iso, _), v in sorted(cpi.items())}
AREAS = {
    41:("CHN","China","ASIA",35.86,104.19,0.08,"CNY"), 110:("JPN","Japan","ASIA",36.20,138.25,0.03,"JPY"),
    117:("KOR","South Korea","ASIA",35.91,127.77,0.03,"KRW"), 100:("IND","India","ASIA",20.59,78.96,0.11,"INR"),
    101:("IDN","Indonesia","ASIA",-0.79,113.92,0.09,"IDR"), 216:("THA","Thailand","ASIA",15.87,100.99,0.24,"THB"),
    237:("VNM","Viet Nam","ASIA",14.06,108.28,0.07,"VND"), 21:("BRA","Brazil","LATAM",-14.24,-51.93,0.27,"BRL"),
    9:("ARG","Argentina","LATAM",-38.42,-63.62,0.92,"ARS"), 138:("MEX","Mexico","LATAM",23.63,-102.55,0.18,"MXN"),
    40:("CHL","Chile","LATAM",-35.68,-71.54,0.07,"CLP"), 44:("COL","Colombia","LATAM",4.57,-74.30,0.04,"COP"),
    169:("PER","Peru","LATAM",-9.19,-75.02,0.11,"PEN"), 33:("CAN","Canada","NORTH_AMERICA",56.13,-106.35,0.96,"CAD"),
    10:("AUS","Australia","OCEANIA",-25.27,133.78,1.15,"AUD"), 156:("NZL","New Zealand","OCEANIA",-40.90,174.89,0.11,"NZD"),
    202:("ZAF","South Africa","AFRICA",-30.56,22.94,0.20,"ZAR")
}
ITEMS = {15:"WHEAT_FLOUR",27:"RICE_MILLED",56:"MAIZE_GRAIN",176:"DRY_BEANS",236:"SOYBEANS",867:"BEEF_BONELESS",1035:"PORK_FRESH",1058:"CHICKEN_WHOLE",882:"MILK_FRESH",1062:"EGGS_FRESH"}
fu = f"https://fenixservices.fao.org/faostat/api/v1/en/data/PP?area={','.join(map(str,AREAS))}&item={','.join(map(str,ITEMS))}&element=5530&year={','.join(map(str,range(1991,2026)))}"
fao = pd.read_csv(io.BytesIO(urllib.request.urlopen(urllib.request.Request(fu, headers={"Accept":"text/csv","User-Agent":"Mozilla/5.0"}), timeout=60).read()), low_memory=False)
fao["Value"] = pd.to_numeric(fao["Value"], errors="coerce")

mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
obs, mkts = [], set()
for _, r in fao.dropna(subset=["Value"]).iterrows():
    ac, ic, yr, val = int(r["Area Code"]), int(r["Item Code"]), int(r["Year"]), float(r["Value"])
    if ac not in AREAS or ic not in ITEMS or val <= 0: continue
    iso, nm, reg, lat, lon, ar, cur = AREAS[ac]
    cc = ITEMS[ic]
    if cc not in eff or (iso,yr) not in w_map or (iso,yr) not in ppp or (iso,yr) not in cpi: continue
    wh, pp, cy = w_map[(iso,yr)], ppp[(iso,yr)], cpi[(iso,yr)]
    if wh <= 0 or pp <= 0 or cy <= 0: continue
    mk = f"FAO_ILO_{iso}"
    if mk not in mkts:
        con.execute("INSERT OR REPLACE INTO dim_markets VALUES (?,?,?,?,?,?,?,0.14);", (mk, f"{nm} (FAO/ILO)", iso, reg, lat, lon, ar))
        mkts.add(mk)
    nom, rusd = val / 1000.0, (val / 1000.0 / pp) * (c26[iso] / cy)
    mid += 1
    obs.append((mid,"FAO_ILO_PRIMARY","OBSERVED_MARKET",mk,cc,f"{yr:04d}-06-15",nom,cur,rusd,rusd*(50.0/eff[cc]),60.0*(nom/wh)*(50.0/eff[cc]),False))

if obs:
    con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)
print(f"[PHASE 4 COMPLETE] Inserted {len(obs):,} primary observations across {len(mkts)} countries.")
print(con.execute("SELECT m.region_macro, m.country_iso3, COUNT(*) AS n, MIN(f.obs_year) AS y0, MAX(f.obs_year) AS y1, ROUND(MEDIAN(f.usd_per_50g_diaas),2) AS med_usd, ROUND(MEDIAN(f.labor_min_50g_diaas),1) AS med_min FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.source_dataset='FAO_ILO_PRIMARY' GROUP BY 1,2 ORDER BY 1,3 DESC;").df().to_string(index=False))
con.close()
