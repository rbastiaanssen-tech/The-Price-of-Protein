import time
import duckdb

t0 = time.time()
con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'STAGED_GLOBAL_CHAINS_46';")

wages = dict(con.execute("""
    SELECT m.country_iso3, MEDIAN(f.usd_per_50g_diaas / NULLIF(f.labor_min_50g_diaas / 60.0, 0))
    FROM fact_protein_observations f JOIN dim_markets m USING(market_id)
    WHERE f.labor_min_50g_diaas > 0 GROUP BY 1;
""").fetchall())

st = duckdb.connect("staging_50_chains.duckdb", read_only=True)
staged = st.execute("SELECT * FROM staged_skus;").fetchall()
st.close()

max_id = con.execute("SELECT COALESCE(MAX(obs_id), 0) FROM fact_protein_observations;").fetchone()[0]
today_str = time.strftime("%Y-%m-%d")
rows = []

for mid, mname, iso3, lat, lon, comm, nom_kg, cur, usd_kg, usd_50g in staged:
    con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?, ?, ?, 'GLOBAL_SUPERMARKETS_46', ?, ?, 0.08, 0.08);",
                [mid, mname[:80], iso3, round(lat, 5), round(lon, 5)])
    w_hr = wages.get(iso3) or 11.0
    max_id += 1
    rows.append((
        max_id, "STAGED_GLOBAL_CHAINS_46", "OBSERVED_RETAIL", mid, comm, today_str,
        nom_kg, cur, usd_kg, usd_50g, round((usd_50g / w_hr) * 60.0, 2), False
    ))

if rows:
    con.executemany("""INSERT INTO fact_protein_observations (
        obs_id, source_dataset, price_basis, market_id, commodity_code, obs_date,
        nominal_price_kg, currency_code, real_usd_per_kg, usd_per_50g_diaas, labor_min_50g_diaas, is_outlier_mad
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""", rows)

con.execute("DELETE FROM dim_markets WHERE market_id NOT IN (SELECT DISTINCT market_id FROM fact_protein_observations);")
tot_r, tot_m = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id) FROM fact_protein_observations;").fetchone()
tot_c = con.execute("SELECT COUNT(DISTINCT country_iso3) FROM dim_markets;").fetchone()[0]
print(f"[TRACK 46 COMPLETE] Merged +{len(rows):,} staged global supermarket SKUs! Total DB: {tot_r:,} rows | {tot_m:,} markets | {tot_c} countries ({time.time()-t0:.1f}s)")
con.close()
