import json, math, duckdb, pandas as pd
from pathlib import Path
import h3

con = duckdb.connect("protein_atlas.duckdb", read_only=True)
tot_obs, tot_iso, tot_mkt = con.execute("SELECT COUNT(*), COUNT(DISTINCT m.country_iso3), COUNT(DISTINCT f.market_id) FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.is_outlier_mad = FALSE;").fetchone()
print(f"[TRACK 13] Computing Plate Complementarity & Protein Wage Share across {tot_obs:,} rows & {tot_mkt:,} markets...")

df = con.execute('''
    SELECT m.market_id, m.country_iso3, m.region_macro,
           m.latitude AS lat, m.longitude AS lon,
           COUNT(*) AS n_obs,
           MEDIAN(f.usd_per_50g_diaas) AS med_usd,
           MEDIAN(f.labor_min_50g_diaas) AS med_labor,
           MEDIAN(CASE WHEN c.commodity_code IN ('WHEAT_FLOUR','RICE_MILLED') THEN f.usd_per_50g_diaas END) AS cereal_usd,
           MEDIAN(CASE WHEN c.commodity_code IN ('BEANS_DRY','LENTILS_DRY','TOFU_TEMPEH') THEN f.usd_per_50g_diaas END) AS pulse_usd,
           MEDIAN(CASE WHEN c.commodity_code IN ('EGGS_CHIX','MILK_FLUID','FISH_FRESH') THEN f.usd_per_50g_diaas END) AS animal_anchor_usd
    FROM fact_protein_observations f
    JOIN dim_markets m USING(market_id)
    JOIN dim_commodities c USING(commodity_code)
    WHERE f.is_outlier_mad = FALSE AND f.obs_year >= 2010 AND m.latitude IS NOT NULL AND m.longitude IS NOT NULL
    GROUP BY 1,2,3,4,5
''').df()
con.close()

def to_h3(lat, lon, res):
    return h3.latlng_to_cell(lat, lon, res) if hasattr(h3, "latlng_to_cell") else h3.geo_to_h3(lat, lon, res)

def h3_ring(cell, k):
    return list(h3.grid_disk(cell, k)) if hasattr(h3, "grid_disk") else list(h3.k_ring(cell, k))

def h3_dist(a, b):
    try:
        return h3.grid_distance(a, b) if hasattr(h3, "grid_distance") else h3.h3_distance(a, b)
    except Exception:
        return 2

def build_strategic_layer(res, smooth_rings=2):
    d = df.copy()
    d["h3"] = [to_h3(r.lat, r.lon, res) for r in d.itertuples(index=False)]
    direct = {}
    for cell, grp in d.groupby("h3"):
        w = grp["n_obs"].clip(upper=5000).values
        u50 = float((grp["med_usd"] * w).sum() / w.sum())
        l50 = float((grp["med_labor"] * w).sum() / w.sum())

        # Strategic Idea #1: Lysine-Complementarity Plate Synergy (70% Cereal + 30% Pulse/Animal boosts effective DIAAS by 1.38x)
        cer = float(grp["cereal_usd"].median()) if grp["cereal_usd"].notna().any() else None
        pul = float(grp["pulse_usd"].median()) if grp["pulse_usd"].notna().any() else None
        ani = float(grp["animal_anchor_usd"].median()) if grp["animal_anchor_usd"].notna().any() else None

        synergy_u50 = None
        if cer is not None and (pul is not None or ani is not None):
            partner = min(x for x in (pul, ani) if x is not None)
            raw_blend = 0.70 * cer + 0.30 * partner
            synergy_u50 = round(raw_blend / 1.38, 3)

        # Strategic Idea #2: Family-of-4 Daily Protein Wage Share (4 * 50g DIAAS = 200g / 480-min workday)
        best_u50 = synergy_u50 if (synergy_u50 is not None and synergy_u50 < u50) else u50
        best_l50 = l50 * (best_u50 / max(u50, 0.01))
        wage_share = round(min(100.0, (4.0 * best_l50 / 480.0) * 100.0), 1)

        direct[cell] = {
            "h": cell,
            "n": int(grp["n_obs"].sum()),
            "m": int(len(grp)),
            "c": str(grp.sort_values("n_obs", ascending=False).iloc[0]["country_iso3"]),
            "u": round(u50, 3),
            "l": round(l50, 1),
            "s": synergy_u50,
            "w": wage_share,
            "i": 0
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
            wts = [math.log1p(direct[k]["n"]) / (max(1, h3_dist(nb, k)) ** 1.8) for k in nbrs]
            tw = sum(wts)
            syn_nbrs = [(direct[k]["s"], w) for k, w in zip(nbrs, wts) if direct[k]["s"] is not None]
            surface[nb] = {
                "h": nb,
                "n": 0,
                "m": 0,
                "c": direct[nbrs[0]]["c"],
                "u": round(sum(direct[k]["u"]*w for k,w in zip(nbrs,wts))/tw, 3),
                "l": round(sum(direct[k]["l"]*w for k,w in zip(nbrs,wts))/tw, 1),
                "s": round(sum(s*w for s,w in syn_nbrs)/sum(w for _,w in syn_nbrs), 3) if syn_nbrs else None,
                "w": round(sum(direct[k]["w"]*w for k,w in zip(nbrs,wts))/tw, 1),
                "i": 1
            }

    return list(surface.values())

hex_r4 = build_strategic_layer(4, smooth_rings=2)
hex_r6 = build_strategic_layer(6, smooth_rings=1)

payload = {
    "meta": {
        "total_observations": int(tot_obs),
        "total_countries": int(tot_iso),
        "total_markets": int(tot_mkt),
        "h3_res4_cells": len(hex_r4),
        "h3_res6_cells": len(hex_r6),
        "schema": "h:h3_index, n:n_obs, m:n_markets, c:iso3, u:usd_50g, l:labor_min, s:plate_synergy_usd, w:family_wage_share_pct, i:interpolated(0/1)"
    },
    "res4_synoptic": hex_r4,
    "res6_regional": hex_r6
}

out_p = Path("public/data/h3_weather_grid.json")
out_p.write_text(json.dumps(payload, separators=(",",":"), allow_nan=False))
print(f"[TRACK 13 COMPLETE] Exported {out_p} ({out_p.stat().st_size/1024:.1f} KB) | Res-4 Hexes: {len(hex_r4):,} | Res-6 Hexes: {len(hex_r6):,}")
