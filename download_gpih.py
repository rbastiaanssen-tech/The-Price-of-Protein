import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = "https://gpih.ucdavis.edu/Datafilelist.htm"
OUT_DIR = Path("raw_data/gpih")
OUT_DIR.mkdir(parents=True, exist_ok=True)

print("Fetching file index from UC Davis GPIH...")
req = urllib.request.Request(BASE_URL, headers={"User-Agent": "ProteinPriceAtlas/1.0"})
with urllib.request.urlopen(req) as resp:
    html = resp.read().decode("utf-8", errors="ignore")

# Find all spreadsheet links (.xls, .xlsx, .csv, .zip)
links = re.findall(r'href=["\']([^"\']+\.(?:xls|xlsx|csv|zip))["\']', html, flags=re.IGNORECASE)
unique_urls = sorted(set(urllib.parse.urljoin(BASE_URL, link) for link in links))

print(f"Found {len(unique_urls)} archival data files. Downloading into {OUT_DIR}/...\n")

for idx, url in enumerate(unique_urls, 1):
    filename = urllib.parse.unquote(url.split("/")[-1]).replace(" ", "_")
    dest = OUT_DIR / filename
    if dest.exists():
        print(f"[{idx}/{len(unique_urls)}] Already exists: {filename}")
        continue
    try:
        print(f"[{idx}/{len(unique_urls)}] Downloading {filename}...", end=" ", flush=True)
        file_req = urllib.request.Request(url, headers={"User-Agent": "ProteinPriceAtlas/1.0"})
        with urllib.request.urlopen(file_req, timeout=30) as f_resp:
            dest.write_bytes(f_resp.read())
        size_kb = dest.stat().st_size / 1024
        print(f"done ({size_kb:.0f} KB)")
        time.sleep(0.3)  # Polite crawl delay
    except Exception as e:
        print(f"failed ({e})")

print("\nFinished downloading GPIH archive into raw_data/gpih/!")
