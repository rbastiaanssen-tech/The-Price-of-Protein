import json, math, duckdb, pandas as pd
from pathlib import Path
import h3

con = duckdb.connect("protein_atlas.duckdb", read_only=True)
tot_obs, tot_iso, tot_mkt = con.execute("SELECT COUNT(*), COUNT(DISTINCT m.country_iso3), COUNT(DISTINCT f.market_id) FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.is_outlier_mad = FALSE;").fetchone()
print(f"[TRACK 11C] Building High-Resolution H3 Weather Surface (Res-4 Synoptic + Res-6 Regional) from {tot_obs:,} rows & {tot_mkt:,} markets...")

df = con.execute('''
    SELECT m.market_id, m.country_iso3, m.region_macro,
           m.latitude AS lat, m.longitude AS lon,
           COUNT(*) AS n_obs,
           MEDIAN(f.usd_per_50g_diaas) AS med_usd,
           MEDIAN(f.labor_min_50g_diaas) AS med_labor,
           MEDIAN(CASE WHEN c.is_animal_source = FALSE AND c.regime_class NOT LIKE 'WILD%' THEN f.usd_per_50g_diaas END) AS plant_usd,
           MEDIAN(CASE WHEN c.is_animal_source = TRUE AND c.regime_class NOT LIKE 'WILD%' THEN f.usd_per_50g_diaas END) AS animal_usd
    FROM fact_protein_observations f
    JOIN dim_markets m USING(market_id)
    JOIN dim_commodities c USING(commodity_code)
    WHERE f.is_outlier_mad = FALSE AND f.obs_year >= 2010 AND m.latitude IS NOT NULL AND m.longitude IS NOT NULL
    GROUP BY 1,2,3,4,5
''').df()
con.close()

def to_h3(lat, lon, res):
    return h3.latlng_to_cell(lat, lon, res) if hasattr(h3, "latlng_to_cell") else h3.geo_to_h3(lat, lon, res)

def h3_poly(cell):
    if hasattr(h3, "cell_to_boundary"):
        return [[round(lo, 3), round(la, 3)] for la, lo in h3.cell_to_boundary(cell)]
    return [[round(x[0], 4), round(x[1], 4)] for x in h3.h3_to_geo_boundary(cell, geo_json=True)]

def h3_xy(cell):
    la, lo = h3.cell_to_latlng(cell) if hasattr(h3, "cell_to_latlng") else h3.h3_to_geo(cell)
    return round(la, 4), round(lo, 4)

def h3_ring(cell, k):
    return list(h3.grid_disk(cell, k)) if hasattr(h3, "grid_disk") else list(h3.k_ring(cell, k))

def h3_dist(a, b):
    try:
        return h3.grid_distance(a, b) if hasattr(h3, "grid_distance") else h3.h3_distance(a, b)
    except Exception:
        return 2

def build_layer(res, smooth_rings=2):
    d = df.copy()
    d["h3"] = [to_h3(r.lat, r.lon, res) for r in d.itertuples(index=False)]
    direct = {}
    for cell, grp in d.groupby("h3"):
        w = grp["n_obs"].clip(upper=5000).values
        direct[cell] = {
            "h3": cell,
            "n_obs": int(grp["n_obs"].sum()),
            "n_markets": int(len(grp)),
            "iso3": str(grp.sort_values("n_obs", ascending=False).iloc[0]["country_iso3"]),
            "usd_50g": round(float((grp["med_usd"] * w).sum() / w.sum()), 3),
            "labor_min": round(float((grp["med_labor"] * w).sum() / w.sum()), 1),
            "plant_usd": round(float(grp["plant_usd"].median()), 3) if grp["plant_usd"].notna().any() else None,
            "animal_usd": round(float(grp["animal_usd"].median()), 3) if grp["animal_usd"].notna().any() else None,
            "interpolated": False
        }

    surface = dict(direct)
    if smooth_rings > 0:
        cand = set()
        for c in direct:
            for nb in h3_ring(c, smooth_rings):
                if nb not in surface:
                    cand.add(nb)
        for nb in cand:
            nbrs = [k for k in h3_ring(nb, smooth_rings) if k in direct]
            if not nbrs: continue
            wts = []
            for k in nbrs:
                try:
                    dist = max(1, h3_dist(nb, k))
                except Exception:
                    dist = 2
                wts.append(math.log1p(direct[k]["n_obs"]) / (dist ** 1.8))
            tw = sum(wts)
            surface[nb] = {
                "h3": nb,
                "n_obs": 0,
                "n_markets": 0,
                "iso3": direct[nbrs[0]]["iso3"],
                "usd_50g": round(sum(direct[k]["usd_50g"]*w for k,w in zip(nbrs,wts))/tw, 3),
                "labor_min": round(sum(direct[k]["labor_min"]*w for k,w in zip(nbrs,wts))/tw, 1),
                "plant_usd": None,
                "animal_usd": None,
                "interpolated": True
            }

    out = []
    for cell, rec in surface.items():
        la, lo = h3_xy(cell)
        rec["lat"] = la
        rec["lon"] = lo
        rec["boundary"] = h3_poly(cell)
        out.append(rec)
    return out

hex_r4 = build_layer(4, smooth_rings=2)
hex_r6 = build_layer(6, smooth_rings=1)

payload = {
    "meta": {
        "total_observations": int(tot_obs),
        "total_countries": int(tot_iso),
        "total_markets": int(tot_mkt),
        "h3_res4_cells": len(hex_r4),
        "h3_res6_cells": len(hex_r6),
        "description": "High-Resolution Synoptic H3 Weather Surface (Res-4 Global + Res-6 Regional)"
    },
    "res4_synoptic": hex_r4,
    "res6_regional": hex_r6,
    "res3_synoptic": hex_r4,
    "res5_regional": hex_r6
}

out_p = Path("public/data/h3_weather_grid.json")
out_p.write_text(json.dumps(payload, separators=(",",":"), allow_nan=False))
print(f"[TRACK 11C COMPLETE] Exported {out_p} ({out_p.stat().st_size/1024:.1f} KB) | Res-4 Synoptic Hexes: {len(hex_r4):,} | Res-6 Regional Hexes: {len(hex_r6):,}")
