import json
from pathlib import Path
import duckdb, numpy as np, pandas as pd
from p76_build_110_real_metros import METROS, haversine_km

root = Path(".")
con = duckdb.connect("protein_atlas.duckdb", read_only=True)
con.execute("ATTACH 'staging_50_chains.duckdb' AS s50 (READ_ONLY)")

# Pull unified empirical observations + commodity metadata + country wages
q = """
SELECT
    v.source_dataset, v.obs_date,
    EXTRACT(YEAR FROM v.obs_date)::INT AS obs_year,
    EXTRACT(MONTH FROM v.obs_date)::INT AS obs_month,
    v.market_id, v.market_name, UPPER(TRIM(v.country_iso3)) AS iso3,
    v.latitude AS lat, v.longitude AS lon,
    v.commodity_code,
    COALESCE(c.commodity_name, v.commodity_code) AS commodity_name,
    COALESCE(c.regime_class, 'OTHER') AS regime_class,
    COALESCE(c.is_animal_source, FALSE) AS is_animal_source,
    COALESCE(c.diaas_capped, 0.85) AS diaas,
    COALESCE(c.shelf_life_days, 90) AS shelf_life_days,
    v.usd_per_50g_diaas AS u_usd,
    v.labor_min_50g_diaas AS labor_min
FROM v_clean_protein_observations v
LEFT JOIN dim_commodities c USING (commodity_code)
WHERE v.price_basis = 'OBSERVED_MARKET'
  AND v.usd_per_50g_diaas > 0
  AND v.usd_per_50g_diaas < 25.0
"""
df = con.execute(q).df()
print(f"Loaded {len(df):,} clean observed market rows across {df['iso3'].nunique()} countries.")

lats, lons, isos = df["lat"].to_numpy(), df["lon"].to_numpy(), df["iso3"].to_numpy()
frames = []
for m_lat, m_lon, city, iso3, region in METROS:
    c = str(iso3).upper().strip()
    valid = (isos == c) & np.isfinite(lats) & np.isfinite(lons)
    if valid.sum() == 0: continue
    idx = np.where(valid)[0]
    d = haversine_km(m_lat, m_lon, lats[idx], lons[idx])
    keep = idx[d <= 100.0]
    if len(keep) > 0:
        s = df.iloc[keep].copy()
        s["city"], s["region"] = city, region
        frames.append(s)
mdf = pd.concat(frames, ignore_index=True)

# 1. Commodity Substitution Frontier
cg = (
    mdf.groupby(["city", "iso3", "region", "commodity_code", "commodity_name", "regime_class", "is_animal_source"], as_index=False)
    .agg(n_obs=("u_usd", "count"), diaas=("diaas", "median"), shelf_life_days=("shelf_life_days", "median"),
         med_u_usd=("u_usd", "median"), med_labor_min=("labor_min", "median"))
)
cg = cg[cg["n_obs"] >= 3].sort_values(["city", "med_u_usd"])

frontier = []
for (city, iso3, region), g in cg.groupby(["city", "iso3", "region"]):
    g = g.reset_index(drop=True)
    pl, an = g[~g["is_animal_source"]], g[g["is_animal_source"]]
    c1, c2 = g.iloc[0], (g.iloc[1] if len(g) > 1 else g.iloc[0])
    frontier.append({
        "city": city, "country_iso3": iso3, "region": region, "n_commodities": int(len(g)),
        "cheapest_commodity": str(c1["commodity_name"]), "cheapest_u_usd": round(float(c1["med_u_usd"]), 3),
        "cheapest_labor_min": round(float(c1["med_labor_min"]), 1), "runner_up_commodity": str(c2["commodity_name"]),
        "substitution_shock_min": round(float(c2["med_labor_min"] - c1["med_labor_min"]), 1),
        "animal_to_plant_premium": round(float(an.iloc[0]["med_u_usd"] / pl.iloc[0]["med_u_usd"]), 2) if (len(an) > 0 and len(pl) > 0) else None,
        "commodities": [
            {"rank": i + 1, "commodity_code": str(r["commodity_code"]), "commodity_name": str(r["commodity_name"]),
             "regime_class": str(r["regime_class"]), "is_animal_source": bool(r["is_animal_source"]),
             "diaas": round(float(r["diaas"]), 2), "shelf_life_days": int(r["shelf_life_days"]),
             "n_obs": int(r["n_obs"]), "med_u_usd": round(float(r["med_u_usd"]), 3), "med_labor_min": round(float(r["med_labor_min"]), 1)}
            for i, r in g.head(8).iterrows()
        ],
    })
(root / "public/data/commodity_substitution_frontier.json").write_text(json.dumps({"version": "v1.3-empirical", "n_metros": len(frontier), "metros": frontier}, indent=2))

# 2. Seasonality & Volatility Contrast (2018-2026 metros + Developed-Economy Benchmarks)
mo = mdf[mdf["obs_year"] >= 2018].groupby(["city", "iso3", "region", "obs_month"], as_index=False).agg(n_obs=("u_usd", "count"), med_u_usd=("u_usd", "median"), med_labor_min=("labor_min", "median"))
season = []
for (city, iso3, region), g in mo.groupby(["city", "iso3", "region"]):
    if len(g) < 9 or g["n_obs"].sum() < 30: continue
    g = g.sort_values("obs_month")
    m_lm, s_lm = float(g["med_labor_min"].mean()), float(g["med_labor_min"].std(ddof=1))
    pk, tr = g.loc[g["med_labor_min"].idxmax()], g.loc[g["med_labor_min"].idxmin()]
    season.append({
        "city": city, "country_iso3": iso3, "region": region, "months_covered": int(len(g)), "total_obs": int(g["n_obs"].sum()),
        "seasonal_cv_pct": round((s_lm / m_lm) * 100.0, 1) if m_lm > 0 else 0.0,
        "peak_to_trough_swing_pct": round(((pk["med_labor_min"] - tr["med_labor_min"]) / tr["med_labor_min"]) * 100.0, 1),
        "peak_month": int(pk["obs_month"]), "trough_month": int(tr["obs_month"]),
        "peak_labor_min": round(float(pk["med_labor_min"]), 1), "trough_labor_min": round(float(tr["med_labor_min"]), 1),
        "monthly_profile": [{"month": int(r["obs_month"]), "n_obs": int(r["n_obs"]), "med_u_usd": round(float(r["med_u_usd"]), 3), "med_labor_min": round(float(r["med_labor_min"]), 1)} for _, r in g.iterrows()],
    })

# Add high-income country benchmarks (USA BLS_RETAIL, OECD/IMF) for direct contrast
hi = df[(df["obs_year"] >= 2015) & df["source_dataset"].isin(["BLS_RETAIL", "OECD_RETAIL", "IMF_GLOBAL"])].groupby(["iso3", "source_dataset", "obs_month"], as_index=False).agg(n_obs=("u_usd", "count"), med_u_usd=("u_usd", "median"), med_labor_min=("labor_min", "median"))
for (iso3, src), g in hi.groupby(["iso3", "source_dataset"]):
    if len(g) < 9 or g["n_obs"].sum() < 20: continue
    g = g.sort_values("obs_month")
    m_lm, s_lm = float(g["med_labor_min"].mean()), float(g["med_labor_min"].std(ddof=1))
    pk, tr = g.loc[g["med_labor_min"].idxmax()], g.loc[g["med_labor_min"].idxmin()]
    season.append({
        "city": f"{iso3} National Benchmark ({src})", "country_iso3": iso3, "region": "High-Income Benchmark",
        "months_covered": int(len(g)), "total_obs": int(g["n_obs"].sum()),
        "seasonal_cv_pct": round((s_lm / m_lm) * 100.0, 1) if m_lm > 0 else 0.0,
        "peak_to_trough_swing_pct": round(((pk["med_labor_min"] - tr["med_labor_min"]) / tr["med_labor_min"]) * 100.0, 1),
        "peak_month": int(pk["obs_month"]), "trough_month": int(tr["obs_month"]),
        "peak_labor_min": round(float(pk["med_labor_min"]), 1), "trough_labor_min": round(float(tr["med_labor_min"]), 1),
        "monthly_profile": [{"month": int(r["obs_month"]), "n_obs": int(r["n_obs"]), "med_u_usd": round(float(r["med_u_usd"]), 3), "med_labor_min": round(float(r["med_labor_min"]), 1)} for _, r in g.iterrows()],
    })
season.sort(key=lambda x: x["seasonal_cv_pct"], reverse=True)
(root / "public/data/seasonality_volatility_contrast.json").write_text(json.dumps({"version": "v1.3-empirical", "n_series": len(season), "series": season}, indent=2))

# 3. Empirical Perishability Spread (Fresh <=14d vs Shelf-Stable >=180d)
mdf["sc"] = np.where(mdf["shelf_life_days"] <= 14, "Fresh", np.where(mdf["shelf_life_days"] >= 180, "Stable", "Mid"))
pp = mdf[mdf["sc"].isin(["Fresh", "Stable"])].groupby(["city", "iso3", "region", "sc"], as_index=False).agg(n=("u_usd", "count"), u=("u_usd", "median"), lm=("labor_min", "median")).pivot(index=["city", "iso3", "region"], columns="sc", values=["u", "lm", "n"]).dropna()
perish = []
for (city, iso3, region), r in pp.iterrows():
    if r[("n", "Fresh")] < 5 or r[("n", "Stable")] < 5: continue
    fu, su, fl, sl = float(r[("u", "Fresh")]), float(r[("u", "Stable")]), float(r[("lm", "Fresh")]), float(r[("lm", "Stable")])
    perish.append({
        "city": city, "country_iso3": iso3, "region": region, "fresh_obs": int(r[("n", "Fresh")]), "stable_obs": int(r[("n", "Stable")]),
        "shelf_stable_u_usd": round(su, 3), "fresh_perishable_u_usd": round(fu, 3), "perishability_premium_ratio": round(fu / su, 2),
        "shelf_stable_labor_min": round(sl, 1), "fresh_perishable_labor_min": round(fl, 1), "perishability_labor_tax_min": round(fl - sl, 1),
    })
perish.sort(key=lambda x: x["perishability_labor_tax_min"], reverse=True)
(root / "public/data/empirical_perishability_spread.json").write_text(json.dumps({"version": "v1.3-empirical", "n_metros": len(perish), "metros": perish}, indent=2))

print(f"[SUCCESS] Frontier: {len(frontier)} metros | Seasonality: {len(season)} series (max CV: {season[0]['city']} {season[0]['seasonal_cv_pct']}% vs min: {season[-1]['city']} {season[-1]['seasonal_cv_pct']}%) | Perishability: {len(perish)} metros")
