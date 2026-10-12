"""Inspect downloaded brand logos: size / mode / bytes."""
from pathlib import Path
from PIL import Image
import os

ROOT = Path(__file__).resolve().parent / "logos"
for d in sorted(ROOT.iterdir()):
    if not d.is_dir():
        continue
    for f in sorted(d.iterdir()):
        try:
            im = Image.open(f)
            print(f"{d.name}/{f.name}: {im.size} {im.mode} {os.path.getsize(f)//1024}KB")
        except Exception as e:  # noqa: BLE001
            print(f"{d.name}/{f.name}: ERROR {e}")