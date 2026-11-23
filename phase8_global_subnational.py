import gzip, io, json, re, urllib.request, duckdb, pandas as pd

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'OPEN_PRICES_GLOBAL';")
eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())

def wb(ind):
    u = f"https://api.worldbank.org/v2/country/all/indicator/{ind}?format=json&per_page=25000&date=2000:2026"
    js = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent":"ProteinAtlas/1.0"}), timeout=45).read().decode())
    return {(r["countryiso3code"], int(r["date"])): float(r["value"]) for r in (js[1] or []) if r.get("value") is not None and r.get("countryiso3code")}

fx_wb = wb("PA.NUS.FCRF")
fx_latest = {iso: v for (iso, _), v in sorted(fx_wb.items())}
fx_latest["USA"] = 1.0
for eu in ["AUT","BEL","CYP","DEU","ESP","EST","FIN","FRA","GRC","HRV","IRL","ITA","LTU","LUX","LVA","MLT","NLD","PRT","SVK","SVN"]:
    fx_latest[eu] = 0.87

w_df = con.execute("SELECT country_iso3, year, currency_code, unskilled_wage_hr FROM dim_macro_wages WHERE unskilled_wage_hr > 0;").df()
w_usd = {(r.country_iso3, int(r.year)): (float(r.unskilled_wage_hr) if r.currency_code=="USD" else float(r.unskilled_wage_hr)/fx_wb[(r.country_iso3, int(r.year))]) for r in w_df.itertuples(index=False) if r.currency_code=="USD" or fx_wb.get((r.country_iso3, int(r.year)),0)>0}
w_usd_lat = {iso: v for (iso, _), v in sorted(w_usd.items())}
print(f"[PHASE 8A] Built unified USD hourly wage schedule for {len(w_usd_lat)} countries.")
META = {
    "PL":("POL","EUROPE","PLN",0.29),"RO":("ROU","EUROPE","RON",0.44),"CZ":("CZE","EUROPE","CZK",0.24),"HU":("HUN","EUROPE","HUF",0.43),
    "BG":("BGR","EUROPE","BGN",0.51),"UA":("UKR","EUROPE","UAH",0.79),"MD":("MDA","EUROPE","MDL",0.68),"RS":("SRB","EUROPE","RSD",0.38),
    "HR":("HRV","EUROPE","EUR",0.22),"SK":("SVK","EUROPE","EUR",0.25),"SI":("SVN","EUROPE","EUR",0.09),"EE":("EST","EUROPE","EUR",0.52),
    "LV":("LVA","EUROPE","EUR",0.72),"LT":("LTU","EUROPE","EUR",0.81),"GE":("GEO","ASIA","GEL",0.08),"AM":("ARM","ASIA","AMD",0.15),
    "BR":("BRA","LATAM","BRL",0.27),"MX":("MEX","LATAM","MXN",0.18),"AR":("ARG","LATAM","ARS",0.92),"CO":("COL","LATAM","COP",0.04),
    "CL":("CHL","LATAM","CLP",0.07),"PE":("PER","LATAM","PEN",0.11),"EC":("ECU","LATAM","USD",0.06),"BO":("BOL","LATAM","BOB",0.38),
    "UY":("URY","LATAM","UYU",0.58),"PY":("PRY","LATAM","PYG",0.69),"CR":("CRI","LATAM","CRC",0.05),"PA":("PAN","LATAM","PAB",0.13),
    "CN":("CHN","ASIA","CNY",0.08),"JP":("JPN","ASIA","JPY",0.03),"KR":("KOR","ASIA","KRW",0.03),"IN":("IND","ASIA","INR",0.11),
    "ID":("IDN","ASIA","IDR",0.09),"TH":("THA","ASIA","THB",0.24),"VN":("VNM","ASIA","VND",0.07),"PH":("PHL","ASIA","PHP",0.05),
    "MY":("MYS","ASIA","MYR",0.03),"PK":("PAK","ASIA","PKR",0.13),"BD":("BGD","ASIA","BDT",0.05),"LK":("LKA","ASIA","LKR",0.06),
    "KZ":("KAZ","ASIA","KZT",1.52),"UZ":("UZB","ASIA","UZS",0.12),"TR":("TUR","MENA","TRY",0.23),"EG":("EGY","MENA","EGP",0.03),
    "MA":("MAR","MENA","MAD",0.21),"TN":("TUN","MENA","TND",0.23),"DZ":("DZA","MENA","DZD",0.17),"SA":("SAU","MENA","SAR",0.10),
    "AE":("ARE","MENA","AED",0.01),"ZA":("ZAF","AFRICA","ZAR",0.20),"NG":("NGA","AFRICA","NGN",0.16),"KE":("KEN","AFRICA","KES",0.11),
    "GH":("GHA","AFRICA","GHS",0.14),"ET":("ETH","AFRICA","ETB",0.13),"TZ":("TZA","AFRICA","TZS",0.21),"UG":("UGA","AFRICA","UGX",0.15),
    "SN":("SEN","AFRICA","XOF",0.19),"CI":("CIV","AFRICA","XOF",0.13),"CM":("CMR","AFRICA","XAF",0.22),"RE":("FRA","AFRICA","EUR",0.12),
}
def fetch_gz(name):
    raw = urllib.request.urlopen(urllib.request.Request(f"https://prices.openfoodfacts.org/data/{name}.jsonl.gz", headers={"User-Agent":"ProteinAtlas/1.0"}), timeout=90).read()
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
        return [json.loads(line) for line in gz if line.strip()]

print("[PHASE 8A] Scanning global Open Prices dumps for Eastern Europe, LatAm, Asia & Africa cities/towns...")
locs_raw, prods_raw, prices_raw = fetch_gz("locations"), fetch_gz("products"), fetch_gz("prices")
loc_map = {}
for l in locs_raw:
    cc2 = str(l.get("osm_address_country_code") or "").upper()
    city = str(l.get("osm_address_city") or l.get("osm_address_town") or l.get("osm_address_village") or "").strip()
    lat, lon = l.get("osm_lat"), l.get("osm_lon")
    if cc2 in META and city and lat is not None and lon is not None:
        loc_map[l["id"]] = (META[cc2][0], META[cc2][1], META[cc2][2], META[cc2][3], city, float(lat), float(lon))

TAGS = {"en:milks":("MILK_FRESH",1.03),"en:whole-milks":("MILK_FRESH",1.03),"en:eggs":("EGGS_FRESH",0.72),"en:chicken-breasts":("CHICKEN_WHOLE",1.0),"en:chickens":("CHICKEN_WHOLE",1.0),"en:ground-beef":("BEEF_BONELESS",1.0),"en:beef":("BEEF_BONELESS",1.0),"en:pork":("PORK_FRESH",1.0),"en:wheat-flours":("WHEAT_FLOUR",1.0),"en:lentils":("LENTILS_DRY",1.0),"en:chickpeas":("CHICKPEAS_DRY",1.0),"en:beans":("DRY_BEANS",1.0)}
prod_map = {}
for p in prods_raw:
    hit = next((TAGS[t] for t in (p.get("categories_tags") or []) if t in TAGS), None)
    if not hit: continue
    cc, dkg = hit
    qty, qu = p.get("product_quantity"), str(p.get("product_quantity_unit") or "").lower()
    kg = float(qty)/1000.0 if (qty and float(qty)>0 and qu in ("g","gr","ml")) else (float(qty) if (qty and float(qty)>0 and qu in ("kg","l","lt")) else (float(qty)*0.06 if (cc=="EGGS_FRESH" and qty and 4<=float(qty)<=36) else dkg))
    if 0.15 <= kg <= 10.0: prod_map[str(p["code"])] = (cc, kg)
mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
obs, mkts = [], set()
for r in prices_raw:
    lid = r.get("location_id")
    if lid not in loc_map: continue
    iso3, reg, exp_cur, car, city, lat, lon = loc_map[lid]
    if str(r.get("currency") or "").upper() != exp_cur or iso3 not in w_usd_lat or iso3 not in fx_latest: continue
    pcode, ctag = str(r.get("product_code") or ""), str(r.get("category_tag") or "")
    cc, kg = prod_map.get(pcode) or TAGS.get(ctag) or (None, 0)
    if not cc or cc not in eff: continue
    pr = r.get("price")
    if not pr or float(pr) <= 0: continue
    nom_kg = float(pr) if str(r.get("price_per") or "").upper() == "KILOGRAM" else float(pr) / kg
    dt = str(r.get("date") or "")[:10]
    if len(dt) < 10: continue
    yr = int(dt[:4])
    fx = fx_wb.get((iso3, yr), fx_latest[iso3])
    if fx <= 0: continue
    rusd_kg = nom_kg / fx
    wh_u = w_usd.get((iso3, yr), w_usd_lat[iso3])
    u50 = rusd_kg * (50.0 / eff[cc])
    l50 = 60.0 * (rusd_kg / wh_u) * (50.0 / eff[cc])
    if not (0.08 <= u50 <= 25.0 and 0.2 <= l50 <= 1500.0): continue
    slug = re.sub(r"[^A-Z0-9]", "", city.upper())[:18]
    mk = f"OFF_{iso3}_{slug}"
    if mk not in mkts:
        con.execute("INSERT OR REPLACE INTO dim_markets VALUES (?,?,?,?,?,?,?,0.14);", (mk, f"{city}, {iso3}", iso3, reg, lat, lon, car))
        mkts.add(mk)
    mid += 1
    obs.append((mid,"OPEN_PRICES_GLOBAL","OBSERVED_MARKET",mk,cc,dt,nom_kg,exp_cur,rusd_kg,u50,l50,False))
if obs:
    con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)
print(f"[PHASE 8A COMPLETE] Inserted {len(obs):,} observations across {len(mkts)} cities/towns in Eastern Europe, LatAm, Asia & Africa!")
print(con.execute("SELECT m.region_macro, m.country_iso3, COUNT(DISTINCT m.market_id) AS towns, COUNT(*) AS n, ROUND(MEDIAN(f.usd_per_50g_diaas),2) AS med_usd, ROUND(MEDIAN(f.labor_min_50g_diaas),1) AS med_min FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.source_dataset='OPEN_PRICES_GLOBAL' GROUP BY 1,2 ORDER BY 1,4 DESC;").df().to_string(index=False))
con.close()
