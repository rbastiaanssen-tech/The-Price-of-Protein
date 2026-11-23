import json, math, duckdb, numpy as np, pandas as pd
from pathlib import Path
import h3

con = duckdb.connect("protein_atlas.duckdb")

# 1. Ensure regime_class = 'MARINE' on the 3 species rows and print Track 17B summary
con.execute("""
    UPDATE dim_commodities
    SET regime_class = 'MARINE',
        edible_fraction = CASE commodity_code
            WHEN 'FISH_PELAGIC_SMALL' THEN 0.72
            WHEN 'FISH_AQUACULTURE'   THEN 0.58
            ELSE 0.65
        END,
        crude_protein_g_kg = CASE commodity_code
            WHEN 'FISH_PELAGIC_SMALL' THEN 245.0
            WHEN 'FISH_AQUACULTURE'   THEN 265.0
            ELSE 285.0
        END,
        diaas_score = CASE commodity_code
            WHEN 'FISH_PELAGIC_SMALL' THEN 1.04
            WHEN 'FISH_AQUACULTURE'   THEN 1.00
            ELSE 1.06
        END
    WHERE commodity_code IN ('FISH_PELAGIC_SMALL', 'FISH_AQUACULTURE', 'FISH_PREDATOR_HIGH');
""")

# Recompute usd_per_50g_diaas and labor_min_50g_diaas using exact generated eff_protein_g_kg
con.execute("""
    UPDATE fact_protein_observations AS f
    SET usd_per_50g_diaas = ROUND(f.real_usd_per_kg * (50.0 / c.eff_protein_g_kg), 4),
        labor_min_50g_diaas = ROUND(f.labor_min_50g_diaas * ((f.real_usd_per_kg * (50.0 / c.eff_protein_g_kg)) / NULLIF(f.usd_per_50g_diaas, 0)), 2)
    FROM dim_commodities AS c
    WHERE f.commodity_code = c.commodity_code
      AND f.commodity_code IN ('FISH_PELAGIC_SMALL', 'FISH_AQUACULTURE', 'FISH_PREDATOR_HIGH')
      AND f.is_outlier_mad = FALSE;
""")

summary = con.execute("""
    SELECT c.commodity_code, c.commodity_name, ROUND(c.eff_protein_g_kg, 1) AS eff_g_kg,
           COUNT(*) AS n_obs,
           ROUND(MEDIAN(f.usd_per_50g_diaas), 3) AS med_usd_50g,
           ROUND(MEDIAN(f.labor_min_50g_diaas), 1) AS med_labor_min
    FROM fact_protein_observations f
    JOIN dim_commodities c USING(commodity_code)
    WHERE f.is_outlier_mad = FALSE AND c.regime_class = 'MARINE'
    GROUP BY 1, 2, 3 ORDER BY med_usd_50g;
""").df()
print("[TRACK 17B COMPLETE] Species-Level Marine & Aquaculture Tiers:")
print(summary.to_string(index=False))

# 2. Track 17C: Terrain- & Corridor-Aware Rural Ripple Surface
print("\n[TRACK 17C] Computing Topographic & Corridor-Friction Ripples across H3 Grid...")
p_grid = Path("public/data/h3_weather_grid.json")
grid = json.loads(p_grid.read_text())

def cell_latlon(h_idx):
    return h3.cell_to_latlng(h_idx) if hasattr(h3, "cell_to_latlng") else h3.h3_to_geo(h_idx)

def h3_ring(cell, k):
    return list(h3.grid_disk(cell, k)) if hasattr(h3, "grid_disk") else list(h3.k_ring(cell, k))

# Topographic & Transport Friction Index (0.0 = flat coastal/river corridor, 1.0 = high-altitude mountain/arid interior)
# Combines major orogenic belts (Andes, Himalayas/Tibet, Ethiopian/East African Rift, Alps/Caucasus, Rockies) + latitude/continentality
def terrain_friction(lat, lon):
    f = 0.15
    # Himalayas / Tibetan Plateau / Pamirs
    if 25.0 <= lat <= 42.0 and 68.0 <= lon <= 104.0:
        d_core = math.hypot((lat - 33.0)/7.0, (lon - 86.0)/16.0)
        f += max(0.0, 0.85 * math.exp(-d_core**2))
    # Andes Cordillera
    if -45.0 <= lat <= 10.0 and -79.0 <= lon <= -64.0:
        d_andes = abs(lon - (-71.5 + 0.12 * lat)) / 3.5
        f += max(0.0, 0.78 * math.exp(-d_andes**2))
    # Ethiopian Highlands & East African Rift
    if -12.0 <= lat <= 14.0 and 29.0 <= lon <= 42.0:
        d_rift = math.hypot((lat - 4.0)/10.0, (lon - 37.5)/4.5)
        f += max(0.0, 0.65 * math.exp(-d_rift**2))
    # Rockies / Sierra Madre
    if 18.0 <= lat <= 55.0 and -122.0 <= lon <= -103.0:
        d_rock = abs(lon - (-111.0)) / 5.5
        f += max(0.0, 0.55 * math.exp(-d_rock**2))
    # Alps / Zagros / Caucasus
    if 32.0 <= lat <= 48.0 and 6.0 <= lon <= 54.0:
        d_alp = math.hypot((lat - 42.0)/5.0, (lon - 28.0)/22.0)
        f += max(0.0, 0.45 * math.exp(-d_alp**2))
    return round(min(1.0, f), 3)

for key in ("res4_synoptic", "res6_regional"):
    cells = {r["h"]: r for r in grid[key]}
    # Precompute terrain friction for all cells in this layer
    tf_map = {}
    for h_id in cells:
        la, lo = cell_latlon(h_id)
        tf_map[h_id] = terrain_friction(la, lo)

    for h_id, row in cells.items():
        tf = tf_map[h_id]
        row["tf"] = tf  # Topographic/transport friction index [0..1]
        if row["i"] == 1:
            # In high-friction mountain/remote cells, rural prices stay closer to town retail (less farm surplus arbitrage + steep transport)
            # In low-friction plains/valleys, own-farm shadow cost drops sharply (~35% discount)
            direct_nbrs = [cells[nb] for nb in h3_ring(h_id, 2) if nb in cells and cells[nb]["i"] == 0]
            if direct_nbrs:
                anchor = direct_nbrs[0]
                # Asymmetric terrain gradient: steep terrain adds transport penalty (+0.22 * tf), flat plains allow full farm-gate discount
                terrain_mult = 0.76 + 0.34 * tf
                row["f"] = round(anchor["u"] * terrain_mult, 3)
                row["fl"] = round(anchor["l"] * terrain_mult, 1)

grid["meta"]["schema"] = "h:h3, n:obs, m:mkts, c:iso3, u:retail_usd, l:retail_min, f:farmgate_usd, fl:farmgate_min, s:synergy_usd, lp:exact_9aa_lp_usd, aa:binding_amino_acid, sv:seasonal_spike_pct, w:wage_share_p50, w20:wage_share_p20, tf:terrain_friction, i:interp"
p_grid.write_text(json.dumps(grid, separators=(",",":"), allow_nan=False))
print(f"[TRACK 17C COMPLETE] Embedded Topographic & Corridor Friction (`tf`) into {p_grid} ({p_grid.stat().st_size/1024:.1f} KB)")
con.close()
