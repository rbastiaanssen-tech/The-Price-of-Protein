import io, json, time, urllib.request, duckdb, pandas as pd

con = duckdb.connect("protein_atlas.duckdb")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'BLS_APU_METRO_TOWN';")
eff = dict(con.execute("SELECT commodity_code, eff_protein_g_kg FROM dim_commodities;").fetchall())

AREAS = {
    "S11A":("Boston-Cambridge-Newton, MA-NH","25025",42.36,-71.06,0.04),
    "S12A":("New York-Newark-Jersey City, NY-NJ","36061",40.71,-74.01,0.02),
    "S12B":("Philadelphia-Camden-Wilmington, PA","42101",39.95,-75.17,0.08),
    "N12C":("Pittsburgh, PA","42003",40.44,-80.00,0.14),
    "N12D":("Buffalo-Cheektowaga, NY","36029",42.89,-78.88,0.19),
    "N12E":("Rochester, NY","36055",43.16,-77.61,0.24),
    "N12F":("Reading, PA (<100k city)","42011",40.34,-75.93,0.22),
    "S23A":("Chicago-Naperville-Elgin, IL-IN","17031",41.88,-87.63,0.45),
    "S23B":("Detroit-Warren-Dearborn, MI","26163",42.33,-83.05,0.25),
    "S24A":("Minneapolis-St. Paul-Bloomington, MN","27053",44.98,-93.27,0.72),
    "S24B":("St. Louis, MO-IL","29510",38.63,-90.20,0.68),
    "S35A":("Washington-Arlington-Alexandria, DC","11001",38.91,-77.04,0.05),
    "S35B":("Miami-Fort Lauderdale-West Palm, FL","12086",25.76,-80.19,0.06),
    "S35C":("Atlanta-Sandy Springs-Roswell, GA","13121",33.75,-84.39,0.18),
    "S35D":("Baltimore-Columbia-Towson, MD","24510",39.29,-76.61,0.12),
}
AREAS.update({
    "S35E":("Tampa-St. Petersburg-Clearwater, FL","12057",27.95,-82.46,0.09),
    "S37A":("Dallas-Fort Worth-Arlington, TX","48113",32.78,-96.80,0.48),
    "S37B":("Houston-The Woodlands-Sugar Land, TX","48201",29.76,-95.37,0.29),
    "S48A":("San Francisco-Oakland-Hayward, CA","06075",37.78,-122.42,0.08),
    "S48B":("Phoenix-Mesa-Scottsdale, AZ","04013",33.45,-112.07,0.15),
    "S49A":("Los Angeles-Long Beach-Anaheim, CA","06037",34.05,-118.24,0.04),
    "S49B":("San Diego-Carlsbad, CA","06073",32.72,-117.16,0.07),
    "S49C":("Seattle-Tacoma-Bellevue, WA","53033",47.61,-122.33,0.11),
    "S49D":("Denver-Aurora-Lakewood, CO","08031",39.74,-104.99,0.55),
    "S49E":("Urban Hawaii (Honolulu, HI)","15003",21.31,-157.86,0.05),
    "S49F":("Urban Alaska (Anchorage, AK)","02020",61.22,-149.90,0.03),
    "N49H":("Portland-Vancouver-Hillsboro, OR-WA","41051",45.52,-122.68,0.28),
    "N49I":("Santa Rosa, CA","06097",38.44,-122.71,0.22),
    "N49J":("Chico, CA","06007",39.73,-121.84,0.41),
    "N49K":("Moses Lake, WA (<30k rural hub)","53025",47.13,-119.28,1.35),
})
ITEMS = [("703112","BEEF_BONELESS",0.45359237),("706111","CHICKEN_WHOLE",0.45359237),("708111","EGGS_FRESH",0.68038855),("709112","MILK_FRESH",3.899),("701111","WHEAT_FLOUR",0.45359237)]
cpi_map = dict(con.execute("SELECT year, cpi_to_2026 FROM dim_macro_wages WHERE country_iso3='USA' AND cpi_to_2026 > 0;").fetchall())
us_w_fb = dict(con.execute("SELECT year, unskilled_wage_hr FROM dim_macro_wages WHERE country_iso3='USA' AND unskilled_wage_hr > 0;").fetchall())

print("[PHASE 6] Fetching observed county hourly wages from BLS QCEW Open Data API (2016-2025)...")
qcew_w = {}
for ac, (_, fips, _, _, _) in AREAS.items():
    for yr in range(2016, 2026):
        u = f"https://data.bls.gov/cew/data/api/{yr}/a/area/{fips}.csv"
        try:
            df = pd.read_csv(io.BytesIO(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent":"Mozilla/5.0"}), timeout=12).read()), low_memory=False)
            p5 = df[(df["own_code"]==5) & (df["agglvl_code"].isin([70,74])) & (df["avg_wkly_wage"]>0)]
            w722 = p5[p5["industry_code"].astype(str)=="722"]["avg_wkly_wage"]
            w10 = p5[p5["industry_code"].astype(str)=="10"]["avg_wkly_wage"]
            wk = float(w722.iloc[0]) if len(w722) else (float(w10.iloc[0]) if len(w10) else 0.0)
            if wk > 0: qcew_w[(fips, yr)] = wk / 40.0
        except Exception:
            pass
print(f"  -> Loaded {len(qcew_w):,} county-year observed hourly wages from BLS QCEW.")
sids = [f"APU{ac}{suf}" for ac in AREAS for suf, _, _ in ITEMS]
raw = {s: [] for s in sids}
print(f"[PHASE 6] Querying BLS Public API v2 for {len(sids)} metro/town food series (2010-2026)...")
for y0, y1 in [(2010, 2019), (2020, 2026)]:
    for i in range(0, len(sids), 25):
        payload = json.dumps({"seriesid": sids[i:i+25], "startyear": str(y0), "endyear": str(y1)}).encode()
        res = json.loads(urllib.request.urlopen(urllib.request.Request("https://api.bls.gov/publicAPI/v2/timeseries/data/", data=payload, headers={"Content-Type":"application/json"}), timeout=30).read().decode())
        if res.get("status") == "REQUEST_SUCCEEDED":
            for s in res["Results"]["series"]: raw[s["seriesID"]].extend(s["data"])
        time.sleep(0.25)

mid = con.execute("SELECT COALESCE(MAX(obs_id),0) FROM fact_protein_observations;").fetchone()[0]
obs, mkts = [], set()
for ac, (name, fips, lat, lon, ar) in AREAS.items():
    mk = f"US_BLS_{ac}"
    for suf, cc, div in ITEMS:
        if cc not in eff: continue
        for r in raw[f"APU{ac}{suf}"]:
            p = r.get("period","")
            if not (p.startswith("M") and p != "M13"): continue
            yr, mo = int(r["year"]), int(p[1:])
            if yr not in cpi_map: continue
            wh = qcew_w.get((fips, yr)) or qcew_w.get((fips, min(yr, 2025))) or us_w_fb.get(yr, 0)
            if wh <= 0: continue
            try: val = float(r["value"])
            except ValueError: continue
            if val <= 0: continue
            if mk not in mkts:
                con.execute("INSERT OR REPLACE INTO dim_markets VALUES (?,?,'USA','NORTH_AMERICA',?,?,?,0.11);", (mk, name, lat, lon, ar))
                mkts.add(mk)
            nom, rusd = val / div, (val / div) * cpi_map[yr]
            mid += 1
            obs.append((mid,"BLS_APU_METRO_TOWN","OBSERVED_MARKET",mk,cc,f"{yr:04d}-{mo:02d}-15",nom,"USD",rusd,rusd*(50.0/eff[cc]),60.0*(nom/wh)*(50.0/eff[cc]),False))
con.executemany("INSERT INTO fact_protein_observations (obs_id,source_dataset,price_basis,market_id,commodity_code,obs_date,nominal_price_kg,currency_code,real_usd_per_kg,usd_per_50g_diaas,labor_min_50g_diaas,is_outlier_mad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);", obs)
print(f"[PHASE 6 COMPLETE] Inserted {len(obs):,} primary metro/town observations across {len(mkts)} U.S. locations!")
print(con.execute("SELECT m.market_name, COUNT(*) AS n, MIN(f.obs_year) AS y0, MAX(f.obs_year) AS y1, ROUND(MEDIAN(f.usd_per_50g_diaas),2) AS med_usd, ROUND(MEDIAN(f.labor_min_50g_diaas),1) AS med_min FROM fact_protein_observations f JOIN dim_markets m USING(market_id) WHERE f.source_dataset='BLS_APU_METRO_TOWN' GROUP BY 1 ORDER BY 2 DESC;").df().to_string(index=False))
con.close()
