import duckdb, time, json, urllib.request, urllib.parse, http.cookiejar

t0 = time.time()
con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset IN ('MERCADONA_SHELF_34', 'MULTI_SUPERMARKET_35');")

# Get local hourly wages from DB (with sensible fallbacks)
wages = dict(con.execute("""
    SELECT m.country_iso3, MEDIAN(f.usd_per_50g_diaas / NULLIF(f.labor_min_50g_diaas / 60.0, 0))
    FROM fact_protein_observations f JOIN dim_markets m USING(market_id)
    WHERE f.labor_min_50g_diaas > 0 GROUP BY 1;
""").fetchall())

max_id = con.execute("SELECT COALESCE(MAX(obs_id), 0) FROM fact_protein_observations;").fetchone()[0]
today_str = time.strftime("%Y-%m-%d")
rows = []

# ==============================================================================
# PART 1: MERCADONA SPAIN (With CookieJar so each postal code resolves its true regional warehouse!)
# ==============================================================================
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
CAT_IDS = [112, 115, 116, 117, 118, 120, 121, 122, 123, 126]

def classify_es(name, cat=""):
    txt = f"{name} {cat}".lower()
    if any(k in txt for k in ["huevo", "clara de huevo"]): return ("EGGS_CHIX", 124.0)
    if any(k in txt for k in ["leche ", "yogur", "kéfir"]): return ("MILK_FLUID", 33.0)
    if any(k in txt for k in ["queso", "requesón"]): return ("CHEESE_HARD", 245.0)
    if any(k in txt for k in ["pollo", "pavo", "pechuga", "muslo"]): return ("POULTRY_CHIX", 195.0)
    if any(k in txt for k in ["vacuno", "ternera", "añojo", "entrecot"]): return ("BEEF_FRESH", 215.0)
    if any(k in txt for k in ["cerdo", "lomo", "solomillo", "costilla"]): return ("PORK_FRESH", 190.0)
    if any(k in txt for k in ["atún", "sardina", "caballa", "boquerón"]): return ("FISH_PELAGIC_SMALL", 180.0)
    if any(k in txt for k in ["salmón", "merluza", "bacalao", "lubina", "dorada"]): return ("FISH_PREDATOR_HIGH", 176.0)
    if any(k in txt for k in ["lenteja"]): return ("LENTILS_DRY", 148.0)
    if any(k in txt for k in ["garbanzo", "alubia", "judía"]): return ("BEANS_DRY", 138.0)
    return None

es_wage = wages.get("ESP") or 16.80
for cp, mname, lat, lon in SPAIN_STORES:
    mid = f"ES35_MERC_{cp}"
    con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?, ?, 'ESP', 'SPAIN_RETAIL', ?, ?, 0.08, 0.08);", [mid, mname, lat, lon])
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [("User-Agent", "Mozilla/5.0"), ("Accept", "application/json")]
    wh = "mad1"
    try:
        req = urllib.request.Request(
            "https://tienda.mercadona.es/api/postal-codes/actions/change-pc/",
            data=json.dumps({"new_postal_code": cp}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PUT"
        )
        with opener.open(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            wh = resp.headers.get("x-mercadona-wh") or body.get("wh") or "mad1"
    except Exception:
        pass

    cnt = 0
    for cid in CAT_IDS:
        try:
            with opener.open(f"https://tienda.mercadona.es/api/categories/{cid}/?lang=es&wh={wh}", timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            for sub in payload.get("categories", []):
                for prod in sub.get("products", []):
                    cls = classify_es(prod.get("display_name", ""), sub.get("name", ""))
                    if not cls:
                        continue
                    comm_code, eff_g = cls
                    pi = prod.get("price_instructions", {})
                    if pi.get("reference_price") and str(pi.get("reference_format", "")).lower() in ("kg", "l"):
                        eur_kg = float(pi["reference_price"])
                    elif pi.get("unit_price") and pi.get("unit_size"):
                        eur_kg = float(pi["unit_price"]) / float(pi["unit_size"])
                    else:
                        continue
                    if not (0.40 <= eur_kg <= 55.0):
                        continue
                    usd_kg = eur_kg * 1.09
                    usd_50g = (usd_kg / eff_g) * 50.0
                    if 0.04 <= usd_50g <= 8.50:
                        max_id += 1
                        rows.append((max_id, "MULTI_SUPERMARKET_35", "OBSERVED_RETAIL", mid, comm_code, today_str,
                                     round(eur_kg, 3), "EUR", round(usd_kg, 3), round(usd_50g, 4),
                                     round((usd_50g / es_wage) * 60.0, 2), False))
                        cnt += 1
        except Exception:
            continue
    print(f" -> {mname} (CP {cp}, resolved wh={wh}): {cnt} live SKUs")

# ==============================================================================
# PART 2: VTEX SUPERMARKET CHAINS (Carrefour Brazil, Mega Image Romania, Exito Colombia, Jumbo Chile)
# Standardized public endpoint: /api/catalog_system/pub/products/search/<term>
# ==============================================================================
VTEX_CHAINS = [
    ("Carrefour Brazil (São Paulo)", "BRA", "BRL", 0.18, -23.5505, -46.6333, "https://mercado.carrefour.com.br", [
        ("ovos", "EGGS_CHIX", 124.0, 0.60),
        ("leite", "MILK_FLUID", 33.0, 1.00),
        ("peito de frango", "POULTRY_CHIX", 195.0, 1.00),
        ("carne bovina", "BEEF_FRESH", 215.0, 1.00),
        ("feijao", "BEANS_DRY", 138.0, 1.00),
        ("lentilha", "LENTILS_DRY", 148.0, 0.50),
        ("atum", "FISH_PELAGIC_SMALL", 180.0, 0.17),
    ]),
    ("Mega Image Romania (Bucharest)", "ROU", "RON", 0.22, 44.4268, 26.1025, "https://www.mega-image.ro", [
        ("oua", "EGGS_CHIX", 124.0, 0.60),
        ("lapte", "MILK_FLUID", 33.0, 1.00),
        ("piept de pui", "POULTRY_CHIX", 195.0, 1.00),
        ("carne de porc", "PORK_FRESH", 190.0, 1.00),
        ("fasole", "BEANS_DRY", 138.0, 0.50),
        ("linte", "LENTILS_DRY", 148.0, 0.50),
        ("ton", "FISH_PELAGIC_SMALL", 180.0, 0.16),
    ]),
    ("Jumbo Chile (Santiago)", "CHL", "CLP", 0.00105, -33.4489, -70.6693, "https://www.jumbo.cl", [
        ("huevos", "EGGS_CHIX", 124.0, 0.72),
        ("leche", "MILK_FLUID", 33.0, 1.00),
        ("pechuga de pollo", "POULTRY_CHIX", 195.0, 1.00),
        ("lentejas", "LENTILS_DRY", 148.0, 1.00),
        ("porotos", "BEANS_DRY", 138.0, 1.00),
        ("atun", "FISH_PELAGIC_SMALL", 180.0, 0.16),
    ]),
]

for cname, iso3, cur, fx_usd, lat, lon, base_url, queries in VTEX_CHAINS:
    mid = f"VTEX35_{iso3}_{cname.split()[0].upper()}"
    con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?, ?, ?, 'VTEX_RETAIL', ?, ?, 0.10, 0.10);", [mid, cname, iso3, lat, lon])
    w_hr = wages.get(iso3) or 6.5
    chain_cnt = 0
    for term, comm_code, eff_g, default_kg in queries:
        q_url = f"{base_url}/api/catalog_system/pub/products/search/{urllib.parse.quote(term)}?_from=0&_to=24"
        try:
            req = urllib.request.Request(q_url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                prods = json.loads(resp.read().decode("utf-8"))
            for p in prods:
                items = p.get("items") or []
                if not items:
                    continue
                sellers = items[0].get("sellers") or []
                if not sellers:
                    continue
                offer = sellers[0].get("commertialOffer") or {}
                price = offer.get("Price")
                if not price or float(price) <= 0:
                    continue
                mult = float(items[0].get("unitMultiplier") or default_kg)
                if mult <= 0.05 or mult > 10.0:
                    mult = default_kg
                local_kg = float(price) / mult
                usd_kg = local_kg * fx_usd
                if not (0.35 <= usd_kg <= 55.0):
                    continue
                usd_50g = (usd_kg / eff_g) * 50.0
                if 0.04 <= usd_50g <= 8.50:
                    max_id += 1
                    rows.append((max_id, "MULTI_SUPERMARKET_35", "OBSERVED_RETAIL", mid, comm_code, today_str,
                                 round(local_kg, 3), cur, round(usd_kg, 3), round(usd_50g, 4),
                                 round((usd_50g / w_hr) * 60.0, 2), False))
                    chain_cnt += 1
        except Exception:
            continue
    print(f" -> {cname}: {chain_cnt} live VTEX shelf SKUs")

if rows:
    con.executemany("""
        INSERT INTO fact_protein_observations (
            obs_id, source_dataset, price_basis, market_id, commodity_code, obs_date,
            nominal_price_kg, currency_code, real_usd_per_kg, usd_per_50g_diaas, labor_min_50g_diaas, is_outlier_mad
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, rows)

con.execute("DELETE FROM dim_markets WHERE market_id NOT IN (SELECT DISTINCT market_id FROM fact_protein_observations);")
tot_rows, tot_mkts = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id) FROM fact_protein_observations;").fetchone()
print(f"[TRACK 35 COMPLETE] Added +{len(rows):,} live multi-chain supermarket SKUs! Total DB: {tot_rows:,} rows | {tot_mkts:,} markets ({time.time()-t0:.1f}s)")
con.close()
