import duckdb
con = duckdb.connect("protein_atlas.duckdb", read_only=True)

print("=== 1. WITHIN-REGION LABOR MINUTES (50g DIAAS RDA) ===")
print(con.execute("""
    SELECT m.region_macro, f.obs_decade, COUNT(*) AS n,
           ROUND(MEDIAN(f.labor_min_50g_diaas), 1) AS med_min,
           ROUND(QUANTILE_CONT(f.labor_min_50g_diaas, 0.10), 1) AS p10_frontier_min
    FROM fact_protein_observations f JOIN dim_markets m USING (market_id)
    WHERE f.is_outlier_mad = FALSE AND f.obs_decade IN (1750, 1850, 1900, 1980, 2000, 2020)
    GROUP BY 1, 2 HAVING COUNT(*) >= 20 ORDER BY 1, 2;
""").df().to_string(index=False))

print("\n=== 2. TROPHIC TAX: ANIMAL VS. PLANT MEDIAN COST (2020s) ===")
print(con.execute("""
    SELECT c.is_animal_source, c.regime_class, COUNT(*) AS n,
           ROUND(MEDIAN(f.usd_per_50g_diaas), 2) AS med_usd,
           ROUND(MEDIAN(f.labor_min_50g_diaas), 1) AS med_min
    FROM fact_protein_observations f JOIN dim_commodities c USING (commodity_code)
    WHERE f.is_outlier_mad = FALSE AND f.obs_decade = 2020
    GROUP BY 1, 2 ORDER BY 4;
""").df().to_string(index=False))

print("\n=== 3. 2026 BIOREACTOR CROSSOVER BENCHMARKS ===")
print(con.execute("""
    SELECT f.commodity_code, f.price_basis, m.market_name,
           ROUND(f.usd_per_50g_diaas, 2) AS usd_50g,
           ROUND(f.labor_min_50g_diaas, 1) AS labor_min
    FROM fact_protein_observations f JOIN dim_markets m USING (market_id)
    WHERE f.obs_year = 2026 AND f.source_dataset IN ('TEA_BENCHMARK', 'OECD_RETAIL')
    ORDER BY 4;
""").df().to_string(index=False))
