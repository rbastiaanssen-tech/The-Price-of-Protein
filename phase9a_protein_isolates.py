import gzip, io, json, re, urllib.request, duckdb

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'OPEN_PRICES_ISOLATES';")

ISOLATE_DEFS = [
    ("WHEY_ISOLATE", "Whey Protein Isolate/Concentrate Powder", "PROCESSED_ISOLATE", True,  1.0, 800.0, 1.0, 1.09, 1.00, 3800.0, 730, 0.94, "FAO 2013 DIAAS / OpenPrices"),
    ("SOY_ISOLATE",  "Soy Protein Isolate Powder",              "PROCESSED_ISOLATE", False, 1.0, 850.0, 1.0, 0.98, 0.98, 3700.0, 730, 0.94, "FAO 2013 DIAAS / OpenPrices"),
    ("PEA_ISOLATE",  "Pea / Plant Protein Isolate Powder",      "PROCESSED_ISOLATE", False, 1.0, 800.0, 1.0, 0.82, 0.82, 3750.0, 730, 0.94, "FAO 2013 DIAAS / OpenPrices"),
]
for row in ISOLATE_DEFS:
    con.execute("INSERT OR REPLACE INTO dim_commodities (commodity_code, commodity_name, regime_class, is_animal_source, edible_fraction, crude_protein_g_kg, prep_retention, diaas_score, diaas_capped, kcal_per_kg, shelf_life_days, dry_matter_fraction, reference_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?);", row)

eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())
def wb(ind):
    u = f"https://api.worldbank.org/v2/country/all/indicator/{ind}?format=json&per_page=25000&date=2015:2026"
    js = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent":"ProteinAtlas/1.0"}), timeout=45).read().decode())
    return {(r["countryiso3code"], int(r["date"])): float(r["value"]) for r in (js[1] or []) if r.get("value") is not None and r.get("countryiso3code")}

fx_wb = wb("PA.NUS.FCRF")
fx_lat = {iso: v for (iso, _), v in sorted(fx_wb.items())}
fx_lat["USA"] = 1.0
for eu in ["AUT","BEL","CYP","DEU","ESP","EST","FIN","FRA","GRC","HRV","IRL","ITA","LTU","LUX","LVA","MLT","NLD","PRT","SVK","SVN"]:
    fx_lat[eu] = 0.87

w_df = con.execute("SELECT country_iso3, year, currency_code, unskilled_wage_hr FROM dim_macro_wages WHERE unskilled_wage_hr > 0;").df()
w_usd = {(r.country_iso3, int(r.year)): (float(r.unskilled_wage_hr) if r.currency_code=="USD" else float(r.unskilled_wage_hr)/fx_wb[(r.country_iso3, int(r.year))]) for r in w_df.itertuples(index=False) if r.currency_code=="USD" or fx_wb.get((r.country_iso3, int(r.year)),0)>0}
w_usd_lat = {iso: v for (iso, _), v in sorted(w_usd.items())}

def fetch_gz(name):
    raw = urllib.request.urlopen(urllib.request.Request(f"https://prices.openfoodfacts.org/data/{name}.jsonl.gz", headers={"User-Agent":"ProteinAtlas/1.0"}), timeout=90).read()
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
        return [json.loads(line) for line in gz if line.strip()]

locs_raw, prods_raw, prices_raw = fetch_gz("locations"), fetch_gz("products"), fetch_gz("prices")
ISO = {
    "US":("USA","NORTH_AMERICA","USD",0.47),"CA":("CAN","NORTH_AMERICA","CAD",0.96),"GB":("GBR","EUROPE","GBP",0.09),"UK":("GBR","EUROPE","GBP",0.09),
    "FR":("FRA","EUROPE","EUR",0.27),"DE":("DEU","EUROPE","EUR",0.14),"ES":("ESP","EUROPE","EUR",0.25),"IT":("ITA","EUROPE","EUR",0.12),
    "NL":("NLD","EUROPE","EUR",0.06),"BE":("BEL","EUROPE","EUR",0.08),"AT":("AUT","EUROPE","EUR",0.15),"CH":("CHE","EUROPE","CHF",0.05),
    "PL":("POL","EUROPE","PLN",0.29),"SE":("SWE","EUROPE","SEK",0.25),"DK":("DNK","EUROPE","DKK",0.41),"CZ":("CZE","EUROPE","CZK",0.24),
    "PT":("PRT","EUROPE","EUR",0.10),"IE":("IRL","EUROPE","EUR",0.09),"FI":("FIN","EUROPE","EUR",0.41),"AU":("AUS","OCEANIA","AUD",1.15),
    "BR":("BRA","LATAM","BRL",0.27),"MX":("MEX","LATAM","MXN",0.18),"IN":("IND","ASIA","INR",0.11),"JP":("JPN","ASIA","JPY",0.03),
}
loc_map = {}
for l in locs_raw:
    cc2 = str(l.get("osm_address_country_code") or "").upper()
    city = str(l.get("osm_address_city") or l.get("osm_address_town") or l.get("osm_address_village") or "").strip()
    lat, lon = l.get("osm_lat"), l.get("osm_lon")
    if cc2 in ISO and city and lat is not None and lon is not None:
        loc_map[l["id"]] = (ISO[cc2][0], ISO[cc2][1], ISO[cc2][2], ISO[cc2][3], city, float(lat), float(lon))
ISO_TAGS = {"en:whey-proteins","en:whey-protein-isolates","en:whey-protein-concentrates","en:protein-powders","en:soy-proteins","en:pea-proteins","en:sports-supplements"}
prod_map = {}
for p in prods_raw:
    tags = set(p.get("categories_tags") or [])
    pname = str(p.get("product_name") or "").lower()
    if not (tags & ISO_TAGS or any(k in pname for k in ("whey","protein powder","proteine en poudre","eiweisspulver","proteina en polvo","pea protein","soy protein"))):
        continue
    if "pea" in pname or "vegan" in pname or "plant" in pname or "en:pea-proteins" in tags: cc = "PEA_ISOLATE"
    elif "soy" in pname or "soja" in pname or "en:soy-proteins" in tags: cc = "SOY_ISOLATE"
    else: cc = "WHEY_ISOLATE"
    qty, qu = p.get("product_quantity"), str(p.get("product_quantity_unit") or "").lower()
    kg = float(qty)/1000.0 if (qty and float(qty)>0 and qu in ("g","gr")) else (float(qty) if (qty and float(qty)>0 and qu=="kg") else 0.907)
    if 0.25 <= kg <= 6.0: prod_map[str(p["code"])] = (cc, kg)
print(f"[PHASE 9A] Matched {len(prod_map):,} commercial protein powder SKUs (Whey / Soy / Pea isolates).")
mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
obs, mkts = [], set()
for r in prices_raw:
    lid, pcode = r.get("location_id"), str(r.get("product_code") or "")
    if lid not in loc_map or pcode not in prod_map: continue
    iso3, reg, exp_cur, car, city, lat, lon = loc_map[lid]
    if str(r.get("currency") or "").upper() != exp_cur or iso3 not in w_usd_lat or iso3 not in fx_lat: continue
    cc, kg = prod_map[pcode]
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
    u50 = rusd_kg * (50.0 / eff[cc])
    l50 = 60.0 * (rusd_kg / wh_u) * (50.0 / eff[cc])
    if not (0.30 <= u50 <= 15.0 and 0.5 <= l50 <= 600.0): continue
    slug = re.sub(r"[^A-Z0-9]", "", city.upper())[:18]
    mk = f"OFF_{iso3}_{slug}"
    if mk not in mkts:
        con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?,?,?,?,?,?,?,0.14);", (mk, f"{city}, {iso3}", iso3, reg, lat, lon, car))
        mkts.add(mk)
    mid += 1
    obs.append((mid,"OPEN_PRICES_ISOLATES","OBSERVED_MARKET",mk,cc,dt,nom_kg,exp_cur,rusd_kg,u50,l50,False))
if obs:
    con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)
print(f"[PHASE 9A COMPLETE] Inserted {len(obs):,} retail protein-isolate tub observations across {len(mkts)} cities/towns!")
print(con.execute("SELECT commodity_code, COUNT(*) n, ROUND(MEDIAN(real_usd_per_kg),2) med_usd_kg, ROUND(MEDIAN(usd_per_50g_diaas),2) med_usd50, ROUND(MEDIAN(labor_min_50g_diaas),1) med_min FROM fact_protein_observations WHERE source_dataset='OPEN_PRICES_ISOLATES' GROUP BY 1 ORDER BY 2 DESC;").df().to_string(index=False))
con.close()
