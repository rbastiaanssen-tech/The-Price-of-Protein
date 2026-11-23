import io, urllib.request, duckdb, pandas as pd

con = duckdb.connect("protein_atlas.duckdb")
eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())
cpi_map = dict(con.execute("SELECT year, cpi_to_2026 FROM dim_macro_wages WHERE country_iso3='USA' AND cpi_to_2026 > 0;").fetchall())
us_w_fb = dict(con.execute("SELECT year, unskilled_wage_hr FROM dim_macro_wages WHERE country_iso3='USA' AND unskilled_wage_hr > 0;").fetchall())

EXTRA_GEO = {
    "A105": ("Buffalo-Niagara Falls, NY",                 "36029", 42.886,  -78.878, 0.19),
    "A106": ("Scranton, PA (<100k city)",                 "42069", 41.409,  -75.662, 0.21),
    "A315": ("Washington, DC-MD-VA",                      "11001", 38.907,  -77.037, 0.05),
    "A317": ("Baltimore, MD",                             "24510", 39.290,  -76.612, 0.12),
    "S49B": ("San Francisco-Oakland-Hayward, CA",         "06075", 37.775, -122.419, 0.08),
    "S49C": ("Riverside-San Bernardino-Ontario, CA",      "06065", 33.981, -117.375, 0.16),
    "S49D": ("Seattle-Tacoma-Bellevue, WA",               "53033", 47.606, -122.332, 0.11),
    "S49E": ("San Diego-Carlsbad, CA",                    "06073", 32.716, -117.161, 0.07),
    "S49F": ("Urban Hawaii (Honolulu, HI)",               "15003", 21.307, -157.858, 0.05),
    "S49G": ("Urban Alaska (Anchorage, AK)",              "02020", 61.218, -149.900, 0.03),
    "D100": ("Northeast Small Towns (<50k, BLS Class D)", "42081", 41.241,  -77.001, 0.35),
    "D200": ("Midwest Small Towns (<50k, BLS Class D)",   "19169", 42.035,  -93.620, 1.45),
    "D300": ("South Small Towns (<50k, BLS Class D)",     "28071", 34.366,  -89.519, 0.78),
    "D400": ("West Small Towns (<50k, BLS Class D)",      "30031", 45.677, -111.043, 1.62),
    "C100": ("Northeast Small Cities (50k-450k, Class C)","36007", 42.099,  -75.918, 0.25),
    "C200": ("Midwest Small Cities (50k-450k, Class C)",  "17019", 40.116,  -88.243, 0.95),
    "C300": ("South Small Cities (50k-450k, Class C)",    "37063", 35.994,  -78.899, 0.31),
    "C400": ("West Small Cities (50k-450k, Class C)",     "16001", 43.615, -116.202, 0.64),
}
ITEMS = {"703112":("BEEF_BONELESS",0.45359237),"706111":("CHICKEN_WHOLE",0.45359237),"708111":("EGGS_FRESH",0.68038855),"709112":("MILK_FRESH",3.899),"701111":("WHEAT_FLOUR",0.45359237),"711111":("POTATOES",0.45359237),"714233":("DRY_BEANS",0.45359237)}

req = urllib.request.Request("https://download.bls.gov/pub/time.series/ap/ap.data.3.Food", headers={"User-Agent": "ProteinAtlasResearch/1.0 (research@proteinatlas.org)"})
food_df = pd.read_csv(io.StringIO(urllib.request.urlopen(req, timeout=45).read().decode("utf-8", errors="replace")), sep="\t")
food_df.columns = [c.strip() for c in food_df.columns]
for c in food_df.select_dtypes(include="object"): food_df[c] = food_df[c].str.strip()
food_df["area"] = food_df["series_id"].str[3:7]
food_df["item"] = food_df["series_id"].str[7:13]
food_df["val"] = pd.to_numeric(food_df["value"], errors="coerce")
sub = food_df[food_df["area"].isin(EXTRA_GEO) & food_df["item"].isin(ITEMS) & food_df["period"].str.startswith("M") & (food_df["period"]!="M13") & (food_df["val"]>0)]
mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
obs, mkts = [], set()
for r in sub.itertuples(index=False):
    yr, mo = int(r.year), int(r.period[1:])
    if yr not in cpi_map or yr not in us_w_fb: continue
    cc, div = ITEMS[r.item]
    if cc not in eff: continue
    name, _, lat, lon, ar = EXTRA_GEO[r.area]
    mk = f"US_BLS_{r.area}"
    if mk not in mkts:
        con.execute("DELETE FROM fact_protein_observations WHERE market_id = ?;", (mk,))
        con.execute("INSERT OR REPLACE INTO dim_markets VALUES (?,?,'USA','NORTH_AMERICA',?,?,?,0.11);", (mk, name, lat, lon, ar))
        mkts.add(mk)
    wh = us_w_fb[yr]
    nom, rusd = float(r.val) / div, (float(r.val) / div) * cpi_map[yr]
    mid += 1
    obs.append((mid,"BLS_APU_METRO_TOWN","OBSERVED_MARKET",mk,cc,f"{yr:04d}-{mo:02d}-15",nom,"USD",rusd,rusd*(50.0/eff[cc]),60.0*(nom/wh)*(50.0/eff[cc]),False))

con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)
print(f"[PATCH COMPLETE] Added {len(obs):,} observations across {len(mkts)} U.S. small-town (<50k), <100k city, and metro nodes!")
print(con.execute("SELECT m.market_name, COUNT(*) AS n, MIN(f.obs_year) AS y0, MAX(f.obs_year) AS y1, ROUND(MEDIAN(f.usd_per_50g_diaas),2) AS med_usd, ROUND(MEDIAN(f.labor_min_50g_diaas),1) AS med_min FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.source_dataset='BLS_APU_METRO_TOWN' GROUP BY 1 ORDER BY 2 DESC;").df().to_string(index=False))
con.close()
