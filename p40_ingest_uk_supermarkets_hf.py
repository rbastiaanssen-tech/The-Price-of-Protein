import duckdb, time, json, urllib.request

t0 = time.time()
con = duckdb.connect("protein_atlas.duckdb")
con.execute("INSTALL httpfs; LOAD httpfs;")
con.execute("DELETE FROM fact_protein_observations WHERE source_dataset = 'UK_SUPERMARKETS_40';")

print("[TRACK 40] Resolving official Parquet shard URLs from Hugging Face Datasets Server...")
api_url = "https://datasets-server.huggingface.co/parquet?dataset=Rif-SQL/time-series-uk-retail-supermarket-price-data"
req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=20) as resp:
    meta = json.loads(resp.read().decode("utf-8"))

parquet_urls = [f["url"] for f in meta.get("parquet_files", []) if f.get("split") == "train"]
print(f" -> Found {len(parquet_urls)} Parquet shard(s) ({sum(f.get('size', 0) for f in meta.get('parquet_files', []))/1e6:.1f} MB total)")

UK_CHAINS = {
    "Tesco": ("UK40_TESCO_LON", "Tesco UK Retail", 51.5074, -0.1278),
    "Sains": ("UK40_SAINS_MAN", "Sainsbury's UK Retail", 53.4808, -2.2426),
    "ASDA":  ("UK40_ASDA_LEE",  "ASDA UK Retail", 53.8008, -1.5491),
    "Morri": ("UK40_MORRI_BRA", "Morrisons UK Retail", 53.7960, -1.7594),
    "Aldi":  ("UK40_ALDI_BIR",  "Aldi UK Retail", 52.4862, -1.8904),
}
for mid, mname, lat, lon in UK_CHAINS.values():
    con.execute("INSERT OR IGNORE INTO dim_markets VALUES (?, ?, 'GBR', 'UK_SUPERMARKETS', ?, ?, 0.08, 0.08);", [mid, mname, lat, lon])

uk_wage = con.execute("""
    SELECT MEDIAN(f.usd_per_50g_diaas / NULLIF(f.labor_min_50g_diaas / 60.0, 0))
    FROM fact_protein_observations f JOIN dim_markets m USING(market_id)
    WHERE m.country_iso3 = 'GBR' AND f.labor_min_50g_diaas > 0;
""").fetchone()[0] or 21.0

urls_sql = "[" + ", ".join(f"'{u}'" for u in parquet_urls) + "]"
max_id = con.execute("SELECT COALESCE(MAX(obs_id), 0) FROM fact_protein_observations;").fetchone()[0]

print("[TRACK 40] Streaming & filtering unit-verified UK retail protein SKUs into DuckDB...")
con.execute(f"""
    INSERT INTO fact_protein_observations (
        obs_id, source_dataset, price_basis, market_id, commodity_code, obs_date,
        nominal_price_kg, currency_code, real_usd_per_kg, usd_per_50g_diaas, labor_min_50g_diaas, is_outlier_mad
    )
    WITH raw_uk AS (
        SELECT
            CAST(supermarket_name AS VARCHAR) AS chain,
            TRY_CAST(price_unit_gbp AS DOUBLE) AS gbp_kg,
            LOWER(CAST(unit AS VARCHAR)) AS unit,
            LOWER(CAST(product_name AS VARCHAR)) AS pname,
            COALESCE(CAST(TRY_CAST(capture_date AS DATE) AS VARCHAR), '2024-03-01') AS obs_date
        FROM read_parquet({urls_sql})
        WHERE LOWER(CAST(unit AS VARCHAR)) IN ('kg', 'l')
          AND TRY_CAST(price_unit_gbp AS DOUBLE) BETWEEN 0.35 AND 35.0
    ),
    mapped AS (
        SELECT
            CASE
                WHEN chain LIKE 'Tesco%' THEN 'UK40_TESCO_LON'
                WHEN chain LIKE 'Sains%' THEN 'UK40_SAINS_MAN'
                WHEN chain LIKE 'ASDA%'  THEN 'UK40_ASDA_LEE'
                WHEN chain LIKE 'Morri%' THEN 'UK40_MORRI_BRA'
                WHEN chain LIKE 'Aldi%'  THEN 'UK40_ALDI_BIR'
                ELSE NULL
            END AS market_id,
            CASE
                WHEN REGEXP_MATCHES(pname, '(egg)') THEN 'EGGS_CHIX'
                WHEN REGEXP_MATCHES(pname, '(milk|yogurt)') THEN 'MILK_FLUID'
                WHEN REGEXP_MATCHES(pname, '(cheddar|cheese)') THEN 'CHEESE_HARD'
                WHEN REGEXP_MATCHES(pname, '(chicken breast|chicken thigh|chicken drumstick|whole chicken)') THEN 'POULTRY_CHIX'
                WHEN REGEXP_MATCHES(pname, '(beef mince|beef steak|beef joint|braising beef)') THEN 'BEEF_FRESH'
                WHEN REGEXP_MATCHES(pname, '(pork loin|pork chop|pork shoulder|pork leg|pork mince)') THEN 'PORK_FRESH'
                WHEN REGEXP_MATCHES(pname, '(lamb)') THEN 'MUTTON_FRESH'
                WHEN REGEXP_MATCHES(pname, '(tuna|sardine|mackerel)') THEN 'FISH_PELAGIC_SMALL'
                WHEN REGEXP_MATCHES(pname, '(salmon|cod|haddock)') THEN 'FISH_PREDATOR_HIGH'
                WHEN REGEXP_MATCHES(pname, '(lentil)') THEN 'LENTILS_DRY'
                WHEN REGEXP_MATCHES(pname, '(kidney bean|chickpea|butter bean|black bean)') THEN 'BEANS_DRY'
                ELSE NULL
            END AS commodity_code,
            gbp_kg,
            gbp_kg * 1.28 AS usd_kg,
            obs_date
        FROM raw_uk
    ),
    calc AS (
        SELECT *,
            CASE commodity_code
                WHEN 'EGGS_CHIX' THEN 124.0 WHEN 'MILK_FLUID' THEN 33.0 WHEN 'CHEESE_HARD' THEN 245.0
                WHEN 'POULTRY_CHIX' THEN 195.0 WHEN 'BEEF_FRESH' THEN 215.0 WHEN 'PORK_FRESH' THEN 190.0
                WHEN 'MUTTON_FRESH' THEN 195.0 WHEN 'FISH_PELAGIC_SMALL' THEN 180.0 WHEN 'FISH_PREDATOR_HIGH' THEN 176.0
                WHEN 'LENTILS_DRY' THEN 148.0 WHEN 'BEANS_DRY' THEN 138.0
            END AS eff_g
        FROM mapped
        WHERE market_id IS NOT NULL AND commodity_code IS NOT NULL
    )
    SELECT
        {max_id} + ROW_NUMBER() OVER (),
        'UK_SUPERMARKETS_40', 'OBSERVED_RETAIL',
        market_id, commodity_code, obs_date,
        ROUND(gbp_kg, 3), 'GBP', ROUND(usd_kg, 3),
        ROUND((usd_kg / eff_g) * 50.0, 4),
        ROUND(((usd_kg / eff_g) * 50.0) / {uk_wage} * 60.0, 2),
        FALSE
    FROM calc
    WHERE ((usd_kg / eff_g) * 50.0) BETWEEN 0.04 AND 4.50;
""")

added = con.execute("SELECT COUNT(*) FROM fact_protein_observations WHERE source_dataset = 'UK_SUPERMARKETS_40';").fetchone()[0]
tot_rows, tot_mkts = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id) FROM fact_protein_observations;").fetchone()
print(f"[TRACK 40 COMPLETE] Added +{added:,} UK 5-supermarket unit-verified observations! Total DB: {tot_rows:,} rows | {tot_mkts:,} markets ({time.time()-t0:.1f}s)")
con.close()
