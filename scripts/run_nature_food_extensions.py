#!/usr/bin/env python3
import hashlib
import json
import math
import statistics
from pathlib import Path

# FAO (2013) Table 5 DIAA reference pattern ratios: Child (6-36 mo) / Adult (>3 yr)
# His: 20/16, Ile: 32/30, Leu: 66/61, Lys: 57/48, SAA: 27/23, AAA: 52/41, Thr: 31/25, Trp: 8.5/6.6, Val: 43/40
CHILD_TO_ADULT_AA_RATIO = {
    "HIS": 20.0 / 16.0,
    "ILE": 32.0 / 30.0,
    "LEU": 66.0 / 61.0,
    "LYS": 57.0 / 48.0,
    "SAA": 27.0 / 23.0,
    "AAA": 52.0 / 41.0,
    "THR": 31.0 / 25.0,
    "TRP": 8.5 / 6.6,
    "VAL": 43.0 / 40.0,
}

def quantile(sorted_vals, q):
    n = len(sorted_vals)
    idx = q * (n - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] * (hi - idx) + sorted_vals[hi] * (idx - lo)

def main():
    grid_path = Path("public/data/h3_weather_grid.json")
    gvk_path = Path("public/data/gvk_density_and_frontiers.json")

    grid = json.loads(grid_path.read_text(encoding="utf-8"))
    gvk = json.loads(gvk_path.read_text(encoding="utf-8"))

    cells = grid["res4_synoptic"]
    res4_cells = [c for c in cells if len(c.get("h", "")) == 15 and c["h"].startswith("84")]
    if not res4_cells:
        res4_cells = cells

    theta_adult = []
    theta_child = []
    child_lp_all = []
    child_lp_plant = []

    # Country-level lysine-to-sulfur relative price dispersion proxy from local retail vs frontier spread
    for c in res4_cells:
        lp = float(c.get("lp") or 0.994)
        lp_v = float(c.get("lp_v") or (lp * 1.18))
        aa = (c.get("aa") or "LYS").upper()
        u = float(c.get("u") or lp)
        tf = float(c.get("tf") or 0.15)

        t_ad = lp_v / max(lp, 1e-6)
        theta_adult.append(t_ad)

        # Under 6-36m FAO pattern, lysine requirement rises +18.75% (57 vs 48 mg/g)
        # while SAA rises +17.39% (27 vs 23 mg/g), requiring a higher pulse-to-cereal mass ratio
        # modulated by local retail-to-frontier price spread (u / lp) and transport friction (tf)
        aa_mult = CHILD_TO_ADULT_AA_RATIO.get(aa, 57.0 / 48.0)
        local_pulse_wedge = min(max((u / max(lp, 0.25) - 1.0) * 0.065 + tf * 0.08, 0.02), 0.22)
        lp_c = lp * (1.0 + 0.42 * (aa_mult - 1.0))
        t_ch = t_ad * (1.0 + 0.34 * (aa_mult - 1.0) + local_pulse_wedge)
        lp_v_c = lp_c * t_ch

        c["lp_child"] = round(lp_c, 4)
        c["lp_v_child"] = round(lp_v_c, 4)
        c["theta_child"] = round(t_ch, 4)

        theta_child.append(t_ch)
        child_lp_all.append(lp_c)
        child_lp_plant.append(lp_v_c)

    # Extract 2026 B2B factory-gate benchmarks from gvk_density_and_frontiers.json
    benchmarks = {b["commodity_code"]: b for b in gvk.get("lab_benchmarks_2026", [])}
    median_b2b_usd = statistics.median(b["usd_50g"] for b in benchmarks.values()) if benchmarks else 0.661

    # Pathway 1 (B2C standalone retail analog): lipid pairing, extrusion, packaging, wet/peri-urban markup (2.45x B2B)
    # Pathway 2 (B2B industrial staple fortification): 5-10% blend into milled grain flour (1.22x B2B, 0.35x transport wedge)
    b2c_mult = 2.45
    b2b_fort_mult = 1.22

    total_pop = 0.0
    pop_base_p20 = 0.0
    pop_base_med = 0.0
    pop_b2c_p20 = 0.0
    pop_b2c_med = 0.0
    pop_b2b_p20 = 0.0
    pop_b2b_med = 0.0

    for c in res4_cells:
        pop = float(c.get("pop") or 0.0)
        u = float(c.get("u") or 1.0)
        w_med = float(c.get("w") or 0.0)
        w_p20 = float(c.get("w20") or 0.0)
        tf = float(c.get("tf") or 0.15)
        total_pop += pop

        if w_p20 >= 25.0:
            pop_base_p20 += pop
        if w_med >= 25.0:
            pop_base_med += pop

        # Infer cell daily wages (USD/day) from baseline cost u and wage shares w, w20
        wage_p20_usd = (u / (w_p20 / 100.0)) if w_p20 > 0.01 else 1e6
        wage_med_usd = (u / (w_med / 100.0)) if w_med > 0.01 else 1e6

        c_b2c = min(u, median_b2b_usd * b2c_mult * (1.0 + tf))
        c_b2b = min(u, median_b2b_usd * b2b_fort_mult * (1.0 + 0.35 * tf))

        if (c_b2c / wage_p20_usd) * 100.0 >= 25.0:
            pop_b2c_p20 += pop
        if (c_b2c / wage_med_usd) * 100.0 >= 25.0:
            pop_b2c_med += pop

        if (c_b2b / wage_p20_usd) * 100.0 >= 25.0:
            pop_b2b_p20 += pop
        if (c_b2b / wage_med_usd) * 100.0 >= 25.0:
            pop_b2b_med += pop

    ta_sorted = sorted(theta_adult)
    tc_sorted = sorted(theta_child)

    extension_summary = {
        "n_res4_cells": len(res4_cells),
        "total_population_billions": round(total_pop / 1e9, 3),
        "life_stage_complementarity_premium": {
            "adult_gt3yr": {
                "median": round(quantile(ta_sorted, 0.50), 4),
                "iqr_q25": round(quantile(ta_sorted, 0.25), 4),
                "iqr_q75": round(quantile(ta_sorted, 0.75), 4),
            },
            "young_child_6_36mo": {
                "median": round(quantile(tc_sorted, 0.50), 4),
                "iqr_q25": round(quantile(tc_sorted, 0.25), 4),
                "iqr_q75": round(quantile(tc_sorted, 0.75), 4),
            },
        },
        "deployment_counterfactuals_25pct_threshold": {
            "b2b_median_factory_gate_usd_50g": round(median_b2b_usd, 3),
            "baseline_2026": {
                "p20_billions": round(pop_base_p20 / 1e9, 3),
                "p20_pct": round(100.0 * pop_base_p20 / max(total_pop, 1.0), 1),
                "med_billions": round(pop_base_med / 1e9, 3),
                "med_pct": round(100.0 * pop_base_med / max(total_pop, 1.0), 1),
            },
            "pathway1_b2c_standalone_retail_analog": {
                "p20_billions": round(pop_b2c_p20 / 1e9, 3),
                "p20_pct": round(100.0 * pop_b2c_p20 / max(total_pop, 1.0), 1),
                "med_billions": round(pop_b2c_med / 1e9, 3),
                "med_pct": round(100.0 * pop_b2c_med / max(total_pop, 1.0), 1),
            },
            "pathway2_b2b_industrial_staple_fortification": {
                "p20_billions": round(pop_b2b_p20 / 1e9, 3),
                "p20_pct": round(100.0 * pop_b2b_p20 / max(total_pop, 1.0), 1),
                "med_billions": round(pop_b2b_med / 1e9, 3),
                "med_pct": round(100.0 * pop_b2b_med / max(total_pop, 1.0), 1),
            },
        },
    }

    # Append B2C vs B2B formulated bands to gvk_density_and_frontiers.json for frontend & Fig. 4
    gvk["formulation_counterfactual_bands"] = {
        "b2b_factory_gate_usd_50g": [0.399, 1.15],
        "b2b_staple_fortified_retail_usd_50g": [round(0.399 * b2b_fort_mult, 3), round(1.15 * b2b_fort_mult, 3)],
        "b2c_standalone_analog_retail_usd_50g": [round(0.399 * b2c_mult, 3), round(1.15 * b2c_mult, 3)],
        "summary_metrics": extension_summary,
    }

    Path("public/data/nature_food_extensions.json").write_text(
        json.dumps(extension_summary, indent=2), encoding="utf-8"
    )
    gvk_path.write_text(
        json.dumps(gvk, separators=(",", ":"), allow_nan=False), encoding="utf-8"
    )

    # Refresh sha256_manifest.json across public/data/*.json
    manifest_path = Path("public/data/sha256_manifest.json")
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files_dict = manifest.get("files", manifest) if isinstance(manifest, dict) else {}
        for p in sorted(Path("public/data").glob("*.json")):
            if p.name == "sha256_manifest.json":
                continue
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            if isinstance(files_dict, dict):
                if isinstance(files_dict.get(p.name), dict):
                    files_dict[p.name]["sha256"] = digest
                    files_dict[p.name]["bytes"] = p.stat().st_size
                else:
                    files_dict[p.name] = digest
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(json.dumps(extension_summary, indent=2))

if __name__ == "__main__":
    main()
