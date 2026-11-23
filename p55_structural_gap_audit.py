import duckdb

con = duckdb.connect("protein_atlas.duckdb", read_only=True)

print("=== 1. HISTORICAL / TEMPORAL COVERAGE (CLEAN ROWS BY YEAR & PRICE BASIS) ===")
for yr, mkt_cnt, ret_cnt, fg_cnt, n_ctry in con.execute("""
    SELECT
        SUBSTR(CAST(obs_date AS VARCHAR), 1, 4) AS yr,
        SUM(CASE WHEN price_basis = 'OBSERVED_MARKET' THEN 1 ELSE 0 END) AS obs_mkt,
        SUM(CASE WHEN price_basis = 'OBSERVED_RETAIL' THEN 1 ELSE 0 END) AS obs_ret,
        SUM(CASE WHEN price_basis = 'FARM_GATE_SHADOW' THEN 1 ELSE 0 END) AS obs_fg,
        COUNT(DISTINCT country_iso3) AS countries
    FROM v_clean_protein_observations
    GROUP BY 1 ORDER BY 1 ASC;
""").fetchall():
    print(f" {str(yr):6s} | Market: {mkt_cnt:9,d} | Retail: {ret_cnt:8,d} | Farm-Gate: {fg_cnt:7,d} | Countries: {n_ctry:3d}")

print("\n=== 2. PROTEIN TAXONOMY & SOURCE-GROUP GAPS ===")
for grp, comms, rows, ctry_cnt, ret_share in con.execute("""
    WITH classified AS (
        SELECT
            CASE
                WHEN commodity_code IN ('BEEF_FRESH','POULTRY_CHIX','PORK_FRESH','MUTTON_FRESH') THEN '1. Terrestrial Meat'
                WHEN commodity_code IN ('EGGS_CHIX','MILK_FLUID','CHEESE_HARD') THEN '2. Dairy & Eggs'
                WHEN commodity_code IN ('FISH_PELAGIC_SMALL','FISH_PREDATOR_HIGH','FISH_AQUACULTURE','COD_SALTED') THEN '3. Aquatic / Seafood'
                WHEN commodity_code IN ('BEANS_DRY','LENTILS_DRY','SOYBEANS_DRY','GROUNDNUTS','TOFU_TEMPEH') THEN '4. Whole Legumes, Soy & Nuts'
                WHEN commodity_code IN ('RICE_MILLED','WHEAT_FLOUR') THEN '5. Cereal Staples (Ref)'
                ELSE '6. Concentrates, Isolates & Alt-Protein'
            END AS protein_group,
            commodity_code,
            country_iso3,
            price_basis
        FROM v_clean_protein_observations
    )
    SELECT
        protein_group,
        COUNT(DISTINCT commodity_code) AS n_commodities,
        COUNT(*) AS clean_rows,
        COUNT(DISTINCT country_iso3) AS countries,
        ROUND(100.0 * SUM(CASE WHEN price_basis = 'OBSERVED_RETAIL' THEN 1 ELSE 0 END) / COUNT(*), 2) AS retail_pct
    FROM classified
    GROUP BY 1 ORDER BY 1 ASC;
""").fetchall():
    print(f" {grp:38s} | {comms:2d} items | {rows:9,d} rows | {ctry_cnt:3d} countries | Retail share: {ret_share:5.2f}%")

print("\n=== 3. THIN COMMODITY x COUNTRY CELLS (< 10 COUNTRIES COVERED) ===")
for comm, ctry_cnt, rows, basis_list in con.execute("""
    SELECT
        commodity_code,
        COUNT(DISTINCT country_iso3) AS countries,
        COUNT(*) AS clean_rows,
        STRING_AGG(DISTINCT price_basis, ', ') AS bases
    FROM v_clean_protein_observations
    GROUP BY 1
    HAVING COUNT(DISTINCT country_iso3) < 40
    ORDER BY 2 ASC, 3 ASC;
""").fetchall():
    print(f" {comm:20s} | {ctry_cnt:3d} countries | {rows:7,d} rows | Bases: {basis_list}")

con.close()
