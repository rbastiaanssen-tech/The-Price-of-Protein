import json
import math
import subprocess
import sys
import h3
import numpy as np
from pathlib import Path
from scipy.spatial import cKDTree
from global_land_mask import globe

def h3_xy(cell):
    return h3.cell_to_latlng(cell) if hasattr(h3, "cell_to_latlng") else h3.h3_to_geo(cell)

def h3_ring(cell, k):
    return list(h3.grid_disk(cell, k)) if hasattr(h3, "grid_disk") else list(h3.k_ring(cell, k))

def sanitize(obj):
    if isinstance(obj, float):
        return 0.0 if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items() if k != "boundary"}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    return obj

# Step 1: Regenerate the full untruncated Res-4 (24,417+) and Res-6 (36,109+) enriched layers
base_pipeline = [
    "p15b_rural_wedge_h3_engine.py",
    "p16_publication_boundary.py",
    "p17b_and_17c_terrain_ripples.py",
    "p18b_population_cultural_subsistence_grid.py",
    "p19_close_continental_gaps.py",
]
for script in base_pipeline:
    if Path(script).exists():
        print(f"Running {script}...")
        subprocess.run([sys.executable, script], check=True)

p_grid = Path("public/data/h3_weather_grid.json")
grid = sanitize(json.loads(p_grid.read_text(encoding="utf-8")))

# Step 2: Preserve ALL 24,417+ Res-4 cells (including full 7.80B global population) & fill any remaining terrestrial land gaps
existing_r4 = {}
for r in grid.get("res4_synoptic", []):
    rec = dict(r)
    rec["i"] = 0 if rec.get("n", 0) > 0 else 1
    existing_r4[rec["h"]] = rec

print(f"Loaded {len(existing_r4):,} full-population H3 Res-4 cells (Pop: {sum(r.get('pop', 0) for r in existing_r4.values()) / 1e9:.2f}B).")

frontier = set(existing_r4.keys())
for _ in range(25):
    next_frontier = set()
    for c in frontier:
        for nb in h3_ring(c, 1):
            if nb not in existing_r4 and nb not in next_frontier:
                la, lo = h3_xy(nb)
                if -56.0 <= la <= 72.0 and globe.is_land(la, lo):
                    next_frontier.add(nb)
    if not next_frontier:
        break

    cur_list = list(existing_r4.values())
    cur_rad = np.radians([h3_xy(r["h"]) for r in cur_list])
    cur_xyz = np.column_stack([
        np.cos(cur_rad[:, 0]) * np.cos(cur_rad[:, 1]),
        np.cos(cur_rad[:, 0]) * np.sin(cur_rad[:, 1]),
        np.sin(cur_rad[:, 0]),
    ])
    tree = cKDTree(cur_xyz)

    cand_list = list(next_frontier)
    cand_rad = np.radians([h3_xy(c) for c in cand_list])
    cand_xyz = np.column_stack([
        np.cos(cand_rad[:, 0]) * np.cos(cand_rad[:, 1]),
        np.cos(cand_rad[:, 0]) * np.sin(cand_rad[:, 1]),
        np.sin(cand_rad[:, 0]),
    ])
    dists, idxs = tree.query(cand_xyz, k=min(6, len(cur_list)))
    dist_km = np.maximum(20.0, dists * 6371.0)

    for idx_c, nb in enumerate(cand_list):
        nn = idxs[idx_c]
        wts = 1.0 / (dist_km[idx_c] ** 1.6)
        tw = float(np.sum(wts))
        anchor = cur_list[nn[0]]

        def wavg(k, default=0.0, nd=2):
            vals = [float(cur_list[j].get(k, default) or default) for j in nn]
            return round(sum(v * w for v, w in zip(vals, wts)) / tw, nd)

        nr = dict(anchor)
        nr["h"] = nb
        nr["n"] = 0
        nr["m"] = 0
        nr["i"] = 1
        nr["pop"] = max(50, int(round(wavg("pop", 300, 0) * 0.15)))
        nr["u"] = wavg("u", 1.0, 2)
        nr["l"] = wavg("l", 30.0, 1)
        nr["f"] = wavg("f", 0.7, 2)
        nr["fl"] = wavg("fl", 20.0, 1)
        if "s" in anchor:
            nr["s"] = wavg("s", 0.8, 2)
        nr["lp"] = wavg("lp", 0.9, 2)
        nr["lp_v"] = wavg("lp_v", 1.0, 2)
        nr["lp_h"] = wavg("lp_h", 0.9, 2)
        nr["sv"] = wavg("sv", 12.0, 1)
        nr["w"] = wavg("w", 10.0, 1)
        nr["w20"] = wavg("w20", 20.0, 1)
        nr["tf"] = round(min(0.95, wavg("tf", 0.25, 2) + 0.04), 2)
        nr["sub"] = wavg("sub", 5.0, 1)
        existing_r4[nb] = nr

    frontier = next_frontier

final_r4 = list(existing_r4.values())
max_pw4 = max((r.get("pop", 0) for r in final_r4), default=1) or 1
for r in final_r4:
    r["pw"] = round(math.log1p(r.get("pop", 0)) / math.log1p(max_pw4), 2)

# Step 3: Preserve ALL 36,109+ Res-6 regional cells with accurate i=0 / i=1 flags and pw weights
final_r6 = []
for r in grid.get("res6_regional", []):
    rec = dict(r)
    rec["i"] = 0 if rec.get("n", 0) > 0 else 1
    final_r6.append(rec)

max_pw6 = max((r.get("pop", 0) for r in final_r6), default=1) or 1
for r in final_r6:
    r["pw"] = round(math.log1p(r.get("pop", 0)) / math.log1p(max_pw6), 2)

n_meas4 = sum(1 for r in final_r4 if r.get("i") == 0)
n_interp4 = sum(1 for r in final_r4 if r.get("i") == 1)
total_pop = sum(r.get("pop", 0) for r in final_r4)
cliff_p50 = sum(r.get("pop", 0) for r in final_r4 if r.get("w", 0) >= 25.0)
cliff_p20 = sum(r.get("pop", 0) for r in final_r4 if r.get("w20", 0) >= 25.0)

grid["res4_synoptic"] = final_r4
grid["res6_regional"] = final_r6
grid["res3_synoptic"] = []
grid["res5_regional"] = []
grid["meta"]["h3_res4_cells"] = len(final_r4)
grid["meta"]["h3_res4_measured_cells"] = n_meas4
grid["meta"]["h3_res4_interpolated_cells"] = n_interp4
grid["meta"]["h3_res6_cells"] = len(final_r6)
grid["meta"]["total_population"] = int(total_pop)
grid["meta"]["cliff_population_p50"] = int(cliff_p50)
grid["meta"]["cliff_population_p20"] = int(cliff_p20)

out_bytes = json.dumps(grid, separators=(",", ":"), allow_nan=False).encode("utf-8")
p_grid.write_bytes(out_bytes)
print(f"[COMPLETE] {p_grid} -> {len(out_bytes) / (1024*1024):.2f} MB")
print(f"Res-4 cells: {len(final_r4):,} (Measured i=0: {n_meas4:,}, Interpolated i=1: {n_interp4:,})")
print(f"Res-6 regional cells: {len(final_r6):,}")
print(f"Global Population: {total_pop / 1e9:.2f}B | P50 >25% Cliff: {cliff_p50 / 1e9:.2f}B | P20 >25% Cliff: {cliff_p20 / 1e9:.2f}B")
