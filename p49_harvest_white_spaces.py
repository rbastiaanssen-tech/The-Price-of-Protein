import json, time, subprocess, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
import duckdb

t0 = time.time()

def curl_json(url, headers=None, post_body=None, timeout=18):
    cmd = ["curl", "-sL", "--max-time", str(timeout), "-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", "-H", "Accept: application/json"]
    if headers:
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
    if post_body:
        cmd += ["-X", "POST", "-H", "Content-Type: application/json", "-d", post_body]
    cmd.append(url)
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.stdout if res.returncode == 0 else ""

# Targeted high-yield supermarket APIs across White/Grey Spaces (Africa, China/APAC, GCC, LATAM)
TARGETS = [
    # --- SUB-SAHARAN AFRICA ---
    ("AFR_KEN_CARREFOUR", "Carrefour Kenya (Nairobi)", "KEN", "KES", 0.0077, -1.2921, 36.8219,
     "https://www.carrefour.ke/api/v8/search?keyword={q}&pageSize=30&currentPage=0&lang=en",
     {"storeId": "mafken", "lang": "en", "Accept-Language": "en"}),
    ("AFR_ZAF_PNP", "Pick n Pay South Africa (Johannesburg)", "ZAF", "ZAR", 0.055, -26.2041, 28.0473,
     "https://www.pnp.co.za/pnpa/v2/pnp-spa/products/search?fields=FULL&query={q}&pageSize=30", {}),
    ("AFR_ZAF_WOOLWORTHS", "Woolworths Food South Africa (Cape Town)", "ZAF", "ZAR", 0.055, -33.9249, 18.4241,
     "https://www.woolworths.co.za/server/searchCategory?pageURL=%2Fcat%2FFood%2F_%2FN-1z13sk5&searchTerm={q}", {}),

    # --- CHINA / GREATER CHINA / APAC ---
    ("APAC_HKG_PARKNSHOP", "PARKnSHOP Hong Kong (Central)", "HKG", "HKD", 0.128, 22.2819, 114.1585,
     "https://api.parknshop.com/api/v2/pnshk/products/search?fields=FULL&query={q}&pageSize=35&lang=en", {}),
    ("APAC_TWN_CARREFOUR", "Carrefour Taiwan (Taipei)", "TWN", "TWD", 0.031, 25.0330, 121.5654,
     "https://online.carrefour.com.tw/api/search?q={q}&pageSize=30", {}),
    ("APAC_SGP_FAIRPRICE", "NTUC FairPrice Singapore (CBD)", "SGP", "SGD", 0.75, 1.2834, 103.8607,
     "https://website-api.omni.fairprice.com.sg/api/product/v2?algoliaQuery={q}&page=1&pageSize=35&storeId=165", {}),
    ("APAC_MYS_LOTUSS", "Lotus's Malaysia (Kuala Lumpur)", "MYS", "MYR", 0.225, 3.1390, 101.6869,
     "https://api.lotuss.com.my/api/v1/products/search?q={q}&limit=30", {}),
    ("APAC_THA_TOPS", "Tops Supermarket Thailand (Bangkok)", "THA", "THB", 0.029, 13.7563, 100.5018,
     "https://www.tops.co.th/api/v1/products/search?q={q}&limit=30", {}),
    ("APAC_IND_BIGBASKET", "BigBasket India (Bengaluru)", "IND", "INR", 0.0118, 12.9716, 77.5946,
     "https://www.bigbasket.com/listing-svc/v2/products?type=ps&slug={q}&page=1", {}),

    # --- MIDDLE EAST / GCC ---
    ("GCC_ARE_CARREFOUR", "Carrefour UAE (Dubai)", "ARE", "AED", 0.272, 25.2048, 55.2708,
     "https://www.carrefouruae.com/api/v8/search?keyword={q}&pageSize=30&currentPage=0&lang=en",
     {"storeId": "mafuae", "lang": "en"}),
    ("GCC_SAU_CARREFOUR", "Carrefour Saudi Arabia (Riyadh)", "SAU", "SAR", 0.266, 24.7136, 46.6753,
     "https://www.carrefourksa.com/api/v8/search?keyword={q}&pageSize=30&currentPage=0&lang=en",
     {"storeId": "mafsau", "lang": "en"}),
    ("GCC_QAT_CARREFOUR", "Carrefour Qatar (Doha)", "QAT", "QAR", 0.274, 25.2854, 51.5310,
     "https://www.carrefourqatar.com/api/v8/search?keyword={q}&pageSize=30&currentPage=0&lang=en",
     {"storeId": "mafqat", "lang": "en"}),

    # --- LATAM VTEX LEGACY CATALOGS (Direct unblocked catalog endpoints) ---
    ("LAT_BRA_CARREFOUR", "Carrefour Brasil (Sao Paulo)", "BRA", "BRL", 0.18, -23.5505, -46.6333,
     "https://mercado.carrefour.com.br/api/catalog_system/pub/products/search/{q}?_from=0&_to=39", {}),
    ("LAT_BRA_ASSAI", "Assai Atacadista Brasil (Rio)", "BRA", "BRL", 0.18, -22.9068, -43.1729,
     "https://www.assai.com.br/api/catalog_system/pub/products/search/{q}?_from=0&_to=39", {}),
    ("LAT_CHL_JUMBO", "Jumbo Chile (Santiago)", "CHL", "CLP", 0.00105, -33.4489, -70.6693,
     "https://www.jumbo.cl/api/catalog_system/pub/products/search/{q}?_from=0&_to=39", {}),
    ("LAT_CHL_LIDER", "Lider Walmart Chile (Santiago)", "CHL", "CLP", 0.00105, -33.4372, -70.6506,
     "https://www.lider.cl/supermercado/api/catalog_system/pub/products/search/{q}?_from=0&_to=39", {}),
    ("LAT_COL_OLIMPICA", "Supertiendas Olimpica (Barranquilla)", "COL", "COP", 0.00024, 10.9685, -74.7813,
     "https://www.olimpica.com/api/catalog_system/pub/products/search/{q}?_from=0&_to=39", {}),
    ("LAT_MEX_WALMART", "Walmart Supercenter Mexico (CDMX)", "MEX", "MXN", 0.052, 19.4326, -99.1332,
     "https://super.walmart.com.mx/api/v2/page/search?query={q}", {}),
    ("LAT_ARG_COTO", "Coto Digital Argentina (Buenos Aires)", "ARG", "ARS", 0.00095, -34.6037, -58.3816,
     "https://www.cotodigital3.com.ar/sitios/cdigi/browse?_dyncharset=utf-8&Ntt={q}&format=json", {}),
]
QUERIES = [
    ("eggs", "EGGS_CHIX", 124.0, 0.60),
    ("milk", "MILK_FLUID", 33.0, 1.00),
    ("cheese", "CHEESE_HARD", 245.0, 0.35),
    ("chicken breast", "POULTRY_CHIX", 195.0, 0.70),
    ("beef", "BEEF_FRESH", 215.0, 0.70),
    ("pork", "PORK_FRESH", 190.0, 0.70),
    ("tuna", "FISH_PELAGIC_SMALL", 180.0, 0.16),
    ("lentils", "LENTILS_DRY", 148.0, 0.50),
    ("beans", "BEANS_DRY", 138.0, 0.50),
]

def extract_items_recursive(obj, out):
    if isinstance(obj, dict):
        name = obj.get("name") or obj.get("productName") or obj.get("title") or obj.get("displayName")
        # Extract price across Hybris, Carrefour MAF, FairPrice, VTEX, BigBasket schemas
        price = None
        if isinstance(obj.get("price"), (int, float)):
            price = float(obj["price"])
        elif isinstance(obj.get("price"), dict):
            p_d = obj["price"]
            price = p_d.get("value") or p_d.get("price") or p_d.get("discountPrice") or p_d.get("regularPrice")
        elif isinstance(obj.get("final_price"), (int, float)):
            price = float(obj["final_price"])
        elif isinstance(obj.get("pricing"), dict):
            price = obj["pricing"].get("price") or obj["pricing"].get("promo_price")
        elif isinstance(obj.get("commertialOffer"), dict):
            price = obj["commertialOffer"].get("Price")

        if name and isinstance(name, str) and price and isinstance(price, (int, float)) and price > 0:
            out.append((name, float(price)))

        for v in obj.values():
            if isinstance(v, (dict, list)):
                extract_items_recursive(v, out)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                extract_items_recursive(item, out)

def harvest_chain(target):
    mid, mname, iso3, cur, fx, lat, lon, url_tpl, hdrs = target
    skus = []
    seen = set()
    for q_word, comm, eff_g, d_kg in QUERIES:
        url = url_tpl.format(q=urllib.parse.quote(q_word))
        raw = curl_json(url, headers=hdrs, timeout=14)
        if not raw or (not raw.startswith("{") and not raw.startswith("[")):
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        pairs = []
        extract_items_recursive(data, pairs)
        for title, price in pairs:
            key = (comm, title[:40])
            if key in seen:
                continue
            seen.add(key)
            nom_kg = price / d_kg
            usd_kg = nom_kg * fx
            usd_50g = (usd_kg / eff_g) * 50.0
            if 0.30 <= usd_kg <= 48.0 and 0.04 <= usd_50g <= 5.50:
                skus.append((mid, mname, iso3, lat, lon, comm, round(nom_kg, 3), cur, round(usd_kg, 3), round(usd_50g, 4)))
    return mname, iso3, skus

print(f"[TRACK 49] Probing {len(TARGETS)} high-priority chains across Africa, China/APAC, GCC & LATAM (12 workers)...")
all_rows = []
with ThreadPoolExecutor(max_workers=12) as ex:
    futs = {ex.submit(harvest_chain, t): t for t in TARGETS}
    for fut in as_completed(futs):
        mname, iso3, items = fut.result()
        status = f"{len(items)} live SKUs" if items else "blocked/empty"
        print(f" -> [{iso3}] {mname:42s}: {status}")
        all_rows.extend(items)

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'WHITESPACE_CHAINS_49';")

wages = dict(con.execute("""
    SELECT m.country_iso3, MEDIAN(f.usd_per_50g_diaas / NULLIF(f.labor_min_50g_diaas / 60.0, 0))
    FROM fact_protein_observations f JOIN dim_markets m USING(market_id)
    WHERE f.labor_min_50g_diaas > 0 GROUP BY 1;
""").fetchall())

max_id = con.execute("SELECT COALESCE(MAX(obs_id), 0) FROM fact_protein_observations;").fetchone()[0]
today_str = time.strftime("%Y-%m-%d")
ins = []

for mid, mname, iso3, lat, lon, comm, nom_kg, cur, usd_kg, usd_50g in all_rows:
    con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?, ?, ?, 'WHITESPACE_CHAINS_49', ?, ?, 0.08, 0.08);",
                [mid, mname, iso3, lat, lon])
    w_hr = wages.get(iso3) or 10.0
    max_id += 1
    ins.append((
        max_id, "WHITESPACE_CHAINS_49", "OBSERVED_RETAIL", mid, comm, today_str,
        nom_kg, cur, usd_kg, usd_50g, round((usd_50g / w_hr) * 60.0, 2), False
    ))

if ins:
    con.executemany("""INSERT INTO fact_protein_observations (
        obs_id, source_dataset, price_basis, market_id, commodity_code, obs_date,
        nominal_price_kg, currency_code, real_usd_per_kg, usd_per_50g_diaas, labor_min_50g_diaas, is_outlier_mad
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""", ins)

con.execute("DELETE FROM dim_markets WHERE market_id NOT IN (SELECT DISTINCT market_id FROM fact_protein_observations);")
tot_r, tot_m = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id) FROM fact_protein_observations;").fetchone()
tot_c = con.execute("SELECT COUNT(DISTINCT country_iso3) FROM dim_markets;").fetchone()[0]
print(f"\n[TRACK 49 COMPLETE] Added +{len(ins):,} live SKUs across Africa/APAC/GCC/LATAM! Total DB: {tot_r:,} rows | {tot_m:,} markets | {tot_c} countries ({time.time()-t0:.1f}s)")
con.close()
