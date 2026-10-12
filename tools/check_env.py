"""PhishLens Edge - environment readiness check (T16).

Read-only. Verifies everything the local service + extension need to run,
and prints one [OK]/[MISSING]/[WARN] line per item with fix hints.

Usage:
    .venv/Scripts/python tools/check_env.py

Exit code 0 = ready, 1 = something missing.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import platform
import socket
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PI = REPO / "third_party" / "PhishIntention"
LIB = REPO / "brand_library"
EXT = REPO / "browser-extension"
MODEL = REPO / "model"
TOOLS = REPO / "tools"

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"
fails = 0
warns = 0


def check(label: str, ok: bool, hint: str = ""):
    global fails
    if ok:
        print(f"{GREEN}[OK]{RESET}      {label}")
    else:
        fails += 1
        print(f"{RED}[MISSING]{RESET}  {label}" + (f"  -> {hint}" if hint else ""))


def warn(label: str, hint: str = ""):
    global warns
    warns += 1
    print(f"{YELLOW}[WARN]{RESET}    {label}" + (f"  -> {hint}" if hint else ""))


def _ver_lt(a: str, b: str) -> bool:
    """Pure-stdlib version compare (a < b) on dotted numeric prefixes."""
    va = [int(x) for x in a.replace("-", ".").split(".") if x.isdigit()]
    vb = [int(x) for x in b.replace("-", ".").split(".") if x.isdigit()]
    return va < vb


def pkg_ok(mod: str, need: str = "", pip: str = "", dist: str = ""):
    """Check importable module; version read from distribution name (dist,
    default = mod) because some dists differ from the import name (PIL->Pillow)."""
    spec = importlib.util.find_spec(mod)
    if spec is None:
        return False, f"pip install {pip or mod}"
    if need:
        try:
            have = importlib.metadata.version(dist or mod)
            # compare "2.1.0+cpu" -> "2.1.0"
            h = have.split("+")[0]
            if _ver_lt(h, need):
                return False, f"have {have}, need >= {need}"
        except importlib.metadata.PackageNotFoundError:
            return False, f"{dist or mod} metadata missing (install with {pip or dist})"
    return True, ""


# ---------------- Python ----------------
print("== Python ==")
check(
    "python >= 3.11 (vision wheels are cp311)",
    sys.version_info >= (3, 11),
    f"current {platform.python_version()}",
)

# ---------------- Dependencies ----------------
print("\n== Dependencies (server + vision stack) ==")
DEPS = [
    ("fastapi", "0.110", "fastapi>=0.110"),
    ("uvicorn", "0.29", "uvicorn[standard]>=0.29"),
    ("pydantic", "2.6", "pydantic>=2.6"),
    ("tldextract", "5.1", "tldextract>=5.1"),
    ("yaml", "", "PyYAML>=6.0"),
    ("numpy", "1.24", "numpy<2"),
    ("PIL", "10", "Pillow>=10", "Pillow"),
    ("torch", "2.1.0", "see README: CPU wheel from download.pytorch.org"),
    ("torchvision", "0.16.0", "see README: CPU wheel from download.pytorch.org"),
    ("detectron2", "0.6", "MiroPsota win wheel (third_party/wheels/)"),
    ("sklearn", "", "scikit-learn (UCI prefilter)"),
    ("joblib", "", "joblib (UCI prefilter)"),
    ("cv2", "", "opencv-python"),
]
for dep in DEPS:
    mod, need, pip = dep[0], dep[1], dep[2]
    dist = dep[3] if len(dep) > 3 else ""
    ok, hint = pkg_ok(mod, need, pip, dist)
    check(f"import {mod}" + (f" >= {need}" if need else ""), ok, hint)

# ---------------- Weights / models ----------------
print("\n== Model weights (PhishIntention, reused by the funnel) ==")
CFG = PI / "configs" / "configs.yaml"
weights: list[tuple[str, str]] = []
if CFG.exists():
    import yaml

    cfg = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    weights.append(("layout_detector.pth (AWL)", str(PI / cfg["AWL_MODEL"]["WEIGHTS_PATH"])))
    weights.append(("crp_classifier.pth.tar", str(PI / cfg["CRP_CLASSIFIER"]["WEIGHTS_PATH"])))
    weights.append(("crp_locator.pth (CRP)", str(PI / cfg["CRP_LOCATOR"]["WEIGHTS_PATH"])))
    weights.append(("ocr_siamese.pth.tar", str(PI / cfg["SIAMESE_MODEL"]["WEIGHTS_PATH"])))
    weights.append(("ocr_pretrained.pth.tar", str(PI / cfg["SIAMESE_MODEL"]["OCR_WEIGHTS_PATH"])))
    weights.append(("domain_map.pkl", str(PI / cfg["SIAMESE_MODEL"]["DOMAIN_MAP_PATH"])))
    tlist = cfg["SIAMESE_MODEL"]["TARGETLIST_PATH"]
    weights.append(("expand_targetlist/ (unzipped)", str(PI / os.path.dirname(tlist) / os.path.basename(tlist).replace(".zip", ""))))
else:
    warn("configs/configs.yaml missing — cannot derive weight paths", "clone lindsey98/PhishIntention into third_party/")
for name, path in weights:
    check(f"{name}: {Path(path).relative_to(REPO) if path.startswith(str(REPO)) else path}",
          os.path.exists(path),
          "download from hf-mirror.com code-philia/PhishIntention (see docs/T3)")

check(
    "detectron2 config files (faster_rcnn_web.yaml)",
    (PI / "configs" / "detectron2" / "faster_rcnn_web.yaml").exists(),
    "part of the PhishIntention checkout",
)

# ---------------- Brand library ----------------
print("\n== Brand library (CN matcher) ==")
check("brands.json", (LIB / "brands.json").exists(), "run brand_library/extract_features.py")
check("logo_feats_mnet.npy", (LIB / "logo_feats_mnet.npy").exists(),
      "run brand_library/extract_features.py")
if (LIB / "brands.json").exists():
    try:
        n_brands = len(json.loads((LIB / "brands.json").read_text(encoding="utf-8")))
        check(f"{n_brands} brands registered", n_brands >= 30,
              "rebuild with brand_library/extract_features.py")
    except Exception as exc:  # noqa: BLE001
        check("brands.json parses", False, str(exc))
logo_dir = LIB / "logos"
# Logo image filenames vary across brands (downloaded from official stores /
# sites): logo.png, logo_apple.png, logo_appstore.png, logo_official.png,
# logo_iphone2x.png, logo_pad2x.png, mg_*.png (app signup icons), logo.in.svg ...
LOGO_PATTERNS = (
    "logo.png", "logo_apple.png", "logo_appstore.png", "logo_official.png",
    "logo_iphone2x.png", "logo_pad2x.png", "logo.in.svg",
)


def _dir_has_logo(d: Path) -> bool:
    if any((d / name).exists() for name in LOGO_PATTERNS):
        return True
    return any(p.name.startswith("mg_") and p.suffix.lower() in (".png", ".jpg", ".jpeg") for p in d.iterdir())


if logo_dir.is_dir():
    with_logo = [p for p in logo_dir.iterdir() if p.is_dir() and _dir_has_logo(p)]
    check(f"logo directories with image ({len(with_logo)})", len(with_logo) >= 30,
          "place any of logo.png/logo_apple.png/... under logos/<id>/")
else:
    check("brand_library/logos/ exists", False, "run brand_library/build_brand_library.py")

# MobileNet embedder weights come from torchhub on first ever run.
hub = Path.home() / ".cache" / "torch" / "hub" / "checkpoints"
mnet = sorted((hub.glob("mobilenet_v3_large-*.pth")) if hub.is_dir() else [])
warn("MobileNet embedder weights in torch cache", "") if not mnet else None
if not mnet:
    warn("MobileNet-V3 embedder not cached yet", "first run needs internet once; it caches ~22MB under ~/.cache/torch")

# ---------------- Models (UCI prefilter) ----------------
print("\n== UCI prefilter (model/) ==")
check("uci_phishing_clf.joblib", (MODEL / "uci_phishing_clf.joblib").exists(),
      "train with tools/train_uci_phish.py")
check("uci_phishing_features.json", (MODEL / "uci_phishing_features.json").exists(),
      "train with tools/train_uci_phish.py")

# ---------------- Extension ----------------
print("\n== Browser extension ==")
for f in ("manifest.json", "content.js", "background.js"):
    check(f"browser-extension/{f}", (EXT / f).exists())
if (EXT / "manifest.json").exists():
    mv = json.loads((EXT / "manifest.json").read_text(encoding="utf-8")).get("manifest_version")
    check("manifest version 3", mv == 3, f"got {mv}")

# ---------------- Demo pages ----------------
print("\n== Demo pages (testset/fake/) ==")
for p in ("fake_alipay.html", "fake_icbc.html", "fake_wechat.html"):
    check(f"testset/fake/{p}", (REPO / "testset" / "fake" / p).exists(),
          "run tools/make_fake_pages.py")

# ---------------- Ports ----------------
print("\n== Ports ==")
for port, service in ((8765, "analysis service"), (8001, "demo page server")):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        warn(f"port {port} ({service}) free", "expected free before start_demo.bat")
    except OSError:
        check(f"port {port} ({service}) already up", True)
    finally:
        s.close()

print("\n" + ("=" * 46))
if fails == 0:
    print(f"{GREEN}READY{RESET} — {warns} warning(s). Run start_demo.bat.")
else:
    print(f"{RED}{fails} issue(s) must be fixed{RESET} — {warns} warning(s). See hints above.")
sys.exit(1 if fails else 0)