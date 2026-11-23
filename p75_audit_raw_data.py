import json
import pandas as pd
from pathlib import Path

print("=== 1. CURRENT JSON EXPORTS IN public/data/ ===")
for p in sorted(Path("public/data").glob("*.json")):
    raw = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        n = len(raw)
    elif isinstance(raw, dict):
        lists = [len(v) for v in raw.values() if isinstance(v, list)]
        n = max(lists) if lists else len(raw)
    else:
        n = 0
    print(f"  {p.name:35s} -> {p.stat().st_size/1e6:6.2f} MB | primary records: {n:,}")

print("\n=== 2. RAW / INTERMEDIATE PARQUET & CSV COVERAGE ===")
if Path("protein_atlas_clean.parquet").exists():
    df_pa = pd.read_parquet("protein_atlas_clean.parquet")
    print(f"  protein_atlas_clean.parquet         -> {len(df_pa):,} rows | cols: {list(df_pa.columns[:12])}")

if Path("data_raw/open_prices.parquet").exists():
    df_op = pd.read_parquet("data_raw/open_prices.parquet")
    print(f"  open_prices.parquet                 -> {len(df_op):,} rows | cols: {list(df_op.columns[:14])}")

if Path("raw_data/wfp/wfp_markets_global.csv").exists():
    df_wm = pd.read_csv("raw_data/wfp/wfp_markets_global.csv")
    print(f"  wfp_markets_global.csv              -> {len(df_wm):,} markets | cols: {list(df_wm.columns)}")

if Path("raw_data/wfp/wfp_food_prices_global_2026.csv").exists():
    df_wp = pd.read_csv("raw_data/wfp/wfp_food_prices_global_2026.csv", low_memory=False)
    uniq_m = df_wp["market"].nunique() if "market" in df_wp.columns else df_wp.iloc[:, 3].nunique()
    print(f"  wfp_food_prices_global_2026.csv     -> {len(df_wp):,} quotes | unique markets: {uniq_m:,}")
