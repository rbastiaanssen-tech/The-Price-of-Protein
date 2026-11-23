from pathlib import Path
src = Path("phase9a_protein_isolates.py").read_text()

idx = src.find("def wb(ind):")
header = '''import gzip, io, json, re, urllib.request, duckdb

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'OPEN_PRICES_ISOLATES';")

ISOLATE_DEFS = [
    ("WHEY_ISOLATE", "Whey Protein Isolate/Concentrate Powder", "PROCESSED_ISOLATE", True,  1.0, 800.0, 1.0, 1.09, 1.00, 3800.0, 730, 0.94, "FAO 2013 DIAAS / OpenPrices"),
    ("SOY_ISOLATE",  "Soy Protein Isolate Powder",              "PROCESSED_ISOLATE", False, 1.0, 850.0, 1.0, 0.98, 0.98, 3700.0, 730, 0.94, "FAO 2013 DIAAS / OpenPrices"),
    ("PEA_ISOLATE",  "Pea / Plant Protein Isolate Powder",      "PROCESSED_ISOLATE", False, 1.0, 800.0, 1.0, 0.82, 0.82, 3750.0, 730, 0.94, "FAO 2013 DIAAS / OpenPrices"),
]
for row in ISOLATE_DEFS:
    con.execute("INSERT OR REPLACE INTO dim_commodities (commodity_code, commodity_name, regime_class, is_animal_source, edible_fraction, crude_protein_g_kg, prep_retention, diaas_score, diaas_capped, kcal_per_kg, shelf_life_days, dry_matter_fraction, reference_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?);", row)

eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())
'''

body = src[idx:]
if "mid = con.execute" not in body:
    body = body.replace("obs, mkts = [], set()", "mid = con.execute('SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;').fetchone()[0]\nobs, mkts = [], set()")

Path("phase9a_protein_isolates.py").write_text(header + body)
print("Patched phase9a_protein_isolates.py successfully.")
