from __future__ import annotations
import glob, json, re, urllib.request, warnings
from pathlib import Path
import pandas as pd
from etl_protein_atlas import ProteinAtlasETL

warnings.filterwarnings("ignore")
etl = ProteinAtlasETL("protein_atlas.duckdb", "schema.sql")
con = etl.con

# 1. Parse real historical unskilled day-wages from raw_data/gpih/ (assume 10-hr workday -> g Ag/hr)
CITY_ISO = {
    "london": "GBR", "england": "GBR", "amsterdam": "NLD", "antwerp": "BEL",
    "paris": "FRA", "strasbourg": "FRA", "madrid": "ESP", "valencia": "ESP",
    "vienna": "AUT", "munich": "DEU", "leipzig": "DEU", "augsburg": "DEU",
    "gdansk": "POL", "warsaw": "POL", "krakow": "POL", "milan": "ITA",
    "florence": "ITA", "naples": "ITA", "istanbul": "TUR", "cairo": "EGY",
    "beijing": "CHN", "china": "CHN", "kyoto": "JPN", "japan": "JPN",
    "india": "IND", "moscow": "RUS", "russia": "RUS", "argentina": "ARG",
    "bolivia": "BOL", "south africa": "ZAF", "san francisco": "USA",
}

hist_wages = []
for fpath in sorted(glob.glob("raw_data/gpih/*.xls*")):
    if not any(k in Path(fpath).stem.lower() for k in ["labourer", "wage", "cairo", "argentina", "south_africa", "san_francisco"]):
        continue
    try:
        xl = pd.ExcelFile(fpath)
        for sheet in xl.sheet_names[:8]:
            df = xl.parse(sheet, nrows=450)
            if df.shape[1] < 2 or df.shape[0] < 10:
                continue
            yrs = pd.to_numeric(df.iloc[:, 0], errors="coerce")
            if not yrs.between(1650, 1945).sum() >= 10:
                # Try row 1-5 as header
                for h in range(min(6, len(df))):
                    yrs_try = pd.to_numeric(df.iloc[h+1:, 0], errors="coerce")
                    if yrs_try.between(1650, 1945).sum() >= 10:
                        df.columns = [str(v).strip() for v in df.iloc[h].values]
                        df = df.iloc[h+1:].copy()
                        yrs = yrs_try
                        break
            ok = yrs.between(1650, 1945)
            if ok.sum() < 10:
                continue
            for col in df.columns[1:]:
                key = f"{Path(fpath).stem} {sheet} {col}".lower()
                iso3 = next((v for k, v in CITY_ISO.items() if k in key), None)
                if not iso3:
                    continue
                vals = pd.to_numeric(df[col], errors="coerce")
                med = vals[ok & (vals > 0)].median()
                # Day wages in grams of silver typically sit between 1.5 and 35 g Ag/day
                if pd.isna(med) or not (1.0 <= med <= 45.0):
                    continue
                for yv, wv in zip(yrs[ok], vals[ok]):
                    if pd.notna(wv) and wv > 0:
                        hist_wages.append((iso3, int(yv), float(wv) / 10.0))
    except Exception:
        continue

if hist_wages:
    hw_df = pd.DataFrame(hist_wages, columns=["iso3", "yr", "hr_ag"]).groupby(["iso3", "yr"], as_index=False)["hr_ag"].median()
    con.register("hw_df", hw_df)
    con.execute("""
        UPDATE dim_macro_wages
        SET unskilled_wage_hr = hw_df.hr_ag, wage_source = 'GPIH Empirical Labourer Wage'
        FROM hw_df
        WHERE dim_macro_wages.country_iso3 = hw_df.iso3
          AND dim_macro_wages.year = hw_df.yr
          AND dim_macro_wages.currency_code = 'XAG_GRAM';
    """)
    print(f"[GPIH Wages] Updated {len(hw_df):,} country-year historical silver wage cells.")

# 2. Pull World Bank Nominal GDP/capita (LCU) & CPI (1960–2026) for modern LCU hourly wage proxy
def fetch_wb(indicator: str) -> pd.DataFrame:
    url = f"https://api.worldbank.org/v2/country/all/indicator/{indicator}?format=json&per_page=25000&date=1960:2026"
    req = urllib.request.Request(url, headers={"User-Agent": "ProteinAtlas/1.0"})
    raw = json.loads(urllib.request.urlopen(req, timeout=45).read().decode("utf-8"))
    recs = [(r["countryiso3code"], int(r["date"]), float(r["value"]))
            for r in raw[1] if r.get("countryiso3code") and len(r["countryiso3code"]) == 3 and r.get("value") is not None]
    return pd.DataFrame(recs, columns=["iso3", "yr", "val"])

try:
    gdp_lcu = fetch_wb("NY.GDP.PCAP.CN").rename(columns={"val": "gdp_pc_lcu"})
    cpi_df  = fetch_wb("FP.CPI.TOTL").rename(columns={"val": "cpi"})
    # Anchor CPI to 2026 (2010=100 base; latest ~155 in 2026)
    latest_cpi = cpi_df.sort_values("yr").groupby("iso3")["cpi"].last().rename("cpi_2026")
    wb_m = gdp_lcu.merge(cpi_df, on=["iso3", "yr"], how="left").merge(latest_cpi, on="iso3", how="left")
    # Unskilled hourly wage ≈ 0.52 * (GDP per capita LCU / 2200 hrs)
    wb_m["est_hr_wage_lcu"] = 0.52 * (wb_m["gdp_pc_lcu"] / 2200.0)
    wb_m["cpi_mult"] = (wb_m["cpi_2026"] / wb_m["cpi"]).clip(lower=0.5, upper=500.0).fillna(1.0)
    con.register("wb_m", wb_m)
    con.execute("""
        UPDATE dim_macro_wages
        SET unskilled_wage_hr = wb_m.est_hr_wage_lcu,
            cpi_to_2026 = wb_m.cpi_mult,
            wage_source = 'World Bank LCU Earnings & CPI'
        FROM wb_m
        WHERE dim_macro_wages.country_iso3 = wb_m.iso3
          AND dim_macro_wages.year = wb_m.yr
          AND dim_macro_wages.currency_code NOT IN ('USD', 'XAG_GRAM')
          AND wb_m.est_hr_wage_lcu > 0;
    """)
    print(f"[World Bank] Calibrated {len(wb_m):,} modern country-year LCU wage & CPI rows.")
except Exception as e:
    print(f"[World Bank API] Warning: {e}")

# 3. Recompute labor_min_50g_diaas & usd_per_50g_diaas across all observations
u_cols = [r[1] for r in con.execute("PRAGMA table_info('dim_units')").fetchall()]
u_key = u_cols[0]
con.execute(f"""
    UPDATE fact_protein_observations AS f
    SET
        labor_min_50g_diaas = ((f.raw_price / u.kg_per_unit) * (50.0 / (c.edible_fraction * c.crude_protein_g_kg * c.prep_retention * c.diaas_score)) / w.unskilled_wage_hr) * 60.0,
        usd_per_50g_diaas   = ((f.raw_price / u.kg_per_unit) * (50.0 / (c.edible_fraction * c.crude_protein_g_kg * c.prep_retention * c.diaas_score)) / w.ppp_to_2026_usd) * w.cpi_to_2026
    FROM dim_commodities c, dim_units u, dim_markets m, dim_macro_wages w
    WHERE f.commodity_code = c.commodity_code
      AND LOWER(TRIM(f.raw_unit)) = LOWER(TRIM(u.{u_key}))
      AND f.market_id = m.market_id
      AND m.country_iso3 = w.country_iso3
      AND f.obs_year = w.year
      AND f.currency_code = w.currency_code
      AND w.unskilled_wage_hr > 0;
""")

etl.flag_mad_outliers(z_threshold=6.0)
etl.export_analytical_parquet("protein_atlas_clean.parquet")
print("[Done] Recomputed all labor-minute & real USD protein prices.")
