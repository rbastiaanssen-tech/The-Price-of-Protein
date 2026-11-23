from __future__ import annotations
import glob
from pathlib import Path
import pandas as pd

files = sorted(glob.glob("raw_data/gpih/*.xls*"))
print(f"Found {len(files)} workbooks in raw_data/gpih/\n" + "=" * 72)

for fpath in files:
    fname = Path(fpath).name
    # Skip Allen-Unger European workbooks already parsed cleanly
    if "allen" in fname.lower() and "europe" in fname.lower():
        continue
    try:
        xl = pd.ExcelFile(fpath)
        print(f"\nFILE: {fname}")
        print(f"SHEETS: {xl.sheet_names}")
        for sheet in xl.sheet_names[:5]:
            df = xl.parse(sheet, header=None, nrows=14)
            df = df.dropna(how="all").dropna(axis=1, how="all")
            if df.empty:
                continue
            print(f"  --- Sheet: {sheet} (shape {df.shape}) ---")
            for idx, row in df.head(10).iterrows():
                vals = [str(v).strip().replace("\n", " ")[:38] for v in row.values[:8] if pd.notna(v)]
                if vals:
                    print(f"    r{idx:02d}: {' | '.join(vals)}")
    except Exception as e:
        print(f"\nFILE: {fname} -> ERROR: {e}")
