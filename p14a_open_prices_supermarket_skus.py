import json, re, subprocess, duckdb

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'OPEN_PRICES_RETAIL_SKU';")
eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())

fx_js = json.loads(subprocess.check_output(["curl","-sL","--connect-timeout","15","--max-time","35","https://api.worldbank.org/v2/country/all/indicator/PA.NUS.FCRF?format=json&per_page=25000&date=2020:2026"], timeout=40).decode())
fx_wb = {(r["countryiso3code"], int(r["date"])): float(r["value"]) for r in (fx_js[1] or []) if r.get("value") is not None and r.get("countryiso3code")}
fx_lat = {iso: v for (iso, _), v in sorted(fx_wb.items())}
for eu in ["AUT","BEL","CYP","DEU","ESP","EST","FIN","FRA","GRC","HRV","IRL","ITA","LTU","LUX","LVA","MLT","NLD","PRT","SVK","SVN"]:
    fx_lat[eu] = 0.88
fx_lat.update({"USA": 1.0, "GBR": 0.78, "CAN": 1.36, "CHE": 0.88, "POL": 3.98, "SWE": 10.4, "BRA": 5.15, "MEX": 17.5})

w_df = con.execute("SELECT country_iso3, year, currency_code, unskilled_wage_hr FROM dim_macro_wages WHERE unskilled_wage_hr > 0;").df()
w_usd = {(r.country_iso3, int(r.year)): (float(r.unskilled_wage_hr) if r.currency_code=="USD" else float(r.unskilled_wage_hr)/fx_wb[(r.country_iso3, int(r.year))]) for r in w_df.itertuples(index=False) if r.currency_code=="USD" or fx_wb.get((r.country_iso3, int(r.year)),0)>0}
w_usd_lat = {iso: v for (iso, _), v in sorted(w_usd.items())}

CC2_TO_ISO3 = {
    "FR":"FRA","ES":"ESP","DE":"DEU","IT":"ITA","GB":"GBR","UK":"GBR","US":"USA","CA":"CAN","BE":"BEL",
    "NL":"NLD","CH":"CHE","AT":"AUT","PT":"PRT","PL":"POL","SE":"SWE","IE":"IRL","BR":"BRA","MX":"MEX",
    "AU":"AUS","NZ":"NZL","IN":"IND","JP":"JPN","AR":"ARG","CL":"CHL","CO":"COL","MA":"MAR","ZA":"ZAF"
}
ISO_GEO = {r[0]: (r[1], r[2], r[3], r[4]) for r in con.execute("SELECT country_iso3, FIRST(region_macro), MEDIAN(latitude), MEDIAN(longitude), MEDIAN(arable_ha_per_cap) FROM dim_markets GROUP BY 1;").fetchall()}

RULES = [
    (r"whey|protein isolate|protein powder", "WHEY_ISOLATE"),
    (r"soy protein|soja isolate", "SOY_ISOLATE"),
    (r"pea protein", "PEA_ISOLATE"),
    (r"tofu|tempeh", "TOFU_TEMPEH"),
    (r"beef|boeuf|ternera|rindfleisch|manzo", "BEEF_FRESH"),
    (r"pork|porc|cerdo|schwein|maiale|jambon|ham", "PORK_FRESH"),
    (r"chicken|poulet|pollo|hähnchen|volaille", "POULTRY_CHIX"),
    (r"egg|oeuf|huevo|eier|uova", "EGGS_CHIX"),
    (r"milk|lait|leche|milch|latte|yogurt|yaourt", "MILK_FLUID"),
    (r"cheese|fromage|queso|käse|formaggio|cheddar|mozzarella|emmental|comte", "CHEESE_HARD"),
    (r"sardine|tuna|thon|atun|mackerel|maquereau|herring|anchov", "FISH_CANNED"),
    (r"salmon|saumon|cod|cabillaud|fish|poisson|pescado|tilapia|shrimp|crevette", "FISH_FRESH"),
    (r"lentil|lentille|lenteja|chickpea|pois chiche|garbanzo", "LENTILS_DRY"),
    (r"bean|haricot|frijol|alubia|soy|soja|peanut|cacahuete", "BEANS_DRY"),
    (r"flour|farine|harina|bread|pain|pan|pasta|pates|spaghetti|oats|flocons", "WHEAT_FLOUR"),
    (r"rice|riz|arroz|reis", "RICE_MILLED"),
]
COMPILED = [(re.compile(p), c) for p, c in RULES]

print("[TRACK 14A] Streaming Open Prices Crowdsourced Supermarket SKU Receipts...")
mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
mkts = set(r[0] for r in con.execute("SELECT market_id FROM dim_markets;").fetchall())
obs = []

for page in range(1, 55):
    url = f"https://prices.openfoodfacts.org/api/v1/prices?page={page}&size=100"
    try:
        raw = subprocess.check_output(["curl","-sL","--connect-timeout","10","--max-time","25", url], timeout=30).decode("utf-8", errors="ignore")
        js = json.loads(raw)
        items = js.get("items", [])
        if not items: break
        for it in items:
            price = it.get("price")
            if not price or float(price) <= 0: continue
            prod = it.get("product") or {}
            loc = it.get("location") or {}
            cc2 = str(loc.get("osm_address_country_code") or "").upper()
            iso3 = CC2_TO_ISO3.get(cc2)
            if not iso3 or iso3 not in w_usd_lat or iso3 not in ISO_GEO: continue

            text_blob = f"{it.get('product_name','')} {prod.get('product_name','')} {' '.join(prod.get('categories_tags',[]))}".lower()
            cc = next((c for rx, c in COMPILED if rx.search(text_blob)), None)
            if not cc or cc not in eff: continue

            qty = prod.get("product_quantity")
            unit = str(prod.get("product_quantity_unit") or "g").lower()
            if qty and float(qty) > 0:
                kg = float(qty) / 1000.0 if unit in ("g","ml") else float(qty)
            else:
                kg = 0.72 if cc == "EGGS_CHIX" else 1.0
            if not (0.08 <= kg <= 15.0): continue

            dt = str(it.get("date") or "2025-06-15")[:10]
            yr = int(dt[:4]) if dt[:4].isdigit() else 2025
            nom_kg = float(price) / kg
            fx = fx_wb.get((iso3, yr), fx_lat.get(iso3, 1.0))
            rusd_kg = nom_kg / fx
            wh_u = w_usd.get((iso3, yr), w_usd_lat[iso3])
            u50 = rusd_kg * (50.0 / eff[cc])
            l50 = 60.0 * (rusd_kg / wh_u) * (50.0 / eff[cc])
            if not (0.10 <= u50 <= 25.0): continue

            city = str(loc.get("osm_address_city") or iso3).strip()
            la = loc.get("osm_lat") or ISO_GEO[iso3][1]
            lo = loc.get("osm_lon") or ISO_GEO[iso3][2]
            mk = f"OP_{iso3}_{re.sub(r'[^A-Z0-9]','',city.upper())[:16]}"
            if mk not in mkts:
                reg, _, _, car = ISO_GEO[iso3]
                con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?,?,?,?,?,?,?,0.12);", (mk, f"{city} (OpenPrices), {iso3}", iso3, reg, float(la), float(lo), float(car)))
                mkts.add(mk)
            mid += 1
            obs.append((mid,"OPEN_PRICES_RETAIL_SKU","OBSERVED_MARKET",mk,cc,dt,nom_kg,str(it.get("currency") or "EUR")[:6],rusd_kg,u50,l50,False))
    except Exception:
        break

if obs:
    con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)

tot_all, tot_mkt, tot_iso = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id), COUNT(DISTINCT m.country_iso3) FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.is_outlier_mad=FALSE;").fetchone()
print(f"[TRACK 14A COMPLETE] Added +{len(obs):,} Supermarket SKU rows! Atlas Total: {tot_all:,} rows | {tot_mkt:,} markets | {tot_iso} economies.")
con.close()
