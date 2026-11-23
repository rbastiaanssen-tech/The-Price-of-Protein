import duckdb, shutil
from pathlib import Path

db_path = Path("protein_atlas.duckdb")
tmp_path = Path("protein_atlas_clean.duckdb")
exp_dir = Path("tmp_duckdb_export")
size_before = db_path.stat().st_size / (1024 * 1024)

if exp_dir.exists():
    shutil.rmtree(exp_dir)
if tmp_path.exists():
    tmp_path.unlink()

con = duckdb.connect(str(db_path))
clean_counts = con.execute("""
    SELECT COUNT(*), COUNT(DISTINCT market_id), COUNT(DISTINCT country_iso3)
    FROM fact_protein_observations JOIN dim_markets USING(market_id);
""").fetchone()
con.execute(f"EXPORT DATABASE '{exp_dir}' (FORMAT PARQUET);")
con.close()

c_new = duckdb.connect(str(tmp_path))
c_new.execute(f"IMPORT DATABASE '{exp_dir}';")
c_new.close()

shutil.rmtree(exp_dir)
shutil.move(str(tmp_path), str(db_path))
size_after = db_path.stat().st_size / (1024 * 1024)

print("=== CLEAN & COMPACTED DATABASE ===")
print(f"  Verified Observations  : {clean_counts[0]:,}")
print(f"  Active Markets (>0 obs): {clean_counts[1]:,}")
print(f"  Countries Covered      : {clean_counts[2]:,}")
print(f"  Disk Size              : {size_before:.1f} MB -> {size_after:.1f} MB")
