import json
import math
import duckdb
import h3
import numpy as np
import pandas as pd
from pathlib import Path
from scipy.spatial import cKDTree
from global_land_mask import globe

def to_h3(lat, lon, res):
    return h3.latlng_to_cell(lat, lon, res) if hasattr(h3, "latlng_to_cell") else h3.geo_to_h3(lat, lon, res)

def h3_xy(cell):
    return h3.cell_to_latlng(cell) if hasattr(h3, "cell_to_latlng") else h3.h3_to_geo(cell)

def h3_poly(cell):
    if hasattr(h3, "cell_to_boundary"):
        return [[round(lo, 2), round(la, 2)] for la, lo in h3.cell_to_boundary(cell)]
    return [[round(x[0], 2), round(x[1], 2)] for x in h3.h3_to_geo_boundary(cell, geo_json=True)]

def all_land_h3_res3():
    # Enumerate all Res-0 base cells -> children at Res-3 (~41,162 total global cells)
    res0 = h3.get_res0_cells() if hasattr(h3, "get_res0_cells") else h3.get_res0_indexes()
    cells = []
    for c0 in res0:
        kids = h3.cell_to_children(c0, 3) if hasattr(h3, "cell_to_children") else h3.h3_to_children(c0, 3)
        cells.extend(kids)
    land_cells = []
    for c in cells:
        la, lo = h3_xy(c)
        if la < -56.5 or la > 72.5:
            continue
        # Check center + 2 vertices so coastlines/islands don't get clipped
        if globe.is_land(la, lo):
            land_cells.append((c, round(la, 3), round(lo, 3)))
        else:
            poly = h3_poly(c)
            if any(globe.is_land(v[1], v[0]) for v in poly[::2]):
                land_cells.append((c, round(la, 3), round(lo, 3)))
    return land_cells

con = duckdb.connect("protein_atlas.duckdb", read_only=True)
tot_obs = con.execute("SELECT COUNT(*) FROM fact_protein_observations WHERE is_outlier_mad = FALSE;").fetchone()[0]
tot_iso = con.execute("SELECT COUNT(DISTINCT country_iso3) FROM dim_markets;").fetchone()[0]
tot_mkt = con.execute("SELECT COUNT(DISTINCT market_id) FROM dim_markets;").fetchone()[0]

df_mkt = con.execute("""
    SELECT
        mk.market_id,
        mk.country_iso3 AS iso3,
        mk.latitude AS lat,
        mk.longitude AS lon,
        COUNT(*) AS n_obs,
        MEDIAN(f.usd_per_50g_diaas) AS usd_50g,
        MEDIAN(f.labor_min_50g_diaas) AS labor_min
    FROM fact_protein_observations f
    JOIN dim_markets mk ON f.market_id = mk.market_id
    WHERE f.is_outlier_mad = FALSE
      AND f.obs_year >= 2015
      AND f.usd_per_50g_diaas > 0
      AND f.labor_min_50g_diaas > 0
      AND (mk.latitude <> 0 OR mk.longitude <> 0)
    GROUP BY 1, 2, 3, 4;
""").df()
con.close()

# Build 3D unit-sphere KDTree of empirical markets for exact great-circle IDW
lat_rad = np.radians(df_mkt["lat"].values)
lon_rad = np.radians(df_mkt["lon"].values)
mkt_xyz = np.column_stack([
    np.cos(lat_rad) * np.cos(lon_rad),
    np.cos(lat_rad) * np.sin(lon_rad),
    np.sin(lat_rad),
])
tree = cKDTree(mkt_xyz)
mkt_weights_base = np.log1p(df_mkt["n_obs"].values)
mkt_usd = df_mkt["usd_50g"].values
mkt_lab = df_mkt["labor_min"].values
mkt_iso = df_mkt["iso3"].values

# Direct H3 Res-3 aggregation for cells with observed markets
df_mkt["h3"] = [to_h3(la, lo, 3) for la, lo in zip(df_mkt["lat"], df_mkt["lon"])]
direct_map = {}
for cell, grp in df_mkt.groupby("h3"):
    direct_map[cell] = {
        "n_obs": int(grp["n_obs"].sum()),
        "n_markets": int(len(grp)),
        "iso3": str(grp["iso3"].mode().iloc[0]),
        "usd_50g": round(float(np.median(grp["usd_50g"])), 3),
        "labor_min": round(float(np.median(grp["labor_min"])), 1),
    }

land_cells = all_land_h3_res3()
print(f"Enumerated {len(land_cells):,} inhabited terrestrial H3 Res-3 cells globally.")

q_lats = np.radians([x[1] for x in land_cells])
q_lons = np.radians([x[2] for x in land_cells])
q_xyz = np.column_stack([
    np.cos(q_lats) * np.cos(q_lons),
    np.cos(q_lats) * np.sin(q_lons),
    np.sin(q_lats),
])
dists, idxs = tree.query(q_xyz, k=12)
# Convert chord distance to km (~6371 * chord), floor at 15 km
dist_km = np.maximum(15.0, dists * 6371.0)

continuous_surface = []
for i, (cell, la, lo) in enumerate(land_cells):
    if cell in direct_map:
        rec = dict(direct_map[cell])
    else:
        w = mkt_weights_base[idxs[i]] / (dist_km[i] ** 1.65)
        tw = float(np.sum(w))
        rec = {
            "n_obs": 0,
            "n_markets": 0,
            "iso3": str(mkt_iso[idxs[i][0]]),
            "usd_50g": round(float(np.sum(mkt_usd[idxs[i]] * w) / tw), 3),
            "labor_min": round(float(np.sum(mkt_lab[idxs[i]] * w) / tw), 1),
        }
    rec["h3"] = cell
    rec["lat"] = la
    rec["lon"] = lo
    rec["boundary"] = h3_poly(cell)
    continuous_surface.append(rec)

payload = {
    "meta": {
        "total_observations": int(tot_obs),
        "total_countries": int(tot_iso),
        "total_markets": int(tot_mkt),
        "h3_res3_cells": len(continuous_surface),
        "h3_res4_cells": len(continuous_surface),
        "h3_res5_cells": 0,
        "h3_res6_cells": 0,
        "description": "Continuous Global Terrestrial H3 Surface (100% Land-Masked IDW Interpolation)"
    },
    "res3_synoptic": continuous_surface,
    "res4_synoptic": continuous_surface,
    "res5_regional": [],
    "res6_regional": [],
}

out_p = Path("public/data/h3_weather_grid.json")
out_p.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8")
print(f"[EXPORTED] {out_p} ({out_p.stat().st_size / 1024:.1f} KB) | Continuous Land Hexes: {len(continuous_surface):,}")
