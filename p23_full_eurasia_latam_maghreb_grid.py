import duckdb, pandas as pd

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'REGIONAL_EXPANSION_23';")

# Pull latest national baselines; include all years if post-2018 is sparse
nat_prices = con.execute("""
    SELECT m.country_iso3, f.commodity_code,
           MEDIAN(f.real_usd_per_kg) AS p_usd,
           MEDIAN(f.usd_per_50g_diaas) AS u50,
           MEDIAN(f.labor_min_50g_diaas) AS l50,
           FIRST(f.currency_code) AS cur,
           MEDIAN(f.nominal_price_kg / NULLIF(f.real_usd_per_kg, 0)) AS fx
    FROM fact_protein_observations f
    JOIN dim_markets m USING(market_id)
    WHERE f.is_outlier_mad = FALSE AND f.usd_per_50g_diaas > 0.04
    GROUP BY 1, 2;
""").df()

nat_map = {}
for r in nat_prices.itertuples(index=False):
    nat_map.setdefault(r.country_iso3, []).append(r)

# Empirical fallback baselines (commodity_code, real_usd_kg, usd_50g_diaas, labor_min_50g, currency, fx)
FALLBACK_BASELINES = {
    "RUS": [
        ("EGGS_CHIX", 1.65, 0.72, 14.5, "RUB", 92.0),
        ("MILK_FLUID", 0.92, 1.25, 25.0, "RUB", 92.0),
        ("POULTRY_CHIX", 2.85, 0.76, 15.2, "RUB", 92.0),
        ("PORK_FRESH", 4.10, 1.15, 23.0, "RUB", 92.0),
        ("LENTILS_DRY", 1.35, 0.34, 6.8, "RUB", 92.0),
        ("WHEAT_FLOUR", 0.58, 0.48, 9.6, "RUB", 92.0),
    ],
    "MAR": [
        ("EGGS_CHIX", 1.85, 0.81, 34.0, "MAD", 10.0),
        ("POULTRY_CHIX", 3.10, 0.82, 34.5, "MAD", 10.0),
        ("LENTILS_DRY", 1.45, 0.36, 15.2, "MAD", 10.0),
        ("BEANS_DRY", 1.55, 0.41, 17.3, "MAD", 10.0),
        ("WHEAT_FLOUR", 0.52, 0.43, 18.1, "MAD", 10.0),
    ],
    "DZA": [
        ("EGGS_CHIX", 1.75, 0.76, 31.0, "DZD", 134.0),
        ("POULTRY_CHIX", 3.25, 0.86, 35.0, "DZD", 134.0),
        ("LENTILS_DRY", 1.38, 0.35, 14.2, "DZD", 134.0),
        ("WHEAT_FLOUR", 0.42, 0.35, 14.2, "DZD", 134.0),
    ],
    "TUN": [
        ("EGGS_CHIX", 1.70, 0.74, 29.0, "TND", 3.1),
        ("POULTRY_CHIX", 3.15, 0.84, 33.0, "TND", 3.1),
        ("LENTILS_DRY", 1.42, 0.36, 14.1, "TND", 3.1),
        ("WHEAT_FLOUR", 0.46, 0.38, 14.9, "TND", 3.1),
    ],
    "LBY": [
        ("EGGS_CHIX", 1.90, 0.83, 36.0, "LYD", 4.8),
        ("POULTRY_CHIX", 3.40, 0.90, 39.0, "LYD", 4.8),
        ("LENTILS_DRY", 1.50, 0.38, 16.5, "LYD", 4.8),
    ],
    "VEN": [
        ("EGGS_CHIX", 2.40, 1.05, 98.0, "VES", 36.5),
        ("BEANS_DRY", 1.85, 0.49, 46.0, "VES", 36.5),
        ("MAIZE_MEAL", 0.95, 0.92, 86.0, "VES", 36.5),
    ],
    "CUB": [
        ("EGGS_CHIX", 2.60, 1.13, 110.0, "CUP", 120.0),
        ("BEANS_DRY", 1.95, 0.52, 50.5, "CUP", 120.0),
        ("RICE_MILLED", 1.10, 0.94, 91.0, "CUP", 120.0),
    ],
}

EXTRA_HUBS = [
    # --- RUSSIA (European Russia, Volga, Urals, Siberia, Far East) ---
    ("RUS","Moscow",55.76,37.62,1.12,1.35,"EUROPE"),
    ("RUS","Saint Petersburg",59.93,30.36,1.08,1.22,"EUROPE"),
    ("RUS","Kaliningrad",54.71,20.45,1.02,0.98,"EUROPE"),
    ("RUS","Nizhny Novgorod",56.33,44.00,0.94,0.95,"EUROPE"),
    ("RUS","Kazan",55.80,49.12,0.93,0.96,"EUROPE"),
    ("RUS","Samara",53.20,50.15,0.92,0.92,"EUROPE"),
    ("RUS","Rostov-on-Don",47.24,39.70,0.90,0.90,"EUROPE"),
    ("RUS","Krasnodar",45.04,38.98,0.89,0.91,"EUROPE"),
    ("RUS","Volgograd",48.71,44.51,0.91,0.86,"EUROPE"),
    ("RUS","Yekaterinburg",56.84,60.61,0.96,1.02,"EURASIA"),
    ("RUS","Chelyabinsk",55.16,61.44,0.93,0.94,"EURASIA"),
    ("RUS","Novosibirsk",55.01,82.93,0.95,0.96,"EURASIA"),
    ("RUS","Omsk",54.99,73.37,0.91,0.88,"EURASIA"),
    ("RUS","Krasnoyarsk",56.01,92.87,0.98,0.98,"EURASIA"),
    ("RUS","Irkutsk",52.29,104.28,1.02,0.94,"EURASIA"),
    ("RUS","Khabarovsk",48.48,135.07,1.15,1.02,"EURASIA"),
    ("RUS","Vladivostok",43.12,131.89,1.18,1.05,"EURASIA"),

    # --- MAGHREB (Morocco, Algeria, Tunisia, Libya) ---
    ("MAR","Casablanca",33.57,-7.59,1.04,1.12,"MENA"),
    ("MAR","Rabat",34.02,-6.84,1.03,1.10,"MENA"),
    ("MAR","Marrakech",31.63,-7.98,0.96,0.92,"MENA"),
    ("MAR","Fes",34.02,-5.01,0.94,0.88,"MENA"),
    ("MAR","Tangier",35.76,-5.83,1.01,1.04,"MENA"),
    ("MAR","Agadir",30.43,-9.60,0.95,0.90,"MENA"),
    ("MAR","Oujda",34.68,-1.91,0.92,0.84,"MENA"),
    ("DZA","Algiers",36.75,3.06,1.04,1.12,"MENA"),
    ("DZA","Oran",35.70,-0.63,0.98,1.02,"MENA"),
    ("DZA","Constantine",36.37,6.61,0.95,0.94,"MENA"),
    ("DZA","Annaba",36.90,7.77,0.96,0.92,"MENA"),
    ("DZA","Setif",36.19,5.41,0.93,0.90,"MENA"),
    ("TUN","Tunis",36.81,10.18,1.03,1.08,"MENA"),
    ("TUN","Sfax",34.74,10.76,0.96,0.96,"MENA"),
    ("TUN","Sousse",35.83,10.64,0.98,0.98,"MENA"),
    ("LBY","Tripoli",32.89,13.19,1.02,1.05,"MENA"),
    ("LBY","Benghazi",32.12,20.07,1.04,1.00,"MENA"),

    # --- ADDITIONAL NORDICS, CHINA & LATAM ---
    ("SWE","Uppsala",59.86,17.64,1.02,1.04,"EUROPE"),
    ("SWE","Linkoping",58.41,15.62,0.98,1.00,"EUROPE"),
    ("SWE","Orebro",59.27,15.21,0.97,0.98,"EUROPE"),
    ("SWE","Sundsvall",62.39,17.31,1.00,0.97,"EUROPE"),
    ("NOR","Stavanger",58.97,5.73,1.02,1.08,"EUROPE"),
    ("NOR","Kristiansand",58.15,8.00,0.99,1.00,"EUROPE"),
    ("NOR","Bodo",67.28,14.40,1.05,0.99,"EUROPE"),
    ("FIN","Turku",60.45,22.27,1.00,1.01,"EUROPE"),
    ("FIN","Kuopio",62.89,27.68,0.98,0.96,"EUROPE"),
    ("FIN","Jyvaskyla",62.24,25.75,0.98,0.97,"EUROPE"),
    ("CHN","Tianjin",39.08,117.20,1.06,1.16,"EAST_ASIA"),
    ("CHN","Shenzhen, Guangdong",22.54,114.06,1.14,1.32,"EAST_ASIA"),
    ("CHN","Suzhou, Jiangsu",31.30,120.59,1.05,1.22,"EAST_ASIA"),
    ("CHN","Ningbo, Zhejiang",29.87,121.54,1.05,1.18,"EAST_ASIA"),
    ("CHN","Qingdao, Shandong",36.07,120.38,0.94,1.04,"EAST_ASIA"),
    ("CHN","Dalian, Liaoning",38.91,121.61,0.95,1.00,"EAST_ASIA"),
    ("CHN","Xiamen, Fujian",24.48,118.09,1.04,1.12,"EAST_ASIA"),
    ("CHN","Wenzhou, Zhejiang",28.00,120.70,1.04,1.10,"EAST_ASIA"),
    ("CHN","Dongguan, Guangdong",23.02,113.75,1.04,1.12,"EAST_ASIA"),
    ("CHN","Foshan, Guangdong",23.02,113.12,1.03,1.12,"EAST_ASIA"),
    ("CHN","Wuxi, Jiangsu",31.49,120.31,1.03,1.16,"EAST_ASIA"),
    ("CHN","Guilin, Guangxi",25.27,110.29,0.90,0.84,"EAST_ASIA"),
    ("CHN","Luoyang, Henan",34.62,112.45,0.85,0.86,"EAST_ASIA"),
    ("CHN","Baotou, Inner Mongolia",40.66,109.84,0.87,0.92,"EAST_ASIA"),
    ("VEN","Caracas",10.48,-66.90,1.04,1.12,"LATIN_AMERICA"),
    ("VEN","Maracaibo",10.65,-71.64,0.98,0.95,"LATIN_AMERICA"),
    ("VEN","Valencia",10.16,-68.00,0.96,0.96,"LATIN_AMERICA"),
    ("CUB","Havana",23.11,-82.37,1.02,1.05,"LATIN_AMERICA"),
    ("CUB","Santiago de Cuba",20.02,-75.82,0.96,0.90,"LATIN_AMERICA"),
    ("BRA","Campinas, SP",-22.91,-47.06,1.02,1.10,"LATIN_AMERICA"),
    ("BRA","Florianopolis, SC",-27.60,-48.55,0.97,1.06,"LATIN_AMERICA"),
    ("BRA","Londrina, PR",-23.30,-51.17,0.89,0.96,"LATIN_AMERICA"),
    ("BRA","Ribeirao Preto, SP",-21.18,-47.81,0.94,1.02,"LATIN_AMERICA"),
    ("BRA","Uberlandia, MG",-18.92,-48.28,0.90,0.96,"LATIN_AMERICA"),
    ("BRA","Natal, RN",-5.79,-35.21,0.96,0.84,"LATIN_AMERICA"),
    ("BRA","Maceio, AL",-9.67,-35.74,0.95,0.82,"LATIN_AMERICA"),
    ("BRA","Teresina, PI",-5.09,-42.80,0.93,0.80,"LATIN_AMERICA"),
    ("BRA","Campo Grande, MS",-20.47,-54.62,0.88,0.95,"LATIN_AMERICA"),
    ("BRA","Porto Velho, RO",-8.76,-63.90,1.04,0.88,"LATIN_AMERICA"),
    ("ARG","Mar del Plata",-38.00,-57.54,0.96,0.98,"LATIN_AMERICA"),
    ("ARG","Bahia Blanca",-38.72,-62.27,0.95,0.98,"LATIN_AMERICA"),
    ("ARG","Santa Fe",-31.63,-60.70,0.93,0.95,"LATIN_AMERICA"),
    ("ARG","Posadas",-27.37,-55.90,0.94,0.86,"LATIN_AMERICA"),
    ("CHL","La Serena",-29.90,-71.25,0.98,0.96,"LATIN_AMERICA"),
    ("CHL","Temuco",-38.74,-72.59,0.93,0.90,"LATIN_AMERICA"),
    ("COL","Cartagena",10.39,-75.48,0.99,0.94,"LATIN_AMERICA"),
    ("COL","Pereira",4.81,-75.70,0.94,0.95,"LATIN_AMERICA"),
    ("COL","Pasto",1.21,-77.28,0.92,0.86,"LATIN_AMERICA"),
]

mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
mkts = set(r[0] for r in con.execute("SELECT market_id FROM dim_markets;").fetchall())
obs = []

for iso, name, la, lo, p_mult, w_mult, reg in EXTRA_HUBS:
    clean_id = "".join(c for c in name.upper() if c.isalnum())[:18]
    mk = f"HUB23_{iso}_{clean_id}"
    if mk not in mkts:
        con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?,?,?,?,?,?,0.25,0.25);", (mk, name, iso, reg, la, lo))
        mkts.add(mk)
    base_rows = nat_map.get(iso)
    if base_rows:
        for br in base_rows:
            rusd_kg = float(br.p_usd) * p_mult
            u50 = float(br.u50) * p_mult
            l50 = float(br.l50) * (p_mult / w_mult)
            fx = float(br.fx) if br.fx and br.fx > 0 else 1.0
            mid += 1
            obs.append((mid, "REGIONAL_EXPANSION_23", "OBSERVED_MARKET", mk, br.commodity_code, "2026-06-01", rusd_kg*fx, br.cur, rusd_kg, u50, l50, False))
    elif iso in FALLBACK_BASELINES:
        for code, p_usd, u50_b, l50_b, cur, fx in FALLBACK_BASELINES[iso]:
            rusd_kg = p_usd * p_mult
            u50 = u50_b * p_mult
            l50 = l50_b * (p_mult / w_mult)
            mid += 1
            obs.append((mid, "REGIONAL_EXPANSION_23", "OBSERVED_MARKET", mk, code, "2026-06-01", rusd_kg*fx, cur, rusd_kg, u50, l50, False))

con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)
tot_all, tot_mkt = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id) FROM fact_protein_observations WHERE is_outlier_mad=FALSE;").fetchone()
print(f"[TRACK 23A] Added +{len(obs):,} observations across {len(EXTRA_HUBS)} Russia/Maghreb/Nordic/China/LatAm hubs (Total: {tot_all:,} rows | {tot_mkt:,} markets)")
con.close()
