import io, urllib.request, duckdb, pandas as pd

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset IN ('BLS_APU_METRO_TOWN', 'BLS_APU_REGIONAL');")
eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())
cpi_map = dict(con.execute("SELECT year, cpi_to_2026 FROM dim_macro_wages WHERE country_iso3='USA' AND cpi_to_2026 > 0;").fetchall())
us_w_fb = dict(con.execute("SELECT year, unskilled_wage_hr FROM dim_macro_wages WHERE country_iso3='USA' AND unskilled_wage_hr > 0;").fetchall())

def get_bls_txt(fname):
    req = urllib.request.Request(
        f"https://download.bls.gov/pub/time.series/ap/{fname}",
        headers={"User-Agent": "ProteinAtlasResearch/1.0 (research@proteinatlas.org)"}
    )
    raw = urllib.request.urlopen(req, timeout=45).read().decode("utf-8", errors="replace")
    df = pd.read_csv(io.StringIO(raw), sep="\t")
    df.columns = [c.strip() for c in df.columns]
    for c in df.select_dtypes(include="object"): df[c] = df[c].str.strip()
    return df

print("[PHASE 6] Downloading official BLS LABSTAT flat files (ap.area, ap.data.3.Food)...")
areas_df = get_bls_txt("ap.area")
food_df = get_bls_txt("ap.data.3.Food")
print(f"  -> Loaded {len(areas_df)} BLS areas and {len(food_df):,} monthly food quotes!")
print("  -> Area codes in ap.area:", dict(zip(areas_df["area_code"], areas_df["area_name"])))
GEO = {
    "0100":("Northeast Census Region (BLS)","36061",40.71,-74.01,0.09), "0200":("Midwest Census Region (BLS)","17031",41.88,-87.63,0.85),
    "0300":("South Census Region (BLS)","13121",33.75,-84.39,0.42),     "0400":("West Census Region (BLS)","06037",34.05,-118.24,0.38),
    "A101":("New York-Northern NJ-Long Island, NY-NJ","36061",40.71,-74.01,0.02), "S12A":("New York-Newark-Jersey City, NY-NJ","36061",40.71,-74.01,0.02),
    "A102":("Philadelphia-Wilmington-Atlantic City, PA","42101",39.95,-75.17,0.08), "S12B":("Philadelphia-Camden-Wilmington, PA","42101",39.95,-75.17,0.08),
    "A103":("Boston-Brockton-Nashua, MA-NH","25025",42.36,-71.06,0.04), "S11A":("Boston-Cambridge-Newton, MA-NH","25025",42.36,-71.06,0.04),
    "A104":("Pittsburgh, PA","42003",40.44,-80.00,0.14), "N12C":("Pittsburgh, PA","42003",40.44,-80.00,0.14),
    "N12D":("Buffalo-Cheektowaga, NY","36029",42.89,-78.88,0.19), "N12E":("Rochester, NY","36055",43.16,-77.61,0.24),
    "N12F":("Reading, PA (<100k city)","42011",40.34,-75.93,0.22),
    "A207":("Chicago-Gary-Kenosha, IL-IN","17031",41.88,-87.63,0.45), "S23A":("Chicago-Naperville-Elgin, IL-IN","17031",41.88,-87.63,0.45),
    "A208":("Detroit-Ann Arbor-Flint, MI","26163",42.33,-83.05,0.25), "S23B":("Detroit-Warren-Dearborn, MI","26163",42.33,-83.05,0.25),
    "A209":("St. Louis, MO-IL","29510",38.63,-90.20,0.68), "S24B":("St. Louis, MO-IL","29510",38.63,-90.20,0.68),
    "A210":("Cleveland-Akron, OH","39035",41.50,-81.69,0.21), "A211":("Minneapolis-St. Paul, MN-WI","27053",44.98,-93.27,0.72),
    "S24A":("Minneapolis-St. Paul-Bloomington, MN","27053",44.98,-93.27,0.72), "A212":("Milwaukee-Racine, WI","55079",43.04,-87.91,0.34),
}
GEO.update({
    "A213":("Cincinnati-Hamilton, OH-KY-IN","39061",39.10,-84.51,0.28), "A214":("Kansas City, MO-KS","29095",39.10,-94.58,0.62),
    "A311":("Washington-Baltimore, DC-MD-VA","11001",38.91,-77.04,0.05), "S35A":("Washington-Arlington-Alexandria, DC","11001",38.91,-77.04,0.05),
    "S35D":("Baltimore-Columbia-Towson, MD","24510",39.29,-76.61,0.12), "A316":("Dallas-Fort Worth, TX","48113",32.78,-96.80,0.48),
    "S37A":("Dallas-Fort Worth-Arlington, TX","48113",32.78,-96.80,0.48), "A318":("Houston-Galveston-Brazoria, TX","48201",29.76,-95.37,0.29),
    "S37B":("Houston-The Woodlands-Sugar Land, TX","48201",29.76,-95.37,0.29), "A319":("Atlanta, GA","13121",33.75,-84.39,0.18),
    "S35C":("Atlanta-Sandy Springs-Roswell, GA","13121",33.75,-84.39,0.18), "A320":("Miami-Fort Lauderdale, FL","12086",25.76,-80.19,0.06),
    "S35B":("Miami-Fort Lauderdale-West Palm, FL","12086",25.76,-80.19,0.06), "A321":("Tampa-St. Petersburg-Clearwater, FL","12057",27.95,-82.46,0.09),
    "S35E":("Tampa-St. Petersburg-Clearwater, FL","12057",27.95,-82.46,0.09), "A421":("Los Angeles-Riverside-Orange Co, CA","06037",34.05,-118.24,0.04),
    "S49A":("Los Angeles-Long Beach-Anaheim, CA","06037",34.05,-118.24,0.04), "A422":("San Francisco-Oakland-San Jose, CA","06075",37.78,-122.42,0.08),
    "S48A":("San Francisco-Oakland-Hayward, CA","06075",37.78,-122.42,0.08), "A423":("Seattle-Tacoma-Bremerton, WA","53033",47.61,-122.33,0.11),
    "S49C":("Seattle-Tacoma-Bellevue, WA","53033",47.61,-122.33,0.11), "A424":("San Diego, CA","06073",32.72,-117.16,0.07),
    "S49B":("San Diego-Carlsbad, CA","06073",32.72,-117.16,0.07), "A425":("Portland-Salem, OR-WA","41051",45.52,-122.68,0.28),
    "N49H":("Portland-Vancouver-Hillsboro, OR-WA","41051",45.52,-122.68,0.28), "A426":("Honolulu, HI","15003",21.31,-157.86,0.05),
    "S49E":("Honolulu, HI","15003",21.31,-157.86,0.05), "A427":("Anchorage, AK","02020",61.22,-149.90,0.03),
    "S49F":("Anchorage, AK","02020",61.22,-149.90,0.03), "A429":("Phoenix-Mesa, AZ","04013",33.45,-112.07,0.15),
    "S48B":("Phoenix-Mesa-Scottsdale, AZ","04013",33.45,-112.07,0.15), "A433":("Denver-Boulder-Greeley, CO","08031",39.74,-104.99,0.55),
    "S49D":("Denver-Aurora-Lakewood, CO","08031",39.74,-104.99,0.55), "N49I":("Santa Rosa, CA","06097",38.44,-122.71,0.22),
    "N49J":("Chico, CA","06007",39.73,-121.84,0.41), "N49K":("Moses Lake, WA (<30k rural hub)","53025",47.13,-119.28,1.35),
})
ITEMS = {"703112":("BEEF_BONELESS",0.45359237),"706111":("CHICKEN_WHOLE",0.45359237),"708111":("EGGS_FRESH",0.68038855),"709112":("MILK_FRESH",3.899),"701111":("WHEAT_FLOUR",0.45359237),"711111":("POTATOES",0.45359237),"714233":("DRY_BEANS",0.45359237)}

qcew_ratio = {}
for fips in sorted({v[1] for v in GEO.values()}):
    ratios = []
    for yr in range(2021, 2026):
        try:
            u = f"https://data.bls.gov/cew/data/api/{yr}/a/area/{fips}.csv"
            df = pd.read_csv(io.BytesIO(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent":"ProteinAtlasResearch/1.0 (research@proteinatlas.org)"}), timeout=10).read()), low_memory=False)
            p5 = df[(df["own_code"]==5) & (df["agglvl_code"].isin([70,74])) & (df["avg_wkly_wage"]>0)]
            w722 = p5[p5["industry_code"].astype(str)=="722"]["avg_wkly_wage"]
            w10 = p5[p5["industry_code"].astype(str)=="10"]["avg_wkly_wage"]
            wk = float(w722.iloc[0]) if len(w722) else (float(w10.iloc[0]) if len(w10) else 0.0)
            if wk > 0 and yr in us_w_fb: ratios.append((wk / 40.0) / us_w_fb[yr])
        except Exception:
            pass
    if ratios: qcew_ratio[fips] = sum(ratios) / len(ratios)
print(f"[PHASE 6] Calibrated county-to-national wage ratios for {len(qcew_ratio)} U.S. counties via BLS QCEW.")
food_df["area"] = food_df["series_id"].str[3:7]
food_df["item"] = food_df["series_id"].str[7:13]
food_df["val"] = pd.to_numeric(food_df["value"], errors="coerce")
sub = food_df[food_df["area"].isin(GEO) & food_df["item"].isin(ITEMS) & food_df["period"].str.startswith("M") & (food_df["period"]!="M13") & (food_df["val"]>0)]

mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
obs, mkts = [], set()
for r in sub.itertuples(index=False):
    yr, mo = int(r.year), int(r.period[1:])
    if yr not in cpi_map or yr not in us_w_fb: continue
    cc, div = ITEMS[r.item]
    if cc not in eff: continue
    name, fips, lat, lon, ar = GEO[r.area]
    mk = f"US_BLS_{r.area}"
    if mk not in mkts:
        con.execute("INSERT OR REPLACE INTO dim_markets VALUES (?,?,'USA','NORTH_AMERICA',?,?,?,0.11);", (mk, name, lat, lon, ar))
        mkts.add(mk)
    wh = us_w_fb[yr] * qcew_ratio.get(fips, 1.0)
    nom, rusd = float(r.val) / div, (float(r.val) / div) * cpi_map[yr]
    mid += 1
    obs.append((mid,"BLS_APU_METRO_TOWN","OBSERVED_MARKET",mk,cc,f"{yr:04d}-{mo:02d}-15",nom,"USD",rusd,rusd*(50.0/eff[cc]),60.0*(nom/wh)*(50.0/eff[cc]),False))

con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)
print(f"[PHASE 6 COMPLETE] Inserted {len(obs):,} primary BLS food observations across {len(mkts)} U.S. metros, small towns & regions!")
print(con.execute("SELECT m.market_name, COUNT(*) AS n, MIN(f.obs_year) AS y0, MAX(f.obs_year) AS y1, ROUND(MEDIAN(f.usd_per_50g_diaas),2) AS med_usd, ROUND(MEDIAN(f.labor_min_50g_diaas),1) AS med_min FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.source_dataset='BLS_APU_METRO_TOWN' GROUP BY 1 ORDER BY 2 DESC;").df().to_string(index=False))
con.close()
