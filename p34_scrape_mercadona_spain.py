import duckdb, time, json, urllib.request

t0 = time.time()
con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'MERCADONA_SHELF_34';")

# Spanish postal codes -> (Market Name, Lat, Lon) covering mainland, north, south & islands
SPAIN_STORES = [
    ("08001", "Mercadona Barcelona Ciutat Vella", 41.3809, 2.1733),
    ("28001", "Mercadona Madrid Salamanca",       40.4238, -3.6883),
    ("46001", "Mercadona Valencia Ciutat Vella",  39.4745, -0.3768),
    ("41001", "Mercadona Sevilla Casco Antiguo",  37.3891, -5.9845),
    ("29001", "Mercadona Málaga Centro",          36.7213, -4.4214),
    ("50001", "Mercadona Zaragoza Centro",        41.6488, -0.8891),
    ("30001", "Mercadona Murcia Centro",          37.9922, -1.1307),
    ("48001", "Mercadona Bilbao Abando",          43.2630, -2.9350),
    ("15001", "Mercadona A Coruña Centro",        43.3623, -8.4115),
    ("47001", "Mercadona Valladolid Centro",      41.6523, -4.7245),
    ("07001", "Mercadona Palma de Mallorca",      39.5696, 2.6502),
    ("35001", "Mercadona Las Palmas Gran Canaria",28.1000, -15.4167),
]

# Mercadona top-level protein categories (Dairy/Eggs, Meat, Fish, Legumes/Grains)
CAT_IDS = [112, 115, 116, 117, 118, 120, 121, 122, 123, 126]

def classify_sku(name, cat_name):
    txt = f"{name} {cat_name}".lower()
    if any(k in txt for k in ["huevo", "clara de huevo"]): return ("EGGS_CHIX", 124.0)
    if any(k in txt for k in ["leche ", "yogur", "kéfir"]): return ("MILK_FLUID", 33.0)
    if any(k in txt for k in ["queso", "requesón"]): return ("CHEESE_HARD", 245.0)
    if any(k in txt for k in ["pollo", "pavo", "pechuga", "muslo", "contramuslo"]): return ("POULTRY_CHIX", 195.0)
    if any(k in txt for k in ["vacuno", "ternera", "añojo", "hamburguesa de vacuno", "entrecot"]): return ("BEEF_FRESH", 215.0)
    if any(k in txt for k in ["cerdo", "lomo", "solomillo", "costilla", "chuleta"]): return ("PORK_FRESH", 190.0)
    if any(k in txt for k in ["cordero"]): return ("MUTTON_FRESH", 195.0)
    if any(k in txt for k in ["atún", "sardina", "caballa", "boquerón", "melva"]): return ("FISH_PELAGIC_SMALL", 180.0)
    if any(k in txt for k in ["salmón", "merluza", "bacalao", "lubina", "dorada"]): return ("FISH_PREDATOR_HIGH", 176.0)
    if any(k in txt for k in ["lenteja"]): return ("LENTILS_DRY", 148.0)
    if any(k in txt for k in ["garbanzo", "alubia", "judía", "frijol"]): return ("BEANS_DRY", 138.0)
    return None

# Get Spain median hourly wage from existing DB (or fallback ~$16.80/hr)
w_row = con.execute("""
    SELECT MEDIAN(f.usd_per_50g_diaas / NULLIF(f.labor_min_50g_diaas / 60.0, 0))
    FROM fact_protein_observations f JOIN dim_markets m USING(market_id)
    WHERE m.country_iso3 = 'ESP' AND f.labor_min_50g_diaas > 0;
""").fetchone()
spain_wage_hr = float(w_row[0]) if w_row and w_row[0] else 16.80

max_id = con.execute("SELECT COALESCE(MAX(obs_id), 0) FROM fact_protein_observations;").fetchone()[0]
today_str = time.strftime("%Y-%m-%d")
rows = []

for cp, mname, lat, lon in SPAIN_STORES:
    mid = f"ES34_MERC_{cp}"
    con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?, ?, 'ESP', 'SPAIN_RETAIL', ?, ?, 0.08, 0.08);", [mid, mname, lat, lon])
    
    # Resolve postal code -> Mercadona regional warehouse code (wh)
    wh = "mad1"
    try:
        req = urllib.request.Request(
            "https://tienda.mercadona.es/api/postal-codes/actions/change-pc/",
            data=json.dumps({"new_postal_code": cp}).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            method="PUT"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            wh = resp.headers.get("x-mercadona-wh") or json.loads(resp.read().decode()).get("wh") or "mad1"
    except Exception:
        pass

    store_skus = 0
    for cid in CAT_IDS:
        url = f"https://tienda.mercadona.es/api/categories/{cid}/?lang=es&wh={wh}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            for sub in payload.get("categories", []):
                sub_name = sub.get("name", "")
                for prod in sub.get("products", []):
                    pname = prod.get("display_name", "")
                    cls = classify_sku(pname, sub_name)
                    if not cls:
                        continue
                    comm_code, eff_g = cls
                    pi = prod.get("price_instructions", {})
                    ref_price = pi.get("reference_price")  # Exact EUR per kg or L!
                    ref_fmt = str(pi.get("reference_format", "")).lower()
                    unit_price = pi.get("unit_price")
                    if ref_price and ref_fmt in ("kg", "l"):
                        eur_kg = float(ref_price)
                    elif unit_price and pi.get("unit_size"):
                        eur_kg = float(unit_price) / float(pi["unit_size"])
                    else:
                        continue
                    if not (0.40 <= eur_kg <= 55.0):
                        continue
                    usd_kg = eur_kg * 1.09
                    usd_50g = (usd_kg / eff_g) * 50.0
                    if not (0.04 <= usd_50g <= 8.50):
                        continue
                    l_min = (usd_50g / spain_wage_hr) * 60.0
                    max_id += 1
                    rows.append((
                        max_id, "MERCADONA_SHELF_34", "OBSERVED_RETAIL",
                        mid, comm_code, today_str,
                        round(eur_kg, 3), "EUR", round(usd_kg, 3),
                        round(usd_50g, 4), round(l_min, 2), False
                    ))
                    store_skus += 1
        except Exception:
            continue
    print(f" -> {mname} (CP {cp}, wh={wh}): {store_skus} live protein SKUs scraped")

if rows:
    con.executemany("""
        INSERT INTO fact_protein_observations (
            obs_id, source_dataset, price_basis, market_id, commodity_code, obs_date,
            nominal_price_kg, currency_code, real_usd_per_kg, usd_per_50g_diaas, labor_min_50g_diaas, is_outlier_mad
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, rows)

tot_rows, tot_mkts = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id) FROM fact_protein_observations;").fetchone()
print(f"[TRACK 34 COMPLETE] Added +{len(rows):,} live Mercadona shelf prices! Total DB: {tot_rows:,} rows | {tot_mkts:,} markets ({time.time()-t0:.1f}s)")
con.close()
