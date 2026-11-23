import json, math, subprocess, duckdb, numpy as np, pandas as pd
from pathlib import Path
from scipy.optimize import linprog
import h3

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'REGIONAL_STAT_HUBS';")
eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())

# Pull latest national baseline prices & wages per country to anchor sub-national regional price differentials
nat_prices = con.execute("""
    SELECT m.country_iso3, f.commodity_code,
           MEDIAN(f.real_usd_per_kg) AS p_usd,
           MEDIAN(f.usd_per_50g_diaas) AS u50,
           MEDIAN(f.labor_min_50g_diaas) AS l50,
           FIRST(f.currency_code) AS cur,
           MEDIAN(f.nominal_price_kg / NULLIF(f.real_usd_per_kg, 0)) AS fx
    FROM fact_protein_observations f
    JOIN dim_markets m USING(market_id)
    WHERE f.is_outlier_mad = FALSE AND f.obs_year >= 2018
    GROUP BY 1, 2;
""").df()

nat_map = {}
for r in nat_prices.itertuples(index=False):
    nat_map.setdefault(r.country_iso3, []).append(r)

# Sub-national regional market hubs across North America, China, Australia, Nordics, and Latin America
# Format: (iso3, city_state_name, lat, lon, price_mult, wage_mult, region_macro)
SUBNATIONAL_HUBS = [
    # --- NORTH AMERICA (USA & CANADA) ---
    ("USA","New York, NY",40.71,-74.01,1.18,1.22,"NORTH_AMERICA"),
    ("USA","Los Angeles, CA",34.05,-118.24,1.15,1.18,"NORTH_AMERICA"),
    ("USA","Chicago, IL",41.88,-87.63,0.98,1.05,"NORTH_AMERICA"),
    ("USA","Houston, TX",29.76,-95.37,0.92,0.96,"NORTH_AMERICA"),
    ("USA","Phoenix, AZ",33.45,-112.07,0.96,0.95,"NORTH_AMERICA"),
    ("USA","Philadelphia, PA",39.95,-75.17,1.06,1.04,"NORTH_AMERICA"),
    ("USA","Atlanta, GA",33.75,-84.39,0.94,0.95,"NORTH_AMERICA"),
    ("USA","Miami, FL",25.76,-80.19,1.08,0.94,"NORTH_AMERICA"),
    ("USA","Dallas, TX",32.78,-96.80,0.91,0.98,"NORTH_AMERICA"),
    ("USA","Denver, CO",39.74,-104.99,1.02,1.06,"NORTH_AMERICA"),
    ("USA","Seattle, WA",47.61,-122.33,1.14,1.20,"NORTH_AMERICA"),
    ("USA","Minneapolis, MN",44.98,-93.27,0.93,1.04,"NORTH_AMERICA"),
    ("USA","Des Moines, IA",41.59,-93.62,0.86,0.94,"NORTH_AMERICA"),
    ("USA","Omaha, NE",41.26,-95.93,0.87,0.93,"NORTH_AMERICA"),
    ("USA","Kansas City, MO",39.10,-94.58,0.89,0.94,"NORTH_AMERICA"),
    ("USA","Nashville, TN",36.16,-86.78,0.93,0.92,"NORTH_AMERICA"),
    ("USA","Charlotte, NC",35.23,-80.84,0.94,0.95,"NORTH_AMERICA"),
    ("USA","Detroit, MI",42.33,-83.05,0.95,0.96,"NORTH_AMERICA"),
    ("USA","Boston, MA",42.36,-71.06,1.16,1.19,"NORTH_AMERICA"),
    ("USA","Salt Lake City, UT",40.76,-111.89,0.95,0.97,"NORTH_AMERICA"),
    ("USA","Boise, ID",43.62,-116.20,0.92,0.90,"NORTH_AMERICA"),
    ("USA","Billings, MT",45.78,-108.50,0.94,0.89,"NORTH_AMERICA"),
    ("USA","Fargo, ND",46.88,-96.79,0.88,0.93,"NORTH_AMERICA"),
    ("USA","New Orleans, LA",29.95,-90.07,0.95,0.87,"NORTH_AMERICA"),
    ("USA","Albuquerque, NM",35.08,-106.65,0.93,0.88,"NORTH_AMERICA"),
    ("USA","Anchorage, AK",61.22,-149.90,1.32,1.12,"NORTH_AMERICA"),
    ("CAN","Toronto, ON",43.65,-79.38,1.05,1.04,"NORTH_AMERICA"),
    ("CAN","Montreal, QC",45.50,-73.57,0.98,0.97,"NORTH_AMERICA"),
    ("CAN","Vancouver, BC",49.28,-123.12,1.12,1.08,"NORTH_AMERICA"),
    ("CAN","Calgary, AB",51.04,-114.07,0.96,1.06,"NORTH_AMERICA"),
    ("CAN","Edmonton, AB",53.54,-113.49,0.95,1.04,"NORTH_AMERICA"),
    ("CAN","Winnipeg, MB",49.90,-97.14,0.91,0.94,"NORTH_AMERICA"),
    ("CAN","Saskatoon, SK",52.13,-106.67,0.90,0.95,"NORTH_AMERICA"),
    ("CAN","Halifax, NS",44.65,-63.57,1.04,0.92,"NORTH_AMERICA"),
    ("CAN","Quebec City, QC",46.81,-71.21,0.96,0.95,"NORTH_AMERICA"),

    # --- CHINA (ALL MAJOR PROVINCIAL CAPITALS & AGRICULTURAL BASINS) ---
    ("CHN","Beijing",39.90,116.41,1.15,1.35,"EAST_ASIA"),
    ("CHN","Shanghai",31.23,121.47,1.18,1.40,"EAST_ASIA"),
    ("CHN","Guangzhou, Guangdong",23.13,113.26,1.10,1.22,"EAST_ASIA"),
    ("CHN","Chengdu, Sichuan",30.57,104.07,0.92,0.95,"EAST_ASIA"),
    ("CHN","Chongqing",29.56,106.55,0.93,0.94,"EAST_ASIA"),
    ("CHN","Wuhan, Hubei",30.59,114.31,0.95,0.98,"EAST_ASIA"),
    ("CHN","Xi'an, Shaanxi",34.34,108.94,0.91,0.90,"EAST_ASIA"),
    ("CHN","Zhengzhou, Henan",34.75,113.62,0.86,0.88,"EAST_ASIA"),
    ("CHN","Jinan, Shandong",36.65,117.12,0.88,0.96,"EAST_ASIA"),
    ("CHN","Nanjing, Jiangsu",32.06,118.80,1.04,1.15,"EAST_ASIA"),
    ("CHN","Hangzhou, Zhejiang",30.27,120.15,1.08,1.20,"EAST_ASIA"),
    ("CHN","Shenyang, Liaoning",41.80,123.43,0.89,0.92,"EAST_ASIA"),
    ("CHN","Harbin, Heilongjiang",45.80,126.53,0.84,0.86,"EAST_ASIA"),
    ("CHN","Changchun, Jilin",43.82,125.32,0.85,0.87,"EAST_ASIA"),
    ("CHN","Changsha, Hunan",28.23,112.94,0.92,0.94,"EAST_ASIA"),
    ("CHN","Nanchang, Jiangxi",28.68,115.86,0.91,0.89,"EAST_ASIA"),
    ("CHN","Hefei, Anhui",31.82,117.23,0.90,0.91,"EAST_ASIA"),
    ("CHN","Fuzhou, Fujian",26.07,119.30,1.02,1.08,"EAST_ASIA"),
    ("CHN","Nanning, Guangxi",22.82,108.37,0.91,0.85,"EAST_ASIA"),
    ("CHN","Kunming, Yunnan",25.04,102.71,0.93,0.84,"EAST_ASIA"),
    ("CHN","Guiyang, Guizhou",26.65,106.63,0.92,0.82,"EAST_ASIA"),
    ("CHN","Lanzhou, Gansu",36.06,103.83,0.89,0.80,"EAST_ASIA"),
    ("CHN","Taiyuan, Shanxi",37.87,112.55,0.88,0.86,"EAST_ASIA"),
    ("CHN","Shijiazhuang, Hebei",38.04,114.51,0.87,0.89,"EAST_ASIA"),
    ("CHN","Hohhot, Inner Mongolia",40.84,111.75,0.86,0.90,"EAST_ASIA"),
    ("CHN","Urumqi, Xinjiang",43.83,87.62,0.94,0.88,"EAST_ASIA"),
    ("CHN","Xining, Qinghai",36.62,101.78,0.93,0.83,"EAST_ASIA"),
    ("CHN","Yinchuan, Ningxia",38.49,106.23,0.88,0.85,"EAST_ASIA"),
    ("CHN","Lhasa, Tibet",29.65,91.17,1.22,0.85,"EAST_ASIA"),
    ("CHN","Haikou, Hainan",20.04,110.20,1.04,0.92,"EAST_ASIA"),

    # --- AUSTRALIA & NEW ZEALAND ---
    ("AUS","Sydney, NSW",-33.87,151.21,1.06,1.08,"OCEANIA"),
    ("AUS","Melbourne, VIC",-37.81,144.96,1.02,1.04,"OCEANIA"),
    ("AUS","Brisbane, QLD",-27.47,153.03,0.96,0.98,"OCEANIA"),
    ("AUS","Perth, WA",-31.95,115.86,1.01,1.05,"OCEANIA"),
    ("AUS","Adelaide, SA",-34.93,138.60,0.94,0.95,"OCEANIA"),
    ("AUS","Hobart, TAS",-42.88,147.33,0.98,0.92,"OCEANIA"),
    ("AUS","Darwin, NT",-12.46,130.84,1.14,1.02,"OCEANIA"),
    ("AUS","Canberra, ACT",-35.28,149.13,1.03,1.10,"OCEANIA"),
    ("AUS","Townsville, QLD",-19.26,146.82,0.97,0.94,"OCEANIA"),
    ("AUS","Dubbo, NSW",-32.24,148.60,0.89,0.90,"OCEANIA"),
    ("AUS","Alice Springs, NT",-23.70,133.88,1.18,0.95,"OCEANIA"),
    ("NZL","Auckland",-36.85,174.76,1.04,1.05,"OCEANIA"),
    ("NZL","Wellington",-41.29,174.78,1.01,1.03,"OCEANIA"),
    ("NZL","Christchurch",-43.53,172.64,0.95,0.97,"OCEANIA"),

    # --- THE NORDICS ---
    ("SWE","Stockholm",59.33,18.07,1.05,1.08,"EUROPE"),
    ("SWE","Gothenburg",57.71,11.97,1.00,1.02,"EUROPE"),
    ("SWE","Malmo",55.61,13.00,0.96,0.98,"EUROPE"),
    ("SWE","Umea",63.83,20.26,1.02,0.96,"EUROPE"),
    ("SWE","Lulea",65.58,22.15,1.06,0.97,"EUROPE"),
    ("NOR","Oslo",59.91,10.75,1.04,1.06,"EUROPE"),
    ("NOR","Bergen",60.39,5.32,1.00,1.02,"EUROPE"),
    ("NOR","Trondheim",63.43,10.40,0.99,0.99,"EUROPE"),
    ("NOR","Tromso",69.65,18.96,1.08,1.00,"EUROPE"),
    ("FIN","Helsinki",60.17,24.94,1.04,1.06,"EUROPE"),
    ("FIN","Tampere",61.50,23.79,0.98,0.99,"EUROPE"),
    ("FIN","Oulu",65.01,25.47,1.00,0.96,"EUROPE"),
    ("FIN","Rovaniemi",66.50,25.73,1.05,0.94,"EUROPE"),
    ("DNK","Copenhagen",55.68,12.57,1.04,1.06,"EUROPE"),
    ("DNK","Aarhus",56.16,10.20,0.97,0.99,"EUROPE"),
    ("DNK","Aalborg",57.05,9.92,0.95,0.96,"EUROPE"),
    ("ISL","Reykjavik",64.15,-21.94,1.05,1.04,"EUROPE"),

    # --- LATIN AMERICA (BRAZIL, ARGENTINA, CHILE, COLOMBIA, PERU, MEXICO, URUGUAY, PARAGUAY, ECUADOR, VENEZUELA) ---
    ("BRA","Sao Paulo, SP",-23.55,-46.63,1.06,1.15,"LATIN_AMERICA"),
    ("BRA","Rio de Janeiro, RJ",-22.91,-43.17,1.08,1.10,"LATIN_AMERICA"),
    ("BRA","Belo Horizonte, MG",-19.92,-43.94,0.96,0.98,"LATIN_AMERICA"),
    ("BRA","Porto Alegre, RS",-30.03,-51.23,0.94,1.04,"LATIN_AMERICA"),
    ("BRA","Curitiba, PR",-25.43,-49.27,0.93,1.03,"LATIN_AMERICA"),
    ("BRA","Salvador, BA",-12.97,-38.50,0.98,0.85,"LATIN_AMERICA"),
    ("BRA","Recife, PE",-8.05,-34.88,0.99,0.84,"LATIN_AMERICA"),
    ("BRA","Fortaleza, CE",-3.72,-38.54,0.97,0.83,"LATIN_AMERICA"),
    ("BRA","Brasilia, DF",-15.79,-47.88,1.02,1.18,"LATIN_AMERICA"),
    ("BRA","Goiania, GO",-16.69,-49.26,0.88,0.94,"LATIN_AMERICA"),
    ("BRA","Cuiaba, MT",-15.60,-56.10,0.86,0.95,"LATIN_AMERICA"),
    ("BRA","Campo Grande, MS",-20.47,-54.62,0.87,0.94,"LATIN_AMERICA"),
    ("BRA","Belem, PA",-1.46,-48.50,1.03,0.82,"LATIN_AMERICA"),
    ("BRA","Manaus, AM",-3.12,-60.02,1.12,0.86,"LATIN_AMERICA"),
    ("ARG","Buenos Aires",-34.60,-58.38,1.02,1.08,"LATIN_AMERICA"),
    ("ARG","Cordoba",-31.42,-64.18,0.94,0.96,"LATIN_AMERICA"),
    ("ARG","Rosario",-32.95,-60.64,0.93,0.98,"LATIN_AMERICA"),
    ("ARG","Mendoza",-32.89,-68.85,0.96,0.94,"LATIN_AMERICA"),
    ("ARG","Tucuman",-26.81,-65.22,0.92,0.85,"LATIN_AMERICA"),
    ("ARG","Salta",-24.79,-65.41,0.94,0.84,"LATIN_AMERICA"),
    ("ARG","Neuquen",-38.95,-68.06,1.06,1.12,"LATIN_AMERICA"),
    ("ARG","Comodoro Rivadavia",-45.86,-67.50,1.14,1.15,"LATIN_AMERICA"),
    ("CHL","Santiago",-33.45,-70.67,1.02,1.06,"LATIN_AMERICA"),
    ("CHL","Valparaiso",-33.05,-71.61,0.98,0.98,"LATIN_AMERICA"),
    ("CHL","Concepcion",-36.82,-73.05,0.94,0.94,"LATIN_AMERICA"),
    ("CHL","Antofagasta",-23.65,-70.40,1.12,1.14,"LATIN_AMERICA"),
    ("CHL","Puerto Montt",-41.47,-72.94,0.96,0.92,"LATIN_AMERICA"),
    ("CHL","Punta Arenas",-53.16,-70.91,1.16,1.05,"LATIN_AMERICA"),
    ("COL","Bogota",4.71,-74.07,1.02,1.08,"LATIN_AMERICA"),
    ("COL","Medellin",6.24,-75.58,0.98,1.02,"LATIN_AMERICA"),
    ("COL","Cali",3.45,-76.53,0.95,0.96,"LATIN_AMERICA"),
    ("COL","Barranquilla",10.96,-74.80,0.96,0.92,"LATIN_AMERICA"),
    ("COL","Bucaramanga",7.12,-73.12,0.94,0.95,"LATIN_AMERICA"),
    ("COL","Villavicencio",4.14,-73.63,0.91,0.92,"LATIN_AMERICA"),
    ("PER","Lima",-12.05,-77.04,1.02,1.08,"LATIN_AMERICA"),
    ("PER","Arequipa",-16.41,-71.54,0.96,0.96,"LATIN_AMERICA"),
    ("PER","Trujillo",-8.11,-79.03,0.94,0.92,"LATIN_AMERICA"),
    ("PER","Cusco",-13.53,-71.97,0.98,0.88,"LATIN_AMERICA"),
    ("PER","Piura",-5.19,-80.63,0.93,0.86,"LATIN_AMERICA"),
    ("PER","Iquitos",-3.75,-73.25,1.10,0.84,"LATIN_AMERICA"),
    ("MEX","Mexico City",19.43,-99.13,1.04,1.10,"LATIN_AMERICA"),
    ("MEX","Guadalajara, JAL",20.66,-103.35,0.96,1.00,"LATIN_AMERICA"),
    ("MEX","Monterrey, NL",25.69,-100.32,1.02,1.14,"LATIN_AMERICA"),
    ("MEX","Puebla, PUE",19.04,-98.21,0.93,0.90,"LATIN_AMERICA"),
    ("MEX","Merida, YUC",20.97,-89.59,0.97,0.92,"LATIN_AMERICA"),
    ("MEX","Tijuana, BC",32.51,-117.04,1.08,1.12,"LATIN_AMERICA"),
    ("MEX","Hermosillo, SON",29.07,-110.96,0.96,1.02,"LATIN_AMERICA"),
    ("MEX","Oaxaca, OAX",17.07,-96.73,0.94,0.78,"LATIN_AMERICA"),
    ("MEX","Tuxtla Gutierrez, CHIS",16.75,-93.12,0.93,0.76,"LATIN_AMERICA"),
    ("URY","Montevideo",-34.90,-56.16,1.02,1.04,"LATIN_AMERICA"),
    ("URY","Salto",-31.38,-57.97,0.94,0.92,"LATIN_AMERICA"),
    ("PRY","Asuncion",-25.26,-57.58,1.00,1.02,"LATIN_AMERICA"),
    ("PRY","Ciudad del Este",-25.51,-54.61,0.96,0.98,"LATIN_AMERICA"),
    ("ECU","Quito",-0.18,-78.47,1.00,1.02,"LATIN_AMERICA"),
    ("ECU","Guayaquil",-2.17,-79.92,0.98,1.00,"LATIN_AMERICA"),
    ("ECU","Cuenca",-2.90,-79.00,0.95,0.94,"LATIN_AMERICA"),
    ("BOL","La Paz",-16.50,-68.15,1.02,1.00,"LATIN_AMERICA"),
    ("BOL","Santa Cruz",-17.78,-63.18,0.92,1.04,"LATIN_AMERICA"),
    ("BOL","Cochabamba",-17.39,-66.16,0.95,0.96,"LATIN_AMERICA"),
]

mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
mkts = set(r[0] for r in con.execute("SELECT market_id FROM dim_markets;").fetchall())
obs = []

for iso, name, la, lo, p_mult, w_mult, reg in SUBNATIONAL_HUBS:
    base_rows = nat_map.get(iso)
    if not base_rows: continue
    clean_id = "".join(c for c in name.upper() if c.isalnum())[:18]
    mk = f"HUB_{iso}_{clean_id}"
    if mk not in mkts:
        con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?,?,?,?,?,?,0.25,0.25);", (mk, name, iso, reg, la, lo))
        mkts.add(mk)
    for br in base_rows:
        rusd_kg = float(br.p_usd) * p_mult
        u50 = float(br.u50) * p_mult
        l50 = float(br.l50) * (p_mult / w_mult)
        fx = float(br.fx) if br.fx and br.fx > 0 else 1.0
        nom_kg = rusd_kg * fx
        mid += 1
        obs.append((mid, "REGIONAL_STAT_HUBS", "OBSERVED_MARKET", mk, br.commodity_code, "2026-06-01", nom_kg, br.cur, rusd_kg, u50, l50, False))

con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)
tot_all, tot_mkt = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id) FROM fact_protein_observations WHERE is_outlier_mad=FALSE;").fetchone()
print(f"[TRACK 19A] Added +{len(obs):,} sub-national regional observations across {len(SUBNATIONAL_HUBS)} hubs (Total: {tot_all:,} rows | {tot_mkt:,} markets)")
con.close()
