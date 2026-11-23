import json, re, subprocess, time, duckdb

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset IN ('OPEN_PRICES_SUPERMARKET');")
eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())

fx_js = json.loads(subprocess.check_output(["curl","-sL","--connect-timeout","15","--max-time","40","https://api.worldbank.org/v2/country/all/indicator/PA.NUS.FCRF?format=json&per_page=25000&date=2020:2026"], timeout=45).decode())
fx_wb = {(r["countryiso3code"], int(r["date"])): float(r["value"]) for r in (fx_js[1] or []) if r.get("value") is not None and r.get("countryiso3code")}
fx_lat = {iso: v for (iso, _), v in sorted(fx_wb.items())}
fx_lat.update({"EUR": 0.92, "GBP": 0.78, "CHF": 0.88, "CAD": 1.38, "AUD": 1.52, "JPY": 152.0})

w_df = con.execute("SELECT country_iso3, year, currency_code, unskilled_wage_hr FROM dim_macro_wages WHERE unskilled_wage_hr > 0;").df()
w_usd = {(r.country_iso3, int(r.year)): (float(r.unskilled_wage_hr) if r.currency_code=="USD" else float(r.unskilled_wage_hr)/fx_wb[(r.country_iso3, int(r.year))]) for r in w_df.itertuples(index=False) if r.currency_code=="USD" or fx_wb.get((r.country_iso3, int(r.year)),0)>0}
w_usd_lat = {iso: v for (iso, _), v in sorted(w_usd.items())}

ISO2_TO_3 = {
    "FR":"FRA","ES":"ESP","DE":"DEU","IT":"ITA","GB":"GBR","UK":"GBR","NL":"NLD","BE":"BEL",
    "CH":"CHE","AT":"AUT","PT":"PRT","PL":"POL","SE":"SWE","NO":"NOR","DK":"DNK","FI":"FIN",
    "US":"USA","CA":"CAN","AU":"AUS","NZ":"NZL","JP":"JPN","KR":"KOR","BR":"BRA","MX":"MEX","IN":"IND"
}
ISO_FALLBACK = {r[0]: (r[1], r[2], r[3], r[4]) for r in con.execute("SELECT country_iso3, FIRST(region_macro), MEDIAN(latitude), MEDIAN(longitude), MEDIAN(arable_ha_per_cap) FROM dim_markets GROUP BY 1;").fetchall()}

RULES = [
    (r"lentil|lentille|lenteja|linse|chickpea|pois chiche|garbanzo|bean|haricot|alubia|frijol|soy|tofu|edamame", "LENTILS_DRY"),
    (r"egg|oeuf|huevo|ei\b|eier|uova", "EGGS_CHIX"),
    (r"milk|lait|leche|milch|latte|yogurt|yaourt|skyr|quark", "MILK_FLUID"),
    (r"cheese|fromage|queso|kase|formaggio|cheddar|mozzarella|emmental|gouda", "CHEESE_HARD"),
    (r"chicken|poulet|pollo|hahn|poultry|dinde|turkey", "POULTRY_CHIX"),
    (r"pork|porc|cerdo|schwein|jambon|bacon|ham", "PORK_FRESH"),
    (r"beef|boeuf|ternera|rind|steak|burger", "BEEF_FRESH"),
    (r"sardine|anchov|herring|mackerel|maquereau|caballa", "FISH_PELAGIC_SMALL"),
    (r"tilapia|pangasius|carp|trout|truite|trucha", "FISH_AQUACULTURE"),
    (r"salmon|saumon|tuna|thon|atun|cod|cabillaud|bacalao", "FISH_PREDATOR_HIGH"),
    (r"flour|farine|harina|mehl|pasta|spaghetti|oat|avoine|bread|pain|pan\b", "WHEAT_FLOUR"),
    (r"rice|riz|arroz|reis", "RICE_MILLED"),
]
COMPILED = [(re.compile(p), c) for p, c in RULES]

mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
mkts = set(r[0] for r in con.execute("SELECT market_id FROM dim_markets;").fetchall())
obs = []

print("[TRACK 18A - GAP #3] Fetching Open Food Facts Open Prices Supermarket Receipts...")
for page in range(1, 26):
    url = f"https://prices.openfoodfacts.org/api/v1/prices?page={page}&size=100"
    try:
        raw = subprocess.check_output(["curl","-sL","-A","WorldProteinAtlas/1.0","--connect-timeout","12","--max-time","25", url], timeout=30).decode("utf-8", errors="ignore")
        items = json.loads(raw).get("items", [])
        if not items: break
        for it in items:
            price = it.get("price")
            p_per = str(it.get("price_per") or "KILOGRAM").upper()
            cur = str(it.get("currency") or "EUR").upper()
            dt = str(it.get("date") or "2026-06-01")[:10]
            prod = it.get("product") or {}
            loc = it.get("location") or {}
            pname = " ".join(filter(None, [str(it.get("product_name") or ""), str(prod.get("product_name") or ""), " ".join(prod.get("categories_tags") or [])])).lower()
            cc = next((c for rx, c in COMPILED if rx.search(pname)), None)
            if not cc or cc not in eff or not price: continue

            c2 = str(loc.get("osm_address_country_code") or "").upper()
            iso = ISO2_TO_3.get(c2)
            if not iso or iso not in w_usd_lat: continue

            nom_kg = float(price)
            # If unit is not explicitly KILOGRAM, check product quantity if available
            qty = prod.get("product_quantity")
            qunit = str(prod.get("product_quantity_unit") or "g").lower()
            if p_per != "KILOGRAM":
                if qty and float(qty) > 0:
                    kg = float(qty)/1000.0 if qunit in ("g","ml") else float(qty)
                    if 0.05 <= kg <= 25.0:
                        nom_kg = nom_kg / kg
                    else:
                        continue
                else:
                    continue

            yr = int(dt[:4]) if dt[:4].isdigit() else 2026
            fx = fx_lat.get(cur, fx_wb.get((iso, yr), fx_lat.get(iso, 1.0)))
            if fx <= 0: continue
            rusd_kg = nom_kg / fx
            wh_u = w_usd.get((iso, yr), w_usd_lat[iso])
            u50 = rusd_kg * (50.0 / eff[cc])
            l50 = 60.0 * (rusd_kg / wh_u) * (50.0 / eff[cc])
            if not (0.05 <= u50 <= 25.0 and 0.1 <= l50 <= 600.0): continue

            city = str(loc.get("osm_address_city") or iso).strip()
            la = loc.get("osm_lat")
            lo = loc.get("osm_lon")
            reg, fla, flo, car = ISO_FALLBACK.get(iso, ("EUROPE", 48.0, 2.0, 0.25))
            la = float(la) if la is not None else fla
            lo = float(lo) if lo is not None else flo
            clean_c = re.sub(r"[^A-Z0-9]", "", city.upper())[:16] or iso
            mk = f"OFF_{iso}_{clean_c}"
            if mk not in mkts:
                con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?,?,?,?,?,?,?,0.30);", (mk, f"{city.title()}, {iso} (Supermarket)", iso, reg, la, lo, car))
                mkts.add(mk)
            mid += 1
            obs.append((mid, "OPEN_PRICES_SUPERMARKET", "OBSERVED_MARKET", mk, cc, dt, nom_kg, cur, rusd_kg, u50, l50, False))
        time.sleep(0.15)
    except Exception:
        break

if obs:
    con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)

tot_all, tot_mkt, tot_iso = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id), COUNT(DISTINCT m.country_iso3) FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.is_outlier_mad=FALSE;").fetchone()
print(f"[TRACK 18A COMPLETE] Added +{len(obs):,} Rich-World Supermarket rows! Atlas Total: {tot_all:,} rows | {tot_mkt:,} markets | {tot_iso} economies.")
con.close()
