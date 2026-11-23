import json
import subprocess
import duckdb
import pandas as pd
from pathlib import Path

# 1. Re-run ingest_ethnographic_layer.py to restore the non-market ethnographic societies in DuckDB
print("[1/3] Running ingest_ethnographic_layer.py...")
subprocess.run(["python", "ingest_ethnographic_layer.py"], check=True)

con = duckdb.connect("protein_atlas.duckdb", read_only=True)

# 2. Fetch the restored non-market ethnographic nodes and append them to spatiotemporal_markets.json
eth_df = con.execute("""
    SELECT
        mk.market_id,
        mk.market_name,
        mk.country_iso3,
        ROUND(mk.latitude, 3) AS lat,
        ROUND(mk.longitude, 3) AS lon,
        ROUND(COALESCE(mk.arable_ha_per_cap, 0.05), 4) AS arable_ha,
        CASE WHEN f.obs_year = 2026 THEN 2026 ELSE f.obs_decade END AS decade,
        c.regime_class AS regime,
        NULL AS usd_50g,
        ROUND(MEDIAN(f.labor_min_50g_diaas), 2) AS labor_min,
        COUNT(*) AS n_series
    FROM fact_protein_observations f
    JOIN dim_markets mk ON f.market_id = mk.market_id
    JOIN dim_commodities c ON f.commodity_code = c.commodity_code
    WHERE (f.price_basis = 'ETHNOGRAPHIC_SHADOW' OR mk.market_id LIKE 'ETH_%')
      AND mk.market_id NOT LIKE 'ETH_GRID_%'
    GROUP BY 1,2,3,4,5,6,7,8
    ORDER BY 1, 7, 8;
""").df()

p_map = Path("public/data/spatiotemporal_markets.json")
map_data = json.loads(p_map.read_text(encoding="utf-8"))
cols = map_data["columns"]

# Remove any stale non-grid ETH_ rows before appending fresh ones
keep_idx = [i for i, m in enumerate(cols["market_id"]) if not (str(m).startswith("ETH_") and not str(m).startswith("ETH_GRID_"))]
for k in cols:
    cols[k] = [cols[k][i] for i in keep_idx]

for r in eth_df.itertuples(index=False):
    cols["market_id"].append(str(r.market_id))
    cols["market_name"].append(str(r.market_name))
    cols["country_iso3"].append(str(r.country_iso3))
    cols["lat"].append(float(r.lat))
    cols["lon"].append(float(r.lon))
    cols["arable_ha"].append(float(r.arable_ha))
    cols["decade"].append(int(r.decade))
    cols["regime"].append(str(r.regime))
    cols["usd_50g"].append(None)
    cols["labor_min"].append(float(r.labor_min))
    cols["n_series"].append(int(r.n_series))

map_data["count"] = len(cols["market_id"])
p_map.write_text(json.dumps(map_data, separators=(",", ":"), allow_nan=False), encoding="utf-8")
print(f"[2/3] Appended {len(eth_df)} non-market ethnographic nodes -> {p_map} (total: {map_data['count']:,} nodes, {p_map.stat().st_size / 1024:.1f} KB)")

# 3. Fix heatmap_matrix.json so MOD_SYDNEY (OBSERVED_RETAIL) populates its cells
el_path = Path("export_lovable_json.py")
el_txt = el_path.read_text(encoding="utf-8")
if "AND f.price_basis = 'OBSERVED_MARKET'\n              AND f.is_outlier_mad = FALSE" in el_txt:
    el_txt = el_txt.replace(
        "AND f.price_basis = 'OBSERVED_MARKET'\n              AND f.is_outlier_mad = FALSE",
        "AND f.price_basis IN ('OBSERVED_MARKET', 'OBSERVED_RETAIL')\n              AND f.is_outlier_mad = FALSE"
    )
    el_path.write_text(el_txt, encoding="utf-8")

con.close()

from export_lovable_json import LovableArtifactExporter
LovableArtifactExporter().export_heatmap_matrix()

hm = json.loads(Path("public/data/heatmap_matrix.json").read_text(encoding="utf-8"))
syd_cells = [c for c in hm.get("cells", []) if "SYDNEY" in str(c.get("market_id", "")).upper()]
print(f"[3/3] Updated heatmap_matrix.json -> total cells: {len(hm.get('cells', []))}, Sydney cells: {len(syd_cells)}")
