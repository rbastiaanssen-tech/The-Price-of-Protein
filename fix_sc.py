from pathlib import Path
s = Path("p9.py").read_text()
i0 = s.index("zraw = urllib.request.urlopen")
i1 = s.index('print(f"[4/4]')
head, tail = s[:i0], s[i1:]
mid_block = '''import subprocess
can_added = 0
try:
    zraw = subprocess.check_output(["curl","-sL","-A","Mozilla/5.0","https://www150.statcan.gc.ca/n1/tbl/csv/18100245-eng.zip"], timeout=60)
    with zipfile.ZipFile(io.BytesIO(zraw)) as zf:
        sc = pd.read_csv(zf.open(next(n for n in zf.namelist() if n.endswith(".csv") and "MetaData" not in n)), usecols=["REF_DATE","GEO","Products","VALUE"], low_memory=False)
    sc = sc[sc["GEO"].isin(CAN_GEO) & sc["Products"].isin(CAN_PROD)].dropna(subset=["VALUE"])
    sc["val"] = pd.to_numeric(sc["VALUE"], errors="coerce")
    for r in sc[sc["val"] > 0].itertuples(index=False):
        dt = f"{str(r.REF_DATE)[:7]}-15"
        yr = int(dt[:4])
        cc, kg = CAN_PROD[r.Products]
        mk, mlabel, lat, lon, car = CAN_GEO[r.GEO]
        fx = fx_wb.get(("CAN", yr), fx_lat["CAN"])
        nom_kg = float(r.val) / kg
        rusd_kg = nom_kg / fx
        wh_u = w_usd.get(("CAN", yr), w_usd_lat["CAN"])
        u50, l50 = rusd_kg * (50.0 / eff[cc]), 60.0 * (rusd_kg / wh_u) * (50.0 / eff[cc])
        if not (0.10 <= u50 <= 25.0): continue
        if mk not in mkts:
            con.execute("INSERT OR REPLACE INTO dim_markets VALUES (?,?,'CAN','NORTH_AMERICA',?,?,?,0.12);", (mk, mlabel, lat, lon, car))
            mkts.add(mk)
        mid += 1
        obs.append((mid,"STATCAN_RETAIL","OBSERVED_MARKET",mk,cc,dt,nom_kg,"CAD",rusd_kg,u50,l50,False))
        can_added += 1
except Exception as e:
    print("[4/4] StatCan skipped:", e)
'''
Path("p9.py").write_text(head + mid_block + tail)
print("Patched p9.py cleanly!")
