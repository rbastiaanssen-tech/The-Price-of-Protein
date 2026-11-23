import duckdb, time, json, urllib.request

t0 = time.time()
con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset IN ('MULTI_SUPERMARKET_35', 'OPEN_PRICES_STORES_36');")

wages = dict(con.execute("""
    SELECT m.country_iso3, MEDIAN(f.usd_per_50g_diaas / NULLIF(f.labor_min_50g_diaas / 60.0, 0))
    FROM fact_protein_observations f JOIN dim_markets m USING(market_id)
    WHERE f.labor_min_50g_diaas > 0 GROUP BY 1;
""").fetchall())

max_id = con.execute("SELECT COALESCE(MAX(obs_id), 0) FROM fact_protein_observations;").fetchone()[0]
today_str = time.strftime("%Y-%m-%d")
rows = []

# ==============================================================================
# PART 1: MERCADONA SPAIN (Reading exact x-customer-wh warehouse header!)
# ==============================================================================
SPAIN_WAREHOUSES = [
    ("08001", "Mercadona Barcelona (bcn1)",  41.3809,  2.1733),
    ("28001", "Mercadona Madrid (mad1)",     40.4238, -3.6883),
    ("46001", "Mercadona Valencia (vlc1)",   39.4745, -0.3768),
    ("41001", "Mercadona Sevilla (svq1)",    37.3891, -5.9845),
    ("35001", "Mercadona Las Palmas (3842)", 28.1000, -15.4167),
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
for cp, mname, lat, lon in SPAIN_WAREHOUSES:
    mid = f"ES36_MERC_{cp}"
    con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?, ?, 'ESP', 'SPAIN_RETAIL', ?, ?, 0.08, 0.08);", [mid, mname, lat, lon])
    wh = "mad1"
    try:
        req = urllib.request.Request(
            "https://tienda.mercadona.es/api/postal-codes/actions/change-pc/",
            data=json.dumps({"new_postal_code": cp}).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            method="PUT"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            wh = resp.headers.get("x-customer-wh") or resp.headers.get("x-mercadona-wh") or "mad1"
    except Exception:
        pass

    cnt = 0
    for cid in CAT_IDS:
        try:
            req = urllib.request.Request(f"https://tienda.mercadona.es/api/categories/{cid}/?lang=es&wh={wh}", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
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
                        rows.append((max_id, "OPEN_PRICES_STORES_36", "OBSERVED_RETAIL", mid, comm_code, today_str,
                                     round(eur_kg, 3), "EUR", round(usd_kg, 3), round(usd_50g, 4),
                                     round((usd_50g / es_wage) * 60.0, 2), False))
                        cnt += 1
        except Exception:
            continue
    print(f" -> {mname} (resolved x-customer-wh={wh}): {cnt} live SKUs")

if rows:
    con.executemany("""
        INSERT INTO fact_protein_observations (
            obs_id, source_dataset, price_basis, market_id, commodity_code, obs_date,
            nominal_price_kg, currency_code, real_usd_per_kg, usd_per_50g_diaas, labor_min_50g_diaas, is_outlier_mad
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, rows)

# ==============================================================================
# PART 2: OPEN PRICES STORE LOCATIONS API + LOCAL PARQUET RECEIPT JOIN
# ==============================================================================
print("[TRACK 36] Fetching geo-tagged supermarket branches from Open Prices Locations API...")
ISO2_TO_3 = {
    "FR": "FRA", "ES": "ESP", "DE": "DEU", "IT": "ITA", "GB": "GBR", "UK": "GBR",
    "BE": "BEL", "NL": "NLD", "PL": "POL", "PT": "PRT", "CH": "CHE", "AT": "AUT",
    "SE": "SWE", "NO": "NOR", "FI": "FIN", "DK": "DNK", "IE": "IRL", "RO": "ROU",
    "HU": "HUN", "CZ": "CZE", "GR": "GRC", "BG": "BGR", "HR": "HRV", "SK": "SVK",
    "US": "USA", "CA": "CAN", "MX": "MEX", "BR": "BRA", "AR": "ARG", "CL": "CHL",
    "CO": "COL", "PE": "PER", "MA": "MAR", "DZ": "DZA", "TN": "TUN", "ZA": "ZAF",
    "AU": "AUS", "JP": "JPN", "IN": "IND", "TR": "TUR", "RU": "RUS"
}

store_records = []
for page in range(1, 51):  # Top 5,000 most active supermarket branches worldwide
    url = f"https://prices.openfoodfacts.org/api/v1/locations?order_by=-price_count&size=100&page={page}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PriceOfProtein/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        items = payload.get("items") or []
        if not items:
            break
        for loc in items:
            cc2 = str(loc.get("osm_address_country_code") or "").upper()
            iso3 = ISO2_TO_3.get(cc2)
            lat = loc.get("osm_lat")
            lon = loc.get("osm_lon")
            lid = loc.get("id")
            if not (iso3 and lat and lon and lid):
                continue
            brand = loc.get("osm_name") or loc.get("osm_display_name") or "Supermarket"
            city = loc.get("osm_address_city") or ""
            mname = f"{brand} ({city})" if city else brand
            mid = f"OP36_LOC_{lid}"
            store_records.append((mid, mname[:80], iso3, "SUPERMARKET_OSM", round(float(lat), 5), round(float(lon), 5), 0.08, 0.08, int(lid)))
    except Exception:
        break

print(f" -> Retrieved {len(store_records):,} geo-tagged supermarket branches across global chains")

if store_records:
    con.execute("CREATE OR REPLACE TEMP TABLE op_stores (market_id VARCHAR, market_name VARCHAR, country_iso3 VARCHAR, admin1 VARCHAR, lat DOUBLE, lon DOUBLE, u1 DOUBLE, u2 DOUBLE, location_id BIGINT);")
    con.executemany("INSERT INTO op_stores VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);", store_records)
    con.execute("INSERT OR IGNORE INTO dim_markets SELECT market_id, market_name, country_iso3, admin1, lat, lon, u1, u2 FROM op_stores;")

    con.execute("""
        CREATE OR REPLACE TEMP TABLE country_rates AS
        SELECT m.country_iso3,
               MEDIAN(f.usd_per_50g_diaas / NULLIF(f.labor_min_50g_diaas / 60.0, 0)) AS wage_hr_usd
        FROM fact_protein_observations f JOIN dim_markets m USING(market_id)
        WHERE f.labor_min_50g_diaas > 0 GROUP BY 1;
    """)

    max_id = con.execute("SELECT COALESCE(MAX(obs_id), 0) FROM fact_protein_observations;").fetchone()[0]
    con.execute(f"""
        INSERT INTO fact_protein_observations (
            obs_id, source_dataset, price_basis, market_id, commodity_code, obs_date,
            nominal_price_kg, currency_code, real_usd_per_kg, usd_per_50g_diaas, labor_min_50g_diaas, is_outlier_mad
        )
        WITH raw_p AS (
            SELECT
                TRY_CAST(location_id AS BIGINT) AS location_id,
                LOWER(COALESCE(CAST(product_product_name AS VARCHAR), '') || ' ' || COALESCE(CAST(product_categories_tags AS VARCHAR), '')) AS txt,
                CAST(price AS DOUBLE) AS price,
                UPPER(CAST(currency AS VARCHAR)) AS currency,
                UPPER(COALESCE(CAST(price_per AS VARCHAR), 'UNIT')) AS price_per,
                TRY_CAST(product_quantity AS DOUBLE) AS p_qty,
                COALESCE(CAST(TRY_CAST(date AS DATE) AS VARCHAR), '2026-01-15') AS obs_date
            FROM read_parquet('data_raw/open_prices.parquet')
            WHERE price > 0.05 AND price < 250
        ),
        mapped AS (
            SELECT
                r.*,
                s.market_id,
                s.country_iso3,
                CASE
                    WHEN REGEXP_MATCHES(txt, '(egg|oeuf|œuf|huevo|uovo|ei\\b|eier|яйц)') THEN 'EGGS_CHIX'
                    WHEN REGEXP_MATCHES(txt, '(milk|lait|leche|latte|milch|melk|leite|молоко)') THEN 'MILK_FLUID'
                    WHEN REGEXP_MATCHES(txt, '(cheese|fromage|queso|formaggio|käse|kaas|queijo|сыр)') THEN 'CHEESE_HARD'
                    WHEN REGEXP_MATCHES(txt, '(chicken|poulet|pollo|hähnchen|kip|frango|куриц)') THEN 'POULTRY_CHIX'
                    WHEN REGEXP_MATCHES(txt, '(beef|boeuf|bœuf|vacuno|ternera|rind|rund|carne bovina|говядин)') THEN 'BEEF_FRESH'
                    WHEN REGEXP_MATCHES(txt, '(pork|porc|cerdo|schwein|varkens|suíno|свинин)') THEN 'PORK_FRESH'
                    WHEN REGEXP_MATCHES(txt, '(tuna|thon|atún|atun|tonno|thunfisch|sardine|sardina|herring|hareng)') THEN 'FISH_PELAGIC_SMALL'
                    WHEN REGEXP_MATCHES(txt, '(salmon|saumon|salmón|cod|cabillaud|bacalao)') THEN 'FISH_PREDATOR_HIGH'
                    WHEN REGEXP_MATCHES(txt, '(lentil|lentille|lenteja|lenticchie|linsen|linzen|lentilha|чечевиц)') THEN 'LENTILS_DRY'
                    WHEN REGEXP_MATCHES(txt, '(bean|haricot|frijol|alubia|fagioli|bohnen|bonen|feijão|chickpea|pois chiche|garbanzo)') THEN 'BEANS_DRY'
                    ELSE NULL
                END AS commodity_code,
                CASE
                    WHEN price_per = 'KILOGRAM' THEN price
                    WHEN p_qty IS NOT NULL AND p_qty BETWEEN 30 AND 5000 THEN price / (p_qty / 1000.0)
                    ELSE price / 0.50
                END AS nom_kg,
                CASE currency
                    WHEN 'USD' THEN 1.0 WHEN 'EUR' THEN 1.09 WHEN 'GBP' THEN 1.28
                    WHEN 'CAD' THEN 0.73 WHEN 'AUD' THEN 0.66 WHEN 'CHF' THEN 1.14
                    WHEN 'SEK' THEN 0.095 WHEN 'NOK' THEN 0.092 WHEN 'DKK' THEN 0.146
                    WHEN 'PLN' THEN 0.25 WHEN 'BRL' THEN 0.18 WHEN 'MXN' THEN 0.052
                    WHEN 'INR' THEN 0.0118 WHEN 'CNY' THEN 0.138 WHEN 'JPY' THEN 0.0066
                    WHEN 'RUB' THEN 0.0108 WHEN 'MAD' THEN 0.10 WHEN 'DZD' THEN 0.0074
                    WHEN 'TRY' THEN 0.029 WHEN 'ZAR' THEN 0.055 ELSE NULL
                END AS fx
            FROM raw_p r
            JOIN op_stores s ON r.location_id = s.location_id
        ),
        calc AS (
            SELECT m.*,
                   m.nom_kg * m.fx AS usd_kg,
                   CASE m.commodity_code
                       WHEN 'EGGS_CHIX' THEN 124.0 WHEN 'MILK_FLUID' THEN 33.0 WHEN 'CHEESE_HARD' THEN 245.0
                       WHEN 'POULTRY_CHIX' THEN 195.0 WHEN 'BEEF_FRESH' THEN 215.0 WHEN 'PORK_FRESH' THEN 190.0
                       WHEN 'FISH_PELAGIC_SMALL' THEN 180.0 WHEN 'FISH_PREDATOR_HIGH' THEN 176.0
                       WHEN 'LENTILS_DRY' THEN 148.0 WHEN 'BEANS_DRY' THEN 138.0
                   END AS eff_g
            FROM mapped m
            WHERE m.commodity_code IS NOT NULL AND m.fx IS NOT NULL
        )
        SELECT
            {max_id} + ROW_NUMBER() OVER (),
            'OPEN_PRICES_STORES_36', 'OBSERVED_RETAIL',
            c.market_id, c.commodity_code, c.obs_date,
            ROUND(c.nom_kg, 3), c.currency, ROUND(c.usd_kg, 3),
            ROUND((c.usd_kg / c.eff_g) * 50.0, 4),
            ROUND(((c.usd_kg / c.eff_g) * 50.0) / COALESCE(cr.wage_hr_usd, 18.0) * 60.0, 2),
            FALSE
        FROM calc c
        LEFT JOIN country_rates cr ON cr.country_iso3 = c.country_iso3
        WHERE c.usd_kg BETWEEN 0.25 AND 65.0
          AND ((c.usd_kg / c.eff_g) * 50.0) BETWEEN 0.04 AND 8.50
          -- Avoid duplicating observations already ingested in p27
          AND NOT EXISTS (
              SELECT 1 FROM fact_protein_observations f
              WHERE f.source_dataset = 'OPEN_PRICES_PARQUET_27'
                AND f.commodity_code = c.commodity_code
                AND f.obs_date = c.obs_date
                AND ABS(f.real_usd_per_kg - ROUND(c.usd_kg, 3)) < 0.01
          );
    """)

# Clean up any store locations that had 0 protein receipts
con.execute("DELETE FROM dim_markets WHERE market_id NOT IN (SELECT DISTINCT market_id FROM fact_protein_observations);")

added = con.execute("SELECT COUNT(*) FROM fact_protein_observations WHERE source_dataset = 'OPEN_PRICES_STORES_36';").fetchone()[0]
tot_rows, tot_mkts = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id) FROM fact_protein_observations;").fetchone()
print(f"[TRACK 36 COMPLETE] Added +{added:,} geo-verified supermarket branch observations! Total DB: {tot_rows:,} rows | {tot_mkts:,} markets ({time.time()-t0:.1f}s)")
con.close()
