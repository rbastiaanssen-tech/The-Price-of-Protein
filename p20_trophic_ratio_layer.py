import json, duckdb
from pathlib import Path

con = duckdb.connect("protein_atlas.duckdb", read_only=True)
p_grid = Path("public/data/h3_weather_grid.json")
grid = json.loads(p_grid.read_text())

PLANT_CODES = ("LENTILS_DRY","BEANS_DRY","SOY_TOFU","WHEAT_FLOUR","RICE_MILLED","MAIZE_MEAL")
ANIMAL_CODES = ("EGGS_CHIX","MILK_FLUID","CHEESE_HARD","POULTRY_CHIX","PORK_FRESH","BEEF_FRESH","MUTTON_FRESH","FISH_PELAGIC_SMALL","FISH_AQUACULTURE","FISH_PREDATOR_HIGH")

df = con.execute(f"""
    SELECT m.country_iso3,
           MIN(CASE WHEN f.commodity_code IN {PLANT_CODES} THEN f.usd_per_50g_diaas END) AS min_plant,
           MIN(CASE WHEN f.commodity_code IN {ANIMAL_CODES} THEN f.usd_per_50g_diaas END) AS min_animal
    FROM fact_protein_observations f
    JOIN dim_markets m USING(market_id)
    WHERE f.is_outlier_mad = FALSE AND f.usd_per_50g_diaas > 0.04
    GROUP BY 1;
""").df()

iso_trophic = {}
for r in df.itertuples(index=False):
    pp = float(r.min_plant) if r.min_plant is not None else 0.45
    ap = float(r.min_animal) if r.min_animal is not None else pp * 1.85
    tr = round(max(0.65, min(6.5, ap / max(0.04, pp))), 2)
    iso_trophic[r.country_iso3] = (round(pp, 3), round(ap, 3), tr)

for key in ("res4_synoptic", "res6_regional"):
    for r in grid[key]:
        pp, ap, tr = iso_trophic.get(r["c"], (0.45, 0.85, 1.89))
        r["pp"] = pp
        r["ap"] = ap
        r["tr"] = round(tr * (0.94 if r["i"] == 1 else 1.0), 2)

grid["meta"]["schema"] += ", pp:min_plant_usd, ap:min_animal_usd, tr:animal_to_plant_ratio"
p_grid.write_text(json.dumps(grid, separators=(",",":"), allow_nan=False))
print(f"[TRACK 20 COMPLETE] Added Plant (pp), Animal (ap), and Trophic Ratio (tr) to {p_grid} ({p_grid.stat().st_size/1024:.1f} KB)")
con.close()
