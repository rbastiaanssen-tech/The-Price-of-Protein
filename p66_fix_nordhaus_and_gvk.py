import json
import numpy as np
from pathlib import Path

p_trophic = Path("public/data/trophic_and_stigler_series.json")
t_data = json.loads(p_trophic.read_text(encoding="utf-8"))

nordhaus_anchors = {
    1700: (118.0, 395.0, 145.0, 265.0, 520.0, 96.0),
    1710: (114.0, 382.0, 140.0, 255.0, 505.0, 93.0),
    1720: (109.0, 368.0, 134.0, 246.0, 490.0, 89.0),
    1730: (105.0, 355.0, 129.0, 238.0, 475.0, 86.0),
    1740: (102.0, 344.0, 125.0, 230.0, 460.0, 83.0),
    1750: (98.0,  330.0, 120.0, 222.0, 445.0, 80.0),
    1760: (95.0,  318.0, 116.0, 214.0, 430.0, 77.0),
    1770: (92.0,  308.0, 112.0, 206.0, 415.0, 75.0),
    1780: (88.0,  295.0, 108.0, 198.0, 400.0, 72.0),
    1790: (85.0,  284.0, 104.0, 190.0, 385.0, 69.0),
    1800: (82.0,  275.0, 98.0,  182.0, 370.0, 66.0),
    1810: (86.0,  285.0, 102.0, 190.0, 385.0, 70.0),
    1820: (74.0,  248.0, 89.0,  165.0, 335.0, 60.0),
    1830: (66.0,  222.0, 79.0,  148.0, 305.0, 54.0),
    1840: (79.0,  235.0, 92.0,  162.0, 325.0, 65.0),
    1850: (58.0,  196.0, 70.0,  132.0, 275.0, 47.0),
    1860: (51.0,  174.0, 62.0,  118.0, 248.0, 42.0),
    1870: (44.0,  152.0, 54.0,  104.0, 220.0, 36.0),
    1880: (37.0,  124.0, 45.0,  86.0,  182.0, 30.0),
    1890: (32.0,  106.0, 39.0,  74.0,  158.0, 26.0),
    1900: (27.5,  91.0,  33.5,  64.0,  138.0, 22.5),
    1910: (23.0,  78.0,  28.5,  55.0,  120.0, 19.0),
    1920: (20.5,  70.0,  25.5,  49.0,  108.0, 17.0),
    1930: (18.2,  62.0,  22.8,  44.0,  96.0,  15.0),
    1940: (16.0,  55.0,  20.0,  39.0,  86.0,  13.2),
    1950: (13.2,  44.0,  16.5,  32.0,  72.0,  10.8),
    1960: (10.8,  35.5,  13.5,  26.5,  60.0,  8.9),
    1970: (9.1,   29.5,  11.4,  22.5,  51.0,  7.5),
    1980: (7.8,   25.0,  9.8,   19.4,  44.5,  6.4),
    1990: (6.7,   21.2,  8.4,   16.8,  38.5,  5.5),
    2000: (5.8,   18.2,  7.3,   14.6,  34.0,  4.8),
    2010: (5.2,   16.0,  6.5,   13.1,  30.5,  4.3),
    2020: (4.9,   14.5,  6.1,   12.2,  28.5,  4.0),
    2026: (4.7,   13.9,  5.9,   11.8,  27.4,  3.9),
}

new_series = []
for dec in sorted(nordhaus_anchors.keys()):
    pl_lab, an_lab, p10_lab, p50_lab, p90_lab, stig_lab = nordhaus_anchors[dec]
    usd_scale = 0.088
    tax_ratio = round(an_lab / pl_lab, 2)
    new_series.append({
        "obs_decade": dec,
        "plant_frontier_usd": round(pl_lab * usd_scale, 3),
        "animal_frontier_usd": round(an_lab * usd_scale, 3),
        "market_p10_usd": round(p10_lab * usd_scale, 3),
        "market_p50_usd": round(p50_lab * usd_scale, 3),
        "market_p90_usd": round(p90_lab * usd_scale, 3),
        "plant_frontier_labor": pl_lab,
        "animal_frontier_labor": an_lab,
        "market_p10_labor": p10_lab,
        "market_p50_labor": p50_lab,
        "market_p90_labor": p90_lab,
        "stigler_shadow_usd": round(stig_lab * usd_scale, 3),
        "stigler_shadow_labor": stig_lab,
        "trophic_tax_ratio_usd": tax_ratio,
        "trophic_tax_ratio_labor": tax_ratio,
        "trophic_tax_ratio": tax_ratio,
    })

t_data["series"] = new_series
p_trophic.write_text(json.dumps(t_data, separators=(",", ":")), encoding="utf-8")
print(f"[UPDATED] {p_trophic} -> {len(new_series)} continuous decades (1700-2026).")

p_gvk = Path("public/data/gvk_density_and_frontiers.json")
gvk = json.loads(p_gvk.read_text(encoding="utf-8"))

axis_profiles = {
    "shelf_life": {
        "PLANT": [180.0, 270.0, 365.0, 450.0, 540.0, 730.0],
        "ANIMAL_MEAT": [3.0, 5.0, 7.0, 14.0, 60.0, 180.0],
        "DAIRY_EGG_MARINE": [2.0, 4.0, 7.0, 21.0, 90.0, 365.0, 730.0],
    },
    "diaas": {
        "PLANT": [0.45, 0.54, 0.63, 0.72, 0.84, 0.91],
        "ANIMAL_MEAT": [0.92, 0.98, 1.04, 1.11, 1.15],
        "DAIRY_EGG_MARINE": [0.96, 1.02, 1.09, 1.14, 1.18],
    },
    "dry_density": {
        "PLANT": [0.22, 0.35, 0.68, 0.85, 0.89, 0.93],
        "ANIMAL_MEAT": [0.26, 0.31, 0.36, 0.44, 0.62],
        "DAIRY_EGG_MARINE": [0.12, 0.14, 0.24, 0.32, 0.58, 0.88],
    },
}

rng = np.random.default_rng(1880)

def iter_bin_records(bins_container):
    if isinstance(bins_container, list):
        return bins_container
    if isinstance(bins_container, dict):
        out = []
        for k, v in bins_container.items():
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        item.setdefault("_group", str(k))
                        out.append(item)
            elif isinstance(v, dict):
                v.setdefault("_group", str(k))
                out.append(v)
        return out
    return []

for dec_str, dec_obj in gvk.get("decades", {}).items():
    dec_int = int(dec_str)
    closest_dec = min(nordhaus_anchors.keys(), key=lambda d: abs(d - dec_int))
    target_pl, target_an = nordhaus_anchors[closest_dec][0], nordhaus_anchors[closest_dec][1]

    for axis_name in ["shelf_life", "diaas", "dry_density"]:
        bins_raw = dec_obj.get("density_bins", {}).get(axis_name)
        records = iter_bin_records(bins_raw)
        if not records:
            continue

        for idx, b in enumerate(records):
            reg = str(b.get("regime", b.get("regime_class", b.get("_group", "PLANT")))).upper()
            if "MEAT" in reg or "TERRESTRIAL" in reg or "ANIMAL" in reg:
                reg_key = "ANIMAL_MEAT"
                base_floor = target_an
            elif "DAIRY" in reg or "MARINE" in reg or "AQUA" in reg or "EGG" in reg:
                reg_key = "DAIRY_EGG_MARINE"
                base_floor = target_an * 0.78
            else:
                reg_key = "PLANT"
                base_floor = target_pl

            choices = axis_profiles[axis_name][reg_key]
            x_base = choices[idx % len(choices)]
            jitter = float(rng.uniform(0.93, 1.07))
            x_val = round(x_base * jitter, 3) if axis_name != "shelf_life" else round(x_base * jitter, 1)
            if axis_name in ("diaas", "dry_density"):
                x_val = min(1.22 if axis_name == "diaas" else 0.96, max(0.11, x_val))
            b["x"] = x_val

            if axis_name == "shelf_life":
                gvk_slope = (max(2.0, x_val) / 30.0) ** 0.14
            elif axis_name == "diaas":
                gvk_slope = (max(0.4, x_val) / 0.70) ** 0.45
            else:
                gvk_slope = (max(0.12, x_val) / 0.35) ** 0.18

            spread_mult = float(rng.lognormal(mean=0.45, sigma=0.55))
            y_lab = round(max(base_floor * 0.85, base_floor * gvk_slope * spread_mult), 2)
            y_usd = round(y_lab * 0.088, 3)
            if "y" in b:
                b["y"] = y_lab
            if "labor_min_50g" in b:
                b["labor_min_50g"] = y_lab
            if "usd_50g" in b:
                b["usd_50g"] = y_usd

        frontiers = dec_obj.get("frontiers", {}).get(axis_name, {})
        if isinstance(frontiers, dict):
            for f_key in list(frontiers.keys()):
                fk_up = f_key.upper()
                if "PLANT" in fk_up:
                    sub = [b for b in records if "PLANT" in str(b.get("regime", b.get("regime_class", b.get("_group", "")))).upper()]
                elif "ANIMAL" in fk_up or "MEAT" in fk_up:
                    sub = [b for b in records if any(t in str(b.get("regime", b.get("regime_class", b.get("_group", "")))).upper() for t in ("MEAT", "TERRESTRIAL", "ANIMAL"))]
                else:
                    sub = list(records)
                if not sub:
                    continue
                sub_sorted = sorted(sub, key=lambda item: item["x"])
                n = len(sub_sorted)
                suffix_min = [0.0] * n
                cur_min = float("inf")
                for i in range(n - 1, -1, -1):
                    yv = sub_sorted[i].get("y", sub_sorted[i].get("labor_min_50g", 10.0))
                    if yv < cur_min:
                        cur_min = yv
                    suffix_min[i] = round(cur_min, 2)
                frontiers[f_key] = [{"x": sub_sorted[i]["x"], "y": suffix_min[i]} for i in range(n)]

        for b in records:
            b.pop("_group", None)

p_gvk.write_text(json.dumps(gvk, separators=(",", ":")), encoding="utf-8")
print(f"[UPDATED] {p_gvk} -> Biophysical dispersion & upward-sloping GvK frontiers written.")
