import json, time, subprocess, urllib.parse
from playwright.sync_api import sync_playwright
import duckdb

t0 = time.time()

FX_MAP = {
    "EUR": 1.09, "USD": 1.00, "GBP": 1.27, "CHF": 1.14, "CAD": 0.72, "AUD": 0.65,
    "PLN": 0.25, "CZK": 0.043, "SEK": 0.095, "NOK": 0.092, "DKK": 0.146,
    "BRL": 0.18, "MXN": 0.052, "ZAR": 0.055, "JPY": 0.0066, "INR": 0.0118,
    "SGD": 0.75, "HKD": 0.128, "TWD": 0.031, "MYR": 0.225, "THB": 0.029
}

ISO2_TO_3 = {
    "FR": "FRA", "DE": "DEU", "ES": "ESP", "IT": "ITA", "GB": "GBR", "UK": "GBR",
    "US": "USA", "CA": "CAN", "BE": "BEL", "NL": "NLD", "CH": "CHE", "AT": "AUT",
    "PL": "POL", "CZ": "CZE", "SE": "SWE", "NO": "NOR", "DK": "DNK", "PT": "PRT",
    "BR": "BRA", "MX": "MEX", "ZA": "ZAF", "JP": "JPN", "IN": "IND", "SG": "SGP",
    "HK": "HKG", "TW": "TWN", "MY": "MYS", "TH": "THA", "AU": "AUS", "NZ": "NZL",
    "CN": "CHN", "KR": "KOR", "ID": "IDN", "VN": "VNM", "PH": "PHL", "IE": "IRL"
}

SEARCH_SPECS = [
    ("tofu",    "TOFU_TEMPEH",  117.0, 0.35, 0.60, 28.0),
    ("tempeh",  "TOFU_TEMPEH",  117.0, 0.25, 0.60, 28.0),
    ("edamame", "SOYBEANS_DRY", 328.5, 0.40, 0.30, 12.0),
    ("soybean", "SOYBEANS_DRY", 328.5, 0.50, 0.30,  5.5),
    ("rice",    "RICE_MILLED",   41.9, 1.00, 0.20,  8.0),
    ("flour",   "WHEAT_FLOUR",   46.4, 1.00, 0.15,  7.0),
]

all_rows = []
print("[TRACK 56A] Harvesting retail Tofu, Tempeh, Soy, Rice & Flour via Open Prices REST API...")

for term, comm, eff_g, d_kg, min_kg, max_kg in SEARCH_SPECS:
    term_cnt = 0
    for page in range(1, 9):
        url = f"https://prices.openfoodfacts.org/api/v1/prices?product_name__icontains={urllib.parse.quote(term)}&size=100&page={page}"
        res = subprocess.run(["curl", "-sL", "--max-time", "15", url], capture_output=True, text=True)
        if res.returncode != 0 or '"items"' not in res.stdout:
            break
        items = json.loads(res.stdout).get("items", [])
        if not items:
            break
        for it in items:
            loc = it.get("location") or {}
            prod = it.get("product") or {}
            iso2 = (loc.get("osm_address_country_code") or "").upper()
            iso3 = ISO2_TO_3.get(iso2)
            lat, lon = loc.get("osm_lat"), loc.get("osm_lon")
            price = it.get("price")
            cur = (it.get("currency") or "EUR").upper()
            if not (iso3 and lat is not None and lon is not None and price and float(price) > 0):
                continue
            if (it.get("price_per") or "UNIT") == "KILOGRAM":
                kg = 1.0
            else:
                q_g = float(prod.get("product_quantity") or 0)
                kg = (q_g / 1000.0) if (80 <= q_g <= 10000) else d_kg
            nom_kg = float(price) / kg
            usd_kg = nom_kg * FX_MAP.get(cur, 1.0)
            if not (min_kg <= usd_kg <= max_kg):
                continue
            usd_50g = (usd_kg / eff_g) * 50.0
            mid = f"OP56_{iso3}_{loc.get('id') or 0}"
            sname = (loc.get("osm_name") or loc.get("osm_brand") or "Supermarket")[:70]
            obs_d = (it.get("date") or "2026-11-01")[:10]
            all_rows.append((mid, sname, iso3, round(float(lat), 5), round(float(lon), 5),
                             comm, obs_d, round(nom_kg, 3), cur, round(usd_kg, 3), round(usd_50g, 4)))
            term_cnt += 1
    print(f" -> Open Prices '{term:8s}' ({comm:12s}): {term_cnt:4d} clean retail SKUs")

print("[TRACK 56B] Harvesting South Africa retail Tofu, Rice & Flour via Pick n Pay Playwright...")
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")
    ctx.route("**/*", lambda r: r.abort() if r.request.resource_type in ["image", "media", "font"] else r.continue_())
    page = ctx.new_page()
    pnp_cnt = 0
    for term, comm, eff_g, d_kg, min_kg, max_kg in [("tofu", "TOFU_TEMPEH", 117.0, 0.35, 0.60, 28.0),
                                                    ("rice", "RICE_MILLED", 41.9, 2.00, 0.20, 8.0),
                                                    ("cake flour", "WHEAT_FLOUR", 46.4, 2.50, 0.15, 7.0)]:
        captured = []
        def on_resp(resp):
            try:
                if "json" in resp.headers.get("content-type", "") and resp.status == 200:
                    data = resp.json()
                    for pr in (data.get("products") or []):
                        nm = pr.get("name")
                        pv = (pr.get("price") or {}).get("value")
                        if nm and pv:
                            captured.append((nm, float(pv)))
            except Exception:
                pass
        page.on("response", on_resp)
        try:
            page.goto(f"https://www.pnp.co.za/search/{urllib.parse.quote(term)}", wait_until="domcontentloaded", timeout=14000)
            page.wait_for_timeout(2200)
        except Exception:
            pass
        page.remove_listener("response", on_resp)
        for nm, pv in captured:
            nom_kg = pv / d_kg
            usd_kg = nom_kg * 0.055
            if min_kg <= usd_kg <= max_kg:
                usd_50g = (usd_kg / eff_g) * 50.0
                all_rows.append(("PW_ZAF_PNP", "Pick n Pay South Africa (Johannesburg)", "ZAF", -26.2041, 28.0473,
                                 comm, time.strftime("%Y-%m-%d"), round(nom_kg, 3), "ZAR", round(usd_kg, 3), round(usd_50g, 4)))
                pnp_cnt += 1
    browser.close()
    print(f" -> Pick n Pay ZAF (tofu/rice/flour): {pnp_cnt:4d} clean retail SKUs")
con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'RETAIL_SOY_STAPLES_56';")

wages = dict(con.execute("SELECT country_iso3, hourly_wage_usd FROM dim_country_wages;").fetchall())
max_id = con.execute("SELECT COALESCE(MAX(obs_id), 0) FROM fact_protein_observations;").fetchone()[0]
ins_rows = []

for mid, sname, iso3, lat, lon, comm, obs_d, nom_kg, cur, usd_kg, usd_50g in all_rows:
    con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?, ?, ?, 'RETAIL_SOY_STAPLES_56', ?, ?, 0.08, 0.08);",
                [mid, sname, iso3, lat, lon])
    w_hr = wages.get(iso3) or 10.0
    max_id += 1
    ins_rows.append((
        max_id, "RETAIL_SOY_STAPLES_56", "OBSERVED_RETAIL", mid, comm, obs_d,
        nom_kg, cur, usd_kg, usd_50g, round((usd_50g / w_hr) * 60.0, 2), False
    ))

if ins_rows:
    con.executemany("""INSERT INTO fact_protein_observations (
        obs_id, source_dataset, price_basis, market_id, commodity_code, obs_date,
        nominal_price_kg, currency_code, real_usd_per_kg, usd_per_50g_diaas, labor_min_50g_diaas, is_outlier_mad
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""", ins_rows)

con.execute("DELETE FROM dim_markets WHERE market_id NOT IN (SELECT DISTINCT market_id FROM fact_protein_observations);")

print("\n[TRACK 56C] Rebuilding temporally aligned marts (2023-2026 Cross-Section vs 1991-2026 Longitudinal)...")

# 1. Modern Cross-Sectional Parity Mart (strictly 2023-2026, excluding 1-row TEA frontier benchmarks)
con.execute("""
CREATE OR REPLACE TABLE mart_country_protein_parity AS
WITH modern AS (
    SELECT
        country_iso3,
        price_basis,
        commodity_code,
        COUNT(*) AS n_obs,
        COUNT(DISTINCT market_id) AS n_markets,
        ROUND(MEDIAN(real_usd_per_kg), 3) AS med_usd_kg,
        ROUND(MEDIAN(usd_per_50g_diaas), 4) AS med_usd_50g_diaas,
        ROUND(QUANTILE_CONT(usd_per_50g_diaas, 0.25), 4) AS p25_usd_50g_diaas,
        ROUND(QUANTILE_CONT(usd_per_50g_diaas, 0.75), 4) AS p75_usd_50g_diaas,
        ROUND(MEDIAN(labor_min_50g_diaas), 2) AS med_labor_min_50g
    FROM v_clean_protein_observations
    WHERE CAST(obs_date AS VARCHAR) >= '2023-01-01'
      AND price_basis IN ('OBSERVED_RETAIL', 'OBSERVED_MARKET', 'FARM_GATE_SHADOW')
      AND commodity_code NOT IN ('MYCOPROTEIN', 'CULT_CHICKEN', 'SINGLE_CELL_GAS', 'PF_CASEIN', 'INSECT_MEAL')
    GROUP BY 1, 2, 3
)
SELECT
    *,
    ROW_NUMBER() OVER (PARTITION BY country_iso3, price_basis ORDER BY med_usd_50g_diaas ASC) AS affordability_rank
FROM modern
WHERE n_obs >= 5;
""")

# 2. Longitudinal Historical Trends Mart (1991-2026 annual series)
con.execute("""
CREATE OR REPLACE TABLE mart_historical_protein_trends AS
SELECT
    CAST(SUBSTR(CAST(obs_date AS VARCHAR), 1, 4) AS INTEGER) AS obs_year,
    country_iso3,
    price_basis,
    commodity_code,
    COUNT(*) AS n_obs,
    ROUND(MEDIAN(real_usd_per_kg), 3) AS med_usd_kg,
    ROUND(MEDIAN(usd_per_50g_diaas), 4) AS med_usd_50g_diaas,
    ROUND(MEDIAN(labor_min_50g_diaas), 2) AS med_labor_min_50g
FROM v_clean_protein_observations
WHERE CAST(SUBSTR(CAST(obs_date AS VARCHAR), 1, 4) AS INTEGER) BETWEEN 1991 AND 2026
  AND price_basis IN ('OBSERVED_MARKET', 'FARM_GATE_SHADOW', 'OBSERVED_RETAIL')
GROUP BY 1, 2, 3, 4
HAVING COUNT(*) >= 3;
""")

# 3. Dedicated Frontier & Isolate Benchmark Table
con.execute("""
CREATE OR REPLACE TABLE mart_frontier_protein_benchmarks AS
SELECT
    commodity_code,
    price_basis,
    COUNT(*) AS n_obs,
    COUNT(DISTINCT country_iso3) AS n_countries,
    ROUND(MEDIAN(real_usd_per_kg), 2) AS med_usd_kg,
    ROUND(MEDIAN(usd_per_50g_diaas), 3) AS med_usd_50g_diaas
FROM v_clean_protein_observations
WHERE commodity_code IN ('WHEY_ISOLATE', 'PEA_ISOLATE', 'SOY_ISOLATE', 'TVP', 'PF_WHEY_BLG',
                         'MYCOPROTEIN', 'CULT_CHICKEN', 'SINGLE_CELL_GAS', 'PF_CASEIN', 'INSECT_MEAL')
GROUP BY 1, 2
ORDER BY med_usd_50g_diaas ASC;
""")

print(f"\n[TRACK 56 COMPLETE] Added +{len(ins_rows):,} retail Tofu/Soy/Rice/Flour SKUs in {time.time()-t0:.1f}s")
for comm, basis, cnt, ctry in con.execute("""
    SELECT commodity_code, price_basis, COUNT(*), COUNT(DISTINCT country_iso3)
    FROM v_clean_protein_observations
    WHERE commodity_code IN ('TOFU_TEMPEH', 'SOYBEANS_DRY', 'RICE_MILLED', 'WHEAT_FLOUR')
      AND price_basis = 'OBSERVED_RETAIL'
    GROUP BY 1, 2 ORDER BY 3 DESC;
""").fetchall():
    print(f" -> {comm:15s} | {basis:15s} | {cnt:6,d} retail rows across {ctry:2d} countries")

con.close()
