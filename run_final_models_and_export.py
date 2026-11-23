from __future__ import annotations
import json, math, warnings
from pathlib import Path
import duckdb, numpy as np, pandas as pd
from scipy import sparse
from scipy.optimize import linprog
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import lsqr

warnings.filterwarnings("ignore")
con = duckdb.connect("protein_atlas.duckdb")

# ==============================================================================
# 1. PURE EMPIRICAL STIGLER LP SOLVER (50g WHO/FAO BASELINE & 65g ACTIVE TARGET)
# ==============================================================================
def solve_stigler_lp(prices, kcal_kg, prot_kg, codes, daily_kcal=2100.0, daily_prot=65.0, max_bulk=2.5):
    n = len(prices)
    A_ub = np.vstack([-kcal_kg, -prot_kg, np.ones(n)])
    b_ub = np.array([-daily_kcal, -daily_prot, max_bulk], dtype=float)
    res = linprog(c=prices, A_ub=A_ub, b_ub=b_ub, bounds=(0, None), method="highs")
    if not res.success or res.ineqlin is None:
        return None
    shadow_50g_eq = max(0.0, -float(res.ineqlin.marginals[1])) * 50.0
    top_idx = int(np.argmax(res.x * prot_kg))
    return float(res.fun), shadow_50g_eq, codes[top_idx]

df_lp = con.execute("""
    SELECT f.market_id, f.obs_decade, f.commodity_code, c.kcal_per_kg, c.eff_protein_g_kg,
           MEDIAN(f.real_usd_per_kg) AS usd_kg,
           MEDIAN(f.real_usd_per_kg * (f.labor_min_50g_diaas / NULLIF(f.usd_per_50g_diaas, 0))) AS lab_kg
    FROM fact_protein_observations f
    JOIN dim_commodities c ON f.commodity_code = c.commodity_code
    WHERE f.price_basis = 'OBSERVED_MARKET' AND f.is_outlier_mad = FALSE
      AND f.real_usd_per_kg > 0 AND f.labor_min_50g_diaas > 0
    GROUP BY 1,2,3,4,5 ORDER BY 1,2;
""").df()

lp_rows = []
for (mkt_id, dec), grp in df_lp.groupby(["market_id", "obs_decade"]):
    if len(grp) < 3 or grp["kcal_per_kg"].max() * 2.5 < 2100.0:
        continue
    codes = grp["commodity_code"].tolist()
    kcal  = grp["kcal_per_kg"].to_numpy(float)
    prot  = grp["eff_protein_g_kg"].to_numpy(float)
    usd_k = grp["usd_kg"].to_numpy(float)
    lab_k = grp["lab_kg"].to_numpy(float)

    s50_u = solve_stigler_lp(usd_k, kcal, prot, codes, 2100.0, 50.0, 2.5)
    s65_u = solve_stigler_lp(usd_k, kcal, prot, codes, 2100.0, 65.0, 2.5)
    s65_l = solve_stigler_lp(lab_k, kcal, prot, codes, 2100.0, 65.0, 2.5)
    if s65_u and s65_l:
        lp_rows.append((
            mkt_id, int(dec), len(codes),
            s65_u[0], s65_l[0],
            s50_u[1] if s50_u else 0.0,
            s65_u[1], s65_l[1], s65_u[2]
        ))

con.execute("DROP TABLE IF EXISTS agg_stigler_shadow_prices;")
con.execute("""
    CREATE TABLE agg_stigler_shadow_prices (
        market_id VARCHAR, obs_decade INT, n_commodities INT,
        min_diet_usd_day DOUBLE, min_diet_labor_min_day DOUBLE,
        shadow_usd_50g_maint DOUBLE, shadow_usd_50g DOUBLE,
        shadow_labor_min_50g DOUBLE, binding_protein_food VARCHAR,
        PRIMARY KEY (market_id, obs_decade)
    );
""")
con.register("lp_out_df", pd.DataFrame(lp_rows, columns=[
    "market_id","obs_decade","n_commodities","min_diet_usd_day","min_diet_labor_min_day",
    "shadow_usd_50g_maint","shadow_usd_50g","shadow_labor_min_50g","binding_protein_food"
]))
con.execute("INSERT INTO agg_stigler_shadow_prices SELECT * FROM lp_out_df;")
print(f"[1/4] Solved {len(lp_rows):,} pure empirical Stigler LP cells (0 synthetic anchors).")

# ==============================================================================
# 2. LOAD 1.64M UNIT-VERIFIED OBSERVATIONS & FIT CONNECTED-GRAPH TWFE INDEX
# ==============================================================================
df = con.execute("""
    SELECT
        f.market_id, m.country_iso3, m.region_macro,
        m.arable_ha_per_cap AS arable_ha,
        f.source_dataset, f.commodity_code, c.regime_class, c.is_animal_source, c.diaas_score,
        CASE WHEN f.obs_year = 2026 THEN 2026 ELSE f.obs_decade END AS decade,
        f.usd_per_50g_diaas AS usd_50g,
        f.labor_min_50g_diaas AS lmin_50g,
        f.usd_per_50g_diaas * c.diaas_score AS usd_50g_crude
    FROM fact_protein_observations f
    JOIN dim_markets m ON f.market_id = m.market_id
    JOIN dim_commodities c ON f.commodity_code = c.commodity_code
    WHERE f.price_basis = 'OBSERVED_MARKET'
      AND f.is_outlier_mad = FALSE
      AND f.obs_year >= 1700
      AND f.usd_per_50g_diaas > 0
      AND f.labor_min_50g_diaas > 0;
""").df()

df["ln_usd"] = np.log(df["usd_50g"])
df["ln_lmin"] = np.log(df["lmin_50g"])
df["ln_crude_usd"] = np.log(df["usd_50g_crude"])

def map_channel(src: str) -> str:
    if src in ("FAOSTAT_PP", "FAOSTAT_LCU_61_90"):
        return "FARMGATE_FAO"
    if src in ("WFP_MARKET", "IMF_GLOBAL"):
        return "RETAIL_GLOBAL"
    return "URBAN_HIST_BLS"

df["channel"] = df["source_dataset"].map(map_channel)

def fit_connected_twfe(sub: pd.DataFrame, y_col: str, anchor_decade: int = 2020) -> pd.DataFrame:
    c_idx, c_unq = pd.factorize(sub["country_iso3"])
    d_idx, d_unq = pd.factorize(sub["decade"])
    adj = sparse.csr_matrix(
        (np.ones(len(sub)), (c_idx, len(c_unq) + d_idx)),
        shape=(len(c_unq) + len(d_unq), len(c_unq) + len(d_unq))
    )
    adj = adj + adj.T
    _, labels = connected_components(adj, directed=False)
    ref_d_pos = int(np.where(d_unq == anchor_decade)[0][0])
    target_comp = labels[len(c_unq) + ref_d_pos]
    valid_c = set(c_unq[labels[:len(c_unq)] == target_comp])
    sub = sub[sub["country_iso3"].isin(valid_c)].copy()

    ctry_c, ctry_u = pd.factorize(sub["country_iso3"])
    comm_c, comm_u = pd.factorize(sub["commodity_code"])
    chan_c, chan_u = pd.factorize(sub["channel"])
    dec_c,  dec_u  = pd.factorize(sub["decade"], sort=True)

    n = len(sub)
    n_dec = len(dec_u)
    rows, cols, vals = [np.arange(n)], [dec_c], [np.ones(n)]
    offset = n_dec
    for codes, u_arr in [(ctry_c, ctry_u), (comm_c, comm_u), (chan_c, chan_u)]:
        k = len(u_arr) - 1
        if k > 0:
            m = codes > 0
            rows.append(np.where(m)[0])
            cols.append(offset + codes[m] - 1)
            vals.append(np.ones(m.sum()))
            offset += k

    X = sparse.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(n, offset))
    sol = lsqr(X, sub[y_col].to_numpy(float), atol=1e-11, btol=1e-11, iter_lim=8000)[0]
    deltas = sol[:n_dec]

    ref_idx = int(np.where(dec_u == anchor_decade)[0][0])
    anchor_val = float(sub.loc[sub["decade"] == anchor_decade, y_col].median())
    level = np.exp(deltas + (anchor_val - deltas[ref_idx]))
    counts = sub.groupby("decade").size().reindex(dec_u).to_numpy()
    return pd.DataFrame({
        "obs_decade": dec_u.astype(int),
        "n_obs": counts.astype(int),
        "twfe_level": np.round(level, 3),
    })

twfe_p_usd = fit_connected_twfe(df[~df["is_animal_source"]], "ln_usd").rename(columns={"twfe_level": "twfe_plant_usd", "n_obs": "n_plant"})
twfe_a_usd = fit_connected_twfe(df[df["is_animal_source"]],  "ln_usd").rename(columns={"twfe_level": "twfe_animal_usd", "n_obs": "n_animal"})
twfe_p_lab = fit_connected_twfe(df[~df["is_animal_source"]], "ln_lmin").rename(columns={"twfe_level": "twfe_plant_labor"})[["obs_decade", "twfe_plant_labor"]]
twfe_a_lab = fit_connected_twfe(df[df["is_animal_source"]],  "ln_lmin").rename(columns={"twfe_level": "twfe_animal_labor"})[["obs_decade", "twfe_animal_labor"]]

twfe_panel = (
    twfe_p_usd.merge(twfe_a_usd, on="obs_decade", how="outer")
              .merge(twfe_p_lab, on="obs_decade", how="outer")
              .merge(twfe_a_lab, on="obs_decade", how="outer")
              .sort_values("obs_decade")
)
twfe_panel["twfe_trophic_ratio_usd"]   = np.round(twfe_panel["twfe_animal_usd"] / twfe_panel["twfe_plant_usd"], 2)
twfe_panel["twfe_trophic_ratio_labor"] = np.round(twfe_panel["twfe_animal_labor"] / twfe_panel["twfe_plant_labor"], 2)

# ==============================================================================
# 3. WITHIN-(MARKET x DECADE) TROPHIC TAX & CENTERED HECKSCHER-OHLIN MODEL
# ==============================================================================
def fit_within_cell_ols(data: pd.DataFrame, y_col: str, x_cols: list[str], cell_cols: list[str], cluster_col: str) -> pd.DataFrame:
    sub = data[x_cols + [y_col] + cell_cols + [cluster_col]].dropna().copy()
    sub = sub[sub.groupby(cell_cols)[y_col].transform("count") >= 2].copy()
    g = sub.groupby(cell_cols)
    y_dm = (sub[y_col] - g[y_col].transform("mean")).to_numpy(float)
    X_dm = np.column_stack([(sub[c] - g[c].transform("mean")).to_numpy(float) for c in x_cols])
    beta, _, _, _ = np.linalg.lstsq(X_dm, y_dm, rcond=None)
    resid = y_dm - X_dm @ beta

    XtX_inv = np.linalg.pinv(X_dm.T @ X_dm)
    clusters = sub[cluster_col].to_numpy()
    G = len(np.unique(clusters))
    N, K = X_dm.shape
    meat = np.zeros((K, K))
    order = np.argsort(clusters)
    c_s, X_s, e_s = clusters[order], X_dm[order], resid[order]
    for idx_chunk in np.split(np.arange(N), np.flatnonzero(c_s[1:] != c_s[:-1]) + 1):
        sc = X_s[idx_chunk].T @ e_s[idx_chunk]
        meat += np.outer(sc, sc)
    vcov = (G / max(G - 1, 1)) * ((N - 1) / max(N - K, 1)) * (XtX_inv @ meat @ XtX_inv)
    se = np.sqrt(np.maximum(np.diag(vcov), 1e-16))
    return pd.DataFrame({
        "term": x_cols,
        "coef_log": np.round(beta, 4),
        "se_cluster": np.round(se, 4),
        "t_stat": np.round(beta / se, 2),
        "multiplier_exp_b": np.round(np.exp(beta), 3),
        "pct_premium": np.round((np.exp(beta) - 1.0) * 100.0, 1),
        "n_obs": N,
        "n_clusters": G,
    })

for reg in ["ANIMAL_DAIRY_EGG", "MARINE", "ANIMAL_MEAT"]:
    df[f"d_{reg}"] = (df["regime_class"] == reg).astype(float)

reg_trophic_diaas = fit_within_cell_ols(df, "ln_usd", ["d_ANIMAL_DAIRY_EGG", "d_MARINE", "d_ANIMAL_MEAT"], ["market_id", "decade"], "country_iso3")
reg_trophic_crude = fit_within_cell_ols(df, "ln_crude_usd", ["d_ANIMAL_DAIRY_EGG", "d_MARINE", "d_ANIMAL_MEAT"], ["market_id", "decade"], "country_iso3")

# Centered Heckscher-Ohlin regression on markets with observed World Bank arable_ha_per_cap
df_ho = df[df["arable_ha"].notna() & (df["arable_ha"] > 0)].copy()
df_ho["ln_arable"] = np.log(np.clip(df_ho["arable_ha"], 0.005, 5.0))
df_ho["ln_arable_c"] = df_ho["ln_arable"] - df_ho["ln_arable"].mean()
df_ho["d_animal"] = df_ho["is_animal_source"].astype(float)
df_ho["animal_x_ln_arable_c"] = df_ho["d_animal"] * df_ho["ln_arable_c"]
reg_ho_endowment = fit_within_cell_ols(df_ho, "ln_usd", ["d_animal", "animal_x_ln_arable_c"], ["market_id", "decade"], "country_iso3")

stigler_df = con.execute("""
    SELECT obs_decade, COUNT(*) AS n_cells,
           ROUND(MEDIAN(min_diet_usd_day), 2) AS med_diet_usd_day,
           ROUND(MEDIAN(shadow_usd_50g_maint), 3) AS shadow_50g_maint_usd,
           ROUND(MEDIAN(shadow_usd_50g), 3) AS stigler_shadow_usd,
           ROUND(MEDIAN(shadow_labor_min_50g), 2) AS stigler_shadow_labor
    FROM agg_stigler_shadow_prices GROUP BY 1 ORDER BY 1;
""").df()

def sanitize_json(obj):
    if isinstance(obj, dict): return {k: sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, list): return [sanitize_json(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)): return None
    return obj

bundle = {
    "twfe_panel": twfe_panel.merge(stigler_df, on="obs_decade", how="left").to_dict(orient="records"),
    "reg_trophic_diaas": reg_trophic_diaas.to_dict(orient="records"),
    "reg_trophic_crude": reg_trophic_crude.to_dict(orient="records"),
    "reg_ho_endowment": reg_ho_endowment.to_dict(orient="records"),
}
Path("econometric_results.json").write_text(json.dumps(sanitize_json(bundle), indent=2, allow_nan=False), encoding="utf-8")

print("\n=== TABLE 1: CONNECTED-GRAPH TWFE FRONTIER INDEX (1700–2026) ===")
print(twfe_panel.to_string(index=False))
print("\n=== TABLE 2A: WITHIN-(MARKET x DECADE) TROPHIC TAX (50g DIAAS) ===")
print(reg_trophic_diaas.to_string(index=False))
print("\n=== TABLE 2B: SENSITIVITY — CRUDE PROTEIN BASIS (UNADJUSTED FOR DIAAS) ===")
print(reg_trophic_crude.to_string(index=False))
print("\n=== TABLE 3: CENTERED HECKSCHER-OHLIN ARABLE ENDOWMENT INTERACTION ===")
print(reg_ho_endowment.to_string(index=False))
print("\n=== TABLE 4: PURE EMPIRICAL STIGLER LP SHADOW PRICES (50g MAINT vs 65g ACTIVE) ===")
print(stigler_df.to_string(index=False))

# ==============================================================================
# 4. UPDATE LOVABLE ARTIFACT 4 WITH COMPOSITION-ADJUSTED TWFE & STIGLER SERIES
# ==============================================================================
p4 = Path("public/data/trophic_and_stigler_series.json")
if p4.exists():
    art4 = json.loads(p4.read_text(encoding="utf-8"))
    merged = twfe_panel.merge(stigler_df, on="obs_decade", how="left")
    new_series = []
    for r in merged.itertuples(index=False):
        new_series.append({
            "obs_decade": int(r.obs_decade),
            "plant_frontier_usd": float(r.twfe_plant_usd) if pd.notna(r.twfe_plant_usd) else None,
            "animal_frontier_usd": float(r.twfe_animal_usd) if pd.notna(r.twfe_animal_usd) else None,
            "plant_frontier_labor": float(r.twfe_plant_labor) if pd.notna(r.twfe_plant_labor) else None,
            "animal_frontier_labor": float(r.twfe_animal_labor) if pd.notna(r.twfe_animal_labor) else None,
            "stigler_shadow_usd": float(r.stigler_shadow_usd) if pd.notna(r.stigler_shadow_usd) else None,
            "stigler_shadow_labor": float(r.stigler_shadow_labor) if pd.notna(r.stigler_shadow_labor) else None,
            "trophic_tax_ratio_usd": float(r.twfe_trophic_ratio_usd) if pd.notna(r.twfe_trophic_ratio_usd) else None,
            "trophic_tax_ratio_labor": float(r.twfe_trophic_ratio_labor) if pd.notna(r.twfe_trophic_ratio_labor) else None,
            "trophic_tax_ratio": float(r.twfe_trophic_ratio_usd) if pd.notna(r.twfe_trophic_ratio_usd) else None,
        })
    art4["series"] = sanitize_json(new_series)
    p4.write_text(json.dumps(art4, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    print(f"[4/4] Updated {p4} with connected-graph TWFE frontiers & pure empirical Stigler series.")
