import os, json, time, base64, gzip, urllib.request, urllib.parse
import duckdb

t0 = time.time()
CID = os.getenv("KROGER_CLIENT_ID", "proteindatabase-bbckhv2n")
CSEC = os.getenv("KROGER_CLIENT_SECRET", "kFudrMLVVRkXbOdze60ms3hgpoF_dgLqHRTDD7sA")

def get_kroger_host_and_token(cid, csec):
    auth = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    for host in ["https://api.kroger.com", "https://api-ce.kroger.com"]:
        try:
            req = urllib.request.Request(
                f"{host}/v1/connect/oauth2/token",
                data=b"grant_type=client_credentials&scope=product.compact",
                headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded", "Accept-Encoding": "identity"}
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                tok = json.loads(raw.decode())["access_token"]
                return host, tok
        except Exception:
            continue
    raise RuntimeError("Could not authenticate with either api.kroger.com or api-ce.kroger.com")

HOST, token = get_kroger_host_and_token(CID, CSEC)
env_tag = "PROD" if "api.kroger.com" in HOST else "CERT"
print(f"[TRACK 41] Connected to Kroger {env_tag} ({HOST})! Querying US zip codes...")

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'KROGER_API_41';")

US_ZIPS = [
    "90012","90210","92101","94103","95814","93721","97204","97401","98101","99201","99501",
    "80202","80903","81501","84101","84601","89101","89501","85004","85701","87102","82001","59101","83702",
    "77002","75201","78701","78205","79901","66101","67202","68102",
    "60601","61820","48201","49503","43215","45202","44113","46204","47401","53202","53703","63101","64106",
    "30303","31401","37203","38103","37902","40202","40507","28202","27601","29401","23219","22201","20001","21201","25301","35203","39201","70112","72201"
]
QUERIES = [
    ("large eggs 12 ct", "EGGS_CHIX", 124.0, 0.680),
    ("whole milk 1 gallon", "MILK_FLUID", 33.0, 3.785),
    ("sharp cheddar cheese", "CHEESE_HARD", 245.0, 0.454),
    ("boneless chicken breast", "POULTRY_CHIX", 195.0, 0.454),
    ("ground beef 80 20", "BEEF_FRESH", 215.0, 0.454),
    ("pork tenderloin", "PORK_FRESH", 190.0, 0.454),
    ("chunk light tuna can", "FISH_PELAGIC_SMALL", 180.0, 0.142),
    ("atlantic salmon fillet", "FISH_PREDATOR_HIGH", 176.0, 0.454),
    ("pinto beans dry", "BEANS_DRY", 138.0, 0.454),
    ("brown lentils dry", "LENTILS_DRY", 148.0, 0.454)
]

us_wage = con.execute("""
    SELECT MEDIAN(f.usd_per_50g_diaas / NULLIF(f.labor_min_50g_diaas / 60.0, 0))
    FROM fact_protein_observations f JOIN dim_markets m USING(market_id)
    WHERE m.country_iso3 = 'USA' AND f.labor_min_50g_diaas > 0;
""").fetchone()[0] or 28.50

max_id = con.execute("SELECT COALESCE(MAX(obs_id), 0) FROM fact_protein_observations;").fetchone()[0]
today_str = time.strftime("%Y-%m-%d")
rows, seen_locs = [], set()

def parse_kg(sz, d_kg):
    s = str(sz or "").lower().strip()
    try:
        if "gal" in s:
            return float("".join(c for c in s.split("gal")[0] if c.isdigit() or c == ".") or 1) * 3.7854
        if "fl oz" in s:
            return float("".join(c for c in s.split("fl oz")[0] if c.isdigit() or c == ".") or 16) * 0.02957
        if "oz" in s:
            if "/" in s:
                p = s.split("/")
                ct = float("".join(c for c in p[0] if c.isdigit() or c == ".") or 1)
                oz = float("".join(c for c in p[1].split("oz")[0] if c.isdigit() or c == ".") or 5)
                return ct * oz * 0.02835
            return float("".join(c for c in s.split("oz")[0] if c.isdigit() or c == ".") or 16) * 0.02835
        if "lb" in s:
            return float("".join(c for c in s.split("lb")[0] if c.isdigit() or c == ".") or 1) * 0.45359
    except Exception:
        pass
    return d_kg

def fetch_json(url):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))

for z in US_ZIPS:
    try:
        locs = fetch_json(f"{HOST}/v1/locations?filter.zipCode.near={z}&filter.radiusInMiles=25&filter.limit=2").get("data", [])
    except Exception:
        continue

    for loc in locs:
        lid = loc.get("locationId")
        if not lid or lid in seen_locs:
            continue
        seen_locs.add(lid)
        addr, geo = loc.get("address", {}), loc.get("geolocation", {})
        slat, slon = float(geo.get("latitude", 0)), float(geo.get("longitude", 0))
        if not (slat or slon):
            continue
        sname = f"{loc.get('chain','KROGER')} - {addr.get('city','')}, {addr.get('state','')} ({addr.get('zipCode',z)})"
        mid = f"KR41_USA_{lid}"
        con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?, ?, 'USA', ?, ?, ?, 0.08, 0.08);",
                    [mid, sname[:80], f"USA_{addr.get('state','')}", round(slat, 5), round(slon, 5)])
        cnt = 0
        for term, comm, eff_g, d_kg in QUERIES:
            try:
                prods = fetch_json(f"{HOST}/v1/products?filter.term={urllib.parse.quote(term)}&filter.locationId={lid}&filter.limit=12").get("data", [])
                for p in prods:
                    items = p.get("items") or []
                    if not items:
                        continue
                    reg_p = (items[0].get("price") or {}).get("regular")
                    if not reg_p or float(reg_p) <= 0:
                        continue
                    kg = parse_kg(items[0].get("size"), d_kg)
                    if not (0.08 <= kg <= 12.0):
                        kg = d_kg
                    usd_kg = float(reg_p) / kg
                    usd_50g = (usd_kg / eff_g) * 50.0
                    if 0.45 <= usd_kg <= 48.0 and 0.04 <= usd_50g <= 5.50:
                        max_id += 1
                        rows.append((
                            max_id, "KROGER_API_41", "OBSERVED_RETAIL", mid, comm, today_str,
                            round(usd_kg, 3), "USD", round(usd_kg, 3), round(usd_50g, 4),
                            round((usd_50g / us_wage) * 60.0, 2), False
                        ))
                        cnt += 1
            except Exception:
                continue
        print(f" -> {sname}: {cnt} live SKUs")

if rows:
    con.executemany("""INSERT INTO fact_protein_observations (
        obs_id, source_dataset, price_basis, market_id, commodity_code, obs_date,
        nominal_price_kg, currency_code, real_usd_per_kg, usd_per_50g_diaas, labor_min_50g_diaas, is_outlier_mad
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""", rows)

con.execute("DELETE FROM dim_markets WHERE market_id NOT IN (SELECT DISTINCT market_id FROM fact_protein_observations);")
tot_r, tot_m = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id) FROM fact_protein_observations;").fetchone()
print(f"[TRACK 41 COMPLETE] Added +{len(rows):,} US zip-code supermarket SKUs across {len(seen_locs)} stores! Total DB: {tot_r:,} rows | {tot_m:,} markets ({time.time()-t0:.1f}s)")
con.close()
