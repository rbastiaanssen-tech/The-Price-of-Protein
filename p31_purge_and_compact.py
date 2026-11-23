import duckdb, os, shutil
from pathlib import Path

db_path = Path("protein_atlas.duckdb")
tmp_path = Path("protein_atlas_clean.duckdb")
size_before = db_path.stat().st_size / (1024 * 1024)

con = duckdb.connect(str(db_path))

print("=== 1. PRE-PURGE AUDIT ===")
audit = con.execute("""
    SELECT
        (SELECT COUNT(*) FROM fact_protein_observations) AS total_obs,
        (SELECT COUNT(*) FROM fact_protein_observations WHERE is_outlier_mad = TRUE) AS mad_outliers,
        (SELECT COUNT(*) FROM fact_protein_observations WHERE real_usd_per_kg IS NULL OR real_usd_per_kg <= 0 OR usd_per_50g_diaas IS NULL OR usd_per_50g_diaas <= 0) AS null_or_zero_prices,
        (SELECT COUNT(*) FROM fact_protein_observations WHERE market_id LIKE '%FALLBACK%') AS fallback_rows,
        (SELECT COUNT(*) FROM dim_markets) AS total_markets,
        (SELECT COUNT(*) FROM dim_markets m WHERE NOT EXISTS (SELECT 1 FROM fact_protein_observations f WHERE f.market_id = m.market_id)) AS empty_markets
""").fetchone()

print(f"  Total Observations : {audit[0]:,}")
print(f"  MAD Outliers       : {audit[1]:,}")
print(f"  Null/Zero Prices   : {audit[2]:,}")
print(f"  Fallback Rows      : {audit[3]:,}")
print(f"  Total Markets      : {audit[4]:,}")
print(f"  Empty Markets (0 obs): {audit[5]:,}")

print("\n=== 2. PURGING EMPTY & INVALID ITEMS ===")
# Remove outliers, null/zero prices, and generic fallback rows
con.execute("""
    DELETE FROM fact_protein_observations
    WHERE is_outlier_mad = TRUE
       OR real_usd_per_kg IS NULL OR real_usd_per_kg <= 0
       OR usd_per_50g_diaas IS NULL OR usd_per_50g_diaas <= 0
       OR market_id LIKE '%FALLBACK%';
""")

# Remove orphan markets in dim_markets with zero observations
con.execute("""
    DELETE FROM dim_markets
    WHERE market_id NOT IN (SELECT DISTINCT market_id FROM fact_protein_observations);
""")

# Remove any unused commodities with zero observations (optional: keep or prune—let's check count)
unused_comms = con.execute("""
    SELECT commodity_code FROM dim_commodities
    WHERE commodity_code NOT IN (SELECT DISTINCT commodity_code FROM fact_protein_observations);
""").fetchall()
print(f"  Unused Commodity Codes (0 obs): {[r[0] for r in unused_comms]}")

clean_counts = con.execute("""
    SELECT COUNT(*), COUNT(DISTINCT market_id), COUNT(DISTINCT country_iso3)
    FROM fact_protein_observations JOIN dim_markets USING(market_id);
""").fetchone()
con.close()

# Compact DuckDB file via COPY FROM DATABASE to reclaim deleted block space
if tmp_path.exists():
    tmp_path.unlink()

c_mem = duckdb.connect(":memory:")
c_mem.execute(f"ATTACH '{db_path}' AS src (READ_ONLY);")
c_mem.execute(f"ATTACH '{tmp_path}' AS dst;")
c_mem.execute("COPY FROM DATABASE src TO dst;")
c_mem.close()

shutil.move(str(tmp_path), str(db_path))
size_after = db_path.stat().st_size / (1024 * 1024)

print("\n=== 3. POST-PURGE CLEAN DATABASE ===")
print(f"  Verified Observations : {clean_counts[0]:,}")
print(f"  Active Markets (>0 obs): {clean_counts[1]:,}")
print(f"  Countries Covered     : {clean_counts[2]:,}")
print(f"  Disk Size             : {size_before:.1f} MB -> {size_after:.1f} MB")
