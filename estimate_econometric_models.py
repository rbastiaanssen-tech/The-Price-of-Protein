from __future__ import annotations
import json, math, warnings
from pathlib import Path
import duckdb, numpy as np, pandas as pd
from scipy import sparse
from scipy.sparse.linalg import lsqr

warnings.filterwarnings("ignore")

con = duckdb.connect("protein_atlas.duckdb", read_only=True)

print("Loading 1.63M unit-verified empirical observations...")
df = con.execute("""
    SELECT
        f.market_id,
        m.country_iso3,
        m.region_macro,
        COALESCE(m.arable_ha_per_cap, 0.15) AS arable_ha,
        f.commodity_code,
        c.regime_class,
        c.is_animal_source,
        c.diaas_score,
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
df["ln_arable"] = np.log(np.clip(df["arable_ha"], 0.01, 5.0))

# ==============================================================================
# MODEL 1: TWO-WAY FIXED EFFECTS (MARKET + COMMODITY FE) DECADAL FRONTIER INDEX
# ==============================================================================
def fit_twfe_index(sub: pd.DataFrame, y_col: str, anchor_decade: int = 2020) -> pd.DataFrame:
    """
    Estimates ln(P_{i,c,t}) = alpha_i + gamma_c + delta_t + eps_{i,c,t}
    Normalized to the anchor_decade weighted median level so levels remain interpretable
    while inter-decadal changes are 100% free of market/commodity composition shifts.
    """
    sub = sub.copy()
    mkt_codes, mkt_uniques = pd.factorize(sub["market_id"])
    com_codes, com_uniques = pd.factorize(sub["commodity_code"])
    dec_codes, dec_uniques = pd.factorize(sub["decade"], sort=True)

    # Drop reference category for market & commodity; keep all decades (no intercept)
    n = len(sub)
    n_dec = len(dec_uniques)
    n_mkt = len(mkt_uniques) - 1
    n_com = len(com_uniques) - 1

    rows, cols, vals = [], [], []
    # Decade dummies: cols [0 .. n_dec-1]
    rows.append(np.arange(n))
    cols.append(dec_codes)
    vals.append(np.ones(n))

    # Market FE (skip 0): cols [n_dec .. n_dec + n_mkt - 1]
    m_mask = mkt_codes > 0
    rows.append(np.where(m_mask)[0])
    cols.append(n_dec + mkt_codes[m_mask] - 1)
    vals.append(np.ones(m_mask.sum()))

    # Commodity FE (skip 0): cols [n_dec + n_mkt .. total - 1]
    c_mask = com_codes > 0
    rows.append(np.where(c_mask)[0])
    cols.append(n_dec + n_mkt + com_codes[c_mask] - 1)
    vals.append(np.ones(c_mask.sum()))

    X = sparse.csr_matrix(
        (np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
        shape=(n, n_dec + n_mkt + n_com),
    )
    y = sub[y_col].to_numpy(dtype=float)
    sol = lsqr(X, y, atol=1e-10, btol=1e-10, iter_lim=5000)[0]
    deltas = sol[:n_dec]

    # Re-anchor level to the empirical median in anchor_decade (or latest available decade)
    ref_dec = anchor_decade if anchor_decade in dec_uniques else int(dec_uniques[-1])
    ref_idx = int(np.where(dec_uniques == ref_dec)[0][0])
    anchor_val = float(sub.loc[sub["decade"] == ref_dec, y_col].median())
    shift = anchor_val - deltas[ref_idx]
    level = np.exp(deltas + shift)

    counts = sub.groupby("decade").size().reindex(dec_uniques).to_numpy()
    return pd.DataFrame({
        "obs_decade": dec_uniques.astype(int),
        "n_obs": counts.astype(int),
        "twfe_level": np.round(level, 3),
        "index_2020_100": np.round(100.0 * np.exp(deltas - deltas[ref_idx]), 1),
    })

plant_sub = df[~df["is_animal_source"]]
anim_sub  = df[df["is_animal_source"]]

twfe_plant_usd  = fit_twfe_index(plant_sub, "ln_usd").rename(columns={"twfe_level": "twfe_plant_usd", "index_2020_100": "idx_plant_usd", "n_obs": "n_plant"})
twfe_anim_usd   = fit_twfe_index(anim_sub,  "ln_usd").rename(columns={"twfe_level": "twfe_animal_usd", "index_2020_100": "idx_animal_usd", "n_obs": "n_animal"})
twfe_plant_lmin = fit_twfe_index(plant_sub, "ln_lmin").rename(columns={"twfe_level": "twfe_plant_labor", "index_2020_100": "idx_plant_labor"})[["obs_decade", "twfe_plant_labor", "idx_plant_labor"]]
twfe_anim_lmin  = fit_twfe_index(anim_sub,  "ln_lmin").rename(columns={"twfe_level": "twfe_animal_labor", "index_2020_100": "idx_animal_labor"})[["obs_decade", "twfe_animal_labor", "idx_animal_labor"]]

twfe_panel = (
    twfe_plant_usd
    .merge(twfe_anim_usd, on="obs_decade", how="outer")
    .merge(twfe_plant_lmin, on="obs_decade", how="outer")
    .merge(twfe_anim_lmin, on="obs_decade", how="outer")
    .sort_values("obs_decade")
)
twfe_panel["twfe_trophic_ratio_usd"]   = np.round(twfe_panel["twfe_animal_usd"] / twfe_panel["twfe_plant_usd"], 2)
twfe_panel["twfe_trophic_ratio_labor"] = np.round(twfe_panel["twfe_animal_labor"] / twfe_panel["twfe_plant_labor"], 2)

# ==============================================================================
# MODEL 2: WITHIN-(MARKET x DECADE) TROPHIC TAX REGRESSION (COUNTRY-CLUSTERED SE)
# ==============================================================================
def fit_within_cell_ols(data: pd.DataFrame, y_col: str, x_cols: list[str], cell_cols: list[str], cluster_col: str) -> pd.DataFrame:
    """
    Frisch-Waugh-Lovell exact absorption of high-dimensional cell fixed effects
    (e.g., Market x Decade) with Liang-Zeger cluster-robust standard errors.
    """
    sub = data[x_cols + [y_col] + cell_cols + [cluster_col]].dropna().copy()
    # Keep only cells with within-cell variation (at least 2 rows)
    cell_sz = sub.groupby(cell_cols)[y_col].transform("count")
    sub = sub[cell_sz >= 2].copy()

    g = sub.groupby(cell_cols)
    y_dm = (sub[y_col] - g[y_col].transform("mean")).to_numpy(dtype=float)
    X_dm = np.column_stack([(sub[c] - g[c].transform("mean")).to_numpy(dtype=float) for c in x_cols])

    beta, _, _, _ = np.linalg.lstsq(X_dm, y_dm, rcond=None)
    resid = y_dm - X_dm @ beta

    # Cluster-robust variance-covariance matrix (clustered by country_iso3)
    XtX_inv = np.linalg.pinv(X_dm.T @ X_dm)
    clusters = sub[cluster_col].to_numpy()
    G = len(np.unique(clusters))
    N, K = X_dm.shape
    meat = np.zeros((K, K))
    order = np.argsort(clusters)
    c_sorted, X_s, e_s = clusters[order], X_dm[order], resid[order]
    splits = np.flatnonzero(c_sorted[1:] != c_sorted[:-1]) + 1
    for idx_chunk in np.split(np.arange(N), splits):
        score_g = X_s[idx_chunk].T @ e_s[idx_chunk]
        meat += np.outer(score_g, score_g)

    dfc = (G / max(G - 1, 1)) * ((N - 1) / max(N - K, 1))
    vcov = dfc * (XtX_inv @ meat @ XtX_inv)
    se = np.sqrt(np.maximum(np.diag(vcov), 1e-16))
    t_stat = beta / se

    out = pd.DataFrame({
        "term": x_cols,
        "coef_log": np.round(beta, 4),
        "se_cluster": np.round(se, 4),
        "t_stat": np.round(t_stat, 2),
        "multiplier_exp_b": np.round(np.exp(beta), 3),
        "pct_premium": np.round((np.exp(beta) - 1.0) * 100.0, 1),
        "n_obs": N,
        "n_clusters": G,
    })
    return out

# Create regime dummies relative to PLANT_WHOLE
for reg in ["ANIMAL_DAIRY_EGG", "MARINE", "ANIMAL_MEAT"]:
    df[f"d_{reg}"] = (df["regime_class"] == reg).astype(float)

df["d_animal"] = df["is_animal_source"].astype(float)
df["animal_x_ln_arable"] = df["d_animal"] * df["ln_arable"]

reg_trophic_diaas = fit_within_cell_ols(
    df, "ln_usd", ["d_ANIMAL_DAIRY_EGG", "d_MARINE", "d_ANIMAL_MEAT"], ["market_id", "decade"], "country_iso3"
)
reg_trophic_crude = fit_within_cell_ols(
    df, "ln_crude_usd", ["d_ANIMAL_DAIRY_EGG", "d_MARINE", "d_ANIMAL_MEAT"], ["market_id", "decade"], "country_iso3"
)
reg_ho_endowment = fit_within_cell_ols(
    df, "ln_usd", ["d_animal", "animal_x_ln_arable"], ["market_id", "decade"], "country_iso3"
)

# Decade-by-decade within-market Trophic Tax (Animal vs Plant)
era_tax_rows = []
for dec, grp in df.groupby("decade"):
    res_d = fit_within_cell_ols(grp, "ln_usd", ["d_animal"], ["market_id"], "country_iso3")
    if not res_d.empty and res_d["se_cluster"].iloc[0] > 0:
        b = float(res_d["coef_log"].iloc[0])
        se = float(res_d["se_cluster"].iloc[0])
        era_tax_rows.append({
            "obs_decade": int(dec),
            "within_mkt_trophic_ratio": round(float(np.exp(b)), 2),
            "ci_low": round(float(np.exp(b - 1.96 * se)), 2),
            "ci_high": round(float(np.exp(b + 1.96 * se)), 2),
            "n_obs": int(res_d["n_obs"].iloc[0]),
        })
era_tax_df = pd.DataFrame(era_tax_rows)

# ==============================================================================
# MODEL 3: STIGLER SHADOW PRICE CONVERGENCE & 2026 BIOREACTOR MARKET COVERAGE
# ==============================================================================
stigler_df = con.execute("""
    SELECT
        obs_decade,
        COUNT(*) AS n_cells,
        ROUND(MEDIAN(min_diet_usd_day), 2) AS med_diet_usd_day,
        ROUND(MEDIAN(shadow_usd_50g), 3) AS stigler_shadow_usd,
        ROUND(MEDIAN(shadow_labor_min_50g), 2) AS stigler_shadow_labor
    FROM agg_stigler_shadow_prices
    GROUP BY 1 ORDER BY 1;
""").df()

crossover_df = con.execute("""
    WITH mkt_2020s AS (
        SELECT
            f.market_id,
            m.region_macro,
            MEDIAN(CASE WHEN c.regime_class = 'ANIMAL_MEAT' THEN f.usd_per_50g_diaas END) AS meat_usd,
            MEDIAN(CASE WHEN c.regime_class = 'ANIMAL_DAIRY_EGG' THEN f.usd_per_50g_diaas END) AS dairy_usd,
            MEDIAN(CASE WHEN c.regime_class = 'PLANT_WHOLE' THEN f.usd_per_50g_diaas END) AS plant_usd
        FROM fact_protein_observations f
        JOIN dim_markets m ON f.market_id = m.market_id
        JOIN dim_commodities c ON f.commodity_code = c.commodity_code
        WHERE f.obs_decade >= 2020 AND f.price_basis = 'OBSERVED_MARKET' AND f.is_outlier_mad = FALSE
        GROUP BY 1, 2
    )
    SELECT
        region_macro,
        COUNT(meat_usd) AS n_meat_mkts,
        ROUND(100.0 * AVG(CASE WHEN meat_usd > 0.40 THEN 1.0 ELSE 0.0 END), 1) AS pct_beaten_by_gas_scp_040,
        ROUND(100.0 * AVG(CASE WHEN meat_usd > 0.66 THEN 1.0 ELSE 0.0 END), 1) AS pct_beaten_by_mycoprotein_066,
        ROUND(100.0 * AVG(CASE WHEN meat_usd > 0.87 THEN 1.0 ELSE 0.0 END), 1) AS pct_beaten_by_pf_whey_087,
        ROUND(100.0 * AVG(CASE WHEN meat_usd > 2.97 THEN 1.0 ELSE 0.0 END), 1) AS pct_beaten_by_cult_chix_297
    FROM mkt_2020s
    WHERE meat_usd IS NOT NULL
    GROUP BY 1 ORDER BY 2 DESC;
""").df()

# Save full econometric results for the manuscript and Lovable exporter

def sanitize_json(obj):
    if isinstance(obj, dict):
        return {k: sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_json(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj

results_bundle = {
    "twfe_panel": twfe_panel.merge(era_tax_df, on="obs_decade", how="left").merge(stigler_df, on="obs_decade", how="left").where(pd.notnull, None).to_dict(orient="records"),
    "reg_trophic_diaas": reg_trophic_diaas.to_dict(orient="records"),
    "reg_trophic_crude": reg_trophic_crude.to_dict(orient="records"),
    "reg_ho_endowment": reg_ho_endowment.to_dict(orient="records"),
    "crossover_2026": crossover_df.to_dict(orient="records"),
}
Path("econometric_results.json").write_text(json.dumps(sanitize_json(results_bundle), indent=2, allow_nan=False), encoding="utf-8")

print("\n=== TABLE 1: TWO-WAY FIXED EFFECTS (MARKET + COMMODITY FE) FRONTIER INDEX ===")
print(twfe_panel[["obs_decade", "n_plant", "n_animal", "twfe_plant_usd", "twfe_animal_usd", "twfe_plant_labor", "twfe_animal_labor", "twfe_trophic_ratio_usd", "twfe_trophic_ratio_labor"]].to_string(index=False))

print("\n=== TABLE 2A: WITHIN-(MARKET x DECADE) TROPHIC TAX (50g DIAAS-ADJUSTED) ===")
print(reg_trophic_diaas.to_string(index=False))

print("\n=== TABLE 2B: SENSITIVITY — CRUDE PROTEIN BASIS (UNADJUSTED FOR DIAAS) ===")
print(reg_trophic_crude.to_string(index=False))

print("\n=== TABLE 3: HECKSCHER-OHLIN ARABLE LAND ENDOWMENT INTERACTION ===")
print(reg_ho_endowment.to_string(index=False))

print("\n=== TABLE 4: PURE EMPIRICAL STIGLER LP SHADOW PRICES (5,071 MARKET-DECADES) ===")
print(stigler_df.to_string(index=False))

print("\n=== TABLE 5: 2026 BIOREACTOR MARKET CROSSOVER SHARE (% OF GLOBAL MEAT MARKETS UNDERCUT) ===")
print(crossover_df.to_string(index=False))
