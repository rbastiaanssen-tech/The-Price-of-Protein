import json, h3
from pathlib import Path

# 25 Global Metros: city -> (region, country_iso3, base_50g_usd, center_lat, center_lon)
METROS = {
    "New York": ("North America", "USA", 0.48, 40.75, -73.98),
    "Los Angeles": ("North America", "USA", 0.47, 34.05, -118.25),
    "Chicago": ("North America", "USA", 0.42, 41.88, -87.65),
    "Toronto": ("North America", "CAN", 0.45, 43.67, -79.39),
    "Mexico City": ("Latin America", "MEX", 0.38, 19.42, -99.15),
    "London": ("Europe", "GBR", 0.44, 51.51, -0.12),
    "Paris": ("Europe", "FRA", 0.46, 48.86, 2.34),
    "Amsterdam": ("Europe", "NLD", 0.43, 52.36, 4.89),
    "Berlin": ("Europe", "DEU", 0.39, 52.52, 13.40),
    "Madrid": ("Europe", "ESP", 0.40, 40.42, -3.70),
    "Istanbul": ("Europe", "TUR", 0.36, 41.02, 28.98),
    "Tokyo": ("East Asia", "JPN", 0.49, 35.68, 139.75),
    "Seoul": ("East Asia", "KOR", 0.47, 37.55, 126.99),
    "Shanghai": ("East Asia", "CHN", 0.35, 31.22, 121.47),
    "Beijing": ("East Asia", "CHN", 0.34, 39.91, 116.40),
    "Singapore": ("SE Asia", "SGP", 0.46, 1.31, 103.85),
    "Jakarta": ("SE Asia", "IDN", 0.31, -6.20, 106.84),
    "Mumbai": ("South Asia", "IND", 0.28, 19.05, 72.86),
    "Delhi": ("South Asia", "IND", 0.26, 28.63, 77.22),
    "Riyadh": ("Middle East", "SAU", 0.37, 24.71, 46.68),
    "Cairo": ("Middle East", "EGY", 0.29, 30.05, 31.24),
    "Sao Paulo": ("Latin America", "BRA", 0.36, -23.56, -46.65),
    "Buenos Aires": ("Latin America", "ARG", 0.33, -34.60, -58.42),
    "Lagos": ("Africa", "NGA", 0.34, 6.52, 3.38),
    "Johannesburg": ("Africa", "ZAF", 0.32, -26.19, 28.04),
}

# Format: (city, district_zip, lat, lon, retail_mult, local_wage_hr_usd, channel, regime)
DISTRICTS = [
    # 1. NEW YORK
    ("New York", "10021 · Upper East Side / Tribeca", 40.774, -73.957, 1.48, 62.0, "Whole Foods / Citarella", "Affluent Markup"),
    ("New York", "10014 · West Village / SoHo", 40.734, -74.003, 1.44, 58.0, "Specialty / Small Grocer", "Affluent Markup"),
    ("New York", "11215 · Park Slope, Brooklyn", 40.672, -73.978, 1.24, 44.0, "Co-op / Whole Foods", "Affluent Brownstone"),
    ("New York", "11103 · Astoria, Queens", 40.764, -73.923, 1.02, 29.0, "Key Food / Trade Fair", "Urban Core"),
    ("New York", "11354 · Flushing, Queens", 40.767, -73.833, 0.86, 21.0, "High-Volume Asian Supermarkets", "Discount / Wholesale"),
    ("New York", "10454 · South Bronx (Mott Haven)", 40.816, -73.918, 1.16, 16.5, "Bodegas / Low Supermarket SqFt", "Food-Desert Squeeze"),
    ("New York", "11207 · East New York / Brownsville", 40.666, -73.882, 1.15, 16.2, "Bodegas / Small Grocers", "Food-Desert Squeeze"),
    # 2. LOS ANGELES
    ("Los Angeles", "90210 · Beverly Hills / Brentwood", 34.073, -118.400, 1.52, 64.0, "Erewhon / Bristol Farms", "Affluent Markup"),
    ("Los Angeles", "90401 · Santa Monica / Venice", 34.019, -118.491, 1.36, 48.0, "Whole Foods / Gelson's", "Affluent Markup"),
    ("Los Angeles", "90027 · Silver Lake / Los Feliz", 34.098, -118.273, 1.14, 32.0, "Trader Joe's / Lassen's", "Urban Core"),
    ("Los Angeles", "91801 · Alhambra / San Gabriel", 34.095, -118.127, 0.85, 22.0, "99 Ranch / Ethnic Markets", "Discount / Wholesale"),
    ("Los Angeles", "90011 · South Central / Watts", 34.007, -118.258, 1.14, 16.0, "Liquor-Grocer / Sparse Chains", "Food-Desert Squeeze"),
    ("Los Angeles", "90220 · Compton / Lynwood", 33.896, -118.220, 1.08, 16.8, "Food 4 Less / Corner Mart", "Low-Wage Urban"),
    # 3. CHICAGO
    ("Chicago", "60611 · Gold Coast / Lincoln Park", 41.912, -87.634, 1.38, 52.0, "Whole Foods / Mariano's", "Affluent Markup"),
    ("Chicago", "60607 · West Loop / Fulton Market", 41.882, -87.654, 1.28, 46.0, "Boutique / Mariano's", "Affluent Markup"),
    ("Chicago", "60618 · Avondale / Logan Square", 41.939, -87.702, 0.96, 27.0, "Aldi / Cermak Fresh Market", "Discount Belt"),
    ("Chicago", "60608 · Pilsen / Little Village", 41.852, -87.674, 0.90, 19.5, "High-Volume Mexican Carnicerias", "Discount / Wholesale"),
    ("Chicago", "60624 · West Garfield Park", 41.880, -87.722, 1.15, 15.8, "Corner Stores / Food Desert", "Food-Desert Squeeze"),
    ("Chicago", "60620 · Auburn Gresham / Englewood", 41.744, -87.656, 1.14, 15.5, "Small Grocers / Low Chain Access", "Food-Desert Squeeze"),
    # 4. TORONTO
    ("Toronto", "M5R · Yorkville / Rosedale", 43.676, -79.390, 1.40, 48.0, "Pusateri's / Whole Foods", "Affluent Markup"),
    ("Toronto", "M6G · Annex / Little Italy", 43.665, -79.415, 1.14, 31.0, "Metro / Kensington Market", "Urban Core"),
    ("Toronto", "M1S · Scarborough / Agincourt", 43.785, -79.280, 0.86, 20.5, "No Frills / T&T Supermarket", "Discount / Wholesale"),
    ("Toronto", "M9V · Rexdale / Jane-Finch", 43.745, -79.560, 1.06, 17.5, "Convenience / Sparse Discounter", "Outer-Ring Squeeze"),
    ("Toronto", "L4T · Mississauga / Brampton", 43.683, -79.660, 0.91, 22.0, "FreshCo / Costco / Wal-Mart", "Discount Belt"),
    # 5. MEXICO CITY
    ("Mexico City", "11000 · Lomas de Chapultepec / Polanco", 19.432, -99.202, 1.48, 28.0, "City Market / Superama", "Affluent Markup"),
    ("Mexico City", "06140 · Condesa / Roma Norte", 19.415, -99.168, 1.26, 18.0, "Chedraui Selecto / Organic", "Affluent Markup"),
    ("Mexico City", "09040 · Central de Abasto / Iztapalapa", 19.375, -99.088, 0.78, 5.2, "Wholesale Mega-Market", "Discount / Wholesale"),
    ("Mexico City", "07000 · Gustavo A. Madero / Ecatepec", 19.520, -99.065, 1.06, 4.4, "Misceláneas / Micro-Retail", "Informal Squeeze"),
    ("Mexico City", "56615 · Valle de Chalco", 19.285, -98.945, 1.09, 4.1, "Small Tienditas / Peri-Urban", "Informal Squeeze"),
    # 6. LONDON
    ("London", "SW7 · Kensington & Chelsea", 51.499, -0.194, 1.42, 48.0, "Waitrose / Whole Foods", "Affluent Markup"),
    ("London", "W1 · Mayfair / Marylebone", 51.514, -0.145, 1.48, 56.0, "Boutique / M&S Foodhall", "Affluent Markup"),
    ("London", "N1 · Islington / Highbury", 51.546, -0.103, 1.18, 34.0, "Sainsbury's / Tesco Metro", "Urban Core"),
    ("London", "E1 · Tower Hamlets / Whitechapel", 51.519, -0.061, 0.98, 18.5, "Street Market / Halal Butcher", "Discount Urban"),
    ("London", "E15 · Newham / Stratford", 51.543, 0.003, 1.08, 17.5, "Small Grocer / High-Street", "Food-Desert Squeeze"),
    ("London", "CR0 · Croydon", 51.376, -0.098, 0.92, 21.0, "Lidl / Aldi / Asda Superstore", "Discount Belt"),
    # 7. PARIS
    ("Paris", "75007 · 6e / 7e Arr. (Saint-Germain)", 48.854, 2.326, 1.46, 46.0, "Monoprix / La Grande Épicerie", "Affluent Markup"),
    ("Paris", "75016 · 16e Arr. (Passy)", 48.858, 2.275, 1.38, 44.0, "Franprix / Boutique", "Affluent Markup"),
    ("Paris", "75018 · 18e Arr. (Barbès / Goutte d'Or)", 48.887, 2.352, 0.92, 17.2, "Open-Air Market / Halal", "Discount Urban"),
    ("Paris", "93200 · Saint-Denis (93)", 48.936, 2.357, 1.07, 16.0, "Convenience / Low Hypermarket", "Banlieue Squeeze"),
    ("Paris", "93300 · Aubervilliers / La Courneuve", 48.914, 2.383, 1.08, 15.8, "Small Corner / Sparse Large", "Banlieue Squeeze"),
    ("Paris", "94000 · Créteil / Vitry (94)", 48.790, 2.455, 0.93, 20.5, "Auchan / Leclerc Hypermarket", "Discount Belt"),
    # 8. AMSTERDAM
    ("Amsterdam", "1071 · Oud-Zuid / Museumkwartier", 52.354, 4.876, 1.34, 44.0, "Albert Heijn XL / Ekoplaza", "Affluent Markup"),
    ("Amsterdam", "1016 · Grachtengordel / Centrum", 52.370, 4.889, 1.38, 41.0, "AH City / Marqt", "Affluent Markup"),
    ("Amsterdam", "1072 · De Pijp", 52.354, 4.893, 1.12, 31.0, "Albert Cuyp Market / AH", "Urban Core"),
    ("Amsterdam", "1068 · Nieuw-West (Osdorp)", 52.362, 4.801, 0.92, 19.2, "Dirk / Lidl / Turkish Grocers", "Discount Belt"),
    ("Amsterdam", "1102 · Amsterdam-Zuidoost (Bijlmer)", 52.314, 4.955, 1.04, 18.0, "AH / Toko / Sparse Discounter", "Outer-Ring Squeeze"),
    # 9. BERLIN
    ("Berlin", "14193 · Grunewald / Dahlem", 52.475, 13.275, 1.32, 42.0, "Edeka Zurheide / Bio Company", "Affluent Markup"),
    ("Berlin", "10119 · Mitte / Prenzlauer Berg", 52.532, 13.412, 1.18, 32.0, "Rewe / Denn's Biomarkt", "Urban Core"),
    ("Berlin", "12043 · Neukölln / Sonnenallee", 52.481, 13.435, 0.88, 19.0, "Aldi / Lidl / Turkish Market", "Discount Belt"),
    ("Berlin", "12679 · Marzahn-Hellersdorf", 52.542, 13.565, 0.90, 18.5, "Netto / Kaufland Hypermarket", "Discount Belt"),
    # 10. MADRID
    ("Madrid", "28001 · Salamanca / Recoletos", 40.426, -3.684, 1.38, 36.0, "El Corte Inglés Gourmet / Sanchez Romero", "Affluent Markup"),
    ("Madrid", "28004 · Justicia / Chamberí", 40.428, -3.701, 1.18, 27.0, "Carrefour Express / Mercadona", "Urban Core"),
    ("Madrid", "28026 · Usera / Carabanchel", 40.386, -3.710, 0.91, 15.2, "Lidl / Dia / Mercado Municipal", "Discount Belt"),
    ("Madrid", "28053 · Puente de Vallecas", 40.392, -3.665, 0.93, 14.5, "Alcampo / Ahorramas", "Low-Wage Urban"),
    # 11. ISTANBUL
    ("Istanbul", "34337 · Bebek / Etiler (Beşiktaş)", 41.078, 29.043, 1.42, 18.5, "Macrocenter / Migros Jet", "Affluent Markup"),
    ("Istanbul", "34710 · Kadıköy / Moda", 40.985, 29.028, 1.12, 11.5, "Salı Pazarı / CarrefourSA", "Urban Core"),
    ("Istanbul", "34245 · Bağcılar / Esenler", 41.038, 28.856, 0.89, 5.2, "BİM / A101 Hard Discounters", "Discount Belt"),
    ("Istanbul", "34920 · Sultanbeyli", 40.965, 29.265, 0.94, 4.6, "Şok / BİM / Local Bakkal", "Low-Wage Urban"),
    # 12. TOKYO
    ("Tokyo", "106-0032 · Minato-ku (Roppongi / Azabu)", 35.658, 139.729, 1.44, 45.0, "National Azabu / Meidi-ya", "Affluent Markup"),
    ("Tokyo", "150-0001 · Shibuya / Daikanyama", 35.650, 139.703, 1.28, 36.0, "Seijo Ishii / Tokyu Store", "Affluent Markup"),
    ("Tokyo", "120-0034 · Adachi-ku (Kita-Senju)", 35.749, 139.805, 0.87, 19.5, "Gyomu Super / OK Store", "Discount Belt"),
    ("Tokyo", "133-0056 · Edogawa-ku / Koiwa", 35.733, 139.882, 0.89, 20.0, "Aeon / Ito-Yokado", "Discount Belt"),
    # 13. SEOUL
    ("Seoul", "06000 · Gangnam-gu (Apgujeong)", 37.527, 127.028, 1.42, 38.0, "SSG Food Market / Hyundai Dept", "Affluent Markup"),
    ("Seoul", "04348 · Yongsan-gu (Hannam-dong)", 37.534, 127.002, 1.32, 34.0, "Gourmet 494 / Boutique", "Affluent Markup"),
    ("Seoul", "08208 · Guro-gu / Geumcheon", 37.485, 126.887, 0.89, 17.5, "E-Mart / Traditional Market", "Discount Belt"),
    ("Seoul", "01050 · Gangbuk-gu / Dobong", 37.639, 127.025, 0.92, 16.8, "Homeplus / Neighborhood Mart", "Low-Wage Urban"),
    # 14. SHANGHAI
    ("Shanghai", "200040 · Jing'an / Former French Concession", 31.224, 121.448, 1.45, 22.0, "City'super / Ole' Boutique", "Affluent Markup"),
    ("Shanghai", "200120 · Lujiazui, Pudong", 31.238, 121.505, 1.36, 24.0, "Freshippo (Hema) / Imported", "Affluent Markup"),
    ("Shanghai", "201100 · Minhang / Qibao", 31.112, 121.380, 0.88, 9.5, "Wet Market (Caishichang) / RT-Mart", "Discount / Wholesale"),
    ("Shanghai", "201900 · Baoshan / Outer Ring", 31.395, 121.485, 0.86, 8.2, "Wholesale Market / Yonghui", "Discount / Wholesale"),
    # 15. BEIJING
    ("Beijing", "100020 · Chaoyang (Sanlitun / CBD)", 39.934, 116.455, 1.40, 21.0, "BHG Market Place / Jenny Lou's", "Affluent Markup"),
    ("Beijing", "100084 · Haidian (Zhongguancun)", 39.983, 116.316, 1.14, 18.0, "Wumart / Hema Fresh", "Urban Core"),
    ("Beijing", "102600 · Daxing / Xinfadi Wholesale Belt", 39.730, 116.340, 0.82, 7.8, "Xinfadi Wholesale / Wet Market", "Discount / Wholesale"),
    ("Beijing", "101100 · Tongzhou Sub-Center", 39.902, 116.655, 0.90, 8.5, "Yonghui / Morning Market", "Discount Belt"),
    # 16. SINGAPORE
    ("Singapore", "238801 · Orchard / Tanglin", 1.304, 103.832, 1.42, 42.0, "CS Fresh / Little Farms", "Affluent Markup"),
    ("Singapore", "018956 · Marina Bay / Sentosa Cove", 1.281, 103.854, 1.46, 46.0, "Gourmet Grocer / Boutique", "Affluent Markup"),
    ("Singapore", "520201 · Tampines / Bedok HDB", 1.353, 103.944, 0.90, 18.5, "NTUC FairPrice / Sheng Siong", "Discount Belt"),
    ("Singapore", "730888 · Woodlands / Jurong West", 1.436, 103.786, 0.88, 16.5, "Sheng Siong / Wet Market", "Discount Belt"),
    # 17. JAKARTA
    ("Jakarta", "12190 · SCBD / Menteng", -6.225, 106.809, 1.48, 12.5, "Ranch Market / GrandLucky", "Affluent Markup"),
    ("Jakarta", "11470 · West Jakarta (Grogol)", -6.167, 106.789, 1.02, 4.8, "Super Indo / Pasar Tradisional", "Urban Core"),
    ("Jakarta", "14440 · North Jakarta (Penjaringan / Muara Baru)", -6.115, 106.798, 0.94, 2.6, "Fish Auction / Informal Warung", "Informal Squeeze"),
    ("Jakarta", "13910 · East Jakarta (Cakung / Bekasi Edge)", -6.185, 106.945, 1.05, 2.4, "Micro-Sachet Warung", "Informal Squeeze"),
    # 18. MUMBAI
    ("Mumbai", "400026 · Malabar Hill / Cuffe Parade", 18.955, 72.798, 1.46, 16.0, "Nature's Basket / Foodhall", "Affluent Markup"),
    ("Mumbai", "400050 · Bandra West", 19.055, 72.826, 1.28, 11.5, "Modern Retail / Pali Market", "Affluent Markup"),
    ("Mumbai", "400017 · Dharavi", 19.042, 72.853, 1.06, 1.85, "Micro-Pack Kirana / Street Stall", "Informal Squeeze"),
    ("Mumbai", "400043 · Govandi / Mankhurd (M-East)", 19.055, 72.922, 1.10, 1.55, "Small Kirana / Low Cold-Chain", "Informal Squeeze"),
    # 19. DELHI
    ("Delhi", "110003 · Lutyens' Delhi / Khan Market", 28.600, 77.227, 1.48, 15.5, "Modern Bazaar / Le Marche", "Affluent Markup"),
    ("Delhi", "122002 · Gurugram DLF Phase 5", 28.435, 77.095, 1.34, 14.0, "Foodhall / Spencer's", "Affluent Markup"),
    ("Delhi", "110033 · Azadpur Mandi Belt", 28.707, 77.175, 0.82, 2.4, "Asia's Largest Wholesale Mandi", "Discount / Wholesale"),
    ("Delhi", "110094 · Seemapuri / North-East Delhi", 28.685, 77.318, 1.08, 1.65, "Micro-Pack Kirana / Peri-Urban", "Informal Squeeze"),
    # 20. RIYADH
    ("Riyadh", "11564 · Olaya / Diplomatic Quarter", 24.695, 46.685, 1.36, 32.0, "Tamimi Markets / Danube", "Affluent Markup"),
    ("Riyadh", "13241 · Al Nakheel / Hittin", 24.765, 46.625, 1.24, 28.0, "Carrefour / Tamimi", "Affluent Suburb"),
    ("Riyadh", "12614 · Batha / Manfouha", 24.628, 46.715, 0.88, 6.2, "Panda / Wholesale Souq", "Low-Wage Urban"),
    # 21. CAIRO
    ("Cairo", "11211 · Zamalek / Maadi", 30.062, 31.219, 1.42, 8.5, "Gourmet Egypt / Metro Market", "Affluent Markup"),
    ("Cairo", "11835 · New Cairo (Fifth Settlement)", 30.015, 31.435, 1.28, 7.2, "Carrefour / Seoudi", "Affluent Suburb"),
    ("Cairo", "12651 · Imbaba / Boulaq El Dakrour", 30.075, 31.205, 0.94, 1.8, "Koshk / Subsidized Tamween", "Informal Squeeze"),
    ("Cairo", "11511 · Manshiyat Naser", 30.032, 31.275, 1.04, 1.5, "Micro-Retail / Informal Market", "Informal Squeeze"),
    # 22. SAO PAULO
    ("Sao Paulo", "01415 · Jardins / Itaim Bibi", -23.568, -46.668, 1.44, 24.0, "St. Marche / Pão de Açúcar", "Affluent Markup"),
    ("Sao Paulo", "05424 · Pinheiros / Vila Madalena", -23.562, -46.692, 1.22, 16.5, "Mambo / Municipal Market", "Urban Core"),
    ("Sao Paulo", "08010 · São Miguel Paulista / Zona Leste", -23.495, -46.440, 0.90, 4.8, "Assaí Atacadista / Atacadão", "Discount Belt"),
    ("Sao Paulo", "04935 · Jardim Ângela / Capão Redondo", -23.685, -46.775, 1.06, 4.1, "Small Mercadinhos / Peri-Urban", "Informal Squeeze"),
    # 23. BUENOS AIRES
    ("Buenos Aires", "C1425 · Palermo Chico / Recoleta", -34.582, -58.402, 1.38, 14.5, "Jumbo / Boutique Carnicería", "Affluent Markup"),
    ("Buenos Aires", "C1406 · Flores / Caballito", -34.625, -58.455, 0.96, 6.8, "Coto / Día Hard Discounter", "Discount Belt"),
    ("Buenos Aires", "B1754 · La Matanza (Conurbano)", -34.715, -58.585, 1.04, 3.6, "Almacén de Barrio / Micro-Buy", "Informal Squeeze"),
    # 24. LAGOS
    ("Lagos", "106104 · Victoria Island / Ikoyi", 6.435, 3.425, 1.52, 9.5, "Spar / Ebeano / Cold-Chain", "Affluent Markup"),
    ("Lagos", "100271 · Ikeja / Surulere", 6.595, 3.345, 1.04, 2.8, "Shoprite / Mile 12 Wholesale", "Urban Core"),
    ("Lagos", "101245 · Makoko / Ajegunle", 6.495, 3.392, 1.12, 1.35, "Micro-Portion Sachet / Smoked Fish", "Informal Squeeze"),
    # 25. JOHANNESBURG
    ("Johannesburg", "2196 · Sandton / Hyde Park", -26.107, 28.056, 1.38, 22.0, "Woolworths Food / Checkers Sixty60", "Affluent Markup"),
    ("Johannesburg", "2092 · Melville / Rosebank", -26.148, 28.035, 1.16, 14.0, "Pick n Pay / Spar", "Urban Core"),
    ("Johannesburg", "1804 · Soweto (Vilakazi / Diepkloof)", -26.248, 27.908, 0.94, 3.8, "Boxer / Shoprite / Spaza Shop", "Low-Wage Urban"),
    ("Johannesburg", "2090 · Alexandra Township", -26.105, 28.098, 1.08, 2.9, "Spaza Micro-Packs / High Friction", "Informal Squeeze"),
]

cells = []
seen = set()
for city, dist, lat, lon, r_mult, wage_hr, channel, regime in DISTRICTS:
    reg_name, iso, base_usd, _, _ = METROS[city]
    center_h = h3.latlng_to_cell(lat, lon, 8)
    for ring in range(0, 3):
        for h in h3.grid_ring(center_h, ring):
            if h in seen:
                continue
            seen.add(h)
            jitter = 1.0 - 0.025 * ring
            usd_50g = round(base_usd * r_mult * jitter, 3)
            mins_50g = round(60.0 * (usd_50g / wage_hr), 2)
            wage_share = round(100.0 * (mins_50g / 480.0), 2)
            markup_pct = round((r_mult * jitter - 1.0) * 100.0, 1)
            cells.append({
                "h": h,
                "city": city,
                "reg": reg_name,
                "c": iso,
                "dist": dist,
                "u": usd_50g,
                "l": mins_50g,
                "w": wage_share,
                "mk": markup_pct,
                "st": channel,
                "rg": regime,
                "core": 1 if ring == 0 else 0
            })

metro_index = [
    {"city": k, "reg": v[0], "c": v[1], "base_u": v[2], "lat": v[3], "lon": v[4]}
    for k, v in METROS.items()
]

out = Path("public/data/metro_case_studies.json")
out.write_text(json.dumps({"metros": metro_index, "cells": cells}, separators=(",",":")))
print(f"[TRACK 22 COMPLETE] Generated {len(cells):,} H3 Res-8 zipcode/neighborhood hexagons across {len(METROS)} global metros ({out.stat().st_size/1024:.1f} KB)")
