import gzip, io, json, re, urllib.request, duckdb, pandas as pd

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset IN ('EC_AGRIFOOD_WEEKLY', 'OPEN_PRICES_TOWN');")
eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())

w_df = con.execute("SELECT m.country_iso3, f.obs_year, MEDIAN(f.nominal_price_kg / (f.labor_min_50g_diaas / 60.0 / (50.0 / c.eff_protein_g_kg))) AS lcu_hr FROM fact_protein_observations f JOIN dim_markets m USING(market_id) JOIN dim_commodities c USING(commodity_code) WHERE f.source_dataset IN ('EUROSTAT_OFFICIAL','BLS_APU_METRO_TOWN','FAO_BULK_PRIMARY') AND f.labor_min_50g_diaas > 0 GROUP BY 1,2;").df()
w_yr = {(r.country_iso3, int(r.obs_year)): float(r.lcu_hr) for r in w_df.itertuples(index=False)}
w_lat = {iso: w for (iso, _), w in sorted(w_yr.items())}
print(f"[PHASE 7] Loaded {len(w_yr):,} verified country-year hourly wages across {len(w_lat)} countries.")

def fetch_jsonl_gz(name):
    u = f"https://prices.openfoodfacts.org/data/{name}.jsonl.gz"
    print(f"  -> Downloading {u}...")
    raw = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent":"ProteinAtlasResearch/1.0"}), timeout=90).read()
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
        return [json.loads(line) for line in gz if line.strip()]

locs_raw = fetch_jsonl_gz("locations")
prods_raw = fetch_jsonl_gz("products")
print(f"  -> Parsed {len(locs_raw):,} OSM locations and {len(prods_raw):,} barcoded products.")
ISO = {
    "AT":("AUT","EUR",0.15,1.15),"BE":("BEL","EUR",0.08,1.15),"BG":("BGR","BGN",0.51,0.59),"CH":("CHE","CHF",0.05,1.18),
    "CZ":("CZE","CZK",0.24,0.045),"DE":("DEU","EUR",0.14,1.15),"DK":("DNK","DKK",0.41,0.15),"EE":("EST","EUR",0.52,1.15),
    "GR":("GRC","EUR",0.20,1.15),"ES":("ESP","EUR",0.25,1.15),"FI":("FIN","EUR",0.41,1.15),"FR":("FRA","EUR",0.27,1.15),
    "GB":("GBR","GBP",0.09,1.32),"UK":("GBR","GBP",0.09,1.32),"HR":("HRV","EUR",0.22,1.15),"HU":("HUN","HUF",0.43,0.0028),
    "IE":("IRL","EUR",0.09,1.15),"IT":("ITA","EUR",0.12,1.15),"LT":("LTU","EUR",0.81,1.15),"LU":("LUX","EUR",0.10,1.15),
    "LV":("LVA","EUR",0.72,1.15),"NL":("NLD","EUR",0.06,1.15),"PL":("POL","PLN",0.29,0.26),"PT":("PRT","EUR",0.10,1.15),
    "RO":("ROU","RON",0.44,0.23),"SE":("SWE","SEK",0.25,0.10),"SI":("SVN","EUR",0.09,1.15),"SK":("SVK","EUR",0.25,1.15),
    "US":("USA","USD",0.47,1.00),"CA":("CAN","CAD",0.96,0.74),"AU":("AUS","AUD",1.15,0.67)
}

loc_map = {}
for l in locs_raw:
    cc2 = str(l.get("osm_address_country_code") or "").upper()
    city = str(l.get("osm_address_city") or l.get("osm_address_town") or l.get("osm_address_village") or "").strip()
    lat, lon = l.get("osm_lat"), l.get("osm_lon")
    if cc2 in ISO and city and lat is not None and lon is not None:
        loc_map[l["id"]] = (ISO[cc2][0], ISO[cc2][1], ISO[cc2][2], ISO[cc2][3], city, float(lat), float(lon))

TAG_TO_CC = {
    "en:milks": ("MILK_FRESH", 1.03), "en:whole-milks": ("MILK_FRESH", 1.03), "en:semi-skimmed-milks": ("MILK_FRESH", 1.03),
    "en:eggs": ("EGGS_FRESH", 0.72), "en:chicken-eggs": ("EGGS_FRESH", 0.72),
    "en:chicken-breasts": ("CHICKEN_WHOLE", 1.0), "en:chickens": ("CHICKEN_WHOLE", 1.0),
    "en:ground-beef": ("BEEF_BONELESS", 1.0), "en:beef": ("BEEF_BONELESS", 1.0),
    "en:pork": ("PORK_FRESH", 1.0), "en:wheat-flours": ("WHEAT_FLOUR", 1.0),
    "en:lentils": ("LENTILS_DRY", 1.0), "en:chickpeas": ("CHICKPEAS_DRY", 1.0), "en:beans": ("DRY_BEANS", 1.0),
}
prod_map = {}
for p in prods_raw:
    tags = p.get("categories_tags") or []
    hit = next((TAG_TO_CC[t] for t in tags if t in TAG_TO_CC), None)
    if not hit: continue
    cc, def_kg = hit
    qty, qu = p.get("product_quantity"), str(p.get("product_quantity_unit") or "").lower()
    if qty and float(qty) > 0 and qu in ("g","gr","ml"): kg = float(qty) / 1000.0
    elif qty and float(qty) > 0 and qu in ("kg","l","lt"): kg = float(qty)
    elif cc == "EGGS_FRESH" and qty and 4 <= float(qty) <= 36: kg = float(qty) * 0.06
    else: kg = def_kg
    if 0.15 <= kg <= 10.0:
        prod_map[str(p["code"])] = (cc, kg)
print(f"  -> Indexed {len(loc_map):,} geocoded towns/cities and {len(prod_map):,} protein SKUs.")
prices_raw = fetch_jsonl_gz("prices")
print(f"  -> Scanning {len(prices_raw):,} total receipt & shelf-tag price records...")
mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
obs, mkts = [], set()
for r in prices_raw:
    lid = r.get("location_id")
    if lid not in loc_map: continue
    iso3, exp_cur, car, fx_usd, city, lat, lon = loc_map[lid]
    if str(r.get("currency") or "").upper() != exp_cur or iso3 not in w_lat: continue
    pcode, ctag = str(r.get("product_code") or ""), str(r.get("category_tag") or "")
    if pcode in prod_map: cc, kg = prod_map[pcode]
    elif ctag in TAG_TO_CC: cc, kg = TAG_TO_CC[ctag]
    else: continue
    if cc not in eff: continue
    pr = r.get("price")
    if not pr or float(pr) <= 0: continue
    nom_kg = float(pr) if str(r.get("price_per") or "").upper() == "KILOGRAM" else float(pr) / kg
    rusd_kg = nom_kg * fx_usd
    if not (0.20 <= rusd_kg <= 45.0): continue
    dt = str(r.get("date") or "")[:10]
    if len(dt) < 10: continue
    wh = w_yr.get((iso3, int(dt[:4])), w_lat[iso3])
    slug = re.sub(r"[^A-Z0-9]", "", city.upper())[:18]
    mk = f"OFF_{iso3}_{slug}"
    reg = "NORTH_AMERICA" if iso3 in ("USA","CAN") else ("OCEANIA" if iso3=="AUS" else "EUROPE")
    if mk not in mkts:
        con.execute("INSERT OR REPLACE INTO dim_markets VALUES (?,?,?,?,?,?,?,0.14);", (mk, f"{city}, {iso3}", iso3, reg, lat, lon, car))
        mkts.add(mk)
    mid += 1
    obs.append((mid,"OPEN_PRICES_TOWN","OBSERVED_MARKET",mk,cc,dt,nom_kg,exp_cur,rusd_kg,rusd_kg*(50.0/eff[cc]),60.0*(nom_kg/wh)*(50.0/eff[cc]),False))
if obs:
    con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)
print(f"[PHASE 7 COMPLETE] Inserted {len(obs):,} town-level supermarket observations across {len(mkts)} cities & small towns!")
print(con.execute("SELECT m.country_iso3, COUNT(DISTINCT m.market_id) AS towns, COUNT(*) AS n, ROUND(MEDIAN(f.usd_per_50g_diaas),2) AS med_usd, ROUND(MEDIAN(f.labor_min_50g_diaas),1) AS med_min FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.source_dataset='OPEN_PRICES_TOWN' GROUP BY 1 ORDER BY 3 DESC;").df().to_string(index=False))
con.close()
