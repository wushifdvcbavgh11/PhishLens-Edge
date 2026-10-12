"""PhishLens Edge - vision stack installer (T16).

Installs the CPU vision stack needed by the L1/L2 funnel:
    numpy<2, Pillow, torch==2.1.0+cpu, torchvision==0.16.0+cpu, detectron2==0.6

Preference order:
  1. local wheels under third_party/wheels/ (fastest, offline-capable)
  2. official PyTorch CPU index (download.pytorch.org/whl/cpu) + the
     community detectron2 cp311 wheel (MiroPsota) - requires internet

Usage (from the repo root):
    .venv/Scripts/python tools/setup_vision.py

Re-running is safe: already-satisfied packages are skipped.
"""
from __future__ import annotations

import importlib.metadata
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WHEELS = REPO / "third_party" / "wheels"
PY = Path(sys.executable)


def pip(*args: str) -> int:
    print(f"\n>> {PY} -m pip {' '.join(args)}")
    return subprocess.call([str(PY), "-m", "pip", *args])


def want(mod: str, need: str = "", dist: str = "") -> bool:
    """True if the package must be (re)installed."""
    try:
        have = importlib.metadata.version(dist or mod)
    except importlib.metadata.PackageNotFoundError:
        return True
    if not need:
        return False
    h = have.split("+")[0]
    hv = tuple(int(x) for x in h.split(".") if x.isdigit())
    nv = tuple(int(x) for x in need.split("+")[0].split(".") if x.isdigit())
    return hv < nv


def main() -> int:
    print(f"repo:    {REPO}")
    print(f"python:  {sys.version.split()[0]} ({sys.executable})")
    if sys.version_info < (3, 11) or sys.version_info >= (3, 13):
        print("!! vision wheels are cp311 builds; prefer Python 3.11")
    if not (REPO / ".venv").exists():
        print("!! .venv not found - did you create it first? (python -m venv .venv)")

    fail = 0
    # 1) numpy + Pillow (pure PyPI, same for every platform)
    if want("numpy", "1.24", "numpy"):
        fail += pip("install", "numpy>=1.24,<2") or 0
    if want("PIL", "10", "Pillow"):
        fail += pip("install", "Pillow>=10") or 0

    # 2) torch / torchvision / detectron2
    need_torch = want("torch", "2.1.0", "torch")
    need_tv = want("torchvision", "0.16.0", "torchvision")
    need_d2 = want("detectron2", "0.6", "detectron2")
    if need_torch or need_tv or need_d2:
        # Prefer local wheels under third_party/wheels/ (offline-capable).
        local = {p.name for p in WHEELS.glob("*.whl")} if WHEELS.is_dir() else set()
        picks = []
        if need_torch and any(n.startswith("torch-2.1.0+cpu-") for n in local):
            picks.append(next(WHEELS / n for n in local if n.startswith("torch-2.1.0+cpu-")))
        if need_tv and any(n.startswith("torchvision-0.16.0+cpu-") for n in local):
            picks.append(next(WHEELS / n for n in local if n.startswith("torchvision-0.16.0+cpu-")))
        if need_d2 and any(n.startswith("detectron2-0.6+") for n in local):
            picks.append(next(WHEELS / n for n in local if n.startswith("detectron2-0.6+")))
        if picks:
            fail += pip("install", *(str(p) for p in picks), "--no-deps") or 0
        # Whatever is still missing -> official CPU index.
        if want("torch", "2.1.0", "torch"):
            fail += pip("install", "torch==2.1.0+cpu", "--index-url", "https://download.pytorch.org/whl/cpu") or 0
        if want("torchvision", "0.16.0", "torchvision"):
            fail += pip("install", "torchvision==0.16.0+cpu", "--index-url", "https://download.pytorch.org/whl/cpu") or 0
        if want("detectron2", "0.6", "detectron2"):
            print("\n!! detectron2 has no official CPU wheel. Use the Windows cp311 wheel")
            print("   from MiroPsota (see docs/T3). If third_party/wheels/ has it,")
            print("   this script already tried it. Otherwise download it and retry.")
            fail += 1

    print("\n" + "=" * 46)
    if fail == 0:
        print("OK - vision stack is ready. Run tools/check_env.py to verify.")
        return 0
    print(f"{fail} step(s) failed - see the messages above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())