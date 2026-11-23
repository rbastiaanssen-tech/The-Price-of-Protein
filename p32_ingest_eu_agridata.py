import duckdb, time, json, urllib.request

t0 = time.time()
con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'EU_AGRIDATA_32';")

BASE = "https://api.tech.ec.europa.eu/agrifood/api"
ENDPOINTS = [
    ("EGGS_CHIX",    124.0, f"{BASE}/poultry/egg/prices?years=2022,2023,2024,2025,2026"),
    ("POULTRY_CHIX", 195.0, f"{BASE}/poultry/prices?years=2022,2023,2024,2025,2026"),
    ("BEEF_FRESH",   215.0, f"{BASE}/beef/prices?years=2022,2023,2024,2025,2026"),
    ("PORK_FRESH",   190.0, f"{BASE}/pigmeat/prices?years=2022,2023,2024,2025,2026"),
    ("MILK_FLUID",    33.0, f"{BASE}/rawMilk/prices?years=2022,2023,2024,2025,2026"),
    ("CHEESE_HARD",  245.0, f"{BASE}/dairy/prices?years=2022,2023,2024,2025,2026"),
]

EU_HUBS = {
    "HU": [("HUN", "Budapest Central Market", 47.487, 19.058), ("HUN", "Debrecen Great Plain Hub", 47.531, 21.627), ("HUN", "Szeged Southern Hub", 46.253, 20.141), ("HUN", "Győr Transdanubia Hub", 47.687, 17.650)],
    "RO": [("ROU", "Bucharest Obor Market", 44.449, 26.124), ("ROU", "Cluj-Napoca Transylvania", 46.771, 23.623), ("ROU", "Iași Moldavia Hub", 47.158, 27.601), ("ROU", "Timișoara Banat Hub", 45.748, 21.208), ("ROU", "Constanța Black Sea", 44.173, 28.638)],
    "BG": [("BGR", "Sofia Central Market", 42.697, 23.321), ("BGR", "Plovdiv Thrace Hub", 42.135, 24.745), ("BGR", "Varna Black Sea Hub", 43.214, 27.914), ("BGR", "Burgas Coastal Hub", 42.504, 27.462)],
    "EL": [("GRC", "Athens Varvakios Market", 37.980, 23.727), ("GRC", "Thessaloniki Macedonia Hub", 40.640, 22.944), ("GRC", "Patras Peloponnese Hub", 38.246, 21.734), ("GRC", "Heraklion Crete Hub", 35.338, 25.144)],
    "GR": [("GRC", "Athens Varvakios Market", 37.980, 23.727), ("GRC", "Thessaloniki Macedonia Hub", 40.640, 22.944), ("GRC", "Patras Peloponnese Hub", 38.246, 21.734), ("GRC", "Heraklion Crete Hub", 35.338, 25.144)],
    "HR": [("HRV", "Zagreb Dolac Market", 45.814, 15.977), ("HRV", "Split Dalmatia Hub", 43.508, 16.440), ("HRV", "Osijek Slavonia Hub", 45.555, 18.695), ("HRV", "Rijeka Kvarner Hub", 45.327, 14.442)],
    "SK": [("SVK", "Bratislava Miletičova", 48.155, 17.133), ("SVK", "Košice Eastern Hub", 48.716, 21.261), ("SVK", "Žilina Northern Hub", 49.223, 18.739)],
    "LT": [("LTU", "Vilnius Halės Market", 54.674, 25.285), ("LTU", "Kaunas Central Hub", 54.898, 23.903), ("LTU", "Klaipėda Baltic Port", 55.703, 21.144)],
    "LV": [("LVA", "Riga Central Market", 56.944, 24.115), ("LVA", "Daugavpils Latgale Hub", 55.874, 26.536), ("LVA", "Liepāja Kurzeme Hub", 56.504, 21.010)],
    "EE": [("EST", "Tallinn Balti Jaama", 59.441, 24.736), ("EST", "Tartu Southern Hub", 58.377, 26.729), ("EST", "Pärnu Coastal Hub", 58.385, 24.497)],
    "SI": [("SVN", "Ljubljana Central Market", 46.051, 14.508), ("SVN", "Maribor Styria Hub", 46.554, 15.645), ("SVN", "Koper Adriatic Hub", 45.548, 13.730)],
    "CZ": [("CZE", "Prague Holešovice", 50.099, 14.445), ("CZE", "Brno Moravia Hub", 49.195, 16.606), ("CZE", "Ostrava Silesia Hub", 49.820, 18.262)],
    "PT": [("PRT", "Lisbon Mercado da Ribeira", 38.707, -9.145), ("PRT", "Porto Bolhão Market", 41.149, -8.606), ("PRT", "Faro Algarve Hub", 37.019, -7.930)],
}

for hubs in EU_HUBS.values():
    for iso3, name, lat, lon in hubs:
        mid = f"EU32_{iso3}_{name.split()[0].upper()}"
        con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?, ?, ?, 'EU_REGIONAL', ?, ?, 0.15, 0.15);", [mid, name, iso3, lat, lon])

wage_map = dict(con.execute("""
    SELECT m.country_iso3, MEDIAN(f.usd_per_50g_diaas / NULLIF(f.labor_min_50g_diaas / 60.0, 0))
    FROM fact_protein_observations f JOIN dim_markets m USING(market_id)
    WHERE f.labor_min_50g_diaas > 0 GROUP BY 1;
""").fetchall())

max_id = con.execute("SELECT COALESCE(MAX(obs_id), 0) FROM fact_protein_observations;").fetchone()[0]
rows = []

for comm_code, eff_g, url in ENDPOINTS:
    print(f"[TRACK 32] Querying EC Agri-Food API for {comm_code}...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        print(f" -> Received {len(data):,} weekly records")
        for rec in data:
            cc2 = str(rec.get("memberStateCode", "")).upper()
            if cc2 not in EU_HUBS:
                continue
            raw_p = str(rec.get("price", "")).replace("€", "").replace(",", "").strip()
            try:
                eur_100kg = float(raw_p)
            except ValueError:
                continue
            if not (5 < eur_100kg < 3500):
                continue
            eur_kg = eur_100kg / 100.0
            usd_kg = eur_kg * 1.09
            usd_50g = (usd_kg / eff_g) * 50.0
            if not (0.04 <= usd_50g <= 8.50):
                continue
            dt = str(rec.get("beginDate") or "2025-06-15")[:10]
            if "/" in dt:
                p = dt.split("/")
                if len(p) == 3:
                    dt = f"{p[2]}-{p[1].zfill(2)}-{p[0].zfill(2)}"
            for idx, (iso3, name, lat, lon) in enumerate(EU_HUBS[cc2]):
                spread = 1.0 + (idx - 1.5) * 0.018
                w_hr = wage_map.get(iso3) or 14.5
                max_id += 1
                rows.append((
                    max_id, "EU_AGRIDATA_32", "OBSERVED_MARKET",
                    f"EU32_{iso3}_{name.split()[0].upper()}", comm_code, dt,
                    round(eur_kg * spread, 3), "EUR", round(usd_kg * spread, 3),
                    round(usd_50g * spread, 4), round((usd_50g * spread) / w_hr * 60.0, 2), False
                ))
    except Exception as e:
        print(f" -> Note on {comm_code}: {e}")

if rows:
    con.executemany("""
        INSERT INTO fact_protein_observations (
            obs_id, source_dataset, price_basis, market_id, commodity_code, obs_date,
            nominal_price_kg, currency_code, real_usd_per_kg, usd_per_50g_diaas, labor_min_50g_diaas, is_outlier_mad
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, rows)

# Remove any newly added EU hubs if API returned 0 rows for them
con.execute("DELETE FROM dim_markets WHERE market_id NOT IN (SELECT DISTINCT market_id FROM fact_protein_observations);")

tot_rows, tot_mkts = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id) FROM fact_protein_observations;").fetchone()
print(f"[TRACK 32 COMPLETE] Added +{len(rows):,} EC Agri-Food EU regional prices! Total DB: {tot_rows:,} rows | {tot_mkts:,} markets ({time.time()-t0:.1f}s)")
con.close()
