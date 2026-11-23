import json, time, subprocess, re, urllib.parse
from playwright.sync_api import sync_playwright
import duckdb

t0 = time.time()

def classify_apac(title):
    t = title.lower()
    if any(k in t for k in ["egg", "蛋", "雞蛋", "鸡蛋"]): return ("EGGS_CHIX", 124.0, 0.60)
    if any(k in t for k in ["milk", "奶", "牛奶", "鮮奶", "鲜奶"]): return ("MILK_FLUID", 33.0, 1.00)
    if any(k in t for k in ["cheese", "芝士", "起司", "乳酪"]): return ("CHEESE_HARD", 245.0, 0.35)
    if any(k in t for k in ["chicken", "雞", "鸡", "雞胸", "雞翼"]): return ("POULTRY_CHIX", 195.0, 0.70)
    if any(k in t for k in ["beef", "牛", "牛肉", "牛排"]): return ("BEEF_FRESH", 215.0, 0.70)
    if any(k in t for k in ["pork", "豬", "猪", "豬肉", "猪肉"]): return ("PORK_FRESH", 190.0, 0.70)
    if any(k in t for k in ["tuna", "sardine", "吞拿魚", "鮪魚", "沙甸魚", "鯖魚"]): return ("FISH_PELAGIC_SMALL", 180.0, 0.16)
    if any(k in t for k in ["lentil", "扁豆", "小扁豆"]): return ("LENTILS_DRY", 148.0, 0.50)
    if any(k in t for k in ["bean", "豆", "紅豆", "黑豆", "黃豆"]): return ("BEANS_DRY", 138.0, 0.50)
    return None

all_rows = []

# 1. HONG KONG CONSUMER COUNCIL OFFICIAL OPEN DATA (DATA.GOV.HK / ONLINE PRICE WATCH)
print("[TRACK 52A] Fetching Hong Kong Consumer Council Online Price Watch (DATA.GOV.HK)...")
hk_url = "https://online-price-watch.consumer.org.hk/opw/opendata/pricewatch.json"
res = subprocess.run(["curl", "-sL", "--max-time", "25", hk_url], capture_output=True, text=True)
hk_count = 0
if res.returncode == 0 and res.stdout.strip().startswith("["):
    try:
        items = json.loads(res.stdout)
        seen_hk = set()
        for it in items:
            title = f"{it.get('name_en', '')} {it.get('name_tc', '')} {it.get('brand_en', '')}".strip()
            cls = classify_apac(title)
            if not cls:
                continue
            comm, eff_g, d_kg = cls
            prices = it.get("prices") or {}
            for chain_code, p_val in prices.items():
                try:
                    pval = float(p_val)
                except Exception:
                    continue
                if pval <= 0:
                    continue
                key = (chain_code, comm, title[:35])
                if key in seen_hk:
                    continue
                seen_hk.add(key)
                nom_kg = pval / d_kg
                usd_kg = nom_kg * 0.128  # HKD -> USD
                usd_50g = (usd_kg / eff_g) * 50.0
                if 0.30 <= usd_kg <= 50.0 and 0.04 <= usd_50g <= 5.50:
                    mid = f"HKG_CC_{chain_code.upper()}"
                    mname = f"HK {chain_code.upper()} (Consumer Council)"
                    all_rows.append((mid, mname, "HKG", 22.3193, 114.1694, comm, round(nom_kg, 3), "HKD", round(usd_kg, 3), round(usd_50g, 4)))
                    hk_count += 1
        print(f" -> Hong Kong Consumer Council yielded {hk_count:,} live retail SKUs across HK chains!")
    except Exception as e:
        print(f" -> HK JSON parse note: {e}")
else:
    print(f" -> HK Consumer Council returned status={res.returncode}, len={len(res.stdout)}")

# 2. PLAYWRIGHT DOM + SSR EXTRACTOR FOR TAIWAN (CARREFOUR TW) & SINGAPORE (FAIRPRICE)
SSR_TARGETS = [
    ("TWN_CARREFOUR_TW", "Carrefour Taiwan (Taipei)", "TWN", "TWD", 0.031, 25.0330, 121.5654,
     "https://online.carrefour.com.tw/zh/search?q={q}", ["雞蛋", "牛奶", "起司", "雞胸肉", "牛肉", "豬肉", "鮪魚"]),
    ("SGP_FAIRPRICE_DOM", "NTUC FairPrice Singapore", "SGP", "SGD", 0.75, 1.2834, 103.8607,
     "https://www.fairprice.com.sg/search?query={q}", ["eggs", "milk", "cheese", "chicken breast", "beef", "tuna", "lentils"]),
]

print("[TRACK 52B] Extracting Taiwan & Singapore retail shelves via Playwright DOM evaluation...")
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 900}
    )
    ctx.route("**/*", lambda r: r.abort() if r.request.resource_type in ["image", "media", "font"] else r.continue_())
    page = ctx.new_page()

    for mid, mname, iso3, cur, fx, lat, lon, url_tpl, q_list in SSR_TARGETS:
        chain_skus = []
        seen = set()
        for q_word in q_list:
            cls = classify_apac(q_word)
            if not cls:
                continue
            comm, eff_g, d_kg = cls
            try:
                page.goto(url_tpl.format(q=urllib.parse.quote(q_word)), wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2800)
                # Extract visible product card text lines containing currency symbols ($ / NT$)
                cards = page.evaluate("""() => {
                    const out = [];
                    const els = document.querySelectorAll('a, div, li, article');
                    for (const el of els) {
                        const txt = (el.innerText || '').trim();
                        if (txt.length >= 8 && txt.length <= 180 && /[$￥NT]/.test(txt) && /\\d/.test(txt)) {
                            out.push(txt);
                        }
                    }
                    return out.slice(0, 120);
                }""")
                for card in cards:
                    lines = [ln.strip() for ln in card.splitlines() if ln.strip()]
                    if len(lines) < 2:
                        continue
                    title = max(lines, key=len)
                    m_price = re.search(r'(?:NT\$|\$)\s*(\d+(?:\.\d{1,2})?)', card)
                    if not m_price:
                        continue
                    pval = float(m_price.group(1))
                    key = (comm, title[:35])
                    if key in seen or pval <= 0:
                        continue
                    seen.add(key)
                    nom_kg = pval / d_kg
                    usd_kg = nom_kg * fx
                    usd_50g = (usd_kg / eff_g) * 50.0
                    if 0.30 <= usd_kg <= 50.0 and 0.04 <= usd_50g <= 5.50:
                        chain_skus.append((mid, mname, iso3, lat, lon, comm, round(nom_kg, 3), cur, round(usd_kg, 3), round(usd_50g, 4)))
            except Exception:
                continue
        print(f" -> [{iso3}] {mname:35s}: {len(chain_skus)} live DOM SKUs")
        all_rows.extend(chain_skus)
    browser.close()

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'GREATER_CHINA_APAC_52';")

wages = dict(con.execute("""
    SELECT m.country_iso3, MEDIAN(f.usd_per_50g_diaas / NULLIF(f.labor_min_50g_diaas / 60.0, 0))
    FROM fact_protein_observations f JOIN dim_markets m USING(market_id)
    WHERE f.labor_min_50g_diaas > 0 GROUP BY 1;
""").fetchall())

max_id = con.execute("SELECT COALESCE(MAX(obs_id), 0) FROM fact_protein_observations;").fetchone()[0]
today_str = time.strftime("%Y-%m-%d")
ins = []

for mid, mname, iso3, lat, lon, comm, nom_kg, cur, usd_kg, usd_50g in all_rows:
    con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?, ?, ?, 'GREATER_CHINA_APAC_52', ?, ?, 0.08, 0.08);",
                [mid, mname, iso3, lat, lon])
    w_hr = wages.get(iso3) or 14.0
    max_id += 1
    ins.append((
        max_id, "GREATER_CHINA_APAC_52", "OBSERVED_RETAIL", mid, comm, today_str,
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
print(f"\n[TRACK 52 COMPLETE] Added +{len(ins):,} live SKUs across HKG/TWN/SGP! Total DB: {tot_r:,} rows | {tot_m:,} markets | {tot_c} countries ({time.time()-t0:.1f}s)")
con.close()
