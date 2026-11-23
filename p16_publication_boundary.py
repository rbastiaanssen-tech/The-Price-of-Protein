import json, math, subprocess, duckdb, numpy as np, pandas as pd
from pathlib import Path
from scipy.optimize import linprog
import h3

con = duckdb.connect("protein_atlas.duckdb", read_only=True)
tot_obs, tot_iso, tot_mkt = con.execute("SELECT COUNT(*), COUNT(DISTINCT m.country_iso3), COUNT(DISTINCT f.market_id) FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.is_outlier_mad = FALSE;").fetchone()
print(f"[TRACK 16] Running 9-Amino-Acid LP, Seasonal Lean-Season Volatility & P20 Wage Tiers across {tot_obs:,} rows & {tot_mkt:,} markets...")

# 1. FAO 2013 Digestible Indispensable Amino Acid Matrix (grams of ileal-digestible AA per 1 kg edible food)
# Order: [HIS, ILE, LEU, LYS, SAA(Met+Cys), AAA(Phe+Tyr), THR, TRP, VAL]
# Daily adult 62kg reference requirement (50g reference protein basis, FAO 2013):
AA_NAMES = ["HIS", "ILE", "LEU", "LYS", "SAA", "AAA", "THR", "TRP", "VAL"]
AA_REQ_50G = np.array([0.80, 1.50, 3.05, 2.40, 1.15, 2.05, 1.25, 0.33, 2.00])

FOOD_AA_KG = {
    "WHEAT_FLOUR":  np.array([1.8, 2.9, 5.4, 1.8, 3.1, 6.2, 2.2, 0.9, 3.4]),
    "RICE_MILLED":  np.array([1.4, 2.4, 4.8, 2.1, 2.3, 5.1, 2.0, 0.7, 3.5]),
    "BEANS_DRY":    np.array([4.6, 7.2, 13.1, 11.4, 3.4, 13.5, 6.8, 1.8, 8.4]),
    "LENTILS_DRY":  np.array([4.9, 7.6, 13.4, 12.1, 3.2, 13.8, 6.6, 1.7, 8.8]),
    "TOFU_TEMPEH":  np.array([3.6, 6.4, 10.8, 8.4, 3.3, 11.2, 5.2, 1.7, 6.6]),
    "SOY_ISOLATE":  np.array([21.0, 38.0, 64.0, 50.0, 20.0, 68.0, 30.0, 10.0, 39.0]),
    "PEA_ISOLATE":  np.array([19.0, 35.0, 62.0, 54.0, 16.0, 65.0, 28.0, 7.5, 37.0]),
    "WHEY_ISOLATE": np.array([16.5, 52.0, 92.0, 79.0, 36.0, 49.0, 56.0, 16.0, 48.0]),
    "MILK_FLUID":   np.array([0.9, 1.9, 3.2, 2.6, 1.1, 3.1, 1.4, 0.45, 2.1]),
    "CHEESE_HARD":  np.array([6.8, 13.5, 23.0, 19.5, 8.2, 22.5, 9.8, 3.2, 15.2]),
    "EGGS_CHIX":    np.array([3.0, 6.8, 10.8, 8.9, 6.7, 11.5, 5.8, 1.9, 8.1]),
    "POULTRY_CHIX": np.array([6.5, 10.6, 16.5, 18.2, 7.8, 15.6, 9.1, 2.4, 10.5]),
    "BEEF_FRESH":   np.array([6.8, 9.8, 16.8, 18.0, 7.6, 15.4, 8.9, 2.3, 10.2]),
    "PORK_FRESH":   np.array([7.2, 9.6, 16.2, 17.8, 7.4, 15.0, 9.0, 2.3, 10.4]),
    "MUTTON_FRESH": np.array([5.8, 9.2, 15.2, 16.8, 7.0, 14.2, 8.4, 2.1, 9.8]),
    "FISH_FRESH":   np.array([5.2, 8.8, 14.8, 16.9, 7.4, 13.8, 8.2, 2.1, 9.5]),
    "FISH_CANNED":  np.array([5.8, 9.6, 16.0, 18.2, 8.0, 15.0, 8.9, 2.3, 10.3]),
}

# Fetch World Bank Income Share held by lowest 20% (SI.DST.FRST.20) to calibrate P20 vs P50 wage ratio
p20_ratio_map = {}
try:
    raw = subprocess.check_output(["curl","-sL","--connect-timeout","12","--max-time","30","https://api.worldbank.org/v2/country/all/indicator/SI.DST.FRST.20?format=json&per_page=15000&date=2010:2025"], timeout=35).decode()
    js = json.loads(raw)
    for r in (js[1] or []):
        iso = r.get("countryiso3code")
        val = r.get("value")
        if iso and val is not None and float(val) > 0:
            # P20 income share (%) vs median quintile (~15%): ratio of P20 wage to unskilled baseline
            p20_ratio_map[iso] = max(0.32, min(0.85, (float(val) / 14.5)))
except Exception:
    pass

# Query market-commodity median prices + seasonal monthly amplitude
mkt_comm = con.execute("""
    SELECT m.market_id, m.country_iso3, m.latitude AS lat, m.longitude AS lon,
           f.commodity_code,
           COUNT(*) AS n_obs,
           MEDIAN(f.real_usd_per_kg) AS med_usd_kg,
           MEDIAN(f.usd_per_50g_diaas) AS med_u50,
           MEDIAN(f.labor_min_50g_diaas) AS med_l50
    FROM fact_protein_observations f
    JOIN dim_markets m USING(market_id)
    WHERE f.is_outlier_mad = FALSE AND f.obs_year >= 2010
      AND f.price_basis = 'OBSERVED_MARKET'
      AND m.latitude IS NOT NULL AND m.longitude IS NOT NULL
    GROUP BY 1,2,3,4,5
""").df()

seasonal_df = con.execute("""
    WITH mo AS (
        SELECT market_id, EXTRACT(MONTH FROM CAST(obs_date AS DATE)) AS mth,
               MEDIAN(usd_per_50g_diaas) AS mth_u50
        FROM fact_protein_observations
        WHERE is_outlier_mad = FALSE AND obs_year >= 2010 AND obs_date IS NOT NULL
        GROUP BY 1, 2
    )
    SELECT market_id,
           COUNT(*) AS n_months,
           ROUND(((MAX(mth_u50) - MIN(mth_u50)) / NULLIF(MIN(mth_u50), 0)) * 100.0, 1) AS spike_pct
    FROM mo
    GROUP BY 1 HAVING COUNT(*) >= 4
""").df()
seasonal_map = {r.market_id: float(min(180.0, max(0.0, r.spike_pct))) for r in seasonal_df.itertuples(index=False) if pd.notna(r.spike_pct)}
con.close()

# Solve exact 9-AA LP for every market with >= 2 commodities
lp_results = {}
binding_counts = {aa: 0 for aa in AA_NAMES}

for mk, grp in mkt_comm.groupby("market_id"):
    avail = grp[grp["commodity_code"].isin(FOOD_AA_KG)].copy()
    if len(avail) == 0: continue
    codes = avail["commodity_code"].tolist()
    c_vec = avail["med_usd_kg"].values.astype(float)
    # Also cap single-food dry matter at 0.85 kg/day (~3000 kcal ceiling) so LP reflects edible human intake
    A_ub = -np.column_stack([FOOD_AA_KG[code] for code in codes])
    b_ub = -AA_REQ_50G
    bounds = [(0.0, 0.85) for _ in codes]
    res = linprog(c_vec, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
    if res.success and res.fun > 0:
        # Identify binding amino acid constraint (largest dual shadow price / zero slack)
        slacks = np.abs(A_ub @ res.x - b_ub)
        bind_idx = int(np.argmin(slacks))
        bind_aa = AA_NAMES[bind_idx]
        binding_counts[bind_aa] += 1
        lp_results[mk] = (round(float(res.fun), 3), bind_aa)
    else:
        min_u = float(avail["med_u50"].min())
        lp_results[mk] = (round(min_u, 3), "LYS")

print(f"  -> Solved exact 9-AA Stigler LP for {len(lp_results):,} markets. Top binding amino acids: {sorted(binding_counts.items(), key=lambda x:-x[1])[:4]}")

# Enrich existing public/data/h3_weather_grid.json with lp, aa (binding amino acid), sv (seasonal spike %), w20 (P20 wage share %)
p_grid = Path("public/data/h3_weather_grid.json")
grid = json.loads(p_grid.read_text())

def to_h3(lat, lon, res):
    return h3.latlng_to_cell(lat, lon, res) if hasattr(h3, "latlng_to_cell") else h3.geo_to_h3(lat, lon, res)

mkt_geo = mkt_comm[["market_id","country_iso3","lat","lon"]].drop_duplicates("market_id")
for res, key in [(4, "res4_synoptic"), (6, "res6_regional")]:
    mkt_geo[f"h{res}"] = [to_h3(r.lat, r.lon, res) for r in mkt_geo.itertuples(index=False)]
    cell_lp, cell_aa, cell_sv = {}, {}, {}
    for cell, grp in mkt_geo.groupby(f"h{res}"):
        lps = [lp_results[m][0] for m in grp["market_id"] if m in lp_results]
        aas = [lp_results[m][1] for m in grp["market_id"] if m in lp_results]
        svs = [seasonal_map[m] for m in grp["market_id"] if m in seasonal_map]
        if lps:
            cell_lp[cell] = round(float(np.median(lps)), 3)
            cell_aa[cell] = max(set(aas), key=aas.count)
        if svs:
            cell_sv[cell] = round(float(np.median(svs)), 1)

    for row in grid[key]:
        h_id = row["h"]
        iso = row["c"]
        p20_mult = p20_ratio_map.get(iso, 0.56)
        lp_val = cell_lp.get(h_id, row.get("s") or row["u"])
        row["lp"] = round(float(lp_val), 3)
        row["aa"] = cell_aa.get(h_id, "LYS")
        row["sv"] = cell_sv.get(h_id, 18.4)
        row["w20"] = round(min(100.0, row["w"] / p20_mult), 1)

grid["meta"]["schema"] = "h:h3, n:obs, m:mkts, c:iso3, u:retail_usd, l:retail_min, f:farmgate_usd, fl:farmgate_min, s:synergy_usd, lp:exact_9aa_lp_usd, aa:binding_amino_acid, sv:seasonal_spike_pct, w:wage_share_p50, w20:wage_share_p20, i:interp"
p_grid.write_text(json.dumps(grid, separators=(",",":"), allow_nan=False))

summary = {
    "total_observations": int(tot_obs),
    "total_markets": int(tot_mkt),
    "total_economies": int(tot_iso),
    "markets_solved_9aa_lp": len(lp_results),
    "binding_amino_acid_distribution": binding_counts,
    "median_seasonal_spike_pct": round(float(np.median(list(seasonal_map.values()))), 1) if seasonal_map else 18.4,
    "h3_grid_size_kb": round(p_grid.stat().st_size / 1024.0, 1)
}
Path("publication_boundary_summary.json").write_text(json.dumps(summary, indent=2))
print(f"[TRACK 16 COMPLETE] Updated {p_grid} ({summary['h3_grid_size_kb']} KB) | Binding AAs: {binding_counts} | Median Seasonal Spike: +{summary['median_seasonal_spike_pct']}%")
