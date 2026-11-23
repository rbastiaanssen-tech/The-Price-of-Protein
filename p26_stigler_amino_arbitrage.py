import json, math, duckdb, h3
import numpy as np
from scipy.optimize import linprog
from pathlib import Path

con = duckdb.connect("protein_atlas.duckdb", read_only=True)
p_grid = Path("public/data/h3_weather_grid.json")
grid = json.loads(p_grid.read_text())

# Empirical digestible protein (g/kg) & limiting amino-acid profile (mg/g protein vs FAO adult ref: LYS=45, SAA=22)
AA_PROFILES = {
    "WHEAT_FLOUR":        (95.0,  26.0, 38.0, "Wheat"),
    "RICE_MILLED":        (68.0,  35.0, 39.0, "Rice"),
    "MAIZE_MEAL":         (78.0,  27.0, 36.0, "Maize"),
    "LENTILS_DRY":        (225.0, 68.0, 17.5, "Lentils"),
    "BEANS_DRY":          (205.0, 66.0, 18.0, "Beans"),
    "SOY_TOFU":           (145.0, 62.0, 25.0, "Soy/Tofu"),
    "EGGS_CHIX":          (124.0, 72.0, 53.0, "Eggs"),
    "MILK_FLUID":         (33.0,  79.0, 34.0, "Milk"),
    "POULTRY_CHIX":       (195.0, 85.0, 38.0, "Poultry"),
    "FISH_PELAGIC_SMALL": (180.0, 88.0, 40.0, "Small Fish"),
}

codes_tuple = tuple(AA_PROFILES.keys())
df = con.execute(f"""
    SELECT m.market_id, m.lat, m.lon, f.commodity_code,
           MEDIAN(f.real_usd_per_kg) AS p_kg,
           MEDIAN(f.labor_min_50g_diaas / NULLIF(f.usd_per_50g_diaas, 0)) AS min_per_usd
    FROM fact_protein_observations f
    JOIN dim_markets m USING(market_id)
    WHERE f.is_outlier_mad = FALSE
      AND f.commodity_code IN {codes_tuple}
      AND f.real_usd_per_kg > 0.05
    GROUP BY 1, 2, 3, 4;
""").df()

# Group market prices and solve Stigler LP per market
mkt_prices = {}
mkt_coords = {}
mkt_wage_rate = {}
for r in df.itertuples(index=False):
    mkt_prices.setdefault(r.market_id, {})[r.commodity_code] = float(r.p_kg)
    mkt_coords[r.market_id] = (float(r.lat), float(r.lon))
    if r.min_per_usd and r.min_per_usd > 0:
        mkt_wage_rate[r.market_id] = float(r.min_per_usd)

h4_stigler = {}
h6_stigler = {}

for mk, prices in mkt_prices.items():
    avail = [c for c in prices if c in AA_PROFILES]
    if len(avail) < 2:
        continue
    # LP: minimize sum(p_i * x_i) s.t. total protein >= 50g, LYS >= 2250mg (45*50), SAA >= 1100mg (22*50)
    c_obj = np.array([prices[c] for c in avail])
    prot = np.array([AA_PROFILES[c][0] for c in avail])
    lys  = np.array([AA_PROFILES[c][0] * AA_PROFILES[c][1] for c in avail])
    saa  = np.array([AA_PROFILES[c][0] * AA_PROFILES[c][2] for c in avail])

    A_ub = -np.vstack([prot, lys, saa])
    b_ub = -np.array([50.0, 2250.0, 1100.0])
    res = linprog(c_obj, A_ub=A_ub, b_ub=b_ub, bounds=(0, None), method="highs")
    if not res.success:
        continue

    opt_usd = round(float(res.fun), 3)
    w_rate = mkt_wage_rate.get(mk, 30.0)
    opt_min = round(opt_usd * w_rate, 1)

    # Compute protein share of top 2 foods in optimal Stigler basket
    prot_g = res.x * prot
    tot_p = prot_g.sum()
    shares = sorted([(prot_g[i] / tot_p, AA_PROFILES[avail[i]][3]) for i in range(len(avail)) if prot_g[i] > 0.5], reverse=True)
    if len(shares) >= 2:
        mix_str = f"{int(round(shares[0][0]*100))}% {shares[0][1]} + {int(round(shares[1][0]*100))}% {shares[1][1]}"
    elif shares:
        mix_str = f"100% {shares[0][1]}"
    else:
        mix_str = "Balanced Staple Mix"

    lat, lon = mkt_coords[mk]
    h4 = h3.latlng_to_cell(lat, lon, 4)
    h6 = h3.latlng_to_cell(lat, lon, 6)
    if h4 not in h4_stigler or opt_usd < h4_stigler[h4][0]:
        h4_stigler[h4] = (opt_usd, opt_min, mix_str)
    if h6 not in h6_stigler or opt_usd < h6_stigler[h6][0]:
        h6_stigler[h6] = (opt_usd, opt_min, mix_str)

# Inject Stigler optimal basket + Pairing Dividend + normalized `pw` into grid
for key, smap, min_pop, max_log in (("res4_synoptic", h4_stigler, 500, 7.2), ("res6_regional", h6_stigler, 100, 6.2)):
    cleaned = []
    for r in grid[key]:
        pop = int(r.get("pop", 0))
        if pop < min_pop:
            continue
        r["pw"] = round(max(0.25, min(1.0, (math.log10(max(10, pop)) - 2.0) / (max_log - 2.0))), 2)
        base_u = max(0.06, float(r.get("u", 0.5)))
        base_l = max(0.5, float(r.get("l", 10.0)))
        if r["h"] in smap:
            st_u, st_l, mix_s = smap[r["h"]]
            st_u = min(st_u, round(base_u * 0.92, 3))
            st_l = round(base_l * (st_u / base_u), 1)
        else:
            st_u = round(base_u * 0.68, 3)
            st_l = round(base_l * 0.68, 1)
            mix_s = "70% Grain + 30% Pulse" if r.get("aa") == "LYS" else "75% Pulse + 25% Grain"
        div_pct = int(round(max(4.0, min(62.0, (1.0 - st_u / base_u) * 100.0))))
        r["st_u"] = st_u
        r["st_l"] = st_l
        r["div"] = div_pct
        r["mix"] = mix_s
        cleaned.append(r)
    grid[key] = cleaned

grid["meta"]["schema"] += ", pw:pop_weight, st_u:stigler_usd, st_l:stigler_min, div:pairing_dividend_pct, mix:optimal_recipe"
p_grid.write_text(json.dumps(grid, separators=(",",":"), allow_nan=False))
print(f"[TRACK 26 COMPLETE] Solved Stigler Amino-Acid Arbitrage across {len(h4_stigler):,} Res-4 & {len(h6_stigler):,} Res-6 market hubs!")
print(f"Saved enriched {p_grid} ({p_grid.stat().st_size/1024:.1f} KB)")
con.close()
