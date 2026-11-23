import json
import math
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

p_grid = Path("public/data/h3_weather_grid.json")
grid = sanitize(json.loads(p_grid.read_text(encoding="utf-8")))

# Keep ALL directly measured Res-4 cells (i=0)
all_base = grid["res4_synoptic"]
measured = [dict(r, i=0) for r in all_base if r.get("n", 0) > 0]
existing = {r["h"]: r for r in measured}

# Expand outward ring-by-ring across ALL terrestrial land (including deserts/steppes/plains)
# so continental interiors are 100% solid with zero interior holes
frontier = set(existing.keys())
for _ in range(22):
    next_frontier = set()
    for c in frontier:
        for nb in h3_ring(c, 1):
            if nb not in existing and nb not in next_frontier:
                la, lo = h3_xy(nb)
                if -56.0 <= la <= 68.0 and globe.is_land(la, lo):
                    next_frontier.add(nb)
    if not next_frontier:
        break
    if len(existing) + len(next_frontier) > 9400:
        # Fill from lowest absolute latitude upward so inhabited continents & deserts stay 100% solid
        ordered = sorted(next_frontier, key=lambda cell: abs(h3_xy(cell)[0]))
        next_frontier = set(ordered[:max(0, 9400 - len(existing))])
    if not next_frontier:
        break

    cur_list = list(existing.values())
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
        nr["pop"] = max(100, int(round(wavg("pop", 400, 0) * 0.25)))
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
        existing[nb] = nr

    frontier = next_frontier

final_cells = list(existing.values())
max_pw = max((r.get("pop", 0) for r in final_cells), default=1) or 1
for r in final_cells:
    r["pw"] = round(math.log1p(r.get("pop", 0)) / math.log1p(max_pw), 2)

# Enforce strictly < 1,980 KB by trimming outermost sub-arctic latitude cells first (never interior deserts/plains)
def pack_json(cells):
    meas_c = sum(1 for r in cells if r.get("i") == 0)
    grid["res4_synoptic"] = cells
    grid["res3_synoptic"] = []
    grid["res5_regional"] = []
    grid["res6_regional"] = []
    grid["meta"]["h3_res4_cells"] = len(cells)
    grid["meta"]["h3_res4_measured_cells"] = meas_c
    grid["meta"]["h3_res4_interpolated_cells"] = len(cells) - meas_c
    grid["meta"]["cliff_population_p20"] = int(sum(r.get("pop", 0) for r in cells if r.get("w20", 0) >= 25.0))
    grid["meta"]["cliff_population_p50"] = int(sum(r.get("pop", 0) for r in cells if r.get("w", 0) >= 25.0))
    return json.dumps(grid, separators=(",", ":"), allow_nan=False).encode("utf-8")

out_bytes = pack_json(final_cells)
while len(out_bytes) > 1_980_000:
    meas_only = [r for r in final_cells if r.get("i") == 0]
    interp_only = [r for r in final_cells if r.get("i") == 1]
    interp_only.sort(key=lambda r: abs(h3_xy(r["h"])[0]))
    final_cells = meas_only + interp_only[:max(0, len(interp_only) - 250)]
    out_bytes = pack_json(final_cells)

p_grid.write_bytes(out_bytes)
print(f"[READY TO ATTACH] {p_grid} -> {len(out_bytes) / 1024:.1f} KB | Total Res-4: {len(final_cells):,} (Measured i=0: {sum(1 for r in final_cells if r['i']==0):,}, Interpolated i=1: {sum(1 for r in final_cells if r['i']==1):,})")
