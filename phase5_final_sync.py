import json, duckdb
from pathlib import Path

con = duckdb.connect("protein_atlas.duckdb")
n_bad = con.execute("SELECT COUNT(*) FROM fact_protein_observations WHERE source_dataset='FAO_BULK_PRIMARY' AND market_id='FAO_BULK_PER' AND usd_per_50g_diaas > 25;").fetchone()[0]
if n_bad:
    con.execute("DELETE FROM fact_protein_observations WHERE source_dataset='FAO_BULK_PRIMARY' AND market_id='FAO_BULK_PER';")
    con.execute("DELETE FROM dim_markets WHERE market_id='FAO_BULK_PER';")
    print(f"[AUDIT] Purged {n_bad} Peru FAOSTAT rows due to Inti/Sol scale shift.")

print("=== 100% EMPIRICAL CORPUS SUMMARY ===")
print(con.execute("SELECT source_dataset, price_basis, COUNT(DISTINCT m.country_iso3) AS countries, MIN(obs_year) AS y0, MAX(obs_year) AS y1, COUNT(*) AS n_obs FROM fact_protein_observations f JOIN dim_markets m USING (market_id) WHERE f.is_outlier_mad = FALSE GROUP BY 1, 2 ORDER BY 6 DESC;").df().to_string(index=False))

print("\n=== MODERN (2020-2026) REGIONAL COVERAGE ===")
print(con.execute("SELECT m.region_macro, COUNT(DISTINCT m.country_iso3) AS countries, COUNT(DISTINCT f.market_id) AS markets, COUNT(*) AS obs_2020_26 FROM fact_protein_observations f JOIN dim_markets m USING (market_id) WHERE f.obs_year >= 2020 AND f.price_basis = 'OBSERVED_MARKET' AND f.is_outlier_mad = FALSE GROUP BY 1 ORDER BY 4 DESC;").df().to_string(index=False))
con.close()
