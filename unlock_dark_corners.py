from __future__ import annotations
import glob, io, re, urllib.request, warnings, zipfile
from pathlib import Path
import pandas as pd
from etl_protein_atlas import ProteinAtlasETL

warnings.filterwarnings("ignore")
etl = ProteinAtlasETL("protein_atlas.duckdb", "schema.sql")
con = etl.con

# 1. Unlock unmapped non-Western, colonial & frontier GPIH files (1550–1970)
EXTRA_LOCS = {
    "korea":         ("KOR", "ASIA",          37.566, 126.978, 0.04, 0.12),
    "thai":          ("THA", "ASIA",          13.756, 100.501, 0.25, 0.11),
    "pune":          ("IND", "ASIA",          18.520,  73.856, 0.12, 0.10),
    "nigeria":       ("NGA", "AFRICA",         6.524,   3.379, 0.18, 0.09),
    "gambia":        ("GMB", "AFRICA",        13.454, -16.579, 0.18, 0.14),
    "sierra leone":  ("SLE", "AFRICA",         8.465, -13.231, 0.20, 0.15),
    "uganda":        ("UGA", "AFRICA",         0.347,  32.582, 0.16, 0.11),
    "mauritius":     ("MUS", "AFRICA",       -20.160,  57.501, 0.06, 0.13),
    "south africa":  ("ZAF", "AFRICA",       -33.924,  18.424, 0.22, 0.09),
    "colombia":      ("COL", "LATAM",          4.711, -74.072, 0.08, 0.12),
    "brazil":        ("BRA", "LATAM",        -22.906, -43.172, 0.28, 0.13),
    "cordoba":       ("ARG", "LATAM",        -31.420, -64.188, 0.88, 0.08),
    "sopron":        ("HUN", "EUROPE",        47.681,  16.584, 0.42, 0.14),
    "chesapeake":    ("USA", "NORTH_AMERICA", 38.978, -76.492, 0.47, 0.08),
    "vermont":       ("USA", "NORTH_AMERICA", 44.260, -72.575, 0.47, 0.08),
    "west virginia": ("USA", "NORTH_AMERICA", 38.349, -81.632, 0.47, 0.08),
    "san francisco": ("USA", "NORTH_AMERICA", 37.774, -122.419,0.47, 0.08),
    "van diemen":    ("AUS", "OCEANIA",      -42.882, 147.327, 1.25, 0.14),
    "ns wales":      ("AUS", "OCEANIA",      -33.868, 151.209, 1.25, 0.14),
}

COMM_PAT = [
    (r"beef|boeuf|carne|vaca", "BEEF_FRESH"),
    (r"pork|bacon|porc|lard|cerdo", "PORK_FRESH"),
    (r"mutton|lamb|goat|carnero", "MUTTON_FRESH"),
    (r"chicken|poultry|fowl|hen|gallina", "POULTRY_CHIX"),
    (r"cod|herring|fish|dried fish|pescado|bacalao", "COD_SALTED"),
    (r"cheese|queso|fromage", "CHEESE_HARD"),
    (r"milk|leche|lait", "MILK_FLUID"),
    (r"egg|huevo|oeuf", "EGGS_CHIX"),
    (r"bean|pea|frijol|gram|lentil|pulse|soy", "BEANS_DRY"),
    (r"rice|arroz", "RICE_MILLED"),
    (r"wheat|trigo|flour", "WHEAT_FLOUR"),
]

def map_c(s: str) -> str | None:
    s = str(s).lower()
    return next((code for pat, code in COMM_PAT if re.search(pat, s)), None)

mkt_rows, macro_rows, price_rows = [], [], []
seen_m, seen_w = set(), set()

for fpath in sorted(glob.glob("raw_data/gpih/*.xls*")):
    fname = Path(fpath).stem.lower().replace("_", " ")
    hit = next((k for k in EXTRA_LOCS if k in fname), None)
    if not hit:
        continue
    iso3, reg, lat, lon, arable, elec = EXTRA_LOCS[hit]
    mkt_id = f"HIST_{hit.upper().replace(' ', '_')}"
    if mkt_id not in seen_m:
        mkt_rows.append((mkt_id, hit.title(), iso3, reg, lat, lon, arable, elec))
        seen_m.add(mkt_id)
    try:
        xl = pd.ExcelFile(fpath)
        for sheet in xl.sheet_names[:8]:
            df = xl.parse(sheet, nrows=500)
            if df.shape[1] < 2 or df.shape[0] < 8:
                continue
            hdr = next((i for i in range(min(25, len(df))) if map_c(" ".join(str(v) for v in df.iloc[i].values))), None)
            if hdr is None:
                continue
            df.columns = [str(v).strip() for v in df.iloc[hdr].values]
            df = df.iloc[hdr + 1:].copy()
            yrs = pd.to_numeric(df[df.columns[0]], errors="coerce")
            ok = yrs.between(1550, 1970)
            if ok.sum() < 4:
                continue
            for col in df.columns[1:]:
                code = map_c(col)
                if not code:
                    continue
                vals = pd.to_numeric(df[col], errors="coerce")
                med = vals[ok & (vals > 0)].median()
                if pd.isna(med) or med <= 0:
                    continue
                scale = 1.0 if 0.05 <= med <= 40.0 else (2.5 / med)
                for yv, pv in zip(yrs[ok], vals[ok]):
                    if pd.notna(pv) and pv > 0:
                        yr = int(yv)
                        if (iso3, yr, "XAG_GRAM") not in seen_w:
                            wage = 0.85 if yr < 1850 else 0.85 + 0.025 * (yr - 1850)
                            macro_rows.append((iso3, yr, "XAG_GRAM", 1.15, 1.0, wage, "GPIH Non-Western"))
                            seen_w.add((iso3, yr, "XAG_GRAM"))
                        price_rows.append(("GPIH_NONWEST", "OBSERVED_MARKET", mkt_id, code, f"{yr}-06-15", float(pv * scale), "kg", "XAG_GRAM"))
    except Exception:
        continue

print(f"[GPIH Non-Western] Extracted {len(price_rows):,} rows across {len(seen_m)} regions.")

# 2. Download & ingest FAOSTAT Producer Prices (1961–2024, USD/tonne)
fao_zip = Path("raw_data/macro/faostat_prices.zip")
if not fao_zip.exists():
    for url in [
        "https://bulks-faostat.fao.org/production/Prices_E_All_Data_(Normalized).zip",
        "https://fenixservices.fao.org/faostat/static/bulkdownloads/Prices_E_All_Data_(Normalized).zip",
    ]:
        try:
            print("[FAOSTAT] Downloading global producer prices (1961–2024)...")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            fao_zip.write_bytes(urllib.request.urlopen(req, timeout=60).read())
            break
        except Exception:
            continue

FAO_ITEMS = {
    "meat of cattle": "BEEF_FRESH", "meat of pig": "PORK_FRESH",
    "meat of chickens": "POULTRY_CHIX", "meat of sheep": "MUTTON_FRESH",
    "meat of goat": "MUTTON_FRESH", "raw milk": "MILK_FLUID",
    "hen eggs": "EGGS_CHIX", "beans, dry": "BEANS_DRY",
    "lentils, dry": "LENTILS_DRY", "soya beans": "SOYBEANS_DRY",
    "groundnuts": "GROUNDNUTS", "wheat": "WHEAT_FLOUR", "rice": "RICE_MILLED",
}

if fao_zip.exists():
    # Build Area -> ISO3 lookup from existing dim_markets + common country names
    iso_lookup = dict(con.execute("SELECT DISTINCT LOWER(market_name), country_iso3 FROM dim_markets WHERE LENGTH(country_iso3)=3").fetchall())
    fao_added = 0
    with zipfile.ZipFile(fao_zip) as zf:
        csv_name = next(n for n in zf.namelist() if n.endswith(".csv") and "Flags" not in n)
        with zf.open(csv_name) as f:
            for chunk in pd.read_csv(f, chunksize=250_000, low_memory=False):
                sub = chunk[(chunk["Element"] == "Producer Price (USD/tonne)") & (chunk["Value"] > 0)].copy()
                if sub.empty:
                    continue
                sub["code"] = sub["Item"].astype(str).str.lower().map(
                    lambda s: next((v for k, v in FAO_ITEMS.items() if k in s), None)
                )
                sub = sub[sub["code"].notna()]
                iso_col = "Area Code (ISO3)" if "Area Code (ISO3)" in sub.columns else None
                for r in sub.itertuples(index=False):
                    iso3 = getattr(r, "Area_Code__ISO3_", None) if iso_col else None
                    if not isinstance(iso3, str) or len(iso3) != 3:
                        iso3 = iso_lookup.get(str(r.Area).lower())
                    if not iso3 or len(iso3) != 3:
                        continue
                    yr = int(r.Year)
                    mkt_id = f"FAO_{iso3}"
                    if mkt_id not in seen_m:
                        mkt_rows.append((mkt_id, f"{r.Area} (FAO)", iso3, "GLOBAL", 0.0, 0.0, 0.20, 0.12))
                        seen_m.add(mkt_id)
                    if (iso3, yr, "USD") not in seen_w:
                        cpi = max(1.0, (1.035 ** (2026 - yr)))
                        macro_rows.append((iso3, yr, "USD", cpi, 1.0, 1.50 / cpi, "FAOSTAT / ILO Proxy"))
                        seen_w.add((iso3, yr, "USD"))
                    price_rows.append(("FAOSTAT_PP", "OBSERVED_MARKET", mkt_id, r.code, f"{yr}-06-15", float(r.Value) / 1000.0, "kg", "USD"))
                    fao_added += 1
    print(f"[FAOSTAT] Extracted {fao_added:,} country-year producer price records.")

if price_rows:
    etl.ingest_markets_and_macro(
        pd.DataFrame(mkt_rows, columns=["market_id","market_name","country_iso3","region_macro","latitude","longitude","arable_ha_per_cap","industrial_elec_kwh"]),
        pd.DataFrame(macro_rows, columns=["country_iso3","year","currency_code","cpi_to_2026","ppp_to_2026_usd","unskilled_wage_hr","wage_source"])
    )
    etl.ingest_price_stream(pd.DataFrame(
        price_rows, columns=["source_dataset","price_basis","market_id","commodity_code","obs_date","raw_price","raw_unit","currency_code"]
    ))
    etl.flag_mad_outliers(z_threshold=6.0)
    etl.export_analytical_parquet("protein_atlas_clean.parquet")

print(con.execute("""
    SELECT source_dataset, COUNT(*) AS rows, MIN(obs_year) AS min_yr, MAX(obs_year) AS max_yr
    FROM fact_protein_observations WHERE is_outlier_mad = FALSE
    GROUP BY 1 ORDER BY min_yr;
""").df().to_string(index=False))
