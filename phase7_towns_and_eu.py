import json, re, time, urllib.parse, urllib.request, duckdb

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset IN ('EC_AGRIFOOD_WEEKLY', 'OPEN_PRICES_TOWN');")
eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())

ISO = {
    "AT":("AUT","Austria",47.52,14.55,0.15,"EUR"),"BE":("BEL","Belgium",50.50,4.47,0.08,"EUR"),"BG":("BGR","Bulgaria",42.73,25.49,0.51,"BGN"),
    "CH":("CHE","Switzerland",46.82,8.23,0.05,"CHF"),"CY":("CYP","Cyprus",35.13,33.43,0.08,"EUR"),"CZ":("CZE","Czechia",49.82,15.47,0.24,"CZK"),
    "DE":("DEU","Germany",51.17,10.45,0.14,"EUR"),"DK":("DNK","Denmark",56.26,9.50,0.41,"DKK"),"EE":("EST","Estonia",58.60,25.01,0.52,"EUR"),
    "EL":("GRC","Greece",39.07,21.82,0.20,"EUR"),"GR":("GRC","Greece",39.07,21.82,0.20,"EUR"),"ES":("ESP","Spain",40.46,-3.75,0.25,"EUR"),
    "FI":("FIN","Finland",61.92,25.75,0.41,"EUR"),"FR":("FRA","France",46.23,2.21,0.27,"EUR"),"GB":("GBR","United Kingdom",55.38,-3.44,0.09,"GBP"),
    "UK":("GBR","United Kingdom",55.38,-3.44,0.09,"GBP"),"HR":("HRV","Croatia",45.10,15.20,0.22,"EUR"),"HU":("HUN","Hungary",47.16,19.50,0.43,"HUF"),
    "IE":("IRL","Ireland",53.41,-8.24,0.09,"EUR"),"IT":("ITA","Italy",41.87,12.57,0.12,"EUR"),"LT":("LTU","Lithuania",55.17,23.88,0.81,"EUR"),
    "LU":("LUX","Luxembourg",49.82,6.13,0.10,"EUR"),"LV":("LVA","Latvia",56.88,24.60,0.72,"EUR"),"NL":("NLD","Netherlands",52.13,5.29,0.06,"EUR"),
    "PL":("POL","Poland",51.92,19.15,0.29,"PLN"),"PT":("PRT","Portugal",39.40,-8.22,0.10,"EUR"),"RO":("ROU","Romania",45.94,24.97,0.44,"RON"),
    "SE":("SWE","Sweden",60.13,18.64,0.25,"SEK"),"SI":("SVN","Slovenia",46.15,15.00,0.09,"EUR"),"SK":("SVK","Slovakia",48.67,19.70,0.25,"EUR"),
    "US":("USA","United States",39.83,-98.58,0.47,"USD"),"CA":("CAN","Canada",56.13,-106.35,0.96,"CAD"),"AU":("AUS","Australia",-25.27,133.78,1.15,"AUD")
}
w_df = con.execute("SELECT m.country_iso3, f.obs_year, MEDIAN(f.nominal_price_kg / (f.labor_min_50g_diaas / 60.0 / (50.0 / c.eff_protein_g_kg))) AS lcu_hr FROM fact_protein_observations f JOIN dim_markets m USING(market_id) JOIN dim_commodities c USING(commodity_code) WHERE f.source_dataset IN ('EUROSTAT_OFFICIAL','BLS_APU_METRO_TOWN','FAO_BULK_PRIMARY') AND f.labor_min_50g_diaas > 0 GROUP BY 1,2;").df()
w_yr = {(r.country_iso3, int(r.obs_year)): float(r.lcu_hr) for r in w_df.itertuples(index=False)}
w_lat = {iso: w for (iso, _), w in sorted(w_yr.items())}
print(f"[PHASE 7] Loaded {len(w_yr):,} verified country-year hourly wages across {len(w_lat)} countries.")
mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
obs, mkts = [], set()
MS = ",".join([k for k in ISO if k not in ("GB","UK","CH","US","GR","CA","AU")])
for ep, cc, div in [("beef","BEEF_BONELESS",100.0),("pigmeat","PORK_FRESH",100.0),("poultry","CHICKEN_WHOLE",100.0),("rawMilk","MILK_FRESH",100.0),("cereal","WHEAT_FLOUR",1000.0)]:
    if cc not in eff: continue
    u = f"https://api.tech.ec.europa.eu/agrifood/api/{ep}/prices?memberStateCodes={MS}&years=2020,2021,2022,2023,2024,2025,2026"
    try:
        rows = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent":"ProteinAtlas/1.0","Accept":"application/json"}), timeout=45).read().decode())
        print(f"  -> EC Agri-Food {ep}: {len(rows):,} records.")
    except Exception as e:
        print(f"  -> EC Agri-Food {ep} error: {e}"); continue
    for r in rows:
        ms = str(r.get("memberStateCode","")).upper()
        if ms not in ISO: continue
        iso3, cname, clat, clon, car, cur = ISO[ms]
        wh = w_lat.get(iso3, 0)
        if wh <= 0 or cur != "EUR": continue
        try: p_val = float(re.sub(r"[^0-9.]", "", str(r.get("price","")).replace(",",".")))
        except ValueError: continue
        m_dt = re.match(r"(\d{2})/(\d{2})/(\d{4})", str(r.get("beginDate","")))
        if p_val <= 0 or not m_dt: continue
        dd, mm, yyyy = m_dt.groups()
        wh_y = w_yr.get((iso3, int(yyyy)), wh)
        mk = f"EC_NAT_{iso3}"
        if mk not in mkts:
            con.execute("INSERT OR REPLACE INTO dim_markets VALUES (?,?,?,'EUROPE',?,?,?,0.16);", (mk, f"{cname} (EC Agri-Food)", iso3, clat, clon, car))
            mkts.add(mk)
        nom, rusd = p_val / div, (p_val / div) * 1.15
        mid += 1
        obs.append((mid,"EC_AGRIFOOD_WEEKLY","OBSERVED_MARKET",mk,cc,f"{yyyy}-{mm}-{dd}",nom,"EUR",rusd,rusd*(50.0/eff[cc]),60.0*(nom/wh_y)*(50.0/eff[cc]),False))
FX = {"EUR":1.15,"GBP":1.32,"CHF":1.18,"USD":1.0,"CAD":0.74,"AUD":0.67,"PLN":0.26,"SEK":0.10,"DKK":0.15,"CZK":0.045,"HUF":0.0028,"RON":0.23,"BGN":0.59}
CATS = [("en:milks","MILK_FRESH",1.03),("en:eggs","EGGS_FRESH",0.72),("en:chicken-breasts","CHICKEN_WHOLE",1.0),("en:ground-beef","BEEF_BONELESS",1.0),("en:wheat-flours","WHEAT_FLOUR",1.0),("en:lentils","LENTILS_DRY",1.0)]

print("[PHASE 7] Fetching geocoded city & small-town supermarket quotes from Open Prices API...")
for cat, cc, def_kg in CATS:
    if cc not in eff: continue
    for pg in range(1, 6):
        q = urllib.parse.urlencode({"category_tag": cat, "page": pg, "size": 100})
        try:
            res = json.loads(urllib.request.urlopen(urllib.request.Request(f"https://prices.openfoodfacts.org/api/v1/prices?{q}", headers={"User-Agent":"ProteinAtlas/1.0"}), timeout=25).read().decode())
        except Exception: break
        items = res.get("items", [])
        if not items: break
        for it in items:
            loc = it.get("location") or {}
            cc2 = str(loc.get("osm_address_country_code","")).upper()
            city = str(loc.get("osm_address_city") or loc.get("osm_address_town") or loc.get("osm_address_village") or "").strip()
            lat, lon = loc.get("osm_lat"), loc.get("osm_lon")
            if cc2 not in ISO or not city or lat is None or lon is None: continue
            iso3, _, _, _, car, exp_cur = ISO[cc2]
            cur = str(it.get("currency","")).upper()
            if cur != exp_cur or iso3 not in w_lat: continue
            price = it.get("price")
            if not price or float(price) <= 0: continue
            pper = str(it.get("price_per") or "UNIT").upper()
            if pper == "KILOGRAM":
                nom_kg = float(price)
            else:
                prod = it.get("product") or {}
                qty, qu = prod.get("product_quantity"), str(prod.get("product_quantity_unit","")).lower()
                if qty and float(qty) > 0 and qu in ("g","gr","ml"): kg = float(qty) / 1000.0
                elif qty and float(qty) > 0 and qu in ("kg","l","lt"): kg = float(qty)
                elif cc == "EGGS_FRESH" and qty and 4 <= float(qty) <= 36: kg = float(qty) * 0.06
                else: kg = def_kg
                if not (0.15 <= kg <= 10.0): continue
                nom_kg = float(price) / kg
            rusd_kg = nom_kg * FX.get(cur, 1.0)
            if not (0.20 <= rusd_kg <= 45.0): continue
            dt = str(it.get("date",""))[:10]
            if len(dt) < 10: continue
            wh = w_yr.get((iso3, int(dt[:4])), w_lat[iso3])
            slug = re.sub(r"[^A-Z0-9]", "", city.upper())[:18]
            mk = f"OFF_{iso3}_{slug}"
            reg = "NORTH_AMERICA" if iso3 in ("USA","CAN") else ("OCEANIA" if iso3=="AUS" else "EUROPE")
            if mk not in mkts:
                con.execute("INSERT OR REPLACE INTO dim_markets VALUES (?,?,?,?,?,?,?,0.14);", (mk, f"{city}, {iso3}", iso3, reg, float(lat), float(lon), car))
                mkts.add(mk)
            mid += 1
            obs.append((mid,"OPEN_PRICES_TOWN","OBSERVED_MARKET",mk,cc,dt,nom_kg,cur,rusd_kg,rusd_kg*(50.0/eff[cc]),60.0*(nom_kg/wh)*(50.0/eff[cc]),False))
        time.sleep(0.15)
if obs:
    con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)
print(f"[PHASE 7 COMPLETE] Inserted {len(obs):,} observations across {len(mkts)} European & OECD cities/towns!")
print(con.execute("SELECT m.country_iso3, m.market_name, COUNT(*) AS n, ROUND(MEDIAN(f.usd_per_50g_diaas),2) AS med_usd, ROUND(MEDIAN(f.labor_min_50g_diaas),1) AS med_min FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.source_dataset='OPEN_PRICES_TOWN' GROUP BY 1,2 ORDER BY 3 DESC LIMIT 30;").df().to_string(index=False))
con.close()
