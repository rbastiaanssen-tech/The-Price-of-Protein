from __future__ import annotations
import duckdb
import numpy as np
import pandas as pd
from scipy.optimize import linprog


class StiglerProteinSolver:
    def __init__(
        self,
        db_path: str = "protein_atlas.duckdb",
        daily_kcal_req: float = 2100.0,
        daily_protein_req_g: float = 50.0,
        max_daily_food_kg: float = 2.5,
    ):
        self.db_path = db_path
        self.daily_kcal = daily_kcal_req
        self.daily_protein = daily_protein_req_g
        self.max_bulk_kg = max_daily_food_kg
        self.con = duckdb.connect(self.db_path)

    def init_output_table(self) -> None:
        self.con.execute("DROP TABLE IF EXISTS agg_stigler_shadow_prices;")
        self.con.execute("""
            CREATE TABLE agg_stigler_shadow_prices (
                market_id               VARCHAR NOT NULL,
                obs_decade              INTEGER NOT NULL,
                n_commodities           INTEGER NOT NULL,
                min_diet_usd_day        DOUBLE NOT NULL,
                min_diet_labor_min_day  DOUBLE NOT NULL,
                shadow_usd_50g          DOUBLE NOT NULL,
                shadow_labor_min_50g    DOUBLE NOT NULL,
                binding_protein_food    VARCHAR,
                PRIMARY KEY (market_id, obs_decade)
            );
        """)

    @staticmethod
    def _solve_single_cell(
        prices: np.ndarray,
        kcal_per_kg: np.ndarray,
        eff_prot_g_kg: np.ndarray,
        codes: list[str],
        daily_kcal: float,
        daily_prot: float,
        max_bulk: float,
    ) -> tuple[float, float, str] | None:
        """
        Pure empirical Stigler diet LP (no synthetic grain anchors):
            min_{x >= 0}  c^T x
            s.t.  sum(kcal_i * x_i) >= daily_kcal
                  sum(prot_i * x_i) >= daily_prot
                  sum(x_i)          <= max_bulk
        """
        n = len(prices)
        A_ub = np.vstack([
            -kcal_per_kg,
            -eff_prot_g_kg,
            np.ones(n),
        ])
        b_ub = np.array([-daily_kcal, -daily_prot, max_bulk], dtype=float)

        res = linprog(c=prices, A_ub=A_ub, b_ub=b_ub, bounds=(0, None), method="highs")
        if not res.success or res.ineqlin is None:
            return None

        marginal_per_gram = max(0.0, -float(res.ineqlin.marginals[1]))
        shadow_50g = marginal_per_gram * daily_prot

        prot_supplied = res.x * eff_prot_g_kg
        top_idx = int(np.argmax(prot_supplied))
        return float(res.fun), shadow_50g, codes[top_idx]

    def solve_all_market_decades(self) -> None:
        self.init_output_table()

        df = self.con.execute("""
            SELECT
                f.market_id,
                f.obs_decade,
                f.commodity_code,
                c.kcal_per_kg,
                c.eff_protein_g_kg,
                MEDIAN(f.real_usd_per_kg) AS med_usd_kg,
                MEDIAN(f.real_usd_per_kg * (f.labor_min_50g_diaas / NULLIF(f.usd_per_50g_diaas, 0))) AS med_labor_min_kg
            FROM fact_protein_observations f
            JOIN dim_commodities c ON f.commodity_code = c.commodity_code
            WHERE f.price_basis = 'OBSERVED_MARKET'
              AND f.is_outlier_mad = FALSE
              AND f.real_usd_per_kg > 0
              AND f.labor_min_50g_diaas > 0
            GROUP BY 1, 2, 3, 4, 5
            ORDER BY 1, 2;
        """).df()

        if df.empty:
            print("[Stigler LP] No observed market records found.")
            return

        results = []
        for (mkt_id, decade), grp in df.groupby(["market_id", "obs_decade"]):
            # Require at least 3 distinct observed foods and sufficient caloric density for feasibility
            if len(grp) < 3 or grp["kcal_per_kg"].max() * self.max_bulk_kg < self.daily_kcal:
                continue

            codes = grp["commodity_code"].tolist()
            kcal = grp["kcal_per_kg"].to_numpy(dtype=float)
            prot = grp["eff_protein_g_kg"].to_numpy(dtype=float)
            usd_kg = grp["med_usd_kg"].to_numpy(dtype=float)
            lab_kg = grp["med_labor_min_kg"].to_numpy(dtype=float)

            sol_usd = self._solve_single_cell(
                usd_kg, kcal, prot, codes,
                self.daily_kcal, self.daily_protein, self.max_bulk_kg
            )
            sol_lab = self._solve_single_cell(
                lab_kg, kcal, prot, codes,
                self.daily_kcal, self.daily_protein, self.max_bulk_kg
            )

            if sol_usd and sol_lab:
                results.append((
                    mkt_id,
                    int(decade),
                    len(codes),
                    sol_usd[0],
                    sol_lab[0],
                    sol_usd[1],
                    sol_lab[1],
                    sol_usd[2],
                ))

        out_df = pd.DataFrame(results, columns=[
            "market_id", "obs_decade", "n_commodities",
            "min_diet_usd_day", "min_diet_labor_min_day",
            "shadow_usd_50g", "shadow_labor_min_50g", "binding_protein_food"
        ])

        self.con.register("df_stigler_out", out_df)
        self.con.execute("INSERT INTO agg_stigler_shadow_prices SELECT * FROM df_stigler_out;")
        print(f"[Stigler LP] Solved {len(out_df):,} pure empirical multi-commodity market-decade cells (0 synthetic anchors).")


if __name__ == "__main__":
    solver = StiglerProteinSolver(db_path="protein_atlas.duckdb")
    solver.solve_all_market_decades()
