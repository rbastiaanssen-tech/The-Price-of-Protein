import json
from pathlib import Path

p_gvk = Path("public/data/gvk_density_and_frontiers.json")
gvk = json.loads(p_gvk.read_text(encoding="utf-8"))

for dec_str, dec_obj in gvk.get("decades", {}).items():
    for axis_name in ["shelf_life", "diaas", "dry_density"]:
        bins = dec_obj.get("density_bins", {}).get(axis_name, [])
        frontiers = dec_obj.get("frontiers", {}).get(axis_name, {})
        if not bins or not isinstance(frontiers, dict):
            continue
        for f_key in list(frontiers.keys()):
            if "plant" in f_key.lower():
                sub = [b for b in bins if "PLANT" in b.get("regime", b.get("regime_class", ""))]
            elif "animal" in f_key.lower() or "meat" in f_key.lower():
                sub = [b for b in bins if "MEAT" in b.get("regime", b.get("regime_class", "")) or "TERRESTRIAL" in b.get("regime", b.get("regime_class", ""))]
            else:
                sub = list(bins)
            if not sub:
                continue
            # True GvK frontier: minimum cost required to achieve >= x (suffix minimum from right to left)
            sub_sorted = sorted(sub, key=lambda item: item["x"])
            n = len(sub_sorted)
            suffix_min = [0.0] * n
            cur_min = float("inf")
            for idx in range(n - 1, -1, -1):
                yv = sub_sorted[idx].get("y", sub_sorted[idx].get("labor_min_50g", 10.0))
                if yv < cur_min:
                    cur_min = yv
                suffix_min[idx] = round(cur_min, 2)
            frontiers[f_key] = [{"x": sub_sorted[i]["x"], "y": suffix_min[i]} for i in range(n)]

p_gvk.write_text(json.dumps(gvk, separators=(",", ":")), encoding="utf-8")
print("[FIXED] True upward-sloping GvK lower-envelope frontier (min cost for >= x) written to gvk_density_and_frontiers.json")
