"""PhishLens Edge - download PhishIntention weights (T16).

Downloads the 7 files the L1/L2 funnel needs from hf-mirror.com into
third_party/PhishIntention/models/, then unzips expand_targetlist.zip.
Existing files are skipped (safe to re-run); use --force to re-download.

Provenance (verified byte-for-byte against the local working copy on
2026-09-29; sizes match exactly):

  local name               remote file (hf-mirror.com)                    bytes
  -----------------------  --------------------------------------------  ----------
  layout_detector.pth      code-philia/PhishIntention/                   330145096
                           AWL_detector_utils/output/website_lr0.001/model_final.pth
  crp_locator.pth          code-philia/PhishIntention/                   329981096
                           crp_locator_utils/login_finder/output/lr0.001_finetune/model_final.pth
  crp_classifier.pth.tar   code-philia/PhishIntention/                   188221125
                           crp_classifier_utils/output/Increase_resolution_lr0.005/BiT-M-R50x1V2_0.005.pth.tar
  ocr_siamese.pth.tar      code-philia/PhishIntention/                   193786609
                           OCR_siamese_utils/output/targetlist_lr0.01/bit.pth.tar
  ocr_pretrained.pth.tar   code-philia/PhishIntention/                    84424941
                           OCR_siamese_utils/demo_downgrade.pth.tar
  domain_map.pkl           code-philia/PhishIntention/                      220978
                           phishpedia_siamese/domain_map.pkl
  expand_targetlist.zip    code-philia/Phishpedia/                        212261039
                           siamese_pedia/expand_targetlist.zip
                           (Phishpedia version is used, see docs/T3)

The first 5 files sit under different paths in the HF repo than their
target filenames; this script maps them to the names configs/configs.yaml
expects (models/... relative to the PhishIntention checkout root).

Micro-difference: the working copy's domain_map.pkl carries 3 extra entries
(Coinbase, Ledger, Xfinity) added for the testset; the script skips existing
files, so a fresh checkout gets the pristine 5600-entry version, which is
functionally identical for all funnel-relevant brands.

Usage (from the repo root):
    .venv/Scripts/python tools/download_weights.py [--force]

Requires internet. ~1.6 GB total.
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODELS = REPO / "third_party" / "PhishIntention" / "models"

MIRRORS = (
    "https://hf-mirror.com",
    "https://huggingface.co",
)

# (target_filename, remote_repo, remote_path)
FILES = [
    ("layout_detector.pth",
     "code-philia/PhishIntention",
     "AWL_detector_utils/output/website_lr0.001/model_final.pth"),
    ("crp_locator.pth",
     "code-philia/PhishIntention",
     "crp_locator_utils/login_finder/output/lr0.001_finetune/model_final.pth"),
    ("crp_classifier.pth.tar",
     "code-philia/PhishIntention",
     "crp_classifier_utils/output/Increase_resolution_lr0.005/BiT-M-R50x1V2_0.005.pth.tar"),
    ("ocr_siamese.pth.tar",
     "code-philia/PhishIntention",
     "OCR_siamese_utils/output/targetlist_lr0.01/bit.pth.tar"),
    ("ocr_pretrained.pth.tar",
     "code-philia/PhishIntention",
     "OCR_siamese_utils/demo_downgrade.pth.tar"),
    ("domain_map.pkl",
     "code-philia/PhishIntention",
     "phishpedia_siamese/domain_map.pkl"),
    ("expand_targetlist.zip",
     "code-philia/Phishpedia",
     "siamese_pedia/expand_targetlist.zip"),
]


def fetch(url: str, dest: Path) -> None:
    print(f"downloading {os.path.basename(url)} -> {dest.name} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "PhishLensEdge/setup"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-download existing files")
    args = ap.parse_args()

    MODELS.mkdir(parents=True, exist_ok=True)
    errors = 0
    for name, repo, rpath in FILES:
        dest = MODELS / name
        if dest.exists() and not args.force:
            print(f"[skip] {name} (exists)")
            continue
        last = None
        ok = False
        for mirror in MIRRORS:
            url = f"{mirror}/{repo}/resolve/main/{rpath}"
            try:
                fetch(url, dest)
                ok = True
                break
            except Exception as exc:  # noqa: BLE001
                last = exc
                if dest.exists():
                    dest.unlink()  # remove partial download before retry
        if not ok:
            print(f"[FAIL] {name}: {last}")
            errors += 1

    # Unzip the logo library in place (configs.yaml points at the .zip; the
    # PhishIntention code opens both the zip and the extracted dir).
    zf = MODELS / "expand_targetlist.zip"
    if zf.exists() and not (MODELS / "expand_targetlist").exists():
        print("unzipping expand_targetlist.zip ...")
        with zipfile.ZipFile(zf) as z:
            z.extractall(MODELS)

    print("\n" + "=" * 46)
    if errors:
        print(f"{errors} file(s) failed. Check network / proxy and re-run.")
        return 1
    if not (MODELS / "expand_targetlist").exists():
        print("note: expand_targetlist/ not extracted (zip missing or --force);"
              " download it to continue (run once without --force).")
        return 1
    print("done. Run tools/check_env.py to verify.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())