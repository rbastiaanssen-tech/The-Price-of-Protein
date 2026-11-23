import json, urllib.request, duckdb, pandas as pd

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'EUROSTAT_OFFICIAL';")
eff_map = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())

EU = {
    "AT": ("AUT", "Austria", 47.52, 14.55, 0.15), "BE": ("BEL", "Belgium", 50.50, 4.47, 0.08),
    "BG": ("BGR", "Bulgaria", 42.73, 25.49, 0.51), "CH": ("CHE", "Switzerland", 46.82, 8.23, 0.05),
    "CY": ("CYP", "Cyprus", 35.13, 33.43, 0.08), "CZ": ("CZE", "Czechia", 49.82, 15.47, 0.24),
    "DE": ("DEU", "Germany", 51.17, 10.45, 0.14), "DK": ("DNK", "Denmark", 56.26, 9.50, 0.41),
    "EE": ("EST", "Estonia", 58.60, 25.01, 0.52), "EL": ("GRC", "Greece", 39.07, 21.82, 0.20),
    "ES": ("ESP", "Spain", 40.46, -3.75, 0.25), "FI": ("FIN", "Finland", 61.92, 25.75, 0.41),
    "FR": ("FRA", "France", 46.23, 2.21, 0.27), "HR": ("HRV", "Croatia", 45.10, 15.20, 0.22),
    "HU": ("HUN", "Hungary", 47.16, 19.50, 0.43), "IE": ("IRL", "Ireland", 53.41, -8.24, 0.09),
    "IS": ("ISL", "Iceland", 64.96, -19.02, 0.33), "IT": ("ITA", "Italy", 41.87, 12.57, 0.12),
    "LT": ("LTU", "Lithuania", 55.17, 23.88, 0.81), "LU": ("LUX", "Luxembourg", 49.82, 6.13, 0.10),
    "LV": ("LVA", "Latvia", 56.88, 24.60, 0.72), "MT": ("MLT", "Malta", 35.94, 14.38, 0.02),
    "NL": ("NLD", "Netherlands", 52.13, 5.29, 0.06), "NO": ("NOR", "Norway", 60.47, 8.47, 0.15),
    "PL": ("POL", "Poland", 51.92, 19.15, 0.29), "PT": ("PRT", "Portugal", 39.40, -8.22, 0.10),
    "RO": ("ROU", "Romania", 45.94, 24.97, 0.44), "RS": ("SRB", "Serbia", 44.02, 21.01, 0.38),
    "SE": ("SWE", "Sweden", 60.13, 18.64, 0.25), "SI": ("SVN", "Slovenia", 46.15, 15.00, 0.09),
    "SK": ("SVK", "Slovakia", 48.67, 19.70, 0.25), "TR": ("TUR", "Türkiye", 38.96, 35.24, 0.24),
    "UK": ("GBR", "United Kingdom", 55.38, -3.44, 0.09),
}
CURR = {"CHE":"CHF","CZE":"CZK","DNK":"DKK","GBR":"GBP","HUN":"HUF","ISL":"ISK","NOR":"NOK","POL":"PLN","ROU":"RON","SRB":"RSD","SWE":"SEK","TUR":"TRY","BGR":"BGN"}

def get_estat(ds, q):
    url = f"https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{ds}?format=JSON&lang=EN&{q}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=45) as r:
        d = json.loads(r.read().decode())
    dims, sizes, vals = d["id"], d["size"], d.get("value", {})
    lbls = {k: d["dimension"][k]["category"].get("label", {}) for k in dims}
    keys = [[x[0] for x in sorted(d["dimension"][k]["category"]["index"].items(), key=lambda i: i[1])] for k in dims]
    st, acc = [], 1
    for s in reversed(sizes): st.append(acc); acc *= s
    st.reverse()
    rows = [{k: ks[(int(i)//s)%len(ks)] for k, ks, s in zip(dims, keys, st)} | {"v": float(v)} for i, v in vals.items() if v is not None]
    return pd.DataFrame(rows), lbls

print("[PHASE 3] Fetching Eurostat wages, PPPs & selling prices...")
mw, _ = get_estat("earn_mw_cur", "currency=NAC"); mw["yr"] = mw["time"].str[:4].astype(int)
lci, _ = get_estat("lc_lci_lev", "unit=NAC&lcstruct=D1_D4_MD5&nace_r2=I&nace_r2=B-S"); lci["yr"] = lci["time"].str[:4].astype(int)
w_map = {**lci[lci["nace_r2"]=="B-S"].set_index(["geo","yr"])["v"].to_dict(), **lci[lci["nace_r2"]=="I"].set_index(["geo","yr"])["v"].to_dict(), **(mw.groupby(["geo","yr"])["v"].mean()/173.33).to_dict()}

ppp, _ = get_estat("prc_ppp_ind", "na_item=PPP_EU27_2020&ppp_cat=GDP"); ppp["yr"] = ppp["time"].str[:4].astype(int)
p_map = ppp.set_index(["geo","yr"])["v"].to_dict()

an, la = get_estat("apri_ap_anouta", "currency=NAC")
cr, lc = get_estat("apri_ap_crpouta", "currency=NAC")

def map_codes(ld):
    m = {}
    for c, txt in ld.items():
        t = txt.lower()
        if "soft wheat" in t and "100 kg" in t: m[c] = ("WHEAT_FLOUR", 100.0)
        elif "grain maize" in t and "100 kg" in t: m[c] = ("MAIZE_GRAIN", 100.0)
        elif ("dried peas" in t or "dried beans" in t) and "100 kg" in t: m[c] = ("DRY_BEANS", 100.0)
        elif ("young cattle" in t or "bullocks" in t or "heifers" in t) and "100 kg" in t: m[c] = ("BEEF_BONELESS", 100.0)
        elif "pigs" in t and "100 kg" in t: m[c] = ("PORK_FRESH", 100.0)
        elif "chickens" in t and "100 kg" in t: m[c] = ("CHICKEN_WHOLE", 100.0)
        elif "raw cows" in t and "milk" in t and "100 kg" in t: m[c] = ("MILK_FRESH", 100.0)
        elif "eggs" in t and "100 items" in t: m[c] = ("EGGS_FRESH", 6.0)
    return m

cmap = {**map_codes(la.get("prod_ani", {})), **map_codes(lc.get("prod_veg", {}))}
an["p"], cr["p"] = an["prod_ani"], cr["prod_veg"]
dfp = pd.concat([an[["geo","p","time","v"]], cr[["geo","p","time","v"]]])
dfp["yr"] = dfp["time"].str[:4].astype(int)

mid = con.execute("SELECT COALESCE(MAX(obs_id), 0) FROM fact_protein_observations;").fetchone()[0]
obs, mkts = [], set()
for r in dfp.itertuples(index=False):
    if r.geo not in EU or r.p not in cmap or r.v <= 0 or (r.geo, r.yr) not in w_map or (r.geo, r.yr) not in p_map: continue
    cc, div = cmap[r.p]
    if cc not in eff_map: continue
    iso3, name, lat, lon, ar = EU[r.geo]
    mk_id = f"ESTAT_{iso3}"
    if mk_id not in mkts:
        con.execute("INSERT OR REPLACE INTO dim_markets VALUES (?, ?, ?, 'EUROPE', ?, ?, ?, 0.16);", (mk_id, f"{name} (Eurostat)", iso3, lat, lon, ar))
        mkts.add(mk_id)
    w, pp = w_map[(r.geo, r.yr)], p_map[(r.geo, r.yr)]
    if w <= 0 or pp <= 0: continue
    nom = r.v / div
    rusd = (nom / pp) * 1.42
    mid += 1
    obs.append((mid, "EUROSTAT_OFFICIAL", "OBSERVED_MARKET", mk_id, cc, f"{r.yr:04d}-06-15", nom, CURR.get(iso3,"EUR"), rusd, rusd*(50.0/eff_map[cc]), 60.0*(nom/w)*(50.0/eff_map[cc]), False))

con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)
print(f"[PHASE 3 COMPLETE] Inserted {len(obs):,} Eurostat observations across {len(mkts)} European countries.")
con.close()
