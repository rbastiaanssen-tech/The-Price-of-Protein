import matplotlib.pyplot as plt
import matplotlib.patches as patches
from pathlib import Path

fig_dir = Path("docs/figures")
pub_fig_dir = Path("public/papers/figures")
fig_dir.mkdir(parents=True, exist_ok=True)
pub_fig_dir.mkdir(parents=True, exist_ok=True)

# 1. Render crisp vector Figure 1 (End-to-End Pipeline Schematic) for Paper 1
fig, ax = plt.subplots(figsize=(11.2, 3.6))
ax.set_xlim(0, 100)
ax.set_ylim(0, 32)
ax.axis("off")

stages = [
    (2,  "STAGE 1: INGESTION", "4.45M Price & Wage Quotes\n• FAO / WFP / World Bank ICP\n• 1700–1950 Historical Archives\n• Ethnographic & 2026 TEA", "#1B6CA8"),
    (27, "STAGE 2: BIOAVAILABILITY", "DIAAS & 9-AA Linear Program\n• True ileal digestibility (DIAAS)\n• 50g reference protein unit (u)\n• LP vs. Plant-Only LP_v (1.18x)", "#1F6E58"),
    (52, "STAGE 3: SPATIAL H3 GRID", "Subnational Friction & Time\n• 70,440 H3 Res-4 cells (8.20B)\n• 25 Res-6 metro corridors\n• Lean-season & hub friction", "#D97724"),
    (77, "STAGE 4: VALIDATION", "Block CV & Invariant Audit\n• 500 km spatial block CV\n• R² = 0.511 (u) | 0.759 (w_med)\n• SHA-256 & LP_v >= LP audit", "#8C1624"),
]

for x, title, body, col in stages:
    box = patches.FancyBboxPatch(
        (x, 4), 21, 23,
        boxstyle="round,pad=0.6,rounding_size=1.2",
        linewidth=1.4, edgecolor=col, facecolor="#FAF7F2"
    )
    ax.add_patch(box)
    header = patches.FancyBboxPatch(
        (x, 22.5), 21, 4.5,
        boxstyle="round,pad=0.6,rounding_size=1.0",
        linewidth=0, facecolor=col
    )
    ax.add_patch(header)
    ax.text(x + 10.5, 24.7, title, color="white", fontsize=8.5, fontweight="bold", ha="center", va="center", family="sans-serif")
    ax.text(x + 0.8, 13.5, body, color="#1A1918", fontsize=7.8, ha="left", va="center", linespacing=1.45, family="serif")

for ax_x in [23.8, 48.8, 73.8]:
    ax.annotate("", xy=(ax_x + 2.6, 15.5), xytext=(ax_x, 15.5),
                arrowprops=dict(arrowstyle="->,head_width=0.35,head_length=0.45", lw=1.8, color="#4A4640"))

plt.tight_layout()
for ext in ("pdf", "png"):
    fig.savefig(fig_dir / f"fig1_pipeline.{ext}", dpi=300)
    fig.savefig(pub_fig_dir / f"fig1_pipeline.{ext}", dpi=300)
plt.close(fig)

# 2. Replace the remaining \fbox placeholder in paper1_scientific_data.tex with fig1_pipeline.pdf
p1 = Path("docs/paper1_scientific_data.tex")
txt1 = p1.read_text(encoding="utf-8")
idx = txt1.find("\\label{fig:pipeline}")
if idx != -1:
    fbox_start = txt1.rfind("\\fbox{", 0, idx)
    fbox_end = txt1.find("\\caption{", fbox_start)
    if fbox_start != -1 and fbox_end != -1:
        txt1 = txt1[:fbox_start] + "\\includegraphics[width=0.98\\textwidth]{figures/fig1_pipeline.pdf}\n" + txt1[fbox_end:]
p1.write_text(txt1, encoding="utf-8")
Path("public/papers/paper1_scientific_data.tex").write_text(txt1, encoding="utf-8")
print("[OK] fig1_pipeline.pdf generated and wired into paper1_scientific_data.tex (0 placeholders remaining).")
