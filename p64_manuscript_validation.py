import json
import math
import h3
import numpy as np
from pathlib import Path
from scipy.spatial import cKDTree

def h3_xy(cell):
    return h3.cell_to_latlng(cell) if hasattr(h3, "cell_to_latlng") else h3.h3_to_geo(cell)

p_grid = Path("public/data/h3_weather_grid.json")
grid = json.loads(p_grid.read_text(encoding="utf-8"))
r4 = grid["res4_synoptic"]

obs = [r for r in r4 if r.get("i") == 0]
interp = [r for r in r4 if r.get("i") == 1]

# Build KDTree over directly observed hubs to classify Near-Market Catchments (<= 100 km)
obs_rad = np.radians([h3_xy(r["h"]) for r in obs])
obs_xyz = np.column_stack([
    np.cos(obs_rad[:, 0]) * np.cos(obs_rad[:, 1]),
    np.cos(obs_rad[:, 0]) * np.sin(obs_rad[:, 1]),
    np.sin(obs_rad[:, 0]),
])
tree_obs = cKDTree(obs_xyz)

all_rad = np.radians([h3_xy(r["h"]) for r in r4])
all_xyz = np.column_stack([
    np.cos(all_rad[:, 0]) * np.cos(all_rad[:, 1]),
    np.cos(all_rad[:, 0]) * np.sin(all_rad[:, 1]),
    np.sin(all_rad[:, 0]),
])
dists_rad, _ = tree_obs.query(all_xyz, k=1)
dists_km = dists_rad * 6371.0

tiers = {
    "Tier 1: Directly Observed Hubs (i=0)": [r for r, d in zip(r4, dists_km) if r.get("i") == 0],
    "Tier 2: Observed + <=100km Catchment": [r for r, d in zip(r4, dists_km) if d <= 100.0],
    "Tier 3: Full Global Spatial Surface": r4,
}

print("=== 1. STRATIFIED POPULATION & WAGE-STRESS SENSITIVITY SWEEP ===")
thresholds = [15.0, 20.0, 25.0, 30.0]
for name, subset in tiers.items():
    tot_pop = sum(r.get("pop", 0) for r in subset)
    p50_str = " | ".join(f">{int(t)}%: {sum(r.get('pop',0) for r in subset if r.get('w',0) > t)/1e9:.2f}B" for t in thresholds)
    p20_str = " | ".join(f">{int(t)}%: {sum(r.get('pop',0) for r in subset if r.get('w20',0) > t)/1e9:.2f}B" for t in thresholds)
    print(f"\n{name} (N={len(subset):,} cells, Pop={tot_pop/1e9:.2f}B)")
    print(f"  P50 Median Worker -> {p50_str}")
    print(f"  P20 Poorest 20%   -> {p20_str}")

# 2. Spatial Block Cross-Validation (10 longitude/latitude spatial blocks across i=0 hubs)
print("\n=== 2. SPATIAL BLOCK CROSS-VALIDATION (10 CONTIGUOUS BLOCKS ON i=0 HUBS) ===")
coords = np.array([h3_xy(r["h"]) for r in obs])
# Assign 10 spatial blocks via 5 longitude bins x 2 hemisphere bins
lon_bins = np.digitize(coords[:, 1], [-60, -10, 35, 95])
lat_bins = (coords[:, 0] >= 0).astype(int)
blocks = lat_bins * 5 + lon_bins

y_true_u, y_pred_u = [], []
y_true_w, y_pred_w = [], []

for b in np.unique(blocks):
    test_mask = (blocks == b)
    train_mask = ~test_mask
    if np.sum(test_mask) < 10 or np.sum(train_mask) < 10:
        continue
    tr_tree = cKDTree(obs_xyz[train_mask])
    tr_rows = [obs[idx] for idx in np.where(train_mask)[0]]
    te_rows = [obs[idx] for idx in np.where(test_mask)[0]]
    d_rad, nn_idx = tr_tree.query(obs_xyz[test_mask], k=6)
    d_km = np.maximum(20.0, d_rad * 6371.0)
    for row_i, te in enumerate(te_rows):
        wts = 1.0 / (d_km[row_i] ** 1.6)
        tw = float(np.sum(wts))
        pred_u = sum(tr_rows[j]["u"] * w for j, w in zip(nn_idx[row_i], wts)) / tw
        pred_w = sum(tr_rows[j]["w"] * w for j, w in zip(nn_idx[row_i], wts)) / tw
        y_true_u.append(te["u"])
        y_pred_u.append(pred_u)
        y_true_w.append(te["w"])
        y_pred_w.append(pred_w)

y_true_u, y_pred_u = np.array(y_true_u), np.array(y_pred_u)
y_true_w, y_pred_w = np.array(y_true_w), np.array(y_pred_w)

rmse_u = float(np.sqrt(np.mean((y_true_u - y_pred_u) ** 2)))
mae_u = float(np.mean(np.abs(y_true_u - y_pred_u)))
r2_u = float(1.0 - np.sum((y_true_u - y_pred_u) ** 2) / np.sum((y_true_u - np.mean(y_true_u)) ** 2))

rmse_w = float(np.sqrt(np.mean((y_true_w - y_pred_w) ** 2)))
mae_w = float(np.mean(np.abs(y_true_w - y_pred_w)))
r2_w = float(1.0 - np.sum((y_true_w - y_pred_w) ** 2) / np.sum((y_true_w - np.mean(y_true_w)) ** 2))

print(f"Retail Price u ($/50g DIAAS) -> Spatial Block RMSE: ${rmse_u:.3f} | MAE: ${mae_u:.3f} | R2: {r2_u:.3f}")
print(f"Wage Share w (% daily wage)  -> Spatial Block RMSE: {rmse_w:.2f}% | MAE: {mae_w:.2f}% | R2: {r2_w:.3f}")

# 3. Stigler LP Shadow Price vs. Single-Commodity Retail Price Comparison
ratios = [r["lp"] / r["u"] for r in obs if r.get("u", 0) > 0 and r.get("lp", 0) > 0]
veg_prem = [r["lp_v"] / r["lp"] for r in obs if r.get("lp", 0) > 0 and r.get("lp_v", 0) > 0]
print("\n=== 3. STIGLER 9-AA SHADOW FRONTIER VS. RETAIL BENCHMARK (i=0 HUBS) ===")
print(f"Median LP / Single-Commodity Retail Ratio (lp / u): {np.median(ratios):.3f} (IQR: {np.percentile(ratios,25):.3f}-{np.percentile(ratios,75):.3f})")
print(f"Median Plant-Only 9-AA Premium (lp_v / lp):         {np.median(veg_prem):.3f} (IQR: {np.percentile(veg_prem,25):.3f}-{np.percentile(veg_prem,75):.3f})")

summary_out = {
    "tiers": {
        k: {
            "cells": len(v),
            "population": int(sum(r.get("pop", 0) for r in v)),
            "p50_cliff_15_20_25_30": [int(sum(r.get("pop", 0) for r in v if r.get("w", 0) > t)) for t in thresholds],
            "p20_cliff_15_20_25_30": [int(sum(r.get("pop", 0) for r in v if r.get("w20", 0) > t)) for t in thresholds],
        }
        for k, v in tiers.items()
    },
    "spatial_block_cv": {
        "retail_usd_rmse": round(rmse_u, 4),
        "retail_usd_mae": round(mae_u, 4),
        "retail_usd_r2": round(r2_u, 4),
        "wage_share_rmse": round(rmse_w, 3),
        "wage_share_mae": round(mae_w, 3),
        "wage_share_r2": round(r2_w, 4),
    },
}
Path("manuscript_validation_summary.json").write_text(json.dumps(summary_out, indent=2), encoding="utf-8")
print("\nSaved manuscript_validation_summary.json")
