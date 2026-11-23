from __future__ import annotations
import warnings
from pathlib import Path
import numpy as np, pandas as pd
from etl_protein_atlas import ProteinAtlasETL

warnings.filterwarnings("ignore")
etl = ProteinAtlasETL("protein_atlas.duckdb", "schema.sql")
con = etl.con

# 1. Purge unverified GPIH_VERIFIED rows from previous pass
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'GPIH_VERIFIED';")
con.execute("DELETE FROM dim_macro_wages WHERE wage_source = 'GPIH Derived Base';")

mkt_rows, macro_rows, price_rows = [], [], []
seen_m, seen_w = set(), set()

def add_mkt(mkt_id: str, name: str, iso3: str, reg: str, lat: float, lon: float, arable: float, elec: float):
    if mkt_id not in seen_m:
        mkt_rows.append((mkt_id, name, iso3, reg, lat, lon, arable, elec))
        seen_m.add(mkt_id)

def add_wage(iso3: str, yr: int, curr: str, cpi: float, ppp: float, hr_wage: float, src: str):
    if (iso3, yr, curr) not in seen_w and hr_wage > 0:
        macro_rows.append((iso3, yr, curr, cpi, ppp, hr_wage, src))
        seen_w.add((iso3, yr, curr))

def add_px(mkt_id: str, comm: str, yr: int, price_kg: float, curr: str):
    if pd.notna(price_kg) and price_kg > 0 and 1460 <= yr <= 1970:
        price_rows.append(("GPIH_NORDHAUS", "OBSERVED_MARKET", mkt_id, comm, f"{yr}-06-15", float(price_kg), "kg", curr))

# A. Colonial Africa Blue Books (1880–1965): pence/lb paired with urban unskilled pence/day
AFRICA_FILES = [
    ("Gambia_w_p_welf_1880-1964.xls",       "HIST_BANJUL",   "Banjul (Gambia)",     "GMB", 13.454, -16.579, 0.18),
    ("Nigeria_w_p_welf_1880-1960_b.xls",    "HIST_LAGOS",    "Lagos (Nigeria)",     "NGA",  6.524,   3.379, 0.18),
    ("Sierra_Leone_w_p_welf_1880-1965_b.xls","HIST_FREETOWN","Freetown (S. Leone)", "SLE",  8.465, -13.231, 0.20),
    ("Uganda_w_p_welf_1906-1959_b.xls",     "HIST_KAMPALA",  "Kampala (Uganda)",    "UGA",  0.347,  32.582, 0.16),
]
COL_COMM = {
    "wheat": "WHEAT_FLOUR", "rice": "RICE_MILLED", "maize": "MAIZE_GRAIN",
    "beef": "BEEF_FRESH", "mutton": "MUTTON_FRESH", "beans": "BEANS_DRY", "millet": "MILLET_GRAIN",
}

for fname, mkt_id, mname, iso3, lat, lon, arable in AFRICA_FILES:
    fpath = Path(f"raw_data/gpih/{fname}")
    if not fpath.exists():
        continue
    xl = pd.ExcelFile(fpath)
    w_sh = next(s for s in xl.sheet_names if s.lower() == "wages")
    p_sh = next(s for s in xl.sheet_names if s.lower() == "prices")

    wdf = xl.parse(w_sh, header=None)
    w_yrs = pd.to_numeric(wdf.iloc[:, 0], errors="coerce")
    w_urb = pd.to_numeric(wdf.iloc[:, 2], errors="coerce").fillna(pd.to_numeric(wdf.iloc[:, 1], errors="coerce"))
    valid_w_yrs = set()
    for yv, wv in zip(w_yrs, w_urb):
        if pd.notna(yv) and pd.notna(wv) and wv > 0:
            yr = int(yv)
            # 1910 penny ≈ $0.75 in 2026 USD (ppp=1.33 d/USD); labor_min cancels GBP_D exactly
            add_wage(iso3, yr, "GBP_D", 1.0, 1.33, float(wv) / 10.0, f"Frankema-van Waijenburg ({iso3})")
            valid_w_yrs.add(yr)

    pdf = xl.parse(p_sh, header=None)
    headers = [str(v).strip().lower() for v in pdf.iloc[3].values]
    p_yrs = pd.to_numeric(pdf.iloc[5:, 0], errors="coerce")
    add_mkt(mkt_id, mname, iso3, "AFRICA", lat, lon, arable, 0.12)
    for c_idx, h in enumerate(headers[1:], start=1):
        comm = next((v for k, v in COL_COMM.items() if h == k or h.startswith(k + " ")), None)
        if not comm:
            continue
        vals = pd.to_numeric(pdf.iloc[5:, c_idx], errors="coerce")
        for yv, pv in zip(p_yrs, vals):
            if pd.notna(yv) and int(yv) in valid_w_yrs and pd.notna(pv) and pv > 0:
                add_px(mkt_id, comm, int(yv), float(pv) / 0.45359237, "GBP_D")

# B. Colombia (1635–1809): Reales/kg & Reales/arroba paired with Reales/day
col_path = Path("raw_data/gpih/Colombia_1635-1809.xls")
if col_path.exists():
    xl = pd.ExcelFile(col_path)
    wdf = xl.parse("Wages", header=None)
    valid_col_yrs = set()
    for yv, rv in zip(pd.to_numeric(wdf.iloc[5:, 0], errors="coerce"), pd.to_numeric(wdf.iloc[5:, 1], errors="coerce")):
        if pd.notna(yv) and pd.notna(rv) and rv > 0:
            yr = int(yv)
            # 1 colonial silver real ≈ 3.2 g Ag ≈ $4.20 in 2026 USD (ppp=0.24 reals/USD)
            add_wage("COL", yr, "COL_REAL", 1.0, 0.24, float(rv) / 10.0, "Cuesta & Arroyo Abad (Colombia)")
            valid_col_yrs.add(yr)
    add_mkt("HIST_BOGOTA", "Bogota (Colombia)", "COL", "LATAM", 4.711, -74.072, 0.08, 0.12)

    bdf = xl.parse("Beef", header=None)
    for yv, pv in zip(pd.to_numeric(bdf.iloc[9:, 0], errors="coerce"), pd.to_numeric(bdf.iloc[9:, 2], errors="coerce")):
        if pd.notna(yv) and int(yv) in valid_col_yrs and pd.notna(pv) and pv > 0:
            add_px("HIST_BOGOTA", "BEEF_FRESH", int(yv), float(pv), "COL_REAL")

    sdf = xl.parse("Starches, beans", header=None)
    # Col 1 = Garbanzo (Reals/Arroba, 1 arroba = 11.485 kg), Col 3 = Rice (Reals/Arroba), Col 6 = Garbanzo (Reals/kg)
    for yv, g_kg, r_arr in zip(
        pd.to_numeric(sdf.iloc[9:, 0], errors="coerce"),
        pd.to_numeric(sdf.iloc[9:, 6], errors="coerce"),
        pd.to_numeric(sdf.iloc[9:, 3], errors="coerce"),
    ):
        if pd.notna(yv) and int(yv) in valid_col_yrs:
            yr = int(yv)
            if pd.notna(g_kg) and g_kg > 0:
                add_px("HIST_BOGOTA", "BEANS_DRY", yr, float(g_kg), "COL_REAL")
            if pd.notna(r_arr) and r_arr > 0:
                add_px("HIST_BOGOTA", "RICE_MILLED", yr, float(r_arr) / 11.485, "COL_REAL")

# C. Istanbul (1469–1914): akches/okka & akches/kile paired with Unskilled akches/day
ist_path = Path("raw_data/gpih/Istanbul_1469-1914.xls")
if ist_path.exists():
    xl = pd.ExcelFile(ist_path)
    wdf = xl.parse("Wages", header=None)
    ist_wages = {}
    for yv, ak_w, ag_w in zip(
        pd.to_numeric(wdf.iloc[8:, 0], errors="coerce"),
        pd.to_numeric(wdf.iloc[8:, 1], errors="coerce"),
        pd.to_numeric(wdf.iloc[8:, 4], errors="coerce"),
    ):
        if pd.notna(yv) and pd.notna(ak_w) and ak_w > 0:
            yr = int(yv)
            # Implied 2026 USD per akche from silver content (1 g Ag ≈ $1.30 2026 USD)
            usd_per_ak = ((ag_w / ak_w) * 1.30) if (pd.notna(ag_w) and ag_w > 0) else 0.40
            add_wage("TUR", yr, "OTT_AKCHE", 1.0, 1.0 / max(usd_per_ak, 0.02), float(ak_w) / 10.0, "Pamuk (Istanbul)")
            ist_wages[yr] = True

    pdf = xl.parse("Prices", header=None)
    add_mkt("HIST_ISTANBUL", "Istanbul", "TUR", "MENA", 41.008, 28.978, 0.25, 0.14)
    # Col 1=Wheat (kile=25.658kg), 2=Flour (kile), 3=Bread (okka=1.2829kg), 4=Rice (kile), 5=Butter (okka), 7=Mutton (okka)
    IST_COLS = [(1, "WHEAT_FLOUR", 25.658), (4, "RICE_MILLED", 25.658), (5, "BUTTER_FAT", 1.2829), (7, "MUTTON_FRESH", 1.2829)]
    p_yrs = pd.to_numeric(pdf.iloc[8:, 0], errors="coerce")
    for col_i, comm, kg_u in IST_COLS:
        if col_i >= pdf.shape[1]:
            continue
        vals = pd.to_numeric(pdf.iloc[8:, col_i], errors="coerce")
        for yv, pv in zip(p_yrs, vals):
            if pd.notna(yv) and int(yv) in ist_wages and pd.notna(pv) and pv > 0:
                add_px("HIST_ISTANBUL", comm, int(yv), float(pv) / kg_u, "OTT_AKCHE")

# D. Japan (1887–1926): yen/koku paired with Ag. Lab. (male) yen/day
jpn_path = Path("raw_data/gpih/Japan_1885-1926.xls")
if jpn_path.exists():
    xl = pd.ExcelFile(jpn_path)
    wdf = xl.parse("wages", header=None)
    valid_jpn_yrs = set()
    for yv, wv in zip(pd.to_numeric(wdf.iloc[6:, 0], errors="coerce"), pd.to_numeric(wdf.iloc[6:, 5], errors="coerce")):
        if pd.notna(yv) and pd.notna(wv) and wv > 0:
            yr = int(yv)
            add_wage("JPN", yr, "JPY_HIST", 1.0, 0.065, float(wv) / 10.0, "Jacks / Bank of Japan")
            valid_jpn_yrs.add(yr)
    pdf = xl.parse("prices", header=None)
    add_mkt("HIST_TOKYO", "Tokyo / Japan", "JPN", "ASIA", 35.676, 139.650, 0.04, 0.12)
    # Col 1=Rice (150 kg/koku), Col 2=Wheat (136.875 kg/koku), Col 3=Soybeans (135 kg/koku)
    for col_i, comm, kg_u in [(1, "RICE_MILLED", 150.0), (2, "WHEAT_FLOUR", 136.875), (3, "SOYBEANS_DRY", 135.0)]:
        for yv, pv in zip(pd.to_numeric(pdf.iloc[9:, 0], errors="coerce"), pd.to_numeric(pdf.iloc[9:, col_i], errors="coerce")):
            if pd.notna(yv) and int(yv) in valid_jpn_yrs and pd.notna(pv) and pv > 0:
                add_px("HIST_TOKYO", comm, int(yv), float(pv) / kg_u, "JPY_HIST")

# E. Korea (1713–1910) & Brazil (1682–1769): Explicit g Ag/kg paired with empirical silver wages
kor_path = Path("raw_data/gpih/Korea_prices_wages_1696-1910.xls")
if kor_path.exists():
    xl = pd.ExcelFile(kor_path)
    add_mkt("HIST_SEOUL", "Seoul / Korea", "KOR", "ASIA", 37.566, 126.978, 0.04, 0.12)
    sdf = xl.parse("Price summary 1713-1910", header=None)
    # Col 2=Polished Rice, 3=Polished barley, 5=Wheat, 7=Bean (all g Ag per kg)
    for col_i, comm in [(2, "RICE_MILLED"), (3, "BARLEY_GRAIN"), (5, "WHEAT_FLOUR"), (7, "BEANS_DRY")]:
        for yv, pv in zip(pd.to_numeric(sdf.iloc[5:, 0], errors="coerce"), pd.to_numeric(sdf.iloc[5:, col_i], errors="coerce")):
            if pd.notna(yv) and pd.notna(pv) and pv > 0:
                add_px("HIST_SEOUL", comm, int(yv), float(pv), "XAG_GRAM")
    cdf = xl.parse("Chicken", header=None)
    for yv, pv in zip(pd.to_numeric(cdf.iloc[3:, 0], errors="coerce"), pd.to_numeric(cdf.iloc[3:, 4], errors="coerce")):
        if pd.notna(yv) and pd.notna(pv) and pv > 0:
            add_px("HIST_SEOUL", "POULTRY_CHIX", int(yv), float(pv) / 0.80, "XAG_GRAM")

bra_path = Path("raw_data/gpih/Brazil_1550-1769.xls")
if bra_path.exists():
    xl = pd.ExcelFile(bra_path)
    add_mkt("HIST_BAHIA", "Bahia (Brazil)", "BRA", "LATAM", -12.977, -38.501, 0.28, 0.13)
    bdf = xl.parse("Beef", header=None)
    for yv, pv in zip(pd.to_numeric(bdf.iloc[9:, 0], errors="coerce"), pd.to_numeric(bdf.iloc[9:, 3], errors="coerce")):
        if pd.notna(yv) and pd.notna(pv) and pv > 0:
            add_px("HIST_BAHIA", "BEEF_FRESH", int(yv), float(pv), "XAG_GRAM")

etl.ingest_markets_and_macro(
    pd.DataFrame(mkt_rows, columns=["market_id","market_name","country_iso3","region_macro","latitude","longitude","arable_ha_per_cap","industrial_elec_kwh"]),
    pd.DataFrame(macro_rows, columns=["country_iso3","year","currency_code","cpi_to_2026","ppp_to_2026_usd","unskilled_wage_hr","wage_source"])
)
etl.ingest_price_stream(pd.DataFrame(
    price_rows, columns=["source_dataset","price_basis","market_id","commodity_code","obs_date","raw_price","raw_unit","currency_code"]
))

# Enforce strict Nordhaus matching: drop any GPIH_NORDHAUS row without an empirical local wage
con.execute("""
    DELETE FROM fact_protein_observations
    WHERE source_dataset = 'GPIH_NORDHAUS'
      AND obs_id NOT IN (
          SELECT f.obs_id
          FROM fact_protein_observations f
          JOIN dim_markets m ON f.market_id = m.market_id
          JOIN dim_macro_wages w
            ON m.country_iso3 = w.country_iso3
           AND f.obs_year = w.year
           AND f.currency_code = w.currency_code
          WHERE f.source_dataset = 'GPIH_NORDHAUS'
            AND w.unskilled_wage_hr > 0
      );
""")
con.execute("""
    UPDATE fact_protein_observations AS f
    SET
        real_usd_per_kg     = (f.nominal_price_kg / w.ppp_to_2026_usd) * w.cpi_to_2026,
        usd_per_50g_diaas   = ((f.nominal_price_kg / w.ppp_to_2026_usd) * w.cpi_to_2026)
                              * (50.0 / (c.edible_fraction * c.crude_protein_g_kg * c.prep_retention * c.diaas_score)),
        labor_min_50g_diaas = ((f.nominal_price_kg * (50.0 / (c.edible_fraction * c.crude_protein_g_kg * c.prep_retention * c.diaas_score)))
                              / w.unskilled_wage_hr) * 60.0
    FROM dim_commodities c, dim_markets m, dim_macro_wages w
    WHERE f.source_dataset = 'GPIH_NORDHAUS'
      AND f.commodity_code = c.commodity_code
      AND f.market_id = m.market_id
      AND m.country_iso3 = w.country_iso3
      AND f.obs_year = w.year
      AND f.currency_code = w.currency_code
      AND w.unskilled_wage_hr > 0;
""")

etl.flag_mad_outliers(z_threshold=5.5)
print("\n=== STRICT NORDHAUS NON-WESTERN SERIES INGESTED ===")
print(con.execute("""
    SELECT m.market_name, f.currency_code, COUNT(*) AS rows,
           MIN(f.obs_year) AS min_yr, MAX(f.obs_year) AS max_yr,
           ROUND(MEDIAN(f.usd_per_50g_diaas), 2) AS med_usd,
           ROUND(MEDIAN(f.labor_min_50g_diaas), 1) AS med_lmin
    FROM fact_protein_observations f JOIN dim_markets m USING (market_id)
    WHERE f.source_dataset = 'GPIH_NORDHAUS' AND f.is_outlier_mad = FALSE
    GROUP BY 1, 2 ORDER BY 4;
""").df().to_string(index=False))
