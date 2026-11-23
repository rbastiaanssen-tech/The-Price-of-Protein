from __future__ import annotations
from etl_protein_atlas import ProteinAtlasETL

etl = ProteinAtlasETL("protein_atlas.duckdb", "schema.sql")
con = etl.con

con.execute("""
    UPDATE fact_protein_observations AS f
    SET
        real_usd_per_kg     = (f.nominal_price_kg / w.ppp_to_2026_usd) * w.cpi_to_2026,
        usd_per_50g_diaas   = ((f.nominal_price_kg / w.ppp_to_2026_usd) * w.cpi_to_2026)
                              * (50.0 / (c.edible_fraction * c.crude_protein_g_kg * c.prep_retention * c.diaas_score)),
        labor_min_50g_diaas = ((f.nominal_price_kg * (50.0 / (c.edible_fraction * c.crude_protein_g_kg * c.prep_retention * c.diaas_score)))
                              / w.unskilled_wage_hr) * 60.0
    FROM dim_commodities c, dim_markets m, dim_macro_wages w
    WHERE f.commodity_code = c.commodity_code
      AND f.market_id = m.market_id
      AND m.country_iso3 = w.country_iso3
      AND f.obs_year = w.year
      AND f.currency_code = w.currency_code
      AND w.unskilled_wage_hr > 0;
""")

etl.flag_mad_outliers(z_threshold=6.0)
etl.export_analytical_parquet("protein_atlas_clean.parquet")
print(con.execute("""
    SELECT obs_decade, COUNT(*) AS rows,
           ROUND(MEDIAN(usd_per_50g_diaas), 2) AS med_usd,
           ROUND(MEDIAN(labor_min_50g_diaas), 1) AS med_labor_min
    FROM fact_protein_observations
    WHERE is_outlier_mad = FALSE AND obs_decade IN (1750, 1850, 1950, 2000, 2020)
    GROUP BY 1 ORDER BY 1;
""").df().to_string(index=False))
