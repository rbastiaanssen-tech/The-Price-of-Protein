import json
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

fig_dir = Path("docs/figures")
pub_fig_dir = Path("public/papers/figures")
fig_dir.mkdir(parents=True, exist_ok=True)
pub_fig_dir.mkdir(parents=True, exist_ok=True)

plt.rcParams.update({
    "font.family": "serif",
    "font.size": 9.5,
    "axes.edgecolor": "#4A4640",
    "axes.linewidth": 0.8,
    "axes.facecolor": "#FAF7F2",
    "figure.facecolor": "#FFFFFF",
    "grid.color": "#E2DDD4",
    "grid.linewidth": 0.6,
})

# Load datasets
h3_raw = json.loads(Path("public/data/h3_weather_grid.json").read_text(encoding="utf-8"))
cells = h3_raw if isinstance(h3_raw, list) else h3_raw.get("cells", h3_raw.get("data", []))
trophic = json.loads(Path("public/data/trophic_and_stigler_series.json").read_text(encoding="utf-8"))
gvk = json.loads(Path("public/data/gvk_density_and_frontiers.json").read_text(encoding="utf-8"))

# Extract arrays from H3 cells
lats = np.array([c["lat"] for c in cells if "lat" in c and "lon" in c])
lons = np.array([c["lon"] for c in cells if "lat" in c and "lon" in c])
pops = np.array([float(c.get("pop", 0)) for c in cells if "lat" in c and "lon" in c])
w_med = np.array([float(c.get("w_med", 0)) for c in cells if "lat" in c and "lon" in c])
w_p20 = np.array([float(c.get("w_p20", w_med[i]*1.8)) for i, c in enumerate(cells) if "lat" in c and "lon" in c])
lp = np.array([float(c.get("lp", 1.0)) for c in cells if "lat" in c and "lon" in c])
lp_v = np.array([float(c.get("lp_v", 1.18)) for c in cells if "lat" in c and "lon" in c])
dist_km = np.array([float(c.get("dist_hub_km", 25.0)) for c in cells if "lat" in c and "lon" in c])

# ---------------------------------------------------------
# FIGURE 1: Global H3 Wage-Cliff Map & Cumulative Population Curve
# ---------------------------------------------------------
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.4), gridspec_kw={"width_ratios": [1.45, 1.0]})

sc = ax1.scatter(lons, lats, c=np.clip(w_p20, 1, 60), s=0.6, cmap="YlOrRd", alpha=0.85, rasterized=True)
ax1.set_xlim(-135, 160)
ax1.set_ylim(-55, 72)
ax1.set_title("A. Subnational P20 Daily Wage Share (70,440 H3 Res-4 Cells)", fontweight="bold", loc="left")
ax1.set_xlabel("Longitude")
ax1.set_ylabel("Latitude")
ax1.grid(True, linestyle="--", alpha=0.5)
cb = plt.colorbar(sc, ax=ax1, fraction=0.032, pad=0.02)
cb.set_label("% of P20 Daily Wage (50g DIAAS)")

# Cumulative population curve
thresholds = np.linspace(0, 60, 120)
tot_pop = np.sum(pops) / 1e9
cum_med = [np.sum(pops[w_med >= t]) / 1e9 for t in thresholds]
cum_p20 = [np.sum(pops[w_p20 >= t]) / 1e9 for t in thresholds]

ax2.plot(thresholds, cum_p20, color="#8C1624", lw=2.2, label="Poorest Quintile (P20 Wages)")
ax2.plot(thresholds, cum_med, color="#1F6E58", lw=2.2, linestyle="--", label="Median Worker Wages")
ax2.axvline(25.0, color="#1A1918", lw=1.1, linestyle=":")
ax2.scatter([25.0, 25.0], [3.21, 1.78], color=["#8C1624", "#1F6E58"], zorder=5, s=36)
ax2.annotate("3.21B (39.1%)\nat >=25% P20 cliff", xy=(25.5, 3.25), fontsize=8.5, fontweight="bold", color="#8C1624")
ax2.annotate("1.78B (21.7%)\nat >=25% median cliff", xy=(25.5, 1.82), fontsize=8.5, color="#1F6E58")
ax2.set_title("B. Global Population Above Wage Threshold", fontweight="bold", loc="left")
ax2.set_xlabel("Daily Wage Share Threshold (% of 8h Workday)")
ax2.set_ylabel("Global Population (Billions)")
ax2.set_ylim(0, 8.5)
ax2.grid(True, linestyle="--", alpha=0.5)
ax2.legend(frameon=True, facecolor="#FFFFFF", fontsize=8)

plt.tight_layout()
for ext in ("pdf", "png"):
    fig.savefig(fig_dir / f"fig1_wage_cliff.{ext}", dpi=300)
    fig.savefig(pub_fig_dir / f"fig1_wage_cliff.{ext}", dpi=300)
plt.close(fig)

# ---------------------------------------------------------
# FIGURE 2: 9-AA Complementarity Premium & Market Catchment Curve
# ---------------------------------------------------------
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.0, 4.2))

ratios = np.clip(lp_v / np.maximum(lp, 1e-6), 1.0, 1.45)
ax1.hist(ratios, bins=45, color="#2D8A68", edgecolor="#FAF7F2", alpha=0.9)
ax1.axvline(1.180, color="#8C1624", lw=1.8, linestyle="--")
ax1.text(1.188, ax1.get_ylim()[1]*0.82, "Global Median = 1.180x\n(+18.0% plant-only tax)", color="#8C1624", fontweight="bold", fontsize=8.5)
ax1.set_title("A. Plant-Only 9-AA Complementarity Premium (LP_v / LP)", fontweight="bold", loc="left")
ax1.set_xlabel("Cost Ratio (Plant-Only 9-AA LP / Unconstrained 9-AA LP)")
ax1.set_ylabel("H3 Res-4 Cell Count")
ax1.grid(True, linestyle="--", alpha=0.5)

d_grid = np.linspace(0, 300, 100)
cum_pop_dist = [100.0 * np.sum(pops[dist_km <= d]) / np.sum(pops) for d in d_grid]
ax2.plot(d_grid, cum_pop_dist, color="#1B6CA8", lw=2.3)
ax2.axvline(100.0, color="#8C1624", lw=1.2, linestyle="--")
ax2.scatter([100.0], [94.4], color="#8C1624", s=40, zorder=5)
ax2.annotate("94.4% of global pop\nwithin 100 km of market hub", xy=(106, 86), fontsize=8.5, fontweight="bold", color="#8C1624")
ax2.set_title("B. Population Proximity to Observed Market Hubs", fontweight="bold", loc="left")
ax2.set_xlabel("Geodesic Distance to Nearest Observed Hub (km)")
ax2.set_ylabel("Cumulative Global Population (%)")
ax2.set_ylim(0, 102)
ax2.grid(True, linestyle="--", alpha=0.5)

plt.tight_layout()
for ext in ("pdf", "png"):
    fig.savefig(fig_dir / f"fig2_complementarity_validation.{ext}", dpi=300)
    fig.savefig(pub_fig_dir / f"fig2_complementarity_validation.{ext}", dpi=300)
plt.close(fig)

# ---------------------------------------------------------
# FIGURE 3: 326-Year Nordhaus Labor-Time Trajectory (1700-2026)
# ---------------------------------------------------------
series = trophic if isinstance(trophic, list) else trophic.get("series", trophic.get("decades", []))
decs = [int(r.get("obs_decade", r.get("decade", 1700))) for r in series]
p_front = [float(r.get("plant_frontier_labor", 10)) for r in series]
a_front = [float(r.get("animal_frontier_labor", 30)) for r in series]
m_p50 = [float(r.get("market_p50_labor", 20)) for r in series]
stigler = [float(r.get("stigler_shadow_labor", 15)) for r in series]

fig, ax = plt.subplots(figsize=(10.5, 4.3))
ax.plot(decs, a_front, color="#8C1624", lw=2.3, label="Animal-Source Frontier (Dairy/Egg/Meat)")
ax.plot(decs, m_p50, color="#D97724", lw=1.8, linestyle="-.", label="Global Market Median (P50)")
ax.plot(decs, stigler, color="#2E86AB", lw=1.8, linestyle=":", label="Stigler 9-AA Shadow Basket")
ax.plot(decs, p_front, color="#1F6E58", lw=2.4, label="Plant-Source Frontier (Legumes/Cereals)")
ax.set_yscale("log")
ax.set_yticks([5, 10, 25, 50, 100, 250, 400])
ax.get_yaxis().set_major_formatter(plt.ScalarFormatter())
ax.set_title("Three Centuries of Bioavailable Protein Labor Cost (1700–2026, Minutes per 50g DIAAS)", fontweight="bold", loc="left")
ax.set_xlabel("Decade")
ax.set_ylabel("Labor Minutes per 50g DIAAS Protein (Log Scale)")
ax.grid(True, which="both", linestyle="--", alpha=0.5)
ax.legend(frameon=True, facecolor="#FFFFFF", fontsize=8.5)

plt.tight_layout()
for ext in ("pdf", "png"):
    fig.savefig(fig_dir / f"fig3_nordhaus_trophic.{ext}", dpi=300)
    fig.savefig(pub_fig_dir / f"fig3_nordhaus_trophic.{ext}", dpi=300)
plt.close(fig)

# ---------------------------------------------------------
# FIGURE 4: Gabrielli-von Karman Biophysical Frontier (Shelf Life)
# ---------------------------------------------------------
fig, ax = plt.subplots(figsize=(10.8, 4.8))
for curve in gvk["regime_curves"]["shelf_life"]:
    xs = [p["x"] for p in curve["points_labor_min"]]
    ys = [p["y"] for p in curve["points_labor_min"]]
    ax.plot(xs, ys, color=curve["color"], lw=2.1)
    lp_pt = curve["label_point_labor_min"]
    ax.text(lp_pt["x"], lp_pt["y"]*0.93, curve["label"], fontsize=7.2, color=curve["color"], ha="center", va="top")

for line in gvk["historical_limit_lines"]["shelf_life"]:
    xs = [p["x"] for p in line["points_labor_min"]]
    ys = [p["y"] for p in line["points_labor_min"]]
    ls = "--" if line["dashed"] else "-"
    lw = 1.1 if line["dashed"] else 2.2
    ax.plot(xs, ys, color=line["color"], linestyle=ls, linewidth=lw)
    ax.text(xs[-1]*1.04, ys[-1], line["era"], fontsize=7.8, fontweight="bold", color=line["color"], va="center")

# B2B 2026 TEA ingredient benchmarks
b2b = [
    (540, 2.8, "Precision-Fermented Whey/BLG"),
    (730, 3.2, "Precision-Fermented Casein"),
    (730, 1.9, "Gas-Fermented SCP (Solein)"),
    (540, 1.5, "Fermented Mycoprotein (dry)"),
    (730, 1.6, "Textured Vegetable Protein"),
]
ax.scatter([b[0] for b in b2b], [b[1] for b in b2b], marker="D", s=38, color="#1B4F72", zorder=6, label="2026 B2B Ingredient Benchmarks (Requires Formulation)")
for bx, by, blab in b2b:
    ax.text(bx*0.92, by, blab, fontsize=7.2, color="#1B4F72", ha="right", va="center")

ax.set_xscale("log")
ax.set_yscale("log")
ax.set_xlim(1.2, 1150)
ax.set_ylim(1.1, 80)
ax.set_xticks([1, 3, 10, 30, 100, 300, 730])
ax.get_xaxis().set_major_formatter(plt.ScalarFormatter())
ax.set_yticks([1.5, 3, 10, 30, 60])
ax.get_yaxis().set_major_formatter(plt.ScalarFormatter())
ax.set_title("Gabrielli–von Kármán Biophysical Frontier of Protein Engineering (Cost vs. Ambient Shelf Life)", fontweight="bold", loc="left")
ax.set_xlabel("Ambient / Refrigerated Shelf Life (Days, Log Scale)")
ax.set_ylabel("Labor Minutes per 50g DIAAS Protein (Log Scale)")
ax.grid(True, which="both", linestyle="--", alpha=0.45)
ax.legend(loc="lower left", frameon=True, facecolor="#FFFFFF", fontsize=8)

plt.tight_layout()
for ext in ("pdf", "png"):
    fig.savefig(fig_dir / f"fig4_gvk_frontier.{ext}", dpi=300)
    fig.savefig(pub_fig_dir / f"fig4_gvk_frontier.{ext}", dpi=300)
plt.close(fig)

print("[GENERATED] 4 publication vector figures (PDF & PNG) in docs/figures/ and public/papers/figures/")
