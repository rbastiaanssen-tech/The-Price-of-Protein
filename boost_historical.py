from __future__ import annotations
import glob, re, warnings
from pathlib import Path
import pandas as pd
from etl_protein_atlas import ProteinAtlasETL

warnings.filterwarnings("ignore")

LOC_MAP = {
    "london": ("GBR", "EUROPE", 51.507, -0.127, 0.09, 0.24),
    "england": ("GBR", "EUROPE", 52.000, -1.200, 0.12, 0.24),
    "edinburgh": ("GBR", "EUROPE", 55.953, -3.188, 0.12, 0.24),
    "amsterdam": ("NLD", "EUROPE", 52.367, 4.904, 0.06, 0.18),
    "holland": ("NLD", "EUROPE", 52.200, 4.600, 0.06, 0.18),
    "antwerp": ("BEL", "EUROPE", 51.219, 4.402, 0.07, 0.19),
    "ghent": ("BEL", "EUROPE", 51.054, 3.717, 0.07, 0.19),
    "paris": ("FRA", "EUROPE", 48.856, 2.352, 0.27, 0.15),
    "strasbourg": ("FRA", "EUROPE", 48.573, 7.752, 0.27, 0.15),
    "aix": ("FRA", "EUROPE", 43.529, 5.447, 0.27, 0.15),
    "france": ("FRA", "EUROPE", 47.000, 2.500, 0.27, 0.15),
    "madrid": ("ESP", "EUROPE", 40.416, -3.703, 0.25, 0.14),
    "barcelona": ("ESP", "EUROPE", 41.387, 2.168, 0.25, 0.14),
    "cataluna": ("ESP", "EUROPE", 41.600, 1.800, 0.25, 0.14),
    "valladolid": ("ESP", "EUROPE", 41.652, -4.728, 0.25, 0.14),
    "spain": ("ESP", "EUROPE", 40.400, -3.700, 0.25, 0.14),
    "lisbon": ("PRT", "EUROPE", 38.722, -9.139, 0.10, 0.15),
    "portugal": ("PRT", "EUROPE", 39.500, -8.000, 0.10, 0.15),
    "vienna": ("AUT", "EUROPE", 48.208, 16.373, 0.15, 0.16),
    "munich": ("DEU", "EUROPE", 48.135, 11.582, 0.14, 0.20),
    "leipzig": ("DEU", "EUROPE", 51.339, 12.373, 0.14, 0.20),
    "augsburg": ("DEU", "EUROPE", 48.370, 10.897, 0.14, 0.20),
    "frankfurt": ("DEU", "EUROPE", 50.110, 8.682, 0.14, 0.20),
    "gdansk": ("POL", "EUROPE", 54.352, 18.646, 0.29, 0.14),
    "warsaw": ("POL", "EUROPE", 52.229, 21.012, 0.29, 0.14),
    "krakow": ("POL", "EUROPE", 50.064, 19.945, 0.29, 0.14),
    "lviv": ("UKR", "EUROPE", 49.839, 24.029, 0.65, 0.10),
    "milan": ("ITA", "EUROPE", 45.464, 9.190, 0.12, 0.22),
    "florence": ("ITA", "EUROPE", 43.769, 11.255, 0.12, 0.22),
    "italy": ("ITA", "EUROPE", 44.500, 11.000, 0.12, 0.22),
    "stockholm": ("SWE", "EUROPE", 59.329, 18.068, 0.24, 0.07),
    "copenhagen": ("DNK", "EUROPE", 55.676, 12.568, 0.40, 0.11),
    "moscow": ("RUS", "EUROPE", 55.755, 37.617, 0.85, 0.08),
    "russia": ("RUS", "EUROPE", 55.755, 37.617, 0.85, 0.08),
    "istanbul": ("TUR", "MENA", 41.008, 28.978, 0.23, 0.11),
    "cairo": ("EGY", "MENA", 30.044, 31.235, 0.03, 0.07),
    "beijing": ("CHN", "ASIA", 39.904, 116.407, 0.08, 0.09),
    "china": ("CHN", "ASIA", 35.000, 114.000, 0.08, 0.09),
    "india": ("IND", "ASIA", 23.000, 78.000, 0.12, 0.10),
    "kyoto": ("JPN", "ASIA", 35.011, 135.768, 0.03, 0.19),
    "osaka": ("JPN", "ASIA", 34.693, 135.502, 0.03, 0.19),
    "japan": ("JPN", "ASIA", 35.676, 139.650, 0.03, 0.19),
    "boston": ("USA", "NORTH_AMERICA", 42.360, -71.058, 0.47, 0.08),
    "massachusetts": ("USA", "NORTH_AMERICA", 42.360, -71.058, 0.47, 0.08),
    "philadelphia": ("USA", "NORTH_AMERICA", 39.952, -75.165, 0.47, 0.08),
    "penn": ("USA", "NORTH_AMERICA", 40.000, -76.000, 0.47, 0.08),
    "new york": ("USA", "NORTH_AMERICA", 40.712, -74.006, 0.47, 0.08),
    "maryland": ("USA", "NORTH_AMERICA", 39.000, -76.700, 0.47, 0.08),
    "mexico": ("MEX", "LATAM", 19.432, -99.133, 0.18, 0.11),
    "lima": ("PER", "LATAM", -12.046, -77.042, 0.11, 0.10),
    "peru": ("PER", "LATAM", -12.046, -77.042, 0.11, 0.10),
    "potosi": ("BOL", "LATAM", -19.583, -65.753, 0.38, 0.09),
    "bolivia": ("BOL", "LATAM", -17.000, -65.000, 0.38, 0.09),
    "buenos aires": ("ARG", "LATAM", -34.603, -58.381, 0.88, 0.08),
    "argentina": ("ARG", "LATAM", -34.603, -58.381, 0.88, 0.08),
    "chile": ("CHL", "LATAM", -33.448, -70.669, 0.07, 0.11),
}

COMM_PATTERNS = [
    (r"beef|boeuf|carne|rindfleisch|vaca", "BEEF_FRESH"),
    (r"pork|bacon|porc|lard|cerdo|schwein", "PORK_FRESH"),
    (r"mutton|lamb|mouton|carnero|hammel", "MUTTON_FRESH"),
    (r"chicken|poultry|fowl|hen|poulet|gallina|huhn", "POULTRY_CHIX"),
    (r"cod|herring|fish|morue|bacalao|pescado|fisch|hareng", "COD_SALTED"),
    (r"cheese|fromage|queso|kase|käse", "CHEESE_HARD"),
    (r"milk|lait|leche|milch", "MILK_FLUID"),
    (r"egg|oeuf|huevo|eier", "EGGS_CHIX"),
    (r"lentil|lentille|lenteja", "LENTILS_DRY"),
    (r"bean|pea|pois|frijol|habas|erbsen|pulse|gram|dal", "BEANS_DRY"),
]

def map_comm(text: str) -> str | None:
    s = str(text).lower()
    for pat, code in COMM_PATTERNS:
        if re.search(pat, s):
            return code
    return None

def main():
    etl = ProteinAtlasETL("protein_atlas.duckdb", "schema.sql")
    con = etl.con

    con.execute("""
        INSERT OR REPLACE INTO dim_units VALUES
        ('kilogram', 1.0, 'Full spelling kg'),
        ('gram', 0.001, 'Full spelling gram'),
        ('litres', 1.03, 'Plural litres'),
        ('number', 0.0567, 'Single egg count');
    """)

    # 1. Re-parse Allen-Unger with full unit & regional coverage
    au = pd.read_csv("raw_data/historical/all_commodities.csv", low_memory=False)
    au.columns = [c.strip() for c in au.columns]
    au["yr"] = pd.to_numeric(au["Item Year"], errors="coerce")
    au["val"] = pd.to_numeric(au["Standard Value"], errors="coerce")
    au = au[au["yr"].between(1700, 1950) & (au["val"] > 0)].copy()
    au["code"] = au["Commodity"].map(map_comm)
    au["meas"] = au["Standard Measure"].astype(str).str.strip().str.lower()
    au = au[au["code"].notna() & au["meas"].isin(["kg", "kilogram", "gram", "litre", "litres", "number", "dozen"])].copy()

    mkt_rows, macro_rows, price_rows = [], [], []
    seen_m, seen_w = set(), set()

    for row in au.to_dict("records"):
        loc_s = str(row["Location"]).lower()
        hit = next((k for k in LOC_MAP if k in loc_s), None)
        if not hit:
            continue
        iso3, reg, lat, lon, arable, elec = LOC_MAP[hit]
        mkt_id = f"HIST_{hit.upper().replace(' ', '_')}"
        if mkt_id not in seen_m:
            mkt_rows.append((mkt_id, hit.title(), iso3, reg, lat, lon, arable, elec))
            seen_m.add(mkt_id)
        yr = int(row["yr"])
        if (iso3, yr, "XAG_GRAM") not in seen_w:
            wage = 0.95 if yr < 1850 else 0.95 + 0.025 * (yr - 1850)
            macro_rows.append((iso3, yr, "XAG_GRAM", 1.15, 1.0, wage, "Allen-Unger Silver"))
            seen_w.add((iso3, yr, "XAG_GRAM"))
        price_rows.append(("ALLEN_UNGER_FULL", "OBSERVED_MARKET", mkt_id, row["code"], f"{yr}-06-15", float(row["val"]), row["meas"], "XAG_GRAM"))

    # 2. Scan UC Davis GPIH Excel workbooks for wide-format Year x Commodity tables
    gpih_count = 0
    for fpath in sorted(glob.glob("raw_data/gpih/*.xls*")):
        fname = Path(fpath).stem.lower()
        if any(skip in fname for skip in ["firewood", "rent", "silver", "unit", "weight", "paper", "wage", "salary"]):
            continue
        hit = next((k for k in LOC_MAP if k in fname), None)
        if not hit:
            continue
        iso3, reg, lat, lon, arable, elec = LOC_MAP[hit]
        mkt_id = f"HIST_{hit.upper().replace(' ', '_')}"
        if mkt_id not in seen_m:
            mkt_rows.append((mkt_id, hit.title(), iso3, reg, lat, lon, arable, elec))
            seen_m.add(mkt_id)

        try:
            xl = pd.ExcelFile(fpath)
            for sheet in xl.sheet_names[:4]:
                df = xl.parse(sheet, nrows=450)
                if df.shape[1] < 2 or df.shape[0] < 10:
                    continue
                # Find header row containing protein keywords
                hdr_idx = None
                for i in range(min(15, len(df))):
                    row_txt = " ".join(str(v) for v in df.iloc[i].values)
                    if map_comm(row_txt):
                        hdr_idx = i
                        break
                if hdr_idx is None:
                    continue
                df.columns = [str(v).strip() for v in df.iloc[hdr_idx].values]
                df = df.iloc[hdr_idx + 1:].copy()
                yr_col = df.columns[0]
                yrs = pd.to_numeric(df[yr_col], errors="coerce")
                valid_yr = yrs.between(1700, 1950)
                if valid_yr.sum() < 5:
                    continue

                for col in df.columns[1:]:
                    code = map_comm(col)
                    if not code:
                        continue
                    vals = pd.to_numeric(df[col], errors="coerce")
                    med_v = vals[valid_yr & (vals > 0)].median()
                    if pd.isna(med_v) or med_v <= 0:
                        continue
                    # Normalize silver/local index scale to ~kg silver equivalent if needed
                    scale = 1.0 if 0.05 <= med_v <= 40.0 else (2.5 / med_v)
                    for y_val, p_val in zip(yrs[valid_yr], vals[valid_yr]):
                        if pd.notna(p_val) and p_val > 0:
                            yr = int(y_val)
                            if (iso3, yr, "XAG_GRAM") not in seen_w:
                                wage = 0.95 if yr < 1850 else 0.95 + 0.025 * (yr - 1850)
                                macro_rows.append((iso3, yr, "XAG_GRAM", 1.15, 1.0, wage, "GPIH Archive"))
                                seen_w.add((iso3, yr, "XAG_GRAM"))
                            price_rows.append(("GPIH_ARCHIVE", "OBSERVED_MARKET", mkt_id, code, f"{yr}-06-15", float(p_val * scale), "kg", "XAG_GRAM"))
                            gpih_count += 1
        except Exception:
            continue

    con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'ALLEN_UNGER';")
    etl.ingest_markets_and_macro(
        pd.DataFrame(mkt_rows, columns=["market_id","market_name","country_iso3","region_macro","latitude","longitude","arable_ha_per_cap","industrial_elec_kwh"]),
        pd.DataFrame(macro_rows, columns=["country_iso3","year","currency_code","cpi_to_2026","ppp_to_2026_usd","unskilled_wage_hr","wage_source"])
    )
    etl.ingest_price_stream(pd.DataFrame(
        price_rows,
        columns=["source_dataset","price_basis","market_id","commodity_code","obs_date","raw_price","raw_unit","currency_code"]
    ))
    etl.flag_mad_outliers(z_threshold=6.0)
    etl.export_analytical_parquet("protein_atlas_clean.parquet")

    print(con.execute("""
        SELECT source_dataset, COUNT(*) AS rows, MIN(obs_year) AS min_yr, MAX(obs_year) AS max_yr
        FROM fact_protein_observations WHERE is_outlier_mad = FALSE
        GROUP BY 1 ORDER BY rows DESC;
    """).df().to_string(index=False))

if __name__ == "__main__":
    main()
