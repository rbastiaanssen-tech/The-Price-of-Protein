import subprocess, shutil
from pathlib import Path

root = Path.cwd()
if root.name == "docs":
    root = root.parent

docs = root / "docs"
pub = root / "public" / "papers"
pub.mkdir(parents=True, exist_ok=True)

if not (docs / "figures" / "fig1_wage_cliff.png").exists():
    subprocess.run(["python", str(root / "p70_generate_publication_figures.py")], cwd=root, check=True)
if not (docs / "figures" / "fig1_pipeline.png").exists():
    subprocess.run(["python", str(root / "p71_camera_ready_build.py")], cwd=root, check=True)
if not (docs / "SI_1.tex").exists():
    subprocess.run(["python", str(root / "p69_build_si_and_manifest.py")], cwd=root, check=True)

for name in ["paper1_scientific_data.tex", "paper2_nature_food.tex"]:
    p = docs / name
    p.write_text(p.read_text(encoding="utf-8").replace(".pdf}", ".png}"), encoding="utf-8")

for doc in ["paper1_scientific_data.tex", "paper2_nature_food.tex", "SI_1.tex"]:
    subprocess.run(["tectonic", "-Z", "continue-on-errors", doc], cwd=docs, check=False)
    pdf = docs / doc.replace(".tex", ".pdf")
    if pdf.exists():
        shutil.move(str(pdf), str(pub / pdf.name))
        print("[OK]", pub / pdf.name, f"({(pub / pdf.name).stat().st_size // 1024} KB)")
    else:
        print("[ERROR] Failed to compile", doc)
