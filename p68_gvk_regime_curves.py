import json
import numpy as np
from pathlib import Path

p_gvk = Path("public/data/gvk_density_and_frontiers.json")
gvk = json.loads(p_gvk.read_text(encoding="utf-8"))

regimes = [
    ("FRESH_PELAGIC", "Small Pelagic Fish (Fresh)", "Marine", "#1B6CA8", (1.5, 6.0), (0.94, 1.04), (0.22, 0.28), 6.2, 0.35),
    ("FLUID_MILK", "Fluid Cow Milk", "Dairy", "#2E86AB", (4.0, 14.0), (1.08, 1.18), (0.12, 0.14), 8.4, 0.42),
    ("SHELL_EGGS", "Shell Eggs", "Dairy & Egg", "#3A9AD9", (14.0, 42.0), (1.08, 1.15), (0.23, 0.26), 9.6, 0.38),
    ("BROILER_CHICKEN", "Broiler Chicken", "Livestock", "#C0392B", (3.0, 12.0), (1.00, 1.08), (0.25, 0.30), 11.2, 0.45),
    ("BEEF_PORK", "Terrestrial Red Meat", "Livestock", "#8C1624", (4.0, 21.0), (0.98, 1.11), (0.28, 0.38), 22.5, 0.50),
    ("HARD_CHEESE", "Aged Hard Cheese", "Dairy", "#D97724", (60.0, 270.0), (1.10, 1.18), (0.58, 0.68), 16.5, 0.40),
    ("STOCKFISH", "Salted / Dried Stockfish", "Marine", "#145374", (180.0, 730.0), (0.96, 1.06), (0.78, 0.86), 13.8, 0.44),
    ("MILK_WHEY_POWDER", "Whey & Skim Milk Powder", "Dairy", "#E67E22", (270.0, 730.0), (1.09, 1.18), (0.92, 0.96), 9.2, 0.36),
    ("FRESH_TOFU", "Fresh Tofu & Tempeh", "Plant", "#2D8A68", (5.0, 21.0), (0.82, 0.91), (0.18, 0.32), 7.4, 0.32),
    ("DRY_PULSES", "Dry Lentils & Beans", "Plant", "#1F6E58", (240.0, 680.0), (0.58, 0.72), (0.86, 0.91), 5.1, 0.34),
    ("SOY_FLOUR_TVP", "Soy Flour & TVP", "Plant", "#15523F", (300.0, 730.0), (0.84, 0.93), (0.90, 0.95), 4.4, 0.30),
    ("MILLED_CEREALS", "Milled Wheat & Rice", "Plant", "#52997A", (180.0, 540.0), (0.42, 0.56), (0.86, 0.90), 6.8, 0.38),
    ("MYCOPROTEIN", "Fermented Mycoprotein", "Microbial", "#6C3483", (180.0, 540.0), (0.91, 0.99), (0.88, 0.94), 8.8, 0.33),
    ("PRECISION_FERM", "Precision-Fermented Casein", "Microbial", "#4A235A", (365.0, 730.0), (1.05, 1.15), (0.93, 0.96), 10.4, 0.36),
    ("CULTIVATED_MEAT", "Cultivated Meat Biomass", "Cellular", "#9B59B6", (7.0, 28.0), (0.98, 1.08), (0.28, 0.35), 29.0, 0.48),
]

axis_map = {
    "shelf_life": (4, True),
    "diaas": (5, False),
    "dry_density": (6, False),
}

regime_curves = {"shelf_life": [], "diaas": [], "dry_density": []}

for axis_name, (tuple_idx, is_log) in axis_map.items():
    for r_id, label, cat, color, sh_r, di_r, dr_r, base_lab, curv in regimes:
        lo, hi = (sh_r if axis_name == "shelf_life" else (di_r if axis_name == "diaas" else dr_r))
        xs = np.geomspace(lo, hi, 15) if is_log else np.linspace(lo, hi, 15)
        u = np.linspace(-1.0, 1.0, 15)
        mult = 1.0 + curv * ((u + 0.25) ** 2)
        pts_lab = []
        pts_usd = []
        for xv, m in zip(xs, mult):
            xr = round(float(xv), 1 if is_log else 3)
            yl = round(float(base_lab * m), 2)
            yu = round(yl * 0.088, 3)
            pts_lab.append({"x": xr, "y": yl})
            pts_usd.append({"x": xr, "y": yu})
        min_idx = int(np.argmin([p["y"] for p in pts_lab]))
        regime_curves[axis_name].append({
            "id": r_id,
            "label": label,
            "category": cat,
            "color": color,
            "points_labor_min": pts_lab,
            "points_usd": pts_usd,
            "label_point_labor_min": pts_lab[min_idx],
            "label_point_usd": pts_usd[min_idx],
        })

era_multipliers = [
    ("1780s", "1780s Pre-Industrial Limit", 14.5, "#9E978E", True),
    ("1880s", "1880s Refrigeration & Steam Limit", 6.2, "#6E675F", True),
    ("1950s", "1950s Haber-Bosch & Feedlot Limit", 2.3, "#3D3833", True),
    ("2026s", "2026s Modern Pareto Frontier", 1.0, "#1A1918", False),
]

historical_limit_lines = {"shelf_life": [], "diaas": [], "dry_density": []}
for axis_name in ["shelf_life", "diaas", "dry_density"]:
    xg = (
        np.geomspace(1.5, 760.0, 32) if axis_name == "shelf_life"
        else (np.linspace(0.42, 1.20, 32) if axis_name == "diaas" else np.linspace(0.11, 0.96, 32))
    )
    for era_key, era_label, mult, col, dashed in era_multipliers:
        pts_lab, pts_usd = [], []
        for xval in xg:
            if axis_name == "shelf_life":
                nx = (np.log(xval) - np.log(1.5)) / (np.log(760.0) - np.log(1.5))
                xr = round(float(xval), 1)
                base_f = 3.8 * (0.85 + 0.55 * (nx ** 1.4))
            elif axis_name == "diaas":
                nx = (xval - 0.42) / (1.20 - 0.42)
                xr = round(float(xval), 3)
                base_f = 3.9 * (0.80 + 0.95 * (nx ** 1.5))
            else:
                nx = (xval - 0.11) / (0.96 - 0.11)
                xr = round(float(xval), 3)
                base_f = 3.8 * (0.85 + 0.45 * (nx ** 1.3))
            yl = round(float(base_f * mult), 2)
            pts_lab.append({"x": xr, "y": yl})
            pts_usd.append({"x": xr, "y": round(yl * 0.088, 3)})
        historical_limit_lines[axis_name].append({
            "era": era_key,
            "label": era_label,
            "color": col,
            "dashed": dashed,
            "points_labor_min": pts_lab,
            "points_usd": pts_usd,
        })

gvk["regime_curves"] = regime_curves
gvk["historical_limit_lines"] = historical_limit_lines
p_gvk.write_text(json.dumps(gvk, separators=(",", ":")), encoding="utf-8")
print(f"[UPDATED] {p_gvk} -> Added 15 flowing food-technology GvK curves & 4 historical Pareto limit lines (1780s-2026s).")
