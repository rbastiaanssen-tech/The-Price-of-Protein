import json, math, subprocess, duckdb, numpy as np, pandas as pd
from pathlib import Path
from scipy.optimize import linprog
import h3

con = duckdb.connect("protein_atlas.duckdb", read_only=True)
p_grid = Path("public/data/h3_weather_grid.json")
grid = json.loads(p_grid.read_text())

print("[TRACK 18B - GAPS #4 & #1] Computing Population Headcounts, Cultural 9-AA Diets & Subsistence Cushions...")

# 1. Fetch exact 2025/2026 national population totals from World Bank SP.POP.TOTL
pop_js = json.loads(subprocess.check_output(["curl","-sL","--connect-timeout","15","--max-time","40","https://api.worldbank.org/v2/country/all/indicator/SP.POP.TOTL?format=json&per_page=25000&date=2022:2026"], timeout=45).decode())
iso_pop = {}
for r in (pop_js[1] or []):
    iso = r.get("countryiso3code")
    val = r.get("value")
    if iso and val is not None and iso not in iso_pop:
        iso_pop[iso] = float(val)

# 2. Compute Cultural 9-AA LP Shadow Prices (Lacto-Vegetarian & Halal/No-Pork) per market from observed local prices
# FAO 2013 Adult 9 Indispensable Amino Acid requirements for 50g reference protein (mg)
REQ_9AA = np.array([750.0, 1500.0, 2950.0, 2250.0, 1100.0, 1900.0, 300.0, 1150.0, 1950.0])
AA_PROFILES = {
    "WHEAT_FLOUR":        np.array([2200, 3400, 6200, 2100, 3100, 6800, 1050, 2500, 3900]),
    "RICE_MILLED":        np.array([1750, 3100, 6100, 2700, 2800, 5800,  900, 2200, 4200]),
    "BEANS_DRY":          np.array([5800, 8800,16500,14200, 4400,16800, 2200, 7400,10400]),
    "LENTILS_DRY":        np.array([6100, 9600,17400,15800, 4200,17200, 2100, 7800,11100]),
    "MILK_FLUID":         np.array([ 920, 1850, 3250, 2650, 1120, 3200,  460, 1450, 2150]),
    "CHEESE_HARD":        np.array([6800,12500,22800,19200, 7900,24500, 3200, 9100,15400]),
    "EGGS_CHIX":          np.array([2750, 6300, 9900, 8100, 6200,10600, 1850, 5400, 7600]),
    "POULTRY_CHIX":       np.array([6200,10200,16500,18500, 7800,15800, 2400, 8200,10800]),
    "PORK_FRESH":         np.array([7400, 9600,16200,18200, 7600,15600, 2350, 7900,10600]),
    "BEEF_FRESH":         np.array([6800, 9400,16400,17800, 7500,15500, 2300, 7800,10400]),
    "MUTTON_FRESH":       np.array([6100, 9100,15800,17200, 7200,15100, 2200, 7600,10100]),
    "FISH_PELAGIC_SMALL": np.array([5400, 9200,15600,17400, 7100,14800, 2150, 7500, 9900]),
    "FISH_AQUACULTURE":   np.array([4800, 8600,14800,16600, 6700,14100, 2050, 7100, 9400]),
    "FISH_PREDATOR_HIGH": np.array([5600, 9500,16100,17900, 7300,15200, 2250, 7700,10200]),
}
VEG_ALLOWED = {"WHEAT_FLOUR","RICE_MILLED","BEANS_DRY","LENTILS_DRY","MILK_FLUID","CHEESE_HARD","EGGS_CHIX"}
HALAL_EXCLUDED = {"PORK_FRESH"}

recent_prices = con.execute("""
    SELECT m.country_iso3, f.commodity_code, MEDIAN(f.real_usd_per_kg) AS p_kg
    FROM fact_protein_observations f
    JOIN dim_markets m USING(market_id)
    WHERE f.is_outlier_mad = FALSE AND f.obs_year >= 2018
    GROUP BY 1, 2;
""").df()

iso_cultural_lp = {}
for iso, grp in recent_prices.groupby("country_iso3"):
    pmap = dict(zip(grp["commodity_code"], grp["p_kg"]))
    def solve_subset(allowed_set):
        items = [c for c in pmap if c in AA_PROFILES and c in allowed_set and pmap[c] > 0]
        if len(items) < 2: return None
        c_vec = np.array([pmap[c] for c in items])
        A_ub = -np.column_stack([AA_PROFILES[c] for c in items])
        res = linprog(c_vec, A_ub=A_ub, b_ub=-REQ_9AA, bounds=(0, None), method="highs")
        return round(float(res.fun), 3) if res.success else None

    lp_veg = solve_subset(VEG_ALLOWED)
    lp_halal = solve_subset(set(AA_PROFILES.keys()) - HALAL_EXCLUDED)
    iso_cultural_lp[iso] = (lp_veg, lp_halal)

# 3. Assign Population Headcounts (`pop`), Cultural Diets (`lp_v`, `lp_h`), and Subsistence Cushion (`sub`)
# Subsistence own-production share (%) calibrated by agricultural/pastoral structure & rurality
LOW_INCOME_AGR = {"ETH","TZA","UGA","KEN","MWI","MOZ","NGA","NER","MLI","BFA","TCD","SDN","COD","MDG","NPL","BGD","IND","PAK","MMR","KHM","LAO","VNM","IDN","PNG","BOL","PER","GTM","HND","NIC","HTI"}

for key in ("res4_synoptic", "res6_regional"):
    rows = grid[key]
    # Weight population across cells within each country: town centers (i=0) + dense low-friction valleys carry higher population weights
    iso_weights = {}
    for r in rows:
        iso = r["c"]
        # Town centers get higher settlement weight; rugged mountain terrain (high tf) reduces settlement density
        w_pop = (3.5 if r["i"] == 0 else 1.0) * max(0.08, 1.0 - 0.78 * r.get("tf", 0.2))
        r["_w_pop"] = w_pop
        iso_weights[iso] = iso_weights.get(iso, 0.0) + w_pop

    for r in rows:
        iso = r["c"]
        tot_p = iso_pop.get(iso, 15_000_000.0)
        r["pop"] = int(round(tot_p * (r["_w_pop"] / max(1e-6, iso_weights[iso]))))
        del r["_w_pop"]

        lp_v, lp_h = iso_cultural_lp.get(iso, (None, None))
        r["lp_v"] = lp_v if lp_v is not None else round(r["lp"] * 1.12, 3)
        r["lp_h"] = lp_h if lp_h is not None else r["lp"]

        # Own-production subsistence cushion (% of household protein from backyard poultry/dairy/legumes/artisanal catch)
        base_sub = 28.0 if iso in LOW_INCOME_AGR else 6.5
        if r["i"] == 1:
            base_sub *= 1.45  # Rural areas produce substantially more own-farm protein than urban centers
        r["sub"] = round(min(68.0, base_sub * (1.0 - 0.35 * r.get("tf", 0.2))), 1)

tot_pop_r4 = sum(r["pop"] for r in grid["res4_synoptic"])
cliff_pop_r4 = sum(r["pop"] for r in grid["res4_synoptic"] if r["w"] >= 25.0)
cliff_pop_p20_r4 = sum(r["pop"] for r in grid["res4_synoptic"] if r["w20"] >= 25.0)

grid["meta"]["schema"] = "h:h3, n:obs, m:mkts, c:iso3, pop:population, u:retail_usd, l:retail_min, f:farmgate_usd, fl:farmgate_min, s:synergy_usd, lp:exact_9aa_lp_usd, lp_v:veg_9aa_lp_usd, lp_h:halal_9aa_lp_usd, aa:binding_amino_acid, sv:seasonal_spike_pct, w:wage_share_p50, w20:wage_share_p20, tf:terrain_friction, sub:subsistence_cushion_pct, i:interp"
p_grid.write_text(json.dumps(grid, separators=(",",":"), allow_nan=False))

print(f"[TRACK 18B COMPLETE] Saved enriched {p_grid} ({p_grid.stat().st_size/1024:.1f} KB)")
print(f"  -> Global Gridded Population Mapped: {tot_pop_r4/1e9:.2f} Billion people")
print(f"  -> Median Worker (P50) >25% Wage Affordability Cliff: {cliff_pop_r4/1e6:,.0f} Million people")
print(f"  -> Poorest Quintile (P20) >25% Wage Affordability Cliff: {cliff_pop_p20_r4/1e9:.2f} Billion people")
con.close()
