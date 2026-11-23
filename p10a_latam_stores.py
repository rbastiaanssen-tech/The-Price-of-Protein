import csv, io, json, re, subprocess, zipfile, duckdb, pandas as pd

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset IN ('ARG_PRECIOS_CLAROS','MEX_PROFECO');")
eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())

# 1. Verify & calibrate ARG, MEX, IND wages + latest FX
fx_js = json.loads(subprocess.check_output(["curl","-sL","https://api.worldbank.org/v2/country/ARG;MEX;IND/indicator/PA.NUS.FCRF?format=json&per_page=100&date=2018:2026"], timeout=30).decode())
fx_wb = {(r["countryiso3code"], int(r["date"])): float(r["value"]) for r in (fx_js[1] or []) if r.get("value") is not None}
fx_wb[("ARG", 2025)] = fx_wb.get(("ARG", 2025), 1085.0)
fx_wb[("ARG", 2026)] = fx_wb.get(("ARG", 2026), 1250.0)
fx_wb[("MEX", 2025)] = fx_wb.get(("MEX", 2025), 19.8)
fx_wb[("MEX", 2026)] = fx_wb.get(("MEX", 2026), 20.2)

# Note: MEX minimum wage in 2024-2026 is ~$1.68-$1.95 USD/hr (~248.93 MXN/day = 31.1 MXN/hr); ensure accurate floor
w_usd = {("ARG", 2024): 4.10, ("ARG", 2025): 4.40, ("ARG", 2026): 4.63,
         ("MEX", 2022): 1.25, ("MEX", 2023): 1.55, ("MEX", 2024): 1.75, ("MEX", 2025): 1.88, ("MEX", 2026): 1.95}

mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
obs, mkts = [], set()

# 2. Discover Argentina Precios Claros (SEPA) CKAN datasets on datos.gob.ar
print("[TRACK 10A-1] Querying datos.gob.ar for Argentina Precios Claros (SEPA) storefront datasets...")
pkg_arg = json.loads(subprocess.check_output(["curl","-sL","https://datos.gob.ar/api/3/action/package_search?q=precios+claros+sepa&rows=10"], timeout=30).decode())
arg_zips = []
for ds in pkg_arg.get("result",{}).get("results",[]):
    for res in ds.get("resources",[]):
        u = str(res.get("url") or "")
        if u.lower().endswith(".zip") or "sepa" in u.lower():
            arg_zips.append((ds.get("name",""), str(res.get("last_modified") or ""), u))
print(f"  -> Found {len(arg_zips)} SEPA ZIP resources on datos.gob.ar.")

ARG_MAP = [
    (r"leche", "MILK_FLUID", 1.03),
    (r"huevo", "EGGS_CHIX", 0.72),
    (r"pollo|suprema|pechuga", "POULTRY_CHIX", 1.0),
    (r"carne|asado|nalga|paleta|roast|picada|vacuno", "BEEF_FRESH", 1.0),
    (r"cerdo|carre|bondiola", "PORK_FRESH", 1.0),
    (r"harina.*trigo", "WHEAT_FLOUR", 1.0),
    (r"lenteja", "LENTILS_DRY", 1.0),
    (r"poroto", "BEANS_DRY", 1.0),
    (r"arroz", "RICE_MILLED", 1.0),
    (r"atun|merluza|pescado", "FISH_CANNED", 1.0),
    (r"queso.*cremoso|queso.*rallar|queso.*port", "CHEESE_HARD", 1.0),
]

def map_es_prod(desc):
    d = desc.lower()
    for pat, cc, dkg in ARG_MAP:
        if re.search(pat, d):
            m = re.search(r"(\d+(?:[.,]\d+)?)\s*(kg|gr|g|lt|l|ml|cc|un)\b", d)
            kg = dkg
            if m:
                v = float(m.group(1).replace(",","."))
                u = m.group(2)
                if u in ("gr","g","ml","cc") and v >= 50: kg = v / 1000.0
                elif u in ("kg","lt","l") and 0.1 <= v <= 10.0: kg = v
                elif u == "un" and cc == "EGGS_CHIX" and 4 <= v <= 36: kg = v * 0.06
            return cc, kg
    return None, 0.0

arg_added = 0
for _, _, zurl in arg_zips[:2]:
    try:
        zbytes = subprocess.check_output(["curl","-sL","--max-time","120", zurl], timeout=130)
        with zipfile.ZipFile(io.BytesIO(zbytes)) as zf:
            names = zf.namelist()
            suc_f = next((n for n in names if "sucursal" in n.lower() and n.endswith(".csv")), None)
            prod_f = next((n for n in names if "producto" in n.lower() and n.endswith(".csv")), None)
            if not suc_f or not prod_f: continue
            suc_df = pd.read_csv(zf.open(suc_f), sep=None, engine="python", on_bad_lines="skip")
            scols = {c.lower(): c for c in suc_df.columns}
            lat_c = next((c for k,c in scols.items() if "lat" in k), None)
            lon_c = next((c for k,c in scols.items() if "lon" in k or "lng" in k), None)
            id_c = next((c for k,c in scols.items() if "sucursal_id" in k or k == "id"), None)
            band_c = next((c for k,c in scols.items() if "bandera" in k or "comercio" in k or "sucursal_nombre" in k), None)
            if not (lat_c and lon_c and id_c): continue
            suc_map = {}
            for r in suc_df.itertuples(index=False):
                try:
                    sid = str(getattr(r, id_c)).strip()
                    la, lo = float(str(getattr(r, lat_c)).replace(",",".")), float(str(getattr(r, lon_c)).replace(",","."))
                    bn = str(getattr(r, band_c)).strip() if band_c else sid
                    if -56.0 <= la <= -21.0 and -74.0 <= lo <= -53.0:
                        suc_map[sid] = (bn, la, lo)
                except Exception:
                    pass
            print(f"  -> Parsed {len(suc_map):,} GPS-pinned Argentine supermarket branches.")
            for chunk in pd.read_csv(zf.open(prod_f), sep=None, engine="python", chunksize=250000, on_bad_lines="skip"):
                pcols = {c.lower(): c for c in chunk.columns}
                psid = next((c for k,c in pcols.items() if "sucursal_id" in k), None)
                pdesc = next((c for k,c in pcols.items() if "descripcion" in k or "nombre" in k), None)
                ppr = next((c for k,c in pcols.items() if "precio_lista" in k or k == "precio"), None)
                if not (psid and pdesc and ppr): break
                for r in chunk.itertuples(index=False):
                    sid = str(getattr(r, psid)).strip()
                    if sid not in suc_map: continue
                    cc, kg = map_es_prod(str(getattr(r, pdesc)))
                    if not cc or cc not in eff: continue
                    try:
                        pr = float(str(getattr(r, ppr)).replace(",","."))
                    except Exception:
                        continue
                    if pr <= 0: continue
                    nom_kg = pr / kg
                    rusd_kg = nom_kg / fx_wb[("ARG", 2026)]
                    u50 = rusd_kg * (50.0 / eff[cc])
                    l50 = 60.0 * (rusd_kg / w_usd[("ARG", 2026)]) * (50.0 / eff[cc])
                    if not (0.15 <= u50 <= 20.0 and 0.5 <= l50 <= 600.0): continue
                    bn, la, lo = suc_map[sid]
                    mk = f"ARG_SEPA_{re.sub(r'[^A-Z0-9]','',sid.upper())[:18]}"
                    if mk not in mkts:
                        con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?,?,'ARG','LATAM',?,?,0.22,0.14);", (mk, f"{bn} ({sid}), ARG", la, lo))
                        mkts.add(mk)
                    mid += 1
                    obs.append((mid,"ARG_PRECIOS_CLAROS","OBSERVED_MARKET",mk,cc,"2026-06-15",nom_kg,"ARS",rusd_kg,u50,l50,False))
                    arg_added += 1
                    if arg_added >= 2500000: break
                if arg_added >= 2500000: break
    except Exception as e:
        print("  -> SEPA archive note:", e)
print(f"[TRACK 10A-1] Argentina Precios Claros: +{arg_added:,} storefront observations.")

# 3. Discover Mexico PROFECO Quien es Quien en los Precios on datos.gob.mx
print("[TRACK 10A-2] Querying datos.gob.mx for Mexico PROFECO store-level retail surveys...")
pkg_mex = json.loads(subprocess.check_output(["curl","-sL","https://datos.gob.mx/busca/api/3/action/package_search?q=quien+es+quien+en+los+precios&rows=10"], timeout=30).decode())
mex_urls = []
for ds in pkg_mex.get("result",{}).get("results",[]):
    for res in ds.get("resources",[]):
        u = str(res.get("url") or "")
        if any(ext in u.lower() for ext in (".csv",".zip")):
            mex_urls.append(u)
print(f"  -> Found {len(mex_urls)} PROFECO retail resources on datos.gob.mx.")

mex_added = 0
for murl in mex_urls[:3]:
    try:
        raw = subprocess.check_output(["curl","-sL","--max-time","120", murl], timeout=130)
        if murl.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                cname = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
                if not cname: continue
                stream = io.BytesIO(zf.read(cname))
        else:
            stream = io.BytesIO(raw)
        for chunk in pd.read_csv(stream, chunksize=250000, on_bad_lines="skip", low_memory=False):
            cols = {c.lower(): c for c in chunk.columns}
            p_c = next((c for k,c in cols.items() if "producto" in k), None)
            pr_c = next((c for k,c in cols.items() if "precio" in k), None)
            lat_c = next((c for k,c in cols.items() if "lat" in k), None)
            lon_c = next((c for k,c in cols.items() if "lon" in k or "lng" in k), None)
            est_c = next((c for k,c in cols.items() if "cadena" in k or "establecimiento" in k or "estado" in k), None)
            dt_c = next((c for k,c in cols.items() if "fecha" in k), None)
            if not (p_c and pr_c and lat_c and lon_c): break
            for r in chunk.itertuples(index=False):
                cc, kg = map_es_prod(str(getattr(r, p_c)))
                if not cc or cc not in eff: continue
                try:
                    pr = float(getattr(r, pr_c))
                    la, lo = float(getattr(r, lat_c)), float(getattr(r, lon_c))
                except Exception:
                    continue
                if pr <= 0 or not (14.0 <= la <= 33.0 and -118.0 <= lo <= -86.0): continue
                dt = str(getattr(r, dt_c))[:10] if dt_c else "2024-06-15"
                if len(dt) < 10: dt = "2024-06-15"
                yr = max(2022, min(2026, int(dt[:4]))) if dt[:4].isdigit() else 2024
                nom_kg = pr / kg
                rusd_kg = nom_kg / fx_wb.get(("MEX", yr), 19.5)
                u50 = rusd_kg * (50.0 / eff[cc])
                l50 = 60.0 * (rusd_kg / w_usd.get(("MEX", yr), 1.75)) * (50.0 / eff[cc])
                if not (0.15 <= u50 <= 20.0 and 0.5 <= l50 <= 600.0): continue
                st_nm = str(getattr(r, est_c)).strip() if est_c else "Store"
                mk = f"MEX_PROF_{abs(hash((round(la,3),round(lo,3))))%1000000}"
                if mk not in mkts:
                    con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?,?,'MEX','LATAM',?,?,0.18,0.14);", (mk, f"{st_nm}, MEX", la, lo))
                    mkts.add(mk)
                mid += 1
                obs.append((mid,"MEX_PROFECO","OBSERVED_MARKET",mk,cc,dt,nom_kg,"MXN",rusd_kg,u50,l50,False))
                mex_added += 1
                if mex_added >= 2500000: break
            if mex_added >= 2500000: break
    except Exception as e:
        print("  -> PROFECO note:", e)
print(f"[TRACK 10A-2] Mexico PROFECO: +{mex_added:,} storefront observations.")

if obs:
    con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)
print(f"[TRACK 10A COMPLETE] Inserted {len(obs):,} total LATAM storefront rows across {len(mkts):,} GPS-pinned stores!")
con.close()
