import json, time, subprocess, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
import duckdb

t0 = time.time()
domains = {}

def curl_get(url, post_data=None, timeout=25):
    cmd = ["curl", "-sL", "--max-time", str(timeout), "-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", "-H", "Accept: application/json"]
    if post_data:
        cmd += ["-X", "POST", "--data-urlencode", f"data={post_data}"]
    cmd.append(url)
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.stdout if res.returncode == 0 else ""

def add_domain(raw_url, brand):
    if not raw_url or not raw_url.startswith("http"):
        return
    parsed = urllib.parse.urlparse(raw_url.strip())
    host = parsed.netloc.lower().strip()
    if not host or any(s in host for s in ["facebook.", "instagram.", "google.", "wikipedia.", "twitter.", "x.com", "youtube.", "linkedin.", "wikidata."]):
        return
    base = f"https://{host}"
    if base not in domains:
        tld = host.split(".")[-1].upper()
        domains[base] = (brand or host, tld)

# 1. WIKIDATA SPARQL: All global supermarket & grocery chains with official websites (P856)
print("[TRACK 45A] Querying Wikidata SPARQL for global supermarket chains...")
sparql = """
SELECT DISTINCT ?label ?site WHERE {
  VALUES ?type { wd:Q180846 wd:Q507619 wd:Q1320047 }
  ?item wdt:P31/wdt:P279* ?type ;
        wdt:P856 ?site .
  OPTIONAL { ?item rdfs:label ?label FILTER(LANG(?label) = "en") }
} LIMIT 1200
"""
wd_raw = curl_get(f"https://query.wikidata.org/sparql?format=json&query={urllib.parse.quote(sparql)}", timeout=30)
if wd_raw:
    try:
        for b in json.loads(wd_raw).get("results", {}).get("bindings", []):
            add_domain(b.get("site", {}).get("value", ""), b.get("label", {}).get("value", ""))
    except Exception:
        pass
print(f" -> Wikidata yielded {len(domains)} unique supermarket chains")

# 2. OVERPASS MIRRORS: Local & regional supermarkets tagged in OpenStreetMap
print("[TRACK 45B] Querying OpenStreetMap Overpass mirrors for regional supermarket domains...")
overpass_q = """
[out:json][timeout:35];
(
  node["shop"="supermarket"]["website"]["brand:wikidata"];
  way["shop"="supermarket"]["website"]["brand:wikidata"];
  node["shop"="supermarket"]["website"]["name"];
);
out tags 3500;
"""
for mirror in ["https://overpass.private.coffee/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]:
    ov_raw = curl_get(mirror, post_data=overpass_q, timeout=40)
    if ov_raw and '"elements"' in ov_raw:
        try:
            for el in json.loads(ov_raw).get("elements", []):
                tags = el.get("tags", {})
                add_domain(tags.get("website") or tags.get("contact:website") or "", tags.get("brand") or tags.get("name") or "")
            break
        except Exception:
            continue

print(f" -> Total unique supermarket domains queued for fingerprinting: {len(domains):,}")

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
    mid = f"DISC45_{iso3}_{idx:03d}"
    found = []

    # 1. VTEX Legacy Catalog API
    raw = curl_get(f"{base}/api/catalog_system/pub/products/search?_from=0&_to=39", timeout=6)
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

    # 2. Shopify /products.json
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

    # 3. WooCommerce Store API
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

disc_rows = []
active_engines = 0
target_list = list(domains.items())[:600]

with ThreadPoolExecutor(max_workers=24) as ex:
    futs = {ex.submit(probe_domain, i, b, m): b for i, (b, m) in enumerate(target_list)}
    for fut in as_completed(futs):
        brand, eng, items = fut.result()
        if items:
            active_engines += 1
            print(f" -> [DISCOVERED {eng:11s}] {brand:32s}: {len(items)} live SKUs")
            disc_rows.extend(items)

st_con = duckdb.connect("staging_50_chains.duckdb")
st_con.execute("""
    CREATE TABLE IF NOT EXISTS staged_skus (
        market_id VARCHAR, market_name VARCHAR, country_iso3 VARCHAR, lat DOUBLE, lon DOUBLE,
        commodity_code VARCHAR, nominal_price_kg DOUBLE, currency_code VARCHAR,
        real_usd_per_kg DOUBLE, usd_per_50g_diaas DOUBLE
    );
""")
st_con.execute("DELETE FROM staged_skus WHERE market_id LIKE 'DISC45_%';")
if disc_rows:
    st_con.executemany("INSERT INTO staged_skus VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);", disc_rows)

tot_staged, tot_chains = st_con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id) FROM staged_skus;").fetchone()
st_con.close()

print(f"\n[TRACK 45 COMPLETE] Discovered +{len(disc_rows):,} SKUs across {active_engines} auto-fingerprinted sites ({time.time()-t0:.1f}s)!")
print(f"Combined staging_50_chains.duckdb now holds {tot_staged:,} live SKUs across {tot_chains} distinct supermarket chains!")
