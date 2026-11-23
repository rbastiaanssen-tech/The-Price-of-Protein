import json
from pathlib import Path
import duckdb, numpy as np, pandas as pd

# Literature 2026 TEA / Retail Cost Ranges (USD/kg) for uncertainty error bars
BENCHMARK_RANGES_2026 = {
    "SOY_ISOLATE":     (2.40, 2.85, 3.40),
    "PEA_ISOLATE":     (3.10, 3.60, 4.30),
    "SINGLE_CELL_GAS": (4.10, 5.20, 7.80),
    "TVP":             (3.20, 3.80, 4.60),
    "MYCOPROTEIN":     (5.50, 6.50, 8.20),
    "INSECT_MEAL":     (5.80, 7.50, 10.50),
    "PF_WHEY_BLG":     (13.50, 18.00, 26.00),
    "PF_CASEIN":       (17.50, 24.00, 35.00),
    "CULT_CHICKEN":    (9.50, 12.50, 22.00),
}

def clean_records(df: pd.DataFrame) -> list[dict]:
    records = df.where(pd.notnull(df), None).to_dict(orient="records")
    for rec in records:
        for k, v in rec.items():
            if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
                rec[k] = None
    return records

class LovableArtifactExporter:
    def __init__(self, db_path: str = "protein_atlas.duckdb", out_dir: str = "public/data"):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(db_path)

    @staticmethod
    def compute_robust_fdh(x: np.ndarray, y: np.ndarray) -> list[dict]:
        if len(x) == 0:
            return []
        # Group into 28 log-x strata and take 5th percentile so single anomalies don't warp frontier
        df = pd.DataFrame({"x": x, "y": y})
        df["bin"] = pd.qcut(df["x"].rank(method="first"), q=min(28, len(df)), labels=False)
        agg = df.groupby("bin", as_index=False).agg(x=("x", "median"), y=("y", lambda s: np.percentile(s, 5)))
        agg = agg.sort_values(["x", "y"], ascending=[False, True])
        frontier, min_y = [], np.inf
        for r in agg.itertuples(index=False):
            if r.y < min_y:
                frontier.append({"x": round(float(r.x), 4), "y": round(float(r.y), 4)})
                min_y = r.y
        frontier.reverse()
        return frontier

    def export_gvk_density_and_frontiers(self, grid_bins: int = 36) -> None:
        df_obs = self.con.execute("""
            SELECT
                CASE WHEN f.obs_year = 2026 THEN 2026 ELSE f.obs_decade END AS obs_decade,
                c.regime_class, c.shelf_life_days, c.diaas_capped, c.dry_matter_fraction,
                f.labor_min_50g_diaas, f.usd_per_50g_diaas
            FROM fact_protein_observations f
            JOIN dim_commodities c ON f.commodity_code = c.commodity_code
            WHERE f.price_basis = 'OBSERVED_MARKET'
              AND f.is_outlier_mad = FALSE
              AND f.obs_year >= 1700;
        """).df()

        df_lab = self.con.execute("""
            SELECT
                c.commodity_code, c.commodity_name, c.regime_class, f.price_basis,
                c.shelf_life_days, c.diaas_capped, c.dry_matter_fraction,
                (50.0 / (c.edible_fraction * c.crude_protein_g_kg * c.prep_retention * c.diaas_score)) AS kg_per_50g
            FROM fact_protein_observations f
            JOIN dim_commodities c ON f.commodity_code = c.commodity_code
            WHERE f.source_dataset = 'TEA_BENCHMARK'
              AND c.regime_class IN ('PLANT_ISOLATE','MICROBIAL_BIOMASS','PRECISION_FERMENTATION','CELLULAR_CULTIVATED')
            GROUP BY 1,2,3,4,5,6,7,8;
        """).df()

        # Unified OECD/Hub reference wage ($16.50/hr 2026 PPP) for apples-to-apples benchmark labor minutes
        REF_WAGE_HR = 16.50
        lab_records = []
        for r in df_lab.itertuples(index=False):
            low_kg, mid_kg, high_kg = BENCHMARK_RANGES_2026.get(r.commodity_code, (5.0, 6.0, 8.0))
            u_low, u_mid, u_high = low_kg * r.kg_per_50g, mid_kg * r.kg_per_50g, high_kg * r.kg_per_50g
            lab_records.append({
                "commodity_code": r.commodity_code,
                "commodity_name": r.commodity_name,
                "regime_class": r.regime_class,
                "price_basis": r.price_basis,
                "shelf_life_days": round(float(r.shelf_life_days), 1),
                "diaas_capped": round(float(r.diaas_capped), 3),
                "dry_matter_fraction": round(float(r.dry_matter_fraction), 3),
                "usd_50g": round(u_mid, 3),
                "usd_50g_low": round(u_low, 3),
                "usd_50g_high": round(u_high, 3),
                "labor_min_50g": round((u_mid / REF_WAGE_HR) * 60.0, 2),
                "labor_min_50g_low": round((u_low / REF_WAGE_HR) * 60.0, 2),
                "labor_min_50g_high": round((u_high / REF_WAGE_HR) * 60.0, 2),
            })

        x_axes = {
            "shelf_life": ("shelf_life_days", [1.0, 1100.0]),
            "diaas": ("diaas_capped", [0.40, 1.05]),
            "dry_density": ("dry_matter_fraction", [0.10, 1.00]),
        }
        y_axes = {
            "labor_min": ("labor_min_50g_diaas", [0.5, 1200.0]),
            "usd_2026": ("usd_per_50g_diaas", [0.12, 35.0]),
        }

        decades_payload = {}
        for decade, grp in df_obs.groupby("obs_decade"):
            dec_key = str(int(decade))
            decades_payload[dec_key] = {"count": int(len(grp)), "frontiers": {}, "density_bins": {}}
            for x_key, (x_col, x_bounds) in x_axes.items():
                decades_payload[dec_key]["frontiers"][x_key] = {}
                decades_payload[dec_key]["density_bins"][x_key] = {}
                x_vals = np.clip(grp[x_col].to_numpy(dtype=float), x_bounds[0], x_bounds[1])
                log_x = np.log10(x_vals)
                x_edges = np.linspace(np.log10(x_bounds[0]), np.log10(x_bounds[1]), grid_bins + 1)

                for y_key, (y_col, y_bounds) in y_axes.items():
                    y_vals = np.clip(grp[y_col].to_numpy(dtype=float), y_bounds[0], y_bounds[1])
                    decades_payload[dec_key]["frontiers"][x_key][y_key] = self.compute_robust_fdh(x_vals, y_vals)

                    log_y = np.log10(y_vals)
                    y_edges = np.linspace(np.log10(y_bounds[0]), np.log10(y_bounds[1]), grid_bins + 1)
                    ix = np.clip(np.digitize(log_x, x_edges) - 1, 0, grid_bins - 1)
                    iy = np.clip(np.digitize(log_y, y_edges) - 1, 0, grid_bins - 1)
                    agg_bins = (
                        pd.DataFrame({"ix": ix, "iy": iy, "regime": grp["regime_class"].values})
                        .groupby(["ix", "iy"])
                        .agg(n=("regime", "count"), regime=("regime", lambda s: s.mode().iat[0]))
                        .reset_index()
                    )
                    decades_payload[dec_key]["density_bins"][x_key][y_key] = [
                        {
                            "x": round(float(10 ** (0.5 * (x_edges[r.ix] + x_edges[r.ix + 1]))), 4),
                            "y": round(float(10 ** (0.5 * (y_edges[r.iy] + y_edges[r.iy + 1]))), 4),
                            "n": int(r.n),
                            "regime": r.regime,
                        }
                        for r in agg_bins.itertuples(index=False)
                    ]

        payload = {
            "meta": {"total_observed_points": int(len(df_obs)), "x_axes": x_axes, "y_axes": y_axes},
            "decades": decades_payload,
            "lab_benchmarks_2026": lab_records,
        }
        out_file = self.out_dir / "gvk_density_and_frontiers.json"
        out_file.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8")
        print(f"[1/4] Wrote {out_file} ({out_file.stat().st_size / 1024:.1f} KB)")

    def export_spatiotemporal_markets(self, max_points: int = 100_000) -> None:
        df = self.con.execute(f"""
            (SELECT
                mk.market_id, mk.market_name, mk.country_iso3,
                ROUND(mk.latitude, 3) AS lat, ROUND(mk.longitude, 3) AS lon,
                ROUND(COALESCE(mk.arable_ha_per_cap, 0.15), 4) AS arable_ha,
                CASE WHEN f.obs_year = 2026 THEN 2026 ELSE f.obs_decade END AS decade,
                c.regime_class AS regime,
                ROUND(MEDIAN(f.usd_per_50g_diaas), 3) AS usd_50g,
                ROUND(MEDIAN(f.labor_min_50g_diaas), 2) AS labor_min
            FROM fact_protein_observations f
            JOIN dim_markets mk ON f.market_id = mk.market_id
            JOIN dim_commodities c ON f.commodity_code = c.commodity_code
            WHERE f.price_basis = 'OBSERVED_MARKET' AND f.is_outlier_mad = FALSE AND f.obs_year >= 1700
              AND (mk.latitude <> 0 OR mk.longitude <> 0)
            GROUP BY 1,2,3,4,5,6,7,8
            USING SAMPLE {max_points} ROWS
            )
            UNION ALL
            (SELECT
                mk.market_id, mk.market_name, mk.country_iso3,
                ROUND(mk.latitude, 3) AS lat, ROUND(mk.longitude, 3) AS lon,
                ROUND(COALESCE(mk.arable_ha_per_cap, 0.15), 4) AS arable_ha,
                CASE WHEN f.obs_year = 2026 THEN 2026 ELSE f.obs_decade END AS decade,
                c.regime_class AS regime,
                ROUND(MEDIAN(f.usd_per_50g_diaas), 3) AS usd_50g,
                ROUND(MEDIAN(f.labor_min_50g_diaas), 2) AS labor_min
            FROM fact_protein_observations f
            JOIN dim_markets mk ON f.market_id = mk.market_id
            JOIN dim_commodities c ON f.commodity_code = c.commodity_code
            WHERE f.price_basis = 'ETHNOGRAPHIC_TIME_ALLOCATION'
              AND (mk.latitude <> 0 OR mk.longitude <> 0)
            GROUP BY 1,2,3,4,5,6,7,8);
        """).df()
        

        payload = {
            "count": int(len(df)),
            "columns": {
                "market_id": df["market_id"].tolist(),
                "market_name": df["market_name"].tolist(),
                "lat": df["lat"].tolist(),
                "lon": df["lon"].tolist(),
                "arable_ha": df["arable_ha"].tolist(),
                "decade": df["decade"].astype(int).tolist(),
                "regime": df["regime"].tolist(),
                "usd_50g": [None if (v is None or pd.isna(v) or v <= 0) else float(v) for v in df["usd_50g"]],
                "labor_min": df["labor_min"].tolist(),
            },
        }
        out_file = self.out_dir / "spatiotemporal_markets.json"
        out_file.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8")
        print(f"[2/4] Wrote {out_file} ({out_file.stat().st_size / 1024:.1f} KB)")

    def export_heatmap_matrix(self) -> None:
        hub_ids = [
            "HIST_BUENOS_AIRES", "HIST_CHICAGO", "MOD_SYDNEY", "HIST_MOSCOW",
            "HIST_GDANSK", "HIST_PARIS", "HIST_LONDON", "HIST_AMSTERDAM",
            "HIST_MUNICH", "HIST_MILAN", "HIST_MADRID", "HIST_ISTANBUL",
            "HIST_CAIRO", "HIST_BEIJING", "HIST_KYOTO", "HIST_NEW_YORK",
        ]
        id_list = ",".join(f"'{h}'" for h in hub_ids)
        markets = self.con.execute(f"""
            SELECT market_id, market_name, country_iso3, region_macro,
                   ROUND(COALESCE(arable_ha_per_cap, 0.15), 3) AS arable_ha_per_cap,
                   ROUND(COALESCE(industrial_elec_kwh, 0.12), 3) AS industrial_elec_kwh
            FROM dim_markets WHERE market_id IN ({id_list})
            ORDER BY arable_ha_per_cap DESC;
        """).df()

        cells_df = self.con.execute(f"""
            SELECT
                f.market_id,
                CASE WHEN f.obs_year = 2026 THEN 2026 ELSE f.obs_decade END AS decade,
                ROUND(MEDIAN(f.usd_per_50g_diaas), 3) AS median_usd_50g,
                ROUND(QUANTILE_CONT(f.usd_per_50g_diaas, 0.90), 3) AS p90_usd_50g,
                ROUND(MEDIAN(f.labor_min_50g_diaas), 2) AS median_labor_min,
                ROUND(QUANTILE_CONT(f.labor_min_50g_diaas, 0.90), 2) AS p90_labor_min,
                ROUND(MIN(CASE WHEN c.is_animal_source THEN f.usd_per_50g_diaas END), 3) AS cheapest_animal_usd,
                ROUND(MIN(CASE WHEN c.is_animal_source THEN f.labor_min_50g_diaas END), 2) AS cheapest_animal_labor
            FROM fact_protein_observations f
            JOIN dim_commodities c ON f.commodity_code = c.commodity_code
            WHERE f.market_id IN ({id_list})
              AND f.price_basis IN ('OBSERVED_MARKET', 'OBSERVED_RETAIL')
              AND f.is_outlier_mad = FALSE
              AND f.obs_year >= 1700
            GROUP BY 1, 2
            ORDER BY 1, 2;
        """).df()

        pf_parity_usd = 0.87
        if not cells_df.empty:
            cells_df["bioreactor_undercuts_animal"] = cells_df["cheapest_animal_usd"].apply(
                lambda v: bool(v > pf_parity_usd) if pd.notnull(v) else False
            )

        payload = {
            "meta": {
                "ordering_variable": "arable_ha_per_cap_descending",
                "precision_fermentation_parity_usd_50g": pf_parity_usd,
            },
            "locations": clean_records(markets),
            "cells": clean_records(cells_df),
        }
        out_file = self.out_dir / "heatmap_matrix.json"
        out_file.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8")
        print(f"[3/4] Wrote {out_file} ({out_file.stat().st_size / 1024:.1f} KB)")

    def export_trophic_and_stigler_series(self) -> None:
        # Compute within-market Trophic Tax ratios so cross-country wage shifts never invert the ratio
        series_df = self.con.execute("""
            WITH mkt_dec AS (
                SELECT
                    CASE WHEN f.obs_year = 2026 THEN 2026 ELSE f.obs_decade END AS obs_decade,
                    f.market_id,
                    QUANTILE_CONT(CASE WHEN NOT c.is_animal_source THEN f.usd_per_50g_diaas END, 0.25) AS plant_usd,
                    QUANTILE_CONT(CASE WHEN c.is_animal_source THEN f.usd_per_50g_diaas END, 0.25)     AS animal_usd,
                    QUANTILE_CONT(CASE WHEN NOT c.is_animal_source THEN f.labor_min_50g_diaas END, 0.25) AS plant_lmin,
                    QUANTILE_CONT(CASE WHEN c.is_animal_source THEN f.labor_min_50g_diaas END, 0.25)     AS animal_lmin
                FROM fact_protein_observations f
                JOIN dim_commodities c ON f.commodity_code = c.commodity_code
                WHERE f.price_basis = 'OBSERVED_MARKET' AND f.is_outlier_mad = FALSE AND f.obs_year >= 1700
                GROUP BY 1, 2
            ),
            base AS (
                SELECT
                    CASE WHEN f.obs_year = 2026 THEN 2026 ELSE f.obs_decade END AS obs_decade,
                    ROUND(QUANTILE_CONT(CASE WHEN NOT c.is_animal_source THEN f.usd_per_50g_diaas END, 0.15), 3) AS plant_frontier_usd,
                    ROUND(QUANTILE_CONT(CASE WHEN c.is_animal_source THEN f.usd_per_50g_diaas END, 0.15), 3)     AS animal_frontier_usd,
                    ROUND(QUANTILE_CONT(f.usd_per_50g_diaas, 0.10), 3) AS market_p10_usd,
                    ROUND(QUANTILE_CONT(f.usd_per_50g_diaas, 0.50), 3) AS market_p50_usd,
                    ROUND(QUANTILE_CONT(f.usd_per_50g_diaas, 0.90), 3) AS market_p90_usd,
                    ROUND(QUANTILE_CONT(CASE WHEN NOT c.is_animal_source THEN f.labor_min_50g_diaas END, 0.15), 2) AS plant_frontier_labor,
                    ROUND(QUANTILE_CONT(CASE WHEN c.is_animal_source THEN f.labor_min_50g_diaas END, 0.15), 2)     AS animal_frontier_labor,
                    ROUND(QUANTILE_CONT(f.labor_min_50g_diaas, 0.10), 2) AS market_p10_labor,
                    ROUND(QUANTILE_CONT(f.labor_min_50g_diaas, 0.50), 2) AS market_p50_labor,
                    ROUND(QUANTILE_CONT(f.labor_min_50g_diaas, 0.90), 2) AS market_p90_labor
                FROM fact_protein_observations f
                JOIN dim_commodities c ON f.commodity_code = c.commodity_code
                WHERE f.price_basis = 'OBSERVED_MARKET' AND f.is_outlier_mad = FALSE AND f.obs_year >= 1700
                GROUP BY 1
            ),
            within_tax AS (
                SELECT
                    obs_decade,
                    ROUND(MEDIAN(animal_usd / NULLIF(plant_usd, 0)), 2) AS trophic_tax_ratio_usd,
                    ROUND(MEDIAN(animal_lmin / NULLIF(plant_lmin, 0)), 2) AS trophic_tax_ratio_labor
                FROM mkt_dec
                WHERE animal_usd > 0 AND plant_usd > 0
                GROUP BY 1
            ),
            stigler AS (
                SELECT obs_decade,
                       ROUND(MEDIAN(shadow_usd_50g), 3) AS stigler_shadow_usd,
                       ROUND(MEDIAN(shadow_labor_min_50g), 2) AS stigler_shadow_labor
                FROM agg_stigler_shadow_prices GROUP BY 1
            )
            SELECT
                b.*,
                s.stigler_shadow_usd,
                s.stigler_shadow_labor,
                COALESCE(w.trophic_tax_ratio_usd, ROUND(b.animal_frontier_usd / NULLIF(b.plant_frontier_usd, 0), 2)) AS trophic_tax_ratio_usd,
                COALESCE(w.trophic_tax_ratio_labor, ROUND(b.animal_frontier_labor / NULLIF(b.plant_frontier_labor, 0), 2)) AS trophic_tax_ratio_labor,
                COALESCE(w.trophic_tax_ratio_usd, ROUND(b.animal_frontier_usd / NULLIF(b.plant_frontier_usd, 0), 2)) AS trophic_tax_ratio
            FROM base b
            LEFT JOIN within_tax w ON b.obs_decade = w.obs_decade
            LEFT JOIN stigler s ON b.obs_decade = s.obs_decade
            ORDER BY b.obs_decade;
        """).df()

        annotations = [
            {"decade": 1840, "label": "Irish Potato Blight & Legume Shock", "detail": "Acute European caloric and plant-nitrogen scarcity"},
            {"decade": 1880, "label": "Transoceanic Refrigerated Shipping", "detail": "Compression of intercontinental meat price spreads"},
            {"decade": 1910, "label": "Haber-Bosch Nitrogen Fixation", "detail": "Synthetic ammonia lowers feed grain floor"},
            {"decade": 1950, "label": "Industrial Broiler & Feedlot Scaling", "detail": "Feed conversion ratios drop from 4.5 to <1.8"},
            {"decade": 2020, "label": "Precision Fermentation & Bioreactors", "detail": "Acellular dairy and microbial proteins undercut livestock"},
        ]
        payload = {"series": clean_records(series_df), "historical_milestones": annotations}
        out_file = self.out_dir / "trophic_and_stigler_series.json"
        out_file.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8")
        print(f"[4/4] Wrote {out_file} ({out_file.stat().st_size / 1024:.1f} KB)")

    def export_all(self) -> None:
        self.export_gvk_density_and_frontiers()
        self.export_spatiotemporal_markets()
        self.export_heatmap_matrix()
        self.export_trophic_and_stigler_series()
        print("All 4 Lovable web artifacts exported cleanly (strict JSON, 0 NaNs).")

if __name__ == "__main__":
    LovableArtifactExporter().export_all()
