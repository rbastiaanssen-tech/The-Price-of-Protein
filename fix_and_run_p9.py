from pathlib import Path
import duckdb

con = duckdb.connect("protein_atlas.duckdb", read_only=True)
codes = set(r[0] for r in con.execute("SELECT commodity_code FROM dim_commodities;").fetchall())
can_obs = con.execute("SELECT f.obs_year, MEDIAN(f.real_usd_per_kg / (f.labor_min_50g_diaas / 60.0 / (50.0 / c.eff_protein_g_kg))) AS usd_hr FROM fact_protein_observations f JOIN dim_markets m USING(market_id) JOIN dim_commodities c USING(commodity_code) WHERE m.country_iso3='CAN' AND f.labor_min_50g_diaas > 0 GROUP BY 1 ORDER BY 1;").fetchall()
con.close()
print("Valid codes:", sorted(codes))
print("Verified CAN USD/hr from DB:", can_obs[-5:])

s = Path("phase9_complete_atlas.py").read_text()
if "MILK_FRESH" not in codes and "MILK_FLUID" in codes:
    s = s.replace('"MILK_FRESH"', '"MILK_FLUID"')
if "BEEF_BONELESS" not in codes and "BEEF_FRESH" in codes:
    s = s.replace('"BEEF_BONELESS"', '"BEEF_FRESH"')
if "DRY_BEANS" not in codes and "BEANS_DRY" in codes:
    s = s.replace('"DRY_BEANS"', '"BEANS_DRY"')

inject = '''
for yr_c, wh_c in %r:
    w_usd[('CAN', int(yr_c))] = float(wh_c)
if %r:
    w_usd_lat['CAN'] = float(%r[-1][1])
''' % (can_obs, can_obs, can_obs)

s = s.replace('print(f"[PHASE 9] Loaded USD wages', inject + '\nprint(f"[PHASE 9] Loaded USD wages')
s = s.replace('u50, l50 = rusd_kg * (50.0 / eff[cc])', 'if cc not in eff: continue\n    u50, l50 = rusd_kg * (50.0 / eff[cc])')
Path("phase9_complete_atlas.py").write_text(s)
print("Patched phase9_complete_atlas.py cleanly.")
