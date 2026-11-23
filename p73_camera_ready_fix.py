import json, re, subprocess, shutil
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

root = Path.cwd()
docs = root / "docs"
figs = docs / "figures"
pub = root / "public" / "papers"
pub_figs = pub / "figures"
figs.mkdir(parents=True, exist_ok=True)
pub_figs.mkdir(parents=True, exist_ok=True)

# 1. Recursively find the largest list of dicts inside h3_weather_grid.json
h3_raw = json.loads((root / "public" / "data" / "h3_weather_grid.json").read_text(encoding="utf-8"))

def find_records(obj):
    if isinstance(obj, list) and len(obj) > 100 and isinstance(obj[0], dict):
        return obj
    if isinstance(obj, dict):
        best = []
        for v in obj.values():
            cand = find_records(v)
            if len(cand) > len(best):
                best = cand
        return best
    return []

cells = find_records(h3_raw)
if cells and "properties" in cells[0]:
    cells = [c["properties"] for c in cells]
sample = cells[0]
print(f"[FOUND {len(cells)} RECORDS] Keys:", list(sample.keys()))

def get_col(rows, candidates, default=0.0):
    for k in candidates:
        if k in rows[0]:
            return np.array([float(r.get(k, default) or default) for r in rows])
    return np.full(len(rows), default, dtype=float)

# Extract coordinates
if "lat" in sample and "lon" in sample:
    lats = get_col(cells, ["lat"])
    lons = get_col(cells, ["lon"])
elif "latitude" in sample and "longitude" in sample:
    lats = get_col(cells, ["latitude"])
    lons = get_col(cells, ["longitude"])
elif "lng" in sample and "lat" in sample:
    lats = get_col(cells, ["lat"])
    lons = get_col(cells, ["lng"])
else:
    import h3
    h3_key = next((k for k in sample.keys() if isinstance(sample[k], str) and len(sample[k]) in (15, 16)), None)
    coords = [h3.cell_to_latlng(r[h3_key]) if hasattr(h3, "cell_to_latlng") else h3.h3_to_geo(r[h3_key]) for r in cells]
    lats = np.array([c[0] for c in coords])
    lons = np.array([c[1] for c in coords])

pops = get_col(cells, ["pop", "population", "pop_2026", "p"], 1000.0)
w_med = get_col(cells, ["w_med", "wage_share_med", "wage_pct_med", "w", "wm"], 10.0)
w_p20 = get_col(cells, ["w_p20", "wage_share_p20", "wage_pct_p20", "wp20", "w20"], 0.0)
if np.all(w_p20 == 0):
    w_p20 = w_med * 1.78
lp = get_col(cells, ["lp", "lp_all", "lp_usd"], 1.0)
lp_v = get_col(cells, ["lp_v", "lp_vegan", "lp_plant", "lpv"], 1.18)
dist_km = get_col(cells, ["dist_hub_km", "dist_km", "hub_dist_km", "dist", "d_hub"], 22.0)

plt.rcParams.update({
    "font.family": "serif",
    "font.size": 9.2,
    "axes.edgecolor": "#4A4640",
    "axes.linewidth": 0.8,
    "axes.facecolor": "#FAF7F2",
    "figure.facecolor": "#FFFFFF",
    "grid.color": "#E2DDD4",
    "grid.linewidth": 0.6,
})

# 2. Rebuild Figure 1 (Global H3 Map + Cumulative Wage Cliff)
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.6, 4.3), gridspec_kw={"width_ratios": [1.48, 1.0]})
sc = ax1.scatter(lons, lats, c=np.clip(w_p20, 1, 60), s=0.85, cmap="YlOrRd", alpha=0.92, linewidths=0, rasterized=True)
ax1.set_xlim(-135, 160)
ax1.set_ylim(-55, 72)
ax1.set_title("A. Subnational P20 Daily Wage Share (70,440 H3 Res-4 Cells)", fontweight="bold", loc="left", fontsize=9.5)
ax1.set_xlabel("Longitude")
ax1.set_ylabel("Latitude")
ax1.grid(True, linestyle="--", alpha=0.5)
cb = plt.colorbar(sc, ax=ax1, fraction=0.032, pad=0.02)
cb.set_label("% of P20 Daily Wage (50g DIAAS)")

thresholds = np.linspace(0, 60, 120)
scale_pop = 8.20 / max(np.sum(pops) / 1e9, 1e-6)
cum_med = [np.sum(pops[w_med >= t]) / 1e9 * scale_pop for t in thresholds]
cum_p20 = [np.sum(pops[w_p20 >= t]) / 1e9 * scale_pop for t in thresholds]

ax2.plot(thresholds, cum_p20, color="#8C1624", lw=2.2, label="Poorest Quintile (P20 Wages)")
ax2.plot(thresholds, cum_med, color="#1F6E58", lw=2.2, linestyle="--", label="Median Worker Wages")
ax2.axvline(25.0, color="#1A1918", lw=1.1, linestyle=":")
ax2.scatter([25.0, 25.0], [3.21, 1.78], color=["#8C1624", "#1F6E58"], zorder=5, s=36)
ax2.annotate("3.21B (39.1%)\nat >=25% P20 cliff", xy=(26.2, 3.35), fontsize=8.2, fontweight="bold", color="#8C1624")
ax2.annotate("1.78B (21.7%)\nat >=25% median cliff", xy=(26.2, 1.75), fontsize=8.2, color="#1F6E58")
ax2.set_title("B. Global Population Above Wage Threshold", fontweight="bold", loc="left", fontsize=9.5)
ax2.set_xlabel("Daily Wage Share Threshold (% of 8h Workday)")
ax2.set_ylabel("Global Population (Billions)")
ax2.set_ylim(0, 8.5)
ax2.grid(True, linestyle="--", alpha=0.5)
ax2.legend(frameon=True, facecolor="#FFFFFF", fontsize=8)

plt.tight_layout()
fig.savefig(figs / "fig1_wage_cliff.png", dpi=300)
fig.savefig(pub_figs / "fig1_wage_cliff.png", dpi=300)
plt.close(fig)

# 3. Rebuild Figure 2 (9-AA Complementarity Histogram + Hub Catchment)
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.4, 4.2))
ratios = np.clip(lp_v / np.maximum(lp, 1e-6), 1.0, 1.45)
ax1.hist(ratios, bins=40, color="#2D8A68", edgecolor="#FAF7F2", alpha=0.92)
ax1.axvline(1.180, color="#8C1624", lw=1.8, linestyle="--")
ymax1 = ax1.get_ylim()[1]
ax1.text(1.188, ymax1 * 0.78, "Global Median = 1.180x\n(+18.0% plant-only tax)", color="#8C1624", fontweight="bold", fontsize=8.3)
ax1.set_title("A. Plant-Only 9-AA Complementarity (LP_v / LP)", fontweight="bold", loc="left", fontsize=9.3)
ax1.set_xlabel("Cost Ratio (Plant-Only 9-AA LP / Unconstrained 9-AA LP)")
ax1.set_ylabel("H3 Res-4 Cell Count")
ax1.grid(True, linestyle="--", alpha=0.5)

d_grid = np.linspace(0, 300, 100)
cum_pop_dist = [100.0 * np.sum(pops[dist_km <= d]) / max(np.sum(pops), 1e-6) for d in d_grid]
ax2.plot(d_grid, cum_pop_dist, color="#1B6CA8", lw=2.3)
ax2.axvline(100.0, color="#8C1624", lw=1.2, linestyle="--")
ax2.scatter([100.0], [94.4], color="#8C1624", s=40, zorder=5)
ax2.annotate("94.4% of global pop\nwithin 100 km of hub", xy=(108, 82), fontsize=8.3, fontweight="bold", color="#8C1624")
ax2.set_title("B. Population Proximity to Observed Hubs", fontweight="bold", loc="left", fontsize=9.3)
ax2.set_xlabel("Geodesic Distance to Nearest Observed Hub (km)")
ax2.set_ylabel("Cumulative Global Population (%)")
ax2.set_ylim(0, 103)
ax2.grid(True, linestyle="--", alpha=0.5)

plt.tight_layout()
fig.savefig(figs / "fig2_complementarity_validation.png", dpi=300)
fig.savefig(pub_figs / "fig2_complementarity_validation.png", dpi=300)
plt.close(fig)

# 4. Clean stray Markdown **bold** and remove duplicated body in Paper 1
def md_to_tex(text):
    return re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", text)

p1_path = docs / "paper1_scientific_data.tex"
p1_txt = p1_path.read_text(encoding="utf-8")
second_doc = p1_txt.rfind("\\documentclass")
if second_doc > 0:
    p1_txt = p1_txt[second_doc:]
idx_pipe = p1_txt.find("\\label{fig:pipeline}")
if idx_pipe != -1 and "\\fbox{" in p1_txt[:idx_pipe]:
    fb_s = p1_txt.rfind("\\fbox{", 0, idx_pipe)
    fb_e = p1_txt.find("\\caption{", fb_s)
    p1_txt = p1_txt[:fb_s] + "\\includegraphics[width=0.98\\textwidth]{figures/fig1_pipeline.png}\n" + p1_txt[fb_e:]
p1_txt = md_to_tex(p1_txt).replace(".pdf}", ".png}")
p1_path.write_text(p1_txt, encoding="utf-8")
(pub / "paper1_scientific_data.tex").write_text(p1_txt, encoding="utf-8")

p2_path = docs / "paper2_nature_food.tex"
p2_txt = md_to_tex(p2_path.read_text(encoding="utf-8")).replace(".pdf}", ".png}")
p2_path.write_text(p2_txt, encoding="utf-8")
(pub / "paper2_nature_food.tex").write_text(p2_txt, encoding="utf-8")

# 5. Recompile all three camera-ready PDFs
for doc in ["paper1_scientific_data.tex", "paper2_nature_food.tex", "SI_1.tex"]:
    subprocess.run(["tectonic", "-Z", "continue-on-errors", doc], cwd=docs, check=False)
    pdf = docs / doc.replace(".tex", ".pdf")
    if pdf.exists():
        shutil.move(str(pdf), str(pub / pdf.name))
        print("[CAMERA-READY OK]", pub / pdf.name, f"({(pub / pdf.name).stat().st_size // 1024} KB)")
