import os, time
import duckdb

t0 = time.time()
out_dir = "exports"
os.makedirs(out_dir, exist_ok=True)

con = duckdb.connect("protein_atlas.duckdb", read_only=True)

TABLES_TO_EXPORT = [
    ("mart_country_protein_parity",      "country_iso3, price_basis, affordability_rank"),
    ("mart_historical_protein_trends",   "obs_year, country_iso3, price_basis, commodity_code"),
    ("mart_frontier_protein_benchmarks", "med_usd_50g_diaas ASC"),
    ("dim_country_wages",                "hourly_wage_usd DESC"),
]

print(f"[TRACK 57] Exporting clean analytical marts to ./{out_dir}/ ...")

for tbl, order_clause in TABLES_TO_EXPORT:
    csv_path = os.path.join(out_dir, f"{tbl}.csv")
    pq_path  = os.path.join(out_dir, f"{tbl}.parquet")

    con.execute(f"COPY (SELECT * FROM {tbl} ORDER BY {order_clause}) TO '{csv_path}' (FORMAT CSV, HEADER, DELIMITER ',');")
    con.execute(f"COPY (SELECT * FROM {tbl} ORDER BY {order_clause}) TO '{pq_path}' (FORMAT PARQUET, COMPRESSION ZSTD);")

    n_rows = con.execute(f"SELECT COUNT(*) FROM {tbl};").fetchone()[0]
    csv_kb = os.path.getsize(csv_path) / 1024.0
    pq_kb  = os.path.getsize(pq_path) / 1024.0
    print(f" -> {tbl:34s} | {n_rows:7,d} rows | CSV: {csv_kb:7.1f} KB | Parquet: {pq_kb:6.1f} KB")

con.close()
print(f"\n[TRACK 57 COMPLETE] All CSV & Parquet deliverables exported in {time.time()-t0:.2f}s")
