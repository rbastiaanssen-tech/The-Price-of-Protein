import json
import subprocess
import sys
import h3
import numpy as np
from pathlib import Path
from scipy.spatial import cKDTree
from global_land_mask import globe

def to_h3(lat, lon, res):
    return h3.latlng_to_cell(lat, lon, res) if hasattr(h3, "latlng_to_cell") else h3.geo_to_h3(lat, lon, res)

def h3_xy(cell):
    return h3.cell_to_latlng(cell) if hasattr(h3, "cell_to_latlng") else h3.h3_to_geo(cell)

def h3_ring(cell, k):
    return list(h3.grid_disk(cell, k)) if hasattr(h3, "grid_disk") else list(h3.k_ring(cell, k))

# Step 1: Run your exact Res-4 full-schema enrichment pipeline (skipping p21's min_pop deletion!)
pipeline = [
    "p15b_rural_wedge_h3_engine.py",
    "p16_publication_boundary.py",
    "p17b_and_17c_terrain_ripples.py",
    "p18b_population_cultural_subsistence_grid.py",
    "p19_close_continental_gaps.py",
    "p20_trophic_ratio_layer.py",
    "p23_full_eurasia_latam_maghreb_grid.py",
    "p25_visual_weights.py",
    "p26_stigler_amino_arbitrage.py",
]

for script in pipeline:
    if Path(script).exists():
        print(f"Running {script}...")
        subprocess.run([sys.executable, script], check=True)

p_grid = Path("public/data/h3_weather_grid.json")
grid = json.loads(p_grid.read_text(encoding="utf-8"))

# Verify true H3 resolution of base cells
base_cells = grid.get("res4_synoptic", [])
if not base_cells:
    raise RuntimeError("res4_synoptic is empty after base pipeline!")

sample_res = h3.get_resolution(base_cells[0]["h"]) if hasattr(h3, "get_resolution") else h3.h3_get_resolution(base_cells[0]["h"])
print(f"Base res4_synoptic count: {len(base_cells):,} | Verified H3 resolution: {sample_res}")

# Index existing Res-4 cells and mark measured vs interpolated accurately
existing = {}
for r in base_cells:
    rec = dict(r)
    rec["i"] = 0 if rec.get("n", 0) > 0 else 1
    existing[rec["h"]] = rec

# Step 2: Expand iteratively across terrestrial land at H3 Resolution 4 (up to 16 rings = ~350km reach across interior plains/mountains)
# until all continental gaps are filled while staying < 1.92 MB
frontier = set(existing.keys())
for ring_step in range(1, 18):
    next_frontier = set()
    for c in frontier:
        for nb in h3_ring(c, 1):
            if nb not in existing and nb not in next_frontier:
                la, lo = h3_xy(nb)
                if -56.0 <= la <= 71.0 and globe.is_land(la, lo):
                    next_frontier.add(nb)
    if not next_frontier:
        break
    # Stop expansion if approaching Lovable's 2MB limit (~15,600 full-schema cells)
    if len(existing) + len(next_frontier) > 15600:
        # Sort candidates by proximity to lower latitudes (inhabited corridors)
        ordered = sorted(next_frontier, key=lambda cell: abs(h3_xy(cell)[0]))
        allowed = 15600 - len(existing)
        next_frontier = set(ordered[:max(0, allowed)])

    # Build KDTree over current populated Res-4 cells for fast IDW interpolation of all fields
    cur_list = list(existing.values())
    cur_coords = np.array([h3_xy(r["h"]) for r in cur_list])
    cur_rad = np.radians(cur_coords)
    cur_xyz = np.column_stack([
        np.cos(cur_rad[:, 0]) * np.cos(cur_rad[:, 1]),
        np.cos(cur_rad[:, 0]) * np.sin(cur_rad[:, 1]),
        np.sin(cur_rad[:, 0]),
    ])
    tree = cKDTree(cur_xyz)

    cand_list = list(next_frontier)
    cand_coords = np.array([h3_xy(c) for c in cand_list])
    cand_rad = np.radians(cand_coords)
    cand_xyz = np.column_stack([
        np.cos(cand_rad[:, 0]) * np.cos(cand_rad[:, 1]),
        np.cos(cand_rad[:, 0]) * np.sin(cand_rad[:, 1]),
        np.sin(cand_rad[:, 0]),
    ])
    dists, idxs = tree.query(cand_xyz, k=min(6, len(cur_list)))
    dist_km = np.maximum(20.0, dists * 6371.0)

    for idx_c, nb in enumerate(cand_list):
        nn_indices = idxs[idx_c]
        wts = 1.0 / (dist_km[idx_c] ** 1.6)
        tw = float(np.sum(wts))
        anchor = cur_list[nn_indices[0]]

        def wavg(key, default=0.0, ndigits=2):
            vals = [float(cur_list[j].get(key, default) or default) for j in nn_indices]
            return round(sum(v * w for v, w in zip(vals, wts)) / tw, ndigits)

        nr = dict(anchor)
        nr["h"] = nb
        nr["n"] = 0
        nr["m"] = 0
        nr["i"] = 1  # Explicitly flagged as interpolated land-gap cell
        nr["pop"] = max(250, int(round(wavg("pop", 500, 0) * 0.35)))
        nr["u"] = wavg("u", anchor.get("u", 1.0), 3)
        nr["l"] = wavg("l", anchor.get("l", 30.0), 1)
        nr["f"] = wavg("f", anchor.get("f", 0.7), 3)
        nr["fl"] = wavg("fl", anchor.get("fl", 20.0), 1)
        if "s" in anchor:
            nr["s"] = wavg("s", anchor.get("s", 0.8), 3)
        nr["lp"] = wavg("lp", anchor.get("lp", 0.9), 3)
        nr["lp_v"] = wavg("lp_v", anchor.get("lp_v", 1.0), 3)
        nr["lp_h"] = wavg("lp_h", anchor.get("lp_h", 0.9), 3)
        nr["sv"] = wavg("sv", anchor.get("sv", 12.0), 1)
        nr["w"] = wavg("w", anchor.get("w", 10.0), 2)
        nr["w20"] = wavg("w20", anchor.get("w20", 20.0), 2)
        nr["tf"] = round(min(0.95, wavg("tf", 0.25, 2) + 0.04), 2)
        nr["sub"] = wavg("sub", anchor.get("sub", 5.0), 1)
        if "pw" in anchor:
            nr["pw"] = round(max(0.05, wavg("pw", 0.1, 3) * 0.5), 3)
        existing[nb] = nr

    frontier = next_frontier
    if len(existing) >= 15600:
        break

final_cells = list(existing.values())
n_measured = sum(1 for r in final_cells if r.get("i") == 0)
n_interp = sum(1 for r in final_cells if r.get("i") == 1)

# Recompute cliff population counter accurately across full Res-4 surface
cliff_pop_p20 = sum(r.get("pop", 0) for r in final_cells if r.get("w20", 0) >= 25.0)
cliff_pop_p50 = sum(r.get("pop", 0) for r in final_cells if r.get("w", 0) >= 25.0)

grid["res4_synoptic"] = final_cells
grid["res3_synoptic"] = []
grid["res5_regional"] = []
grid["res6_regional"] = []
grid["meta"]["h3_res4_cells"] = len(final_cells)
grid["meta"]["h3_res4_measured_cells"] = n_measured
grid["meta"]["h3_res4_interpolated_cells"] = n_interp
grid["meta"]["cliff_population_p20"] = int(cliff_pop_p20)
grid["meta"]["cliff_population_p50"] = int(cliff_pop_p50)

p_grid.write_text(json.dumps(grid, separators=(",", ":"), allow_nan=False), encoding="utf-8")
size_kb = p_grid.stat().st_size / 1024
print(f"[DONE] {p_grid} -> {size_kb:.1f} KB | Total Res-4 cells: {len(final_cells):,} (Measured i=0: {n_measured:,}, Interpolated i=1: {n_interp:,})")
print("Sample cell keys:", sorted(final_cells[0].keys()))
