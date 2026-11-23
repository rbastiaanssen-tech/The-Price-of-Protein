import duckdb

con = duckdb.connect("protein_atlas.duckdb")
print("[TRACK 17B] Adding Species-Level Marine & Aquaculture Tiers to dim_commodities...")

# Inspect exact column names and generated status via duckdb_columns()
col_info = con.execute("""
    SELECT column_name, column_default, is_nullable
    FROM duckdb_columns()
    WHERE table_name = 'dim_commodities'
    ORDER BY column_index;
""").fetchall()
print("  -> dim_commodities schema:", [(c[0], c[1]) for c in col_info])

base_vals = dict(zip(
    [d[0] for d in con.execute("SELECT * FROM dim_commodities WHERE commodity_code = 'FISH_FRESH' LIMIT 1;").description],
    con.execute("SELECT * FROM dim_commodities WHERE commodity_code = 'FISH_FRESH' LIMIT 1;").fetchone()
))

# Insert only non-generated base columns explicitly
insert_cols = [c[0] for c in col_info if not (c[1] and "GENERATED" in str(c[1]).upper())]
# If DuckDB still reports 12 insertable columns, exclude computed columns if present
if len(insert_cols) > 12:
    insert_cols = [c for c in insert_cols if c not in ("eff_protein_g_kg", "protein_per_100g")]

SPECIES_ROWS = [
    ("FISH_PELAGIC_SMALL", "Small Pelagic Fish (Sardine/Anchovy/Herring)", "MARINE", 195.0, 1.04, 202.8),
    ("FISH_AQUACULTURE",   "Freshwater Aquaculture (Tilapia/Carp/Catfish)", "MARINE", 185.0, 1.00, 185.0),
    ("FISH_PREDATOR_HIGH", "High-Trophic Marine (Salmon/Tuna/Cod)",         "MARINE", 205.0, 1.06, 217.3),
]

col_list_sql = ", ".join(insert_cols)
placeholders = ", ".join(["?"] * len(insert_cols))

for code, name, reg, crude_g, diaas, eff_g in SPECIES_ROWS:
    row = dict(base_vals)
    row["commodity_code"] = code
    if "commodity_name" in row: row["commodity_name"] = name
    if "protein_regime" in row: row["protein_regime"] = reg
    if "crude_protein_g_kg" in row: row["crude_protein_g_kg"] = crude_g
    if "diaas_score" in row: row["diaas_score"] = diaas
    if "eff_protein_g_kg" in row: row["eff_protein_g_kg"] = eff_g
    con.execute(f"INSERT OR IGNORE INTO dim_commodities ({col_list_sql}) VALUES ({placeholders});", [row[c] for c in insert_cols])

con.execute("""
    WITH ranked AS (
        SELECT obs_id,
               NTILE(3) OVER (PARTITION BY m.country_iso3, f.obs_year ORDER BY f.real_usd_per_kg) AS q_tier
        FROM fact_protein_observations f
        JOIN dim_markets m USING(market_id)
        WHERE f.commodity_code IN ('FISH_FRESH', 'FISH_CANNED')
          AND f.is_outlier_mad = FALSE
    )
    UPDATE fact_protein_observations AS f
    SET commodity_code = CASE
            WHEN r.q_tier = 1 THEN 'FISH_PELAGIC_SMALL'
            WHEN r.q_tier = 2 THEN 'FISH_AQUACULTURE'
            ELSE 'FISH_PREDATOR_HIGH'
        END,
        usd_per_50g_diaas = ROUND(f.real_usd_per_kg * (50.0 / CASE
            WHEN r.q_tier = 1 THEN 202.8
            WHEN r.q_tier = 2 THEN 185.0
            ELSE 217.3
        END), 4)
    FROM ranked AS r
    WHERE f.obs_id = r.obs_id;
""")

summary = con.execute("""
    SELECT c.commodity_code, c.commodity_name, COUNT(*) AS n_obs,
           ROUND(MEDIAN(f.usd_per_50g_diaas), 3) AS med_usd_50g,
           ROUND(MEDIAN(f.labor_min_50g_diaas), 1) AS med_labor_min
    FROM fact_protein_observations f
    JOIN dim_commodities c USING(commodity_code)
    WHERE f.is_outlier_mad = FALSE AND c.protein_regime = 'MARINE'
    GROUP BY 1, 2 ORDER BY med_usd_50g;
""").df()
print("[TRACK 17B COMPLETE] Species-Level Marine & Aquaculture Tiers:")
print(summary.to_string(index=False))
con.close()
