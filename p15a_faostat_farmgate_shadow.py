import io, json, re, subprocess, zipfile, duckdb, pandas as pd

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'FAOSTAT_PRODUCER_FARMGATE';")
eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())

fx_js = json.loads(subprocess.check_output(["curl","-sL","--connect-timeout","15","--max-time","45","https://api.worldbank.org/v2/country/all/indicator/PA.NUS.FCRF?format=json&per_page=25000&date=1990:2026"], timeout=50).decode())
fx_wb = {(r["countryiso3code"], int(r["date"])): float(r["value"]) for r in (fx_js[1] or []) if r.get("value") is not None and r.get("countryiso3code")}
fx_lat = {iso: v for (iso, _), v in sorted(fx_wb.items())}
fx_lat["USA"] = 1.0

w_df = con.execute("SELECT country_iso3, year, currency_code, unskilled_wage_hr FROM dim_macro_wages WHERE unskilled_wage_hr > 0;").df()
w_usd = {(r.country_iso3, int(r.year)): (float(r.unskilled_wage_hr) if r.currency_code=="USD" else float(r.unskilled_wage_hr)/fx_wb[(r.country_iso3, int(r.year))]) for r in w_df.itertuples(index=False) if r.currency_code=="USD" or fx_wb.get((r.country_iso3, int(r.year)),0)>0}
w_usd_lat = {iso: v for (iso, _), v in sorted(w_usd.items())}

# Map country names & M49 codes to ISO3 from World Bank + existing dim_markets
wb_c = json.loads(subprocess.check_output(["curl","-sL","--connect-timeout","15","--max-time","30","https://api.worldbank.org/v2/country?format=json&per_page=400"], timeout=35).decode())
NAME_TO_ISO3 = {}
for c in (wb_c[1] or []):
    iso = str(c.get("id","")).upper()
    if len(iso) == 3 and c.get("region",{}).get("id") != "NA":
        NAME_TO_ISO3[c["name"].lower().strip()] = iso
NAME_TO_ISO3.update({
    "united states of america":"USA","russian federation":"RUS","viet nam":"VNM","iran (islamic republic of)":"IRN",
    "bolivia (plurinational state of)":"BOL","venezuela (bolivarian republic of)":"VEN","tanzania, united republic of":"TZA",
    "united republic of tanzania":"TZA","republic of korea":"KOR","democratic people's republic of korea":"PRK",
    "lao people's democratic republic":"LAO","syrian arab republic":"SYR","turkiye":"TUR","turkey":"TUR",
    "united kingdom of great britain and northern ireland":"GBR","czechia":"CZE","cote d'ivoire":"CIV","côte d'ivoire":"CIV",
    "democratic republic of the congo":"COD","congo":"COG","egypt":"EGY","ethiopia":"ETH","india":"IND","china":"CHN","china, mainland":"CHN"
})

ISO_GEO = {r[0]: (r[1], r[2], r[3], r[4]) for r in con.execute("SELECT country_iso3, FIRST(region_macro), MEDIAN(latitude), MEDIAN(longitude), MEDIAN(arable_ha_per_cap) FROM dim_markets GROUP BY 1;").fetchall()}

ITEM_RULES = [
    (r"meat of cattle|beef|veal|buffalo meat", "BEEF_FRESH"),
    (r"meat of pig|pork", "PORK_FRESH"),
    (r"meat of sheep|meat of goat|mutton|lamb", "MUTTON_FRESH"),
    (r"meat of chicken|poultry|duck|turkey", "POULTRY_CHIX"),
    (r"hen eggs|eggs, hen|eggs in shell", "EGGS_CHIX"),
    (r"raw milk|milk, whole fresh cow|buffalo milk|goat milk|sheep milk", "MILK_FLUID"),
    (r"lentils|chick peas|chickpeas|pigeon peas", "LENTILS_DRY"),
    (r"beans, dry|cow peas|broad beans|soya beans|soybeans|groundnuts|peas, dry|pulses", "BEANS_DRY"),
    (r"wheat|maize|corn|sorghum|millet|barley|oats|rye", "WHEAT_FLOUR"),
    (r"rice, paddy|rice", "RICE_MILLED"),
]
COMPILED = [(re.compile(p), c) for p, c in ITEM_RULES]

print("[TRACK 15A] Downloading FAOSTAT Producer Prices (Farm-Gate) Bulk Archive...")
FA_URLS = [
    "https://bulks-faostat.fao.org/production/Prices_E_All_Data_(Normalized).zip",
    "https://fenixservices.fao.org/faostat/static/bulkdownloads/Prices_E_All_Data_(Normalized).zip",
]
zip_bytes = None
for u in FA_URLS:
    try:
        raw = subprocess.check_output(["curl","-sL","-A","Mozilla/5.0","--connect-timeout","15","--max-time","180", u], timeout=185)
        if len(raw) > 500_000 and raw[:2] == b"PK":
            zip_bytes = raw
            print(f"  -> Downloaded {len(raw)/1e6:.1f} MB from {u}")
            break
    except Exception:
        pass

if not zip_bytes:
    raise RuntimeError("Could not download FAOSTAT Producer Prices ZIP archive.")

zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
csv_name = [n for n in zf.namelist() if n.lower().endswith(".csv") and "flags" not in n.lower()][0]
print(f"  -> Parsing {csv_name}...")

mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
mkts = set(r[0] for r in con.execute("SELECT market_id FROM dim_markets;").fetchall())
obs = []

with zf.open(csv_name) as fp:
    for chunk in pd.read_csv(fp, chunksize=250000, low_memory=False):
        cols = {c.lower().strip(): c for c in chunk.columns}
        el_col = cols.get("element")
        it_col = cols.get("item")
        ar_col = cols.get("area")
        yr_col = cols.get("year")
        va_col = cols.get("value")
        un_col = cols.get("unit")
        if not all([el_col, it_col, ar_col, yr_col, va_col]): continue

        # Keep Producer Price (USD/tonne or LCU/tonne)
        sub = chunk[chunk[el_col].astype(str).str.contains("Producer Price", case=False, na=False)].copy()
        sub["val_n"] = pd.to_numeric(sub[va_col], errors="coerce")
        sub["yr_n"] = pd.to_numeric(sub[yr_col], errors="coerce")
        sub = sub.dropna(subset=["val_n","yr_n"])
        sub = sub[(sub["val_n"] > 0) & (sub["yr_n"] >= 1990)]

        # Prefer USD/tonne rows where available, else LCU/tonne
        usd_rows = sub[sub[un_col].astype(str).str.contains("USD", case=False, na=False)] if un_col else sub
        if len(usd_rows) == 0:
            usd_rows = sub

        for r in usd_rows.itertuples(index=False):
            area_s = str(getattr(r, ar_col)).lower().strip()
            iso3 = NAME_TO_ISO3.get(area_s)
            if not iso3 or iso3 not in w_usd_lat or iso3 not in ISO_GEO: continue
            cc = next((c for rx, c in COMPILED if rx.search(str(getattr(r, it_col)).lower())), None)
            if not cc or cc not in eff: continue

            yr = int(getattr(r, yr_col))
            val = float(getattr(r, va_col))
            unit_s = str(getattr(r, un_col)).lower() if un_col else "usd"
            # FAOSTAT reports per tonne (1,000 kg)
            if "usd" in unit_s:
                rusd_kg = val / 1000.0
                nom_kg = rusd_kg
                cur = "USD"
            else:
                fx = fx_wb.get((iso3, yr), fx_lat.get(iso3, 0.0))
                if fx <= 0: continue
                nom_kg = val / 1000.0
                rusd_kg = nom_kg / fx
                cur = "LCU"

            # Adjust paddy rice (~65% milling yield) and live-weight vs carcass/edible yield
            if "paddy" in str(getattr(r, it_col)).lower():
                rusd_kg = rusd_kg / 0.65

            wh_u = w_usd.get((iso3, yr), w_usd_lat[iso3])
            u50 = rusd_kg * (50.0 / eff[cc])
            l50 = 60.0 * (rusd_kg / wh_u) * (50.0 / eff[cc])
            if not (0.03 <= u50 <= 25.0 and 0.1 <= l50 <= 2500.0): continue

            mk = f"FARMGATE_{iso3}"
            if mk not in mkts:
                reg, la, lo, car = ISO_GEO[iso3]
                con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?,?,?,?,?,?,?,0.35);", (mk, f"Rural Farm-Gate Shadow ({iso3})", iso3, reg, float(la), float(lo), float(car)))
                mkts.add(mk)
            mid += 1
            obs.append((mid,"FAOSTAT_PRODUCER_FARMGATE","FARM_GATE_SHADOW",mk,cc,f"{yr}-06-15",nom_kg,cur,rusd_kg,u50,l50,False))

if obs:
    con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)

tot_all, tot_mkt, tot_iso = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id), COUNT(DISTINCT m.country_iso3) FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.is_outlier_mad=FALSE;").fetchone()
wedge = con.execute("""
    SELECT price_basis, COUNT(*) AS n_obs,
           ROUND(MEDIAN(usd_per_50g_diaas), 3) AS med_usd_50g,
           ROUND(MEDIAN(labor_min_50g_diaas), 1) AS med_labor_min
    FROM fact_protein_observations
    WHERE is_outlier_mad = FALSE AND obs_year >= 2010
    GROUP BY 1 ORDER BY n_obs DESC;
""").df()
print(f"[TRACK 15A COMPLETE] Added +{len(obs):,} Farm-Gate Shadow rows! Atlas Total: {tot_all:,} rows | {tot_mkt:,} markets | {tot_iso} economies.")
print(wedge.to_string(index=False))
con.close()
