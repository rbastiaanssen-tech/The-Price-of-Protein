import json, math, duckdb, pandas as pd
from pathlib import Path
import h3

con = duckdb.connect("protein_atlas.duckdb", read_only=True)
tot_obs, tot_iso, tot_mkt = con.execute("SELECT COUNT(*), COUNT(DISTINCT m.country_iso3), COUNT(DISTINCT f.market_id) FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.is_outlier_mad = FALSE;").fetchone()
print(f"[TRACK 10C] Binning {tot_obs:,} observations across {tot_mkt:,} markets into H3 Resolution 3 & Resolution 5 weather grids...")

df = con.execute('''
    SELECT m.market_id, m.market_name, m.country_iso3, m.region_macro,
           m.latitude AS lat, m.longitude AS lon,
           COUNT(*) AS n_obs,
           MEDIAN(f.usd_per_50g_diaas) AS med_usd,
           MEDIAN(f.labor_min_50g_diaas) AS med_labor,
           MEDIAN(CASE WHEN c.is_animal_source = FALSE AND c.regime_class NOT LIKE 'WILD%' THEN f.usd_per_50g_diaas END) AS plant_usd,
           MEDIAN(CASE WHEN c.is_animal_source = TRUE AND c.regime_class NOT LIKE 'WILD%' THEN f.usd_per_50g_diaas END) AS animal_usd,
           MEDIAN(CASE WHEN c.is_animal_source = FALSE AND c.regime_class NOT LIKE 'WILD%' THEN f.labor_min_50g_diaas END) AS plant_labor,
           MEDIAN(CASE WHEN c.is_animal_source = TRUE AND c.regime_class NOT LIKE 'WILD%' THEN f.labor_min_50g_diaas END) AS animal_labor
    FROM fact_protein_observations f
    JOIN dim_markets m USING(market_id)
    JOIN dim_commodities c USING(commodity_code)
    WHERE f.is_outlier_mad = FALSE AND f.obs_year >= 2015 AND m.latitude IS NOT NULL AND m.longitude IS NOT NULL
    GROUP BY 1,2,3,4,5,6
''').df()
con.close()

def latlng_to_h3(lat, lon, res):
    if hasattr(h3, "latlng_to_cell"): return h3.latlng_to_cell(lat, lon, res)
    return h3.geo_to_h3(lat, lon, res)

def h3_boundary(cell):
    if hasattr(h3, "cell_to_boundary"):
        b = h3.cell_to_boundary(cell)
        return [[round(lon, 4), round(lat, 4)] for lat, lon in b]
    b = h3.h3_to_geo_boundary(cell, geo_json=True)
    return [[round(x[0], 4), round(x[1], 4)] for x in b]

def h3_center(cell):
    if hasattr(h3, "cell_to_latlng"):
        la, lo = h3.cell_to_latlng(cell)
        return round(la, 4), round(lo, 4)
    la, lo = h3.h3_to_geo(cell)
    return round(la, 4), round(lo, 4)

def h3_disk(cell, k):
    if hasattr(h3, "grid_disk"): return list(h3.grid_disk(cell, k))
    return list(h3.k_ring(cell, k))

def build_hex_layer(res, expand_rings=1):
    df_r = df.copy()
    df_r["h3"] = [latlng_to_h3(r.lat, r.lon, res) for r in df_r.itertuples(index=False)]
    direct = {}
    for cell, grp in df_r.groupby("h3"):
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
    # 1-ring Inverse-Distance Weighted (IDW) meteorological field expansion around observed cells
    surface = dict(direct)
    if expand_rings > 0:
        cand = set()
        for c in direct:
            for nb in h3_disk(c, expand_rings):
                if nb not in surface:
                    cand.add(nb)
        for nb in cand:
            nbrs = [k for k in h3_disk(nb, expand_rings) if k in direct]
            if not nbrs: continue
            wts = [math.log1p(direct[k]["n_obs"]) for k in nbrs]
            tot_w = sum(wts)
            surface[nb] = {
                "h3": nb,
                "n_obs": 0,
                "n_markets": 0,
                "iso3": direct[nbrs[0]]["iso3"],
                "usd_50g": round(sum(direct[k]["usd_50g"]*w for k,w in zip(nbrs,wts))/tot_w, 3),
                "labor_min": round(sum(direct[k]["labor_min"]*w for k,w in zip(nbrs,wts))/tot_w, 1),
                "plant_usd": None,
                "animal_usd": None,
                "interpolated": True
            }
    out = []
    for cell, rec in surface.items():
        la, lo = h3_center(cell)
        rec["lat"] = la
        rec["lon"] = lo
        rec["boundary"] = h3_boundary(cell)
        out.append(rec)
    return out

hex_r3 = build_hex_layer(3, expand_rings=1)
hex_r5 = build_hex_layer(5, expand_rings=0)

payload = {
    "meta": {
        "total_observations": int(tot_obs),
        "total_countries": int(tot_iso),
        "total_markets": int(tot_mkt),
        "h3_res3_cells": len(hex_r3),
        "h3_res5_cells": len(hex_r5),
        "description": "Synoptic H3 Hexagonal Protein Price Weather Surface (Res 3 Global + Res 5 Regional)"
    },
    "res3_synoptic": hex_r3,
    "res5_regional": hex_r5
}
out_p = Path("public/data/h3_weather_grid.json")
out_p.parent.mkdir(parents=True, exist_ok=True)
out_p.write_text(json.dumps(payload, separators=(",",":"), allow_nan=False))
print(f"[TRACK 10C COMPLETE] Exported {out_p} ({out_p.stat().st_size/1024:.1f} KB) | Res-3 Hexes: {len(hex_r3):,} | Res-5 Hexes: {len(hex_r5):,}")
