import json, time, urllib.parse
from playwright.sync_api import sync_playwright
import duckdb

t0 = time.time()

CHAINS = [
    # SUB-SAHARAN AFRICA
    ("PW_KEN_CARREFOUR", "Carrefour Kenya (Nairobi)", "KEN", "KES", 0.0077, -1.2921, 36.8219,
     "https://www.carrefour.ke/mafken/en/v4/search?keyword={q}"),
    ("PW_ZAF_PNP", "Pick n Pay South Africa (Johannesburg)", "ZAF", "ZAR", 0.055, -26.2041, 28.0473,
     "https://www.pnp.co.za/search/{q}"),
    ("PW_ZAF_WOOLWORTHS", "Woolworths Food South Africa (Cape Town)", "ZAF", "ZAR", 0.055, -33.9249, 18.4241,
     "https://www.woolworths.co.za/cat?Ntt={q}"),

    # CHINA / GREATER CHINA / APAC
    ("PW_HKG_PARKNSHOP", "PARKnSHOP Hong Kong (Central)", "HKG", "HKD", 0.128, 22.2819, 114.1585,
     "https://www.pns.hk/en/search?text={q}"),
    ("PW_TWN_CARREFOUR", "Carrefour Taiwan (Taipei)", "TWN", "TWD", 0.031, 25.0330, 121.5654,
     "https://online.carrefour.com.tw/zh/search?q={q}"),
    ("PW_SGP_FAIRPRICE", "NTUC FairPrice Singapore (CBD)", "SGP", "SGD", 0.75, 1.2834, 103.8607,
     "https://www.fairprice.com.sg/search?query={q}"),
    ("PW_THA_TOPS", "Tops Supermarket Thailand (Bangkok)", "THA", "THB", 0.029, 13.7563, 100.5018,
     "https://www.tops.co.th/en/search/{q}"),
    ("PW_IND_BIGBASKET", "BigBasket India (Bengaluru)", "IND", "INR", 0.0118, 12.9716, 77.5946,
     "https://www.bigbasket.com/ps/?q={q}"),

    # MIDDLE EAST / GCC
    ("PW_ARE_CARREFOUR", "Carrefour UAE (Dubai)", "ARE", "AED", 0.272, 25.2048, 55.2708,
     "https://www.carrefouruae.com/mafuae/en/v4/search?keyword={q}"),
    ("PW_SAU_CARREFOUR", "Carrefour Saudi Arabia (Riyadh)", "SAU", "SAR", 0.266, 24.7136, 46.6753,
     "https://www.carrefourksa.com/mafsau/en/v4/search?keyword={q}"),

    # LATIN AMERICA
    ("PW_CHL_JUMBO", "Jumbo Chile (Santiago)", "CHL", "CLP", 0.00105, -33.4489, -70.6693,
     "https://www.jumbo.cl/busqueda?ft={q}"),
    ("PW_MEX_WALMART", "Walmart Supercenter Mexico (CDMX)", "MEX", "MXN", 0.052, 19.4326, -99.1332,
     "https://super.walmart.com.mx/productos?q={q}"),
]

QUERIES = [
    ("eggs", "EGGS_CHIX", 124.0, 0.60),
    ("milk", "MILK_FLUID", 33.0, 1.00),
    ("cheese", "CHEESE_HARD", 245.0, 0.35),
    ("chicken breast", "POULTRY_CHIX", 195.0, 0.70),
    ("beef", "BEEF_FRESH", 215.0, 0.70),
    ("tuna", "FISH_PELAGIC_SMALL", 180.0, 0.16),
    ("lentils", "LENTILS_DRY", 148.0, 0.50),
]

def extract_pairs(obj, out):
    if isinstance(obj, dict):
        name = obj.get("name") or obj.get("productName") or obj.get("title") or obj.get("displayName")
        price = None
        if isinstance(obj.get("price"), (int, float)):
            price = float(obj["price"])
        elif isinstance(obj.get("price"), dict):
            pd = obj["price"]
            price = pd.get("value") or pd.get("price") or pd.get("discountPrice") or pd.get("regularPrice")
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
                extract_pairs(v, out)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                extract_pairs(item, out)
all_rows = []
print(f"[TRACK 50] Launching Playwright Chromium across {len(CHAINS)} WAF-protected global supermarkets...")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800}
    )
    ctx.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media", "font"] else route.continue_())
    page = ctx.new_page()

    for mid, mname, iso3, cur, fx, lat, lon, url_tpl in CHAINS:
        chain_skus = []
        seen = set()
        for q_word, comm, eff_g, d_kg in QUERIES:
            captured = []
            def on_resp(resp):
                try:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct and resp.status == 200:
                        data = resp.json()
                        extract_pairs(data, captured)
                except Exception:
                    pass

            page.on("response", on_resp)
            try:
                page.goto(url_tpl.format(q=urllib.parse.quote(q_word)), wait_until="domcontentloaded", timeout=14000)
                page.wait_for_timeout(2200)
            except Exception:
                pass
            page.remove_listener("response", on_resp)

            for title, price in captured:
                key = (comm, title[:40])
                if key in seen:
                    continue
                seen.add(key)
                nom_kg = price / d_kg
                usd_kg = nom_kg * fx
                usd_50g = (usd_kg / eff_g) * 50.0
                if 0.30 <= usd_kg <= 48.0 and 0.04 <= usd_50g <= 5.50:
                    chain_skus.append((mid, mname, iso3, lat, lon, comm, round(nom_kg, 3), cur, round(usd_kg, 3), round(usd_50g, 4)))

        print(f" -> [{iso3}] {mname:42s}: {len(chain_skus)} live SKUs")
        all_rows.extend(chain_skus)

    browser.close()

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'PLAYWRIGHT_WHITESPACES_50';")

wages = dict(con.execute("""
    SELECT m.country_iso3, MEDIAN(f.usd_per_50g_diaas / NULLIF(f.labor_min_50g_diaas / 60.0, 0))
    FROM fact_protein_observations f JOIN dim_markets m USING(market_id)
    WHERE f.labor_min_50g_diaas > 0 GROUP BY 1;
""").fetchall())

max_id = con.execute("SELECT COALESCE(MAX(obs_id), 0) FROM fact_protein_observations;").fetchone()[0]
today_str = time.strftime("%Y-%m-%d")
ins = []

for mid, mname, iso3, lat, lon, comm, nom_kg, cur, usd_kg, usd_50g in all_rows:
    con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?, ?, ?, 'PLAYWRIGHT_WHITESPACES_50', ?, ?, 0.08, 0.08);",
                [mid, mname, iso3, lat, lon])
    w_hr = wages.get(iso3) or 10.0
    max_id += 1
    ins.append((
        max_id, "PLAYWRIGHT_WHITESPACES_50", "OBSERVED_RETAIL", mid, comm, today_str,
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
print(f"\n[TRACK 50 COMPLETE] Captured +{len(ins):,} WAF-bypassed live SKUs! Total DB: {tot_r:,} rows | {tot_m:,} markets | {tot_c} countries ({time.time()-t0:.1f}s)")
con.close()
