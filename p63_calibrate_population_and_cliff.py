import json
from pathlib import Path

p_grid = Path("public/data/h3_weather_grid.json")
grid = json.loads(p_grid.read_text(encoding="utf-8"))

r4 = grid["res4_synoptic"]
r6 = grid["res6_regional"]

# 1. Zero out synthetic phantom population on pure land-fill cells (pop <= 500 and i == 1)
# so Sahara/Siberia/wilderness hexes stay visually solid without inflating world population
for r in r4:
    if r.get("i") == 1 and r.get("pop", 0) <= 500:
        r["pop"] = 0

# 2. Harmonize both Res-4 and Res-6 total population to exact 2026 global population (8.20B)
TARGET_WORLD_POP = 8_200_000_000

sum_r4 = sum(r.get("pop", 0) for r in r4) or 1
scale_r4 = TARGET_WORLD_POP / sum_r4
for r in r4:
    if r.get("pop", 0) > 0:
        r["pop"] = int(round(r["pop"] * scale_r4))

sum_r6 = sum(r.get("pop", 0) for r in r6) or 1
scale_r6 = TARGET_WORLD_POP / sum_r6
for r in r6:
    if r.get("pop", 0) > 0:
        r["pop"] = int(round(r["pop"] * scale_r6))

# 3. Match UI cliff threshold strictly (> 25.0, treating exact 25.0% as below the cliff)
total_pop_r4 = sum(r.get("pop", 0) for r in r4)
total_pop_r6 = sum(r.get("pop", 0) for r in r6)
cliff_p50_r4 = sum(r.get("pop", 0) for r in r4 if r.get("w", 0) > 25.0)
cliff_p20_r4 = sum(r.get("pop", 0) for r in r4 if r.get("w20", 0) > 25.0)
cliff_p50_r6 = sum(r.get("pop", 0) for r in r6 if r.get("w", 0) > 25.0)

grid["meta"]["total_population"] = int(total_pop_r4)
grid["meta"]["total_population_res6"] = int(total_pop_r6)
grid["meta"]["cliff_population_p50"] = int(cliff_p50_r4)
grid["meta"]["cliff_population_p20"] = int(cliff_p20_r4)
grid["meta"]["cliff_threshold_operator"] = ">"

out_bytes = json.dumps(grid, separators=(",", ":"), allow_nan=False).encode("utf-8")
p_grid.write_bytes(out_bytes)

print(f"[CALIBRATED] {p_grid} -> {len(out_bytes) / (1024*1024):.2f} MB")
print(f"Res-4 Pop: {total_pop_r4 / 1e9:.3f}B | Res-6 Pop: {total_pop_r6 / 1e9:.3f}B")
print(f"P50 Cliff (>25.0%): Res-4 = {cliff_p50_r4 / 1e9:.3f}B ({100*cliff_p50_r4/total_pop_r4:.1f}%) | Res-6 = {cliff_p50_r6 / 1e9:.3f}B")
print(f"P20 Cliff (>25.0%): Res-4 = {cliff_p20_r4 / 1e9:.3f}B ({100*cliff_p20_r4/total_pop_r4:.1f}%)")
