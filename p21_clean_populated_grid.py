import json, duckdb, h3
from pathlib import Path

p_grid = Path("public/data/h3_weather_grid.json")
grid = json.loads(p_grid.read_text())

for key, min_pop in (("res4_synoptic", 500), ("res6_regional", 100)):
    existing = {r["h"]: r for r in grid[key]}
    added = {}
    # Expand populated agricultural corridors 1 extra ring beyond existing k-rings
    for h, r in existing.items():
        if r.get("pop", 0) < min_pop * 4:
            continue
        for nb in h3.grid_disk(h, 1):
            if nb not in existing and nb not in added:
                nr = dict(r)
                nr["h"] = nb
                nr["i"] = 2
                nr["pop"] = int(r["pop"] * 0.22)
                nr["tf"] = round(min(0.95, r.get("tf", 0.2) + 0.08), 2)
                added[nb] = nr
    merged = [r for r in list(existing.values()) + list(added.values()) if r.get("pop", 0) >= min_pop]
    grid[key] = merged

p_grid.write_text(json.dumps(grid, separators=(",",":"), allow_nan=False))
print(f"[TRACK 21 COMPLETE] Res-4 tiles: {len(grid['res4_synoptic']):,} | Res-6 tiles: {len(grid['res6_regional']):,} | File: {p_grid.stat().st_size/1024:.1f} KB")
