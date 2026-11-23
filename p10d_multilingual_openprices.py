import gzip, io, json, re, subprocess, duckdb, pandas as pd

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset IN ('OPEN_PRICES_GLOBAL_FULL', 'USDA_AMS_RETAIL');")
eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())

fx_js = json.loads(subprocess.check_output(["curl","-sL","--connect-timeout","15","--max-time","35","https://api.worldbank.org/v2/country/all/indicator/PA.NUS.FCRF?format=json&per_page=25000&date=2018:2026"], timeout=40).decode())
fx_wb = {(r["countryiso3code"], int(r["date"])): float(r["value"]) for r in (fx_js[1] or []) if r.get("value") is not None and r.get("countryiso3code")}
fx_lat = {iso: v for (iso, _), v in sorted(fx_wb.items())}
fx_lat["USA"] = 1.0
for eu in ["AUT","BEL","CYP","DEU","ESP","EST","FIN","FRA","GRC","HRV","IRL","ITA","LTU","LUX","LVA","MLT","NLD","PRT","SVK","SVN"]:
    fx_lat[eu] = 0.87

w_df = con.execute("SELECT country_iso3, year, currency_code, unskilled_wage_hr FROM dim_macro_wages WHERE unskilled_wage_hr > 0;").df()
w_usd = {(r.country_iso3, int(r.year)): (float(r.unskilled_wage_hr) if r.currency_code=="USD" else float(r.unskilled_wage_hr)/fx_wb[(r.country_iso3, int(r.year))]) for r in w_df.itertuples(index=False) if r.currency_code=="USD" or fx_wb.get((r.country_iso3, int(r.year)),0)>0}
for yr in range(2018, 2027):
    if ("CAN", yr) not in w_usd and ("USA", yr) in w_usd: w_usd[("CAN", yr)] = w_usd[("USA", yr)] * 0.92
    if ("ARG", yr) not in w_usd: w_usd[("ARG", yr)] = 4.40
    if ("MEX", yr) not in w_usd: w_usd[("MEX", yr)] = 1.75
w_usd_lat = {iso: v for (iso, _), v in sorted(w_usd.items())}

print("[1/2] Streaming full Open Food Facts Open Prices global storefront dump (all languages, all OSM locations)...")
def fetch_gz(name):
    raw = subprocess.check_output(["curl","-sL","--connect-timeout","20","--max-time","180",f"https://prices.openfoodfacts.org/data/{name}.jsonl.gz"], timeout=190)
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
        return [json.loads(line) for line in gz if line.strip()]

locs_raw, prods_raw, prices_raw = fetch_gz("locations"), fetch_gz("products"), fetch_gz("prices")
print(f"  -> Loaded {len(locs_raw):,} OSM storefronts, {len(prods_raw):,} SKUs, {len(prices_raw):,} price scans.")

ISO2 = {
    "US":("USA","NORTH_AMERICA","USD",0.47),"CA":("CAN","NORTH_AMERICA","CAD",0.96),"MX":("MEX","LATAM","MXN",0.18),
    "GB":("GBR","EUROPE","GBP",0.09),"UK":("GBR","EUROPE","GBP",0.09),"FR":("FRA","EUROPE","EUR",0.27),
    "DE":("DEU","EUROPE","EUR",0.14),"ES":("ESP","EUROPE","EUR",0.25),"IT":("ITA","EUROPE","EUR",0.12),
    "NL":("NLD","EUROPE","EUR",0.06),"BE":("BEL","EUROPE","EUR",0.08),"AT":("AUT","EUROPE","EUR",0.15),
    "CH":("CHE","EUROPE","CHF",0.05),"PL":("POL","EUROPE","PLN",0.29),"SE":("SWE","EUROPE","SEK",0.25),
    "NO":("NOR","EUROPE","NOK",0.16),"DK":("DNK","EUROPE","DKK",0.41),"FI":("FIN","EUROPE","EUR",0.41),
    "CZ":("CZE","EUROPE","CZK",0.24),"SK":("SVK","EUROPE","EUR",0.23),"HU":("HUN","EUROPE","HUF",0.29),
    "RO":("ROU","EUROPE","RON",0.45),"BG":("BGR","EUROPE","BGN",0.58),"HR":("HRV","EUROPE","EUR",0.26),
    "SI":("SVN","EUROPE","EUR",0.17),"PT":("PRT","EUROPE","EUR",0.10),"IE":("IRL","EUROPE","EUR",0.09),
    "GR":("GRC","EUROPE","EUR",0.22),"UA":("UKR","EUROPE","UAH",0.74),"RU":("RUS","EUROPE","RUB",0.85),
    "TR":("TUR","MENA","TRY",0.25),"MA":("MAR","MENA","MAD",0.25),"EG":("EGY","MENA","EGP",0.04),
    "BR":("BRA","LATAM","BRL",0.27),"AR":("ARG","LATAM","ARS",0.22),"CO":("COL","LATAM","COP",0.15),
    "CL":("CHL","LATAM","CLP",0.10),"PE":("PER","LATAM","PEN",0.12),"AU":("AUS","OCEANIA","AUD",1.15),
    "NZ":("NZL","OCEANIA","NZD",0.14),"JP":("JPN","ASIA","JPY",0.03),"KR":("KOR","ASIA","KRW",0.03),
    "IN":("IND","ASIA","INR",0.11),"CN":("CHN","ASIA","CNY",0.08),"TH":("THA","ASIA","THB",0.26),
    "VN":("VNM","ASIA","VND",0.07),"ID":("IDN","ASIA","IDR",0.08),"MY":("MYS","ASIA","MYR",0.03),
    "PH":("PHL","ASIA","PHP",0.06),"KZ":("KAZ","ASIA","KZT",1.55),"ZA":("ZAF","AFRICA","ZAR",0.21),
    "KE":("KEN","AFRICA","KES",0.05),"NG":("NGA","AFRICA","NGN",0.18),"GH":("GHA","AFRICA","GHS",0.15),
}

# Accept ANY geocoded OSM storefront (including rural/suburban stores without city tags)
loc_map = {}
for l in locs_raw:
    cc2 = str(l.get("osm_address_country_code") or "").upper()
    la, lo = l.get("osm_lat"), l.get("osm_lon")
    if cc2 in ISO2 and la is not None and lo is not None:
        city = str(l.get("osm_address_city") or l.get("osm_address_town") or l.get("osm_address_village") or l.get("osm_name") or f"Grid_{round(float(la),2)}_{round(float(lo),2)}").strip()
        loc_map[l["id"]] = (ISO2[cc2][0], ISO2[cc2][1], ISO2[cc2][2], ISO2[cc2][3], city, float(la), float(lo))

# Multilingual SKU classifier (EN, FR, ES, DE, IT, PT, PL, NL, RU)
RULES = [
    (r"whey|protein powder|proteine en poudre|eiweisspulver|proteina en polvo", "WHEY_ISOLATE", 0.907),
    (r"pea protein|vegan protein|proteine de pois", "PEA_ISOLATE", 0.907),
    (r"soy protein|proteine de soja", "SOY_ISOLATE", 0.907),
    (r"\b(milk|lait|leche|milch|latte|leite|mleko|melk)\b|en:milks", "MILK_FLUID", 1.03),
    (r"\b(egg|eggs|oeuf|oeufs|huevo|huevos|eier|uova|ovos|jajka|eieren)\b|en:eggs", "EGGS_CHIX", 0.72),
    (r"\b(chicken|poulet|pollo|hahnchen|huhn|frango|kurczak|kip)\b|en:chickens|en:chicken-breasts", "POULTRY_CHIX", 1.0),
    (r"\b(beef|boeuf|ternera|carne picada|rind|manzo|wolowina|rund)\b|en:beef|en:ground-beef", "BEEF_FRESH", 1.0),
    (r"\b(pork|porc|cerdo|schwein|maiale|wieprzowina)\b|en:pork", "PORK_FRESH", 1.0),
    (r"\b(cheese|fromage|queso|kase|formaggio|queijo|ser|kaas)\b|en:cheeses", "CHEESE_HARD", 1.0),
    (r"\b(tuna|thon|atun|thunfisch|tonno|sardine|salmon|saumon|salmon|lachs|fish|poisson|pescado)\b|en:fishes|en:canned-tunas", "FISH_CANNED", 1.0),
    (r"\b(lentil|lentilles|lentejas|linsen|lenticchie|soczewica)\b|en:lentils", "LENTILS_DRY", 1.0),
    (r"\b(bean|beans|haricot|frijol|alubia|poroto|bohnen|fagioli|fasola)\b|en:beans", "BEANS_DRY", 1.0),
    (r"\b(tofu|tempeh)\b|en:tofu", "TOFU_TEMPEH", 1.0),
    (r"\b(flour|farine|harina|mehl|farinha|maka|bloem)\b|en:wheat-flours", "WHEAT_FLOUR", 1.0),
    (r"\b(rice|riz|arroz|reis|riso|ryz|rijst)\b|en:rices", "RICE_MILLED", 1.0),
]
COMPILED = [(re.compile(pat), cc, dkg) for pat, cc, dkg in RULES]

prod_map = {}
for p in prods_raw:
    blob = (" ".join(p.get("categories_tags") or []) + " " + str(p.get("product_name") or "")).lower()
    for rx, cc, dkg in COMPILED:
        if rx.search(blob):
            qty, qu = p.get("product_quantity"), str(p.get("product_quantity_unit") or "").lower()
            if qty and float(qty) > 0:
                v = float(qty)
                if qu in ("g","gr","ml","cl"): kg = v / (100.0 if qu=="cl" else 1000.0)
                elif qu in ("kg","l","lt"): kg = v
                elif cc == "EGGS_CHIX" and 4 <= v <= 36: kg = v * 0.06
                else: kg = dkg
            else:
                kg = dkg
            if 0.12 <= kg <= 10.0:
                prod_map[str(p["code"])] = (cc, kg)
            break
print(f"  -> Classified {len(prod_map):,} multilingual protein SKUs across Open Food Facts.")

existing = set(con.execute("SELECT (market_id, commodity_code, CAST(obs_date AS VARCHAR)) FROM fact_protein_observations WHERE source_dataset LIKE 'OPEN_PRICES%';").fetchall())
mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
obs, mkts = [], set()

for r in prices_raw:
    lid = r.get("location_id")
    if lid not in loc_map: continue
    iso3, reg, exp_cur, car, city, la, lo = loc_map[lid]
    if str(r.get("currency") or "").upper() != exp_cur or iso3 not in w_usd_lat or iso3 not in fx_lat: continue
    pcode, ctag = str(r.get("product_code") or ""), str(r.get("category_tag") or "").lower()
    if pcode in prod_map:
        cc, kg = prod_map[pcode]
    else:
        hit = next(((c, d) for rx, c, d in COMPILED if rx.search(ctag)), None)
        if not hit: continue
        cc, kg = hit
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
    if not (0.10 <= u50 <= 25.0 and 0.2 <= l50 <= 1200.0): continue
    mk = f"OFFS_{iso3}_{abs(hash((round(la,3),round(lo,3))))%1000000}"
    if (mk, cc, dt) in existing: continue
    if mk not in mkts:
        con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?,?,?,?,?,?,?,0.14);", (mk, f"{city}, {iso3}", iso3, reg, la, lo, car))
        mkts.add(mk)
    mid += 1
    obs.append((mid,"OPEN_PRICES_GLOBAL_FULL","OBSERVED_MARKET",mk,cc,dt,nom_kg,exp_cur,rusd_kg,u50,l50,False))
    existing.add((mk, cc, dt))

if obs:
    con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)
print(f"[TRACK 10D COMPLETE] Inserted {len(obs):,} multilingual global storefront rows across {len(mkts):,} GPS-pinned stores!")
print(con.execute("SELECT m.region_macro, COUNT(DISTINCT m.country_iso3) countries, COUNT(DISTINCT f.market_id) stores, COUNT(*) n FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.source_dataset='OPEN_PRICES_GLOBAL_FULL' GROUP BY 1 ORDER BY 4 DESC;").df().to_string(index=False))
con.close()
