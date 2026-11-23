import json, time, subprocess, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
import duckdb

t0 = time.time()
domains = {}

def curl_get(url, post_data=None, timeout=20):
    cmd = ["curl", "-sL", "--max-time", str(timeout), "-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", "-H", "Accept: application/json"]
    if post_data:
        cmd += ["-X", "POST", "--data-urlencode", f"data={post_data}"]
    cmd.append(url)
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.stdout if res.returncode == 0 else ""

def add_domain(raw_url, brand=""):
    if not raw_url:
        return
    if not raw_url.startswith("http"):
        raw_url = "https://" + raw_url
    parsed = urllib.parse.urlparse(raw_url.strip())
    host = parsed.netloc.lower().strip()
    if not host or "." not in host or any(s in host for s in [
        "facebook.", "instagram.", "google.", "wikipedia.", "twitter.", "x.com", "youtube.", "linkedin.", "vtexcommercestable"
    ]):
        return
    base = f"https://{host}"
    if base not in domains:
        tld = host.split(".")[-1].upper()
        domains[base] = (brand or host.replace("www.", "").split(".")[0].title(), tld)

print("[TRACK 47A] Querying Common Crawl CC-MAIN-2026-43 CDX Index for live VTEX catalog endpoints...")
for pattern in ["*/api/catalog_system/pub/products/search*", "*/_v/api/intelligent-search/product_search*"]:
    cc_raw = curl_get(f"https://index.commoncrawl.org/CC-MAIN-2026-43-index?url={urllib.parse.quote(pattern)}&output=json&limit=450", timeout=25)
    for line in cc_raw.splitlines():
        try:
            obj = json.loads(line)
            add_domain(obj.get("url", ""))
        except Exception:
            continue
print(f" -> Common Crawl yielded {len(domains)} live VTEX storefronts")

print("[TRACK 47B] Querying regional OpenStreetMap slices for supermarket websites...")
BBOXES = [
    ("South America", "-56,-82,13,-34"),
    ("Central/North America", "14,-125,55,-65"),
    ("Western/Central Europe", "36,-10,55,20"),
    ("Eastern/Northern Europe", "42,20,66,35"),
    ("APAC/Middle East", "-42,25,36,155"),
]
for label, bbox in BBOXES:
    q = f'[out:json][timeout:18];node["shop"="supermarket"]["website"]({bbox});out tags 400;'
    ov_raw = curl_get("https://overpass-api.de/api/interpreter", post_data=q, timeout=22)
    if '"elements"' in ov_raw:
        try:
            for el in json.loads(ov_raw).get("elements", []):
                tags = el.get("tags", {})
                add_domain(tags.get("website") or tags.get("contact:website") or "", tags.get("brand") or tags.get("name") or "")
        except Exception:
            pass
    print(f" -> After {label:24s}: {len(domains):,} unique domains")
TLD_MAP = {
    "ES": ("ESP", "EUR", 1.09, 40.42, -3.70), "PT": ("PRT", "EUR", 1.09, 38.72, -9.14),
    "FR": ("FRA", "EUR", 1.09, 48.86, 2.35),  "DE": ("DEU", "EUR", 1.09, 52.52, 13.40),
    "IT": ("ITA", "EUR", 1.09, 41.90, 12.50), "NL": ("NLD", "EUR", 1.09, 52.37, 4.89),
    "BE": ("BEL", "EUR", 1.09, 50.85, 4.35),  "AT": ("AUT", "EUR", 1.09, 48.21, 16.37),
    "PL": ("POL", "PLN", 0.25, 52.23, 21.01), "RO": ("ROU", "RON", 0.22, 44.43, 26.10),
    "HU": ("HUN", "HUF", 0.0027, 47.50, 19.04),"CZ": ("CZE", "CZK", 0.043, 50.08, 14.44),
    "BG": ("BGR", "BGN", 0.56, 42.70, 23.32), "HR": ("HRV", "EUR", 1.09, 45.81, 15.98),
    "SK": ("SVK", "EUR", 1.09, 48.15, 17.11), "SI": ("SVN", "EUR", 1.09, 46.06, 14.51),
    "GR": ("GRC", "EUR", 1.09, 37.98, 23.73), "CH": ("CHE", "CHF", 1.14, 47.38, 8.54),
    "SE": ("SWE", "SEK", 0.095, 59.33, 18.07),"NO": ("NOR", "NOK", 0.092, 59.91, 10.75),
    "DK": ("DNK", "DKK", 0.146, 55.68, 12.57),"FI": ("FIN", "EUR", 1.09, 60.17, 24.94),
    "IE": ("IRL", "EUR", 1.09, 53.35, -6.26), "UK": ("GBR", "GBP", 1.28, 51.51, -0.13),
    "MX": ("MEX", "MXN", 0.052, 19.43, -99.13),"BR": ("BRA", "BRL", 0.18, -23.55, -46.63),
    "AR": ("ARG", "ARS", 0.00095,-34.60, -58.38),"CL": ("CHL", "CLP", 0.00105,-33.45, -70.67),
    "CO": ("COL", "COP", 0.00024, 4.71, -74.07),"PE": ("PER", "PEN", 0.27, -12.05, -77.04),
    "UY": ("URY", "UYU", 0.024, -34.90, -56.16),"EC": ("ECU", "USD", 1.00, -0.18, -78.47),
    "CR": ("CRI", "CRC", 0.00195, 9.93, -84.09),"PA": ("PAN", "USD", 1.00, 8.98, -79.52),
    "AU": ("AUS", "AUD", 0.66, -33.87, 151.21),"NZ": ("NZD", "NZD", 0.60, -36.85, 174.76),
    "CA": ("CAN", "CAD", 0.73, 43.65, -79.38), "ZA": ("ZAF", "ZAR", 0.055, -26.20, 28.05),
    "IN": ("IND", "INR", 0.0118, 19.08, 72.88),"SG": ("SGP", "SGD", 0.75, 1.35, 103.82),
    "AE": ("ARE", "AED", 0.272, 25.20, 55.27), "SA": ("SAU", "SAR", 0.266, 24.71, 46.68),
    "LT": ("LTU", "EUR", 1.09, 54.69, 25.28), "LV": ("LVA", "EUR", 1.09, 56.95, 24.11),
    "EE": ("EST", "EUR", 1.09, 59.44, 24.75), "TR": ("TUR", "TRY", 0.029, 41.01, 28.98),
}

def classify_universal(title):
    t = title.lower()
    if any(k in t for k in ["egg", "huevo", "ovo", "oeuf", "eier", "oua", "vejce", "tojas", "jajk", "uova"]): return ("EGGS_CHIX", 124.0, 0.60)
    if any(k in t for k in ["milk", "leche", "leite", "lait", "milch", "lapte", "mleko", "tej", "latte"]): return ("MILK_FLUID", 33.0, 1.00)
    if any(k in t for k in ["cheese", "queso", "queijo", "fromage", "kaese", "käse", "cascaval", "syr", "ser ", "formaggio"]): return ("CHEESE_HARD", 245.0, 0.35)
    if any(k in t for k in ["chicken", "pollo", "frango", "poulet", "haehnchen", "pui", "kureci", "kurczak"]): return ("POULTRY_CHIX", 195.0, 0.70)
    if any(k in t for k in ["beef", "vacuno", "ternera", "carne molida", "carne moida", "boeuf", "rind", "vita", "wolowina"]): return ("BEEF_FRESH", 215.0, 0.70)
    if any(k in t for k in ["pork", "cerdo", "suino", "porc", "schwein", "wieprzowina"]): return ("PORK_FRESH", 190.0, 0.70)
    if any(k in t for k in ["tuna", "atun", "atún", "atum", "thon", "thunfisch", "ton", "sardine", "tunczyk"]): return ("FISH_PELAGIC_SMALL", 180.0, 0.16)
    if any(k in t for k in ["lentil", "lenteja", "lentilha", "lentille", "linsen", "linte", "cocka", "soczewica"]): return ("LENTILS_DRY", 148.0, 0.50)
    if any(k in t for k in ["bean", "frijol", "feijao", "feijão", "haricot", "fasole", "fazole", "fasola"]): return ("BEANS_DRY", 138.0, 0.50)
    return None

def probe_domain(idx, base, meta):
    brand, tld = meta
    iso3, cur, fx, lat, lon = TLD_MAP.get(tld, ("USA", "USD", 1.0, 39.83, -98.58))
    mid = f"DISC47_{iso3}_{idx:03d}"
    found = []

    raw = curl_get(f"{base}/api/catalog_system/pub/products/search?_from=0&_to=49", timeout=6)
    if raw.startswith("["):
        try:
            for p in json.loads(raw):
                cls = classify_universal(p.get("productName", ""))
                if not cls: continue
                comm, eff_g, d_kg = cls
                skus = p.get("items") or []
                if not skus: continue
                offer = (skus[0].get("sellers") or [{}])[0].get("commertialOffer") or {}
                price = offer.get("Price")
                if not price or float(price) <= 0: continue
                mult = float(skus[0].get("unitMultiplier") or d_kg)
                if not (0.08 <= mult <= 10.0): mult = d_kg
                nom_kg = float(price) / mult
                usd_kg = nom_kg * fx
                usd_50g = (usd_kg / eff_g) * 50.0
                if 0.30 <= usd_kg <= 45.0 and 0.04 <= usd_50g <= 5.50:
                    found.append((mid, brand[:60], iso3, lat, lon, comm, round(nom_kg, 3), cur, round(usd_kg, 3), round(usd_50g, 4)))
            if found:
                return brand, "VTEX_LEGACY", found
        except Exception:
            pass

    raw = curl_get(f"{base}/products.json?limit=50", timeout=6)
    if '"products"' in raw:
        try:
            for p in json.loads(raw).get("products", []):
                cls = classify_universal(p.get("title", ""))
                if not cls: continue
                comm, eff_g, d_kg = cls
                vars_ = p.get("variants") or []
                if not vars_: continue
                price = float(vars_[0].get("price") or 0)
                grams = float(vars_[0].get("grams") or 0)
                kg = (grams / 1000.0) if (80 <= grams <= 10000) else d_kg
                if price <= 0: continue
                nom_kg = price / kg
                usd_kg = nom_kg * fx
                usd_50g = (usd_kg / eff_g) * 50.0
                if 0.35 <= usd_kg <= 48.0 and 0.04 <= usd_50g <= 5.50:
                    found.append((mid, brand[:60], iso3, lat, lon, comm, round(nom_kg, 3), cur, round(usd_kg, 3), round(usd_50g, 4)))
            if found:
                return brand, "SHOPIFY", found
        except Exception:
            pass

    raw = curl_get(f"{base}/wp-json/wc/store/products?per_page=40", timeout=6)
    if raw.startswith("[") and '"prices"' in raw:
        try:
            for p in json.loads(raw):
                cls = classify_universal(p.get("name", ""))
                if not cls: continue
                comm, eff_g, d_kg = cls
                prices = p.get("prices") or {}
                minor = int(prices.get("currency_minor_unit", 2))
                raw_p = float(prices.get("price") or 0) / (10 ** minor)
                if raw_p <= 0: continue
                nom_kg = raw_p / d_kg
                usd_kg = nom_kg * fx
                usd_50g = (usd_kg / eff_g) * 50.0
                if 0.35 <= usd_kg <= 48.0 and 0.04 <= usd_50g <= 5.50:
                    found.append((mid, brand[:60], iso3, lat, lon, comm, round(nom_kg, 3), cur, round(usd_kg, 3), round(usd_50g, 4)))
            if found:
                return brand, "WOOCOMMERCE", found
        except Exception:
            pass

    return brand, None, []
target_list = list(domains.items())[:650]
print(f"[TRACK 47C] Fingerprinting {len(target_list)} domains across VTEX, Shopify & WooCommerce (32 workers)...")

disc_rows = []
active_engines = 0
with ThreadPoolExecutor(max_workers=32) as ex:
    futs = {ex.submit(probe_domain, i, b, m): b for i, (b, m) in enumerate(target_list)}
    for fut in as_completed(futs):
        brand, eng, items = fut.result()
        if items:
            active_engines += 1
            print(f" -> [DISCOVERED {eng:11s}] {brand:32s}: {len(items)} live SKUs")
            disc_rows.extend(items)

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'AUTO_DISCOVERY_47';")

wages = dict(con.execute("""
    SELECT m.country_iso3, MEDIAN(f.usd_per_50g_diaas / NULLIF(f.labor_min_50g_diaas / 60.0, 0))
    FROM fact_protein_observations f JOIN dim_markets m USING(market_id)
    WHERE f.labor_min_50g_diaas > 0 GROUP BY 1;
""").fetchall())

max_id = con.execute("SELECT COALESCE(MAX(obs_id), 0) FROM fact_protein_observations;").fetchone()[0]
today_str = time.strftime("%Y-%m-%d")
ins_rows = []

for mid, mname, iso3, lat, lon, comm, nom_kg, cur, usd_kg, usd_50g in disc_rows:
    con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?, ?, ?, 'AUTO_DISCOVERY_47', ?, ?, 0.08, 0.08);",
                [mid, mname, iso3, round(lat, 5), round(lon, 5)])
    w_hr = wages.get(iso3) or 11.0
    max_id += 1
    ins_rows.append((
        max_id, "AUTO_DISCOVERY_47", "OBSERVED_RETAIL", mid, comm, today_str,
        nom_kg, cur, usd_kg, usd_50g, round((usd_50g / w_hr) * 60.0, 2), False
    ))

if ins_rows:
    con.executemany("""INSERT INTO fact_protein_observations (
        obs_id, source_dataset, price_basis, market_id, commodity_code, obs_date,
        nominal_price_kg, currency_code, real_usd_per_kg, usd_per_50g_diaas, labor_min_50g_diaas, is_outlier_mad
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""", ins_rows)

con.execute("DELETE FROM dim_markets WHERE market_id NOT IN (SELECT DISTINCT market_id FROM fact_protein_observations);")
tot_r, tot_m = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id) FROM fact_protein_observations;").fetchone()
tot_c = con.execute("SELECT COUNT(DISTINCT country_iso3) FROM dim_markets;").fetchone()[0]
print(f"\n[TRACK 47 COMPLETE] Discovered +{len(ins_rows):,} SKUs across {active_engines} auto-fingerprinted chains! Total DB: {tot_r:,} rows | {tot_m:,} markets | {tot_c} countries ({time.time()-t0:.1f}s)")
con.close()
