import json, math
from pathlib import Path

p_grid = Path("public/data/h3_weather_grid.json")
grid = json.loads(p_grid.read_text())

for key, min_pop, max_log in (("res4_synoptic", 500, 7.2), ("res6_regional", 100, 6.2)):
    cleaned = []
    for r in grid[key]:
        pop = int(r.get("pop", 0))
        if pop < min_pop:
            continue
        pw = round(max(0.25, min(1.0, (math.log10(max(10, pop)) - 2.0) / (max_log - 2.0))), 2)
        r["pw"] = pw
        r.setdefault("tr", 1.85)
        r.setdefault("pp", round(r.get("u", 0.5) * 0.65, 3))
        r.setdefault("ap", round(r.get("u", 0.5) * 1.25, 3))
        r.setdefault("aa", "LYS")
        r.setdefault("sub", 8 if r.get("i", 0) == 1 else 0)
        cleaned.append(r)
    grid[key] = cleaned

grid["meta"]["schema"] += ", pw:pop_visual_weight"
p_grid.write_text(json.dumps(grid, separators=(",",":"), allow_nan=False))
print(f"[TRACK 25 COMPLETE] Normalized visual weights (`pw`) across {len(grid['res4_synoptic']):,} Res-4 & {len(grid['res6_regional']):,} Res-6 populated tiles ({p_grid.stat().st_size/1024:.1f} KB)")
