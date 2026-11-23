import json
import urllib.request
from pathlib import Path

API_URL = "https://data.humdata.org/api/3/action/package_show?id=global-wfp-food-prices"
OUT_DIR = Path("raw_data/wfp")
OUT_DIR.mkdir(parents=True, exist_ok=True)

print("Querying Humanitarian Data Exchange (HDX) for WFP Global Food Prices...")
req = urllib.request.Request(API_URL, headers={"User-Agent": "ProteinPriceAtlas/1.0"})
with urllib.request.urlopen(req) as resp:
    meta = json.loads(resp.read().decode("utf-8"))

resources = meta["result"]["resources"]
csv_resources = [r for r in resources if r.get("format", "").upper() == "CSV"]

print(f"Found {len(csv_resources)} CSV files. Downloading into {OUT_DIR}/...\n")
for idx, res in enumerate(csv_resources, 1):
    url = res["url"]
    filename = url.split("/")[-1]
    dest = OUT_DIR / filename
    if dest.exists():
        print(f"[{idx}/{len(csv_resources)}] Already exists: {filename}")
        continue
    print(f"[{idx}/{len(csv_resources)}] Downloading {filename}...", end=" ", flush=True)
    urllib.request.urlretrieve(url, dest)
    size_mb = dest.stat().st_size / (1024 * 1024)
    print(f"done ({size_mb:.1f} MB)")

print("\nAll WFP Global Food Price CSVs downloaded to raw_data/wfp/!")
