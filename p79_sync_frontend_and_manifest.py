import hashlib, json, re, subprocess
from pathlib import Path

root = Path.cwd()
src = root / "src"

# 1. Inspect where metro_case_studies.json is imported/fetched in src/
hits = []
for f in sorted(src.rglob("*.ts*")):
    txt = f.read_text(encoding="utf-8")
    if "metro_case_studies" in txt or "25 cities" in txt or "25 metros" in txt or "25 metropolitan" in txt:
        hits.append(f)
        # Update static copy badges from 25 -> 90 metros / 959 real outlets
        new_txt = txt
        new_txt = re.sub(r"\b25\s+(global\s+)?cities\b", "90 global metros (959 real outlets)", new_txt, flags=re.I)
        new_txt = re.sub(r"\b25\s+metropolitan\s+corridors\b", "90 metropolitan corridors (959 real outlets)", new_txt, flags=re.I)
        new_txt = re.sub(r"\b25\s+metros\b", "90 metros (959 real outlets)", new_txt, flags=re.I)
        if new_txt != txt:
            f.write_text(new_txt, encoding="utf-8")
            print("[UPDATED FRONTEND COPY]", f.relative_to(root))

print("\n[FILES REFERENCING METRO DATA IN src/]:")
for h in hits:
    print(" -", h.relative_to(root))
    for i, line in enumerate(h.read_text(encoding="utf-8").splitlines(), 1):
        if any(k in line for k in ["metro_case_studies", "outlets", "metros", "case_studies"]):
            print(f"    L{i}: {line.strip()[:100]}")

# 2. Refresh SHA-256 checksums for all public/data/* and public/papers/*
manifest = {}
for folder in ["public/data", "public/papers"]:
    for fp in sorted((root / folder).glob("*")):
        if fp.is_file():
            h = hashlib.sha256(fp.read_bytes()).hexdigest()
            manifest[str(fp.relative_to(root))] = {"sha256": h, "bytes": fp.stat().st_size}

(root / "public" / "data" / "sha256_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(f"\n[SHA-256 MANIFEST UPDATED] {len(manifest)} files hashed.")

# 3. Run Vite / TypeScript build check
subprocess.run(["npm", "run", "build"], cwd=root, check=False)
