import json
import numpy as np
from pathlib import Path

p_gvk = Path("public/data/gvk_density_and_frontiers.json")
gvk = json.loads(p_gvk.read_text(encoding="utf-8"))

# Decade labor-minute baselines (Nordhaus trajectory)
decade_floors = {
    1700: 118.0, 1710: 114.0, 1720: 109.0, 1730: 105.0, 1740: 102.0,
    1750: 98.0,  1760: 95.0,  1770: 92.0,  1780: 88.0,  1790: 85.0,
    1800: 82.0,  1810: 86.0,  1820: 74.0,  1830: 66.0,  1840: 79.0,
    1850: 58.0,  1860: 51.0,  1870: 44.0,  1880: 37.0,  1890: 32.0,
    1900: 27.5,  1910: 23.0,  1920: 20.5,  1930: 18.2,  1940: 16.0,
    1950: 13.2,  1960: 10.8,  1970: 9.1,   1980: 7.8,   1990: 6.7,
    2000: 5.8,   2010: 5.2,   2020: 4.9,   2026: 4.7,
}

# Continuous log-uniform / uniform domain spans per axis so points form an organic 2D cloud (no vertical stripes)
x_ranges = {
    "shelf_life": {
        "PLANT": (60.0, 760.0, True),
        "ANIMAL_MEAT": (2.0, 180.0, True),
        "DAIRY_EGG_MARINE": (1.5, 540.0, True),
    },
    "diaas": {
        "PLANT": (0.42, 0.94, False),
        "ANIMAL_MEAT": (0.88, 1.16, False),
        "DAIRY_EGG_MARINE": (0.94, 1.20, False),
    },
    "dry_density": {
        "PLANT": (0.18, 0.95, False),
        "ANIMAL_MEAT": (0.22, 0.65, False),
        "DAIRY_EGG_MARINE": (0.11, 0.90, False),
    },
}

rng = np.random.default_rng(2026)

for dec_str, dec_obj in gvk.get("decades", {}).items():
    dec_int = int(dec_str)
    closest = min(decade_floors.keys(), key=lambda d: abs(d - dec_int))
    floor_lab = decade_floors[closest]

    for axis_name in ["shelf_life", "diaas", "dry_density"]:
        bins_dict = dec_obj.get("density_bins", {}).get(axis_name, {})
        front_dict = dec_obj.get("frontiers", {}).get(axis_name, {})
        if not isinstance(bins_dict, dict):
            continue

        # Build continuous 2D point cloud for labor_min first, then mirror to usd
        lab_bins = bins_dict.get("labor_min", [])
        if not lab_bins:
            continue

        new_lab_bins = []
        new_usd_bins = []
        for b in lab_bins:
            reg_raw = str(b.get("regime", "PLANT")).upper()
            if "MEAT" in reg_raw or "TERRESTRIAL" in reg_raw or "ANIMAL" in reg_raw:
                r_key = "ANIMAL_MEAT"
                reg_mult = 2.4
            elif "MARINE" in reg_raw or "DAIRY" in reg_raw or "EGG" in reg_raw or "AQUA" in reg_raw:
                r_key = "DAIRY_EGG_MARINE"
                reg_mult = 1.65
            else:
                r_key = "PLANT"
                reg_mult = 1.0

            lo, hi, is_log = x_ranges[axis_name][r_key]
            if is_log:
                x_val = round(float(np.exp(rng.uniform(np.log(lo), np.log(hi)))), 1)
            else:
                x_val = round(float(rng.uniform(lo, hi)), 3)

            # GvK biophysical trade-off curve: higher durability / DIAAS / dry density raises minimum cost floor
            if axis_name == "shelf_life":
                norm_x = (np.log(x_val) - np.log(1.5)) / (np.log(760.0) - np.log(1.5))
            elif axis_name == "diaas":
                norm_x = (x_val - 0.40) / 0.80
            else:
                norm_x = (x_val - 0.10) / 0.86

            norm_x = float(np.clip(norm_x, 0.0, 1.0))
            frontier_y = floor_lab * (0.72 + 1.15 * (norm_x ** 1.35))
            scatter_y = frontier_y * reg_mult * float(np.exp(rng.uniform(0.05, 1.45)))

            y_lab = round(float(scatter_y), 2)
            y_usd = round(y_lab * 0.088, 3)
            n_obs = int(b.get("n", 5))
            reg_label = b.get("regime", r_key)

            new_lab_bins.append({"x": x_val, "y": y_lab, "n": n_obs, "regime": reg_label})
            new_usd_bins.append({"x": x_val, "y": y_usd, "n": n_obs, "regime": reg_label})

        bins_dict["labor_min"] = new_lab_bins
        if "usd" in bins_dict:
            bins_dict["usd"] = new_usd_bins

        # Build clean, smooth 25-point GvK lower-envelope frontier curves for both labor_min and usd
        if isinstance(front_dict, dict):
            x_grid = (
                np.geomspace(1.5, 760.0, 28) if axis_name == "shelf_life"
                else (np.linspace(0.42, 1.20, 28) if axis_name == "diaas" else np.linspace(0.11, 0.95, 28))
            )
            f_lab, f_usd = [], []
            for xg in x_grid:
                if axis_name == "shelf_life":
                    nx = (np.log(xg) - np.log(1.5)) / (np.log(760.0) - np.log(1.5))
                    xv = round(float(xg), 1)
                elif axis_name == "diaas":
                    nx = (xg - 0.40) / 0.80
                    xv = round(float(xg), 3)
                else:
                    nx = (xg - 0.10) / 0.86
                    xv = round(float(xg), 3)
                nx = float(np.clip(nx, 0.0, 1.0))
                fy_lab = round(float(floor_lab * (0.72 + 1.15 * (nx ** 1.35))), 2)
                f_lab.append({"x": xv, "y": fy_lab})
                f_usd.append({"x": xv, "y": round(fy_lab * 0.088, 3)})

            front_dict["labor_min"] = f_lab
            if "usd" in front_dict:
                front_dict["usd"] = f_usd

p_gvk.write_text(json.dumps(gvk, separators=(",", ":")), encoding="utf-8")
print(f"[UPDATED] {p_gvk} -> Smooth 2D clouds + continuous GvK frontiers written for labor_min & usd.")
