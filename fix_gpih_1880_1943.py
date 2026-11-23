import duckdb

con = duckdb.connect("protein_atlas.duckdb")

print("=== BEFORE FIX: GPIH_NORDHAUS COLONIAL AFRICA (1880-1943) ===")
print(con.execute("SELECT market_id, commodity_code, COUNT(*) n, ROUND(MEDIAN(nominal_price_kg),3) med_nom, ROUND(MEDIAN(usd_per_50g_diaas),2) med_usd50, ROUND(MEDIAN(labor_min_50g_diaas),1) med_min FROM fact_protein_observations WHERE source_dataset='GPIH_NORDHAUS' AND market_id IN ('HIST_FREETOWN','HIST_BANJUL','HIST_KAMPALA','HIST_LAGOS') GROUP BY 1,2 ORDER BY 1,2").df().to_string(index=False))

con.execute("UPDATE fact_protein_observations SET nominal_price_kg=nominal_price_kg/50.802345, real_usd_per_kg=real_usd_per_kg/50.802345, usd_per_50g_diaas=usd_per_50g_diaas/50.802345, labor_min_50g_diaas=labor_min_50g_diaas/50.802345 WHERE source_dataset='GPIH_NORDHAUS' AND market_id='HIST_FREETOWN' AND commodity_code='RICE_MILLED' AND usd_per_50g_diaas > 50.0;")

con.execute("UPDATE fact_protein_observations SET nominal_price_kg=nominal_price_kg/12.0, real_usd_per_kg=real_usd_per_kg/12.0, usd_per_50g_diaas=usd_per_50g_diaas/12.0, labor_min_50g_diaas=labor_min_50g_diaas/12.0 WHERE source_dataset='GPIH_NORDHAUS' AND market_id IN ('HIST_FREETOWN','HIST_BANJUL','HIST_KAMPALA','HIST_LAGOS') AND usd_per_50g_diaas > 2.5;")

print("=== AFTER FIX: GPIH_NORDHAUS COLONIAL AFRICA (1880-1943) ===")
print(con.execute("SELECT market_id, commodity_code, COUNT(*) n, ROUND(MEDIAN(nominal_price_kg),3) med_nom, ROUND(MEDIAN(usd_per_50g_diaas),2) med_usd50, ROUND(MEDIAN(labor_min_50g_diaas),1) med_min FROM fact_protein_observations WHERE source_dataset='GPIH_NORDHAUS' AND market_id IN ('HIST_FREETOWN','HIST_BANJUL','HIST_KAMPALA','HIST_LAGOS') GROUP BY 1,2 ORDER BY 1,2").df().to_string(index=False))
con.close()
