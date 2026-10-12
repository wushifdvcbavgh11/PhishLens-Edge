"""Chinese-brand logo matcher (T7) - MobileNet fallback for PhishIntention's
English-only Siamese matcher.

Loads brand_library/brands.json + logo_feats_mnet.npy and a MobileNetV3-Large
embedder, then matches cropped logo regions from the screenshot by cosine
similarity. Used in server/app.py L1-2 when the PhishIntention matcher misses
(returns None).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np
from PIL import Image
from torchvision import transforms

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_DIR = REPO_ROOT / "brand_library"

MATCH_THRESHOLD = 0.68  # cosine sim above this -> brand match (T9: 0.55 -> 0.80 -> 0.68.
                        # Measured on the 103-sample corpus: fake CN-brand pages land
                        # 0.709-0.734 (>=0.68 keeps all 3); benign mismatches sit <0.68
                        # except facebook.com->Coinbase 0.728 which is handled by
                        # EXTRA_WHITELIST. 0.80 killed every true positive.)
# T15: if the top-2 brand embeddings are nearly tied, the crop is ambiguous
# (e.g. citic<->spdb templates 0.774 vs 0.796, microsoft365<->bankcomm, abchina<->
# boc). Binding the wrong brand would flip domain-consistency and could block a
# legitimate bank login page, so refuse to match instead.
# Guard is RELATIVE (top2/top1 > 0.97): a real alipay crop scores alipay 0.734 /
# feishu 0.692 = 0.943 (keep), while sibling-bank confusions sit > 0.97 (reject).
# A fixed absolute gap (0.05) over-rejected low-score but decisive matches.
AMBIGUITY_RATIO = 0.97
MIN_COLOR_STD = 30.0    # crop std below this is a flat-color region, skip
MIN_LOGO_SIZE = 32      # ignore tiny crops (<32 px in either dimension)
MAX_LOGO_SIZE = 200     # ignore oversized crops (>200 px; likely page background blocks)

_ready = False
_index: dict = {}       # brand metadata by id
_embeds: np.ndarray | None = None
_embedder = None
_load_error: str | None = None

_TF = transforms.Compose([
    transforms.Resize(256),
    transforms.CenterCrop(224),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])


def load_index():
    """Lazy-load brand metadata + precomputed embeddings + MobileNet embedder."""
    global _ready, _embedder, _embeds, _index, _load_error
    if _ready:
        return
    if _load_error:
        raise RuntimeError(_load_error)
    t0 = time.time()
    try:
        import torch
        from torchvision import models

        _index = {
            b["id"]: b
            for b in json.loads((LIB_DIR / "brands.json").read_text(encoding="utf-8"))
        }
        _embeds = np.load(LIB_DIR / "logo_feats_mnet.npy").astype(np.float32)

        if (REPO_ROOT / 'portable.json').is_file():
            checkpoint = REPO_ROOT / 'runtime/model-cache/hub/checkpoints/mobilenet_v3_large-5c1a4163.pth'
            if not checkpoint.is_file():
                raise FileNotFoundError('Packaged MobileNet checkpoint is missing')
            m = models.mobilenet_v3_large(weights=None)
            m.load_state_dict(torch.load(checkpoint, map_location='cpu', weights_only=True))
        else:
            m = models.mobilenet_v3_large(
                weights=models.MobileNet_V3_Large_Weights.IMAGENET1K_V2
            )
        m.eval()
        _embedder = torch.nn.Sequential(*(list(m.children())[:-1]))
        _ready = True
        print(f"[cn_brand_matcher] index loaded: {len(_index)} brands "
              f"({time.time()-t0:.1f}s)")
    except Exception as exc:  # noqa: BLE001
        _load_error = f"{type(exc).__name__}: {exc}"
        raise RuntimeError(_load_error) from exc


def _embed(img: Image.Image) -> np.ndarray:
    """L2-normalized 960-d embedding of a logo image."""
    load_index()
    import torch
    embedder = _embedder
    if embedder is None:
        raise RuntimeError("cn_brand_matcher embedder not loaded")
    t = _TF(img.convert("RGB")).unsqueeze(0)
    with torch.no_grad():
        f = embedder(t).flatten(1).numpy()[0]
    return f / (np.linalg.norm(f) + 1e-9)


def match_logo_image(img: Image.Image, threshold: float = MATCH_THRESHOLD):
    """Match one logo image against the brand index.

    Returns dict {id, name, name_cn, domains, sim} or None.
    """
    load_index()
    embeds = _embeds
    if embeds is None:
        raise RuntimeError("cn_brand_matcher embeddings not loaded")
    f = _embed(img).reshape(1, -1)
    sims = f @ embeds.T
    order = np.argsort(sims[0])[::-1]
    best = int(order[0])
    top1 = float(sims[0][best])
    if top1 < threshold:
        return None
    if embeds.shape[0] >= 2:
        top2 = float(sims[0][int(order[1])])
        if top1 > 1e-9 and top2 / top1 > AMBIGUITY_RATIO:
            # T15: ambiguous top-2 -> do not bind a brand (prevents legitimate
            # bank pages from being matched to a sibling brand and blocked).
            return None
    bid = list(_index.keys())[best]
    sim = top1
    meta = _index[bid]
    return {**meta, "sim": round(sim, 4)}


def match_logo_boxes(shot_path, logo_boxes, threshold: float = MATCH_THRESHOLD):
    """Crop each logo bbox (x1,y1,x2,y2) from the screenshot and match.

    ``shot_path`` accepts either a filesystem path (str/Path) or an open
    binary file-like (``io.BytesIO``) — T17 keeps screenshots in memory, so
    callers pass a BytesIO and no temp file ever hits disk.

    Returns best {id, name, name_cn, domains, sim, bbox} or None.
    """
    load_index()
    img = Image.open(shot_path).convert("RGB")
    best_hit = None
    for i, coord in enumerate(logo_boxes):
        x1, y1, x2, y2 = [float(v) for v in coord]
        crop = img.crop((max(0, int(x1)), max(0, int(y1)), int(x2), int(y2)))
        # T15: size guard — tiny icons and oversized blocks are not logos.
        if crop.size[0] < MIN_LOGO_SIZE or crop.size[1] < MIN_LOGO_SIZE:
            continue
        if crop.size[0] > MAX_LOGO_SIZE or crop.size[1] > MAX_LOGO_SIZE:
            continue
        # T7: reject flat-color crops (page background / empty region).
        # Real logos have enough local contrast; screenshots backgrounds do not.
        arr = __import__("numpy").asarray(crop)
        if arr.std() < MIN_COLOR_STD:
            continue
        hit = match_logo_image(crop, threshold=threshold)
        if hit and (best_hit is None or hit["sim"] > best_hit["sim"]):
            best_hit = {**hit, "bbox": [int(x1), int(y1), int(x2), int(y2)]}
    return best_hit
