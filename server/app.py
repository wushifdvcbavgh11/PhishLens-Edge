"""
PhishLens Edge Guard - local analysis service (T6)

FastAPI service that receives {url, screenshot_base64, dom_signals} from the
browser extension and runs the multi-level funnel:

  L0  domain whitelist + LRU result cache
  L1-2  logo detection (Faster R-CNN, AWL) + OCR-aided Siamese brand matching
       (reuses the PhishIntention weights already downloaded in third_party/)
  L3  DOM credential-extraction signal
  L4  rule-based "VLM" review (local Qwen2-VL hook placeholder)

Output: decision in {block, allow, uncertain} + brand + official domain +
        per-level timings.
"""

from __future__ import annotations

import base64
import io
import os
import pickle
import sys
import tempfile
import time
import traceback
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlparse

import tldextract
import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# sibling module (server/): Chinese-brand MobileNet fallback matcher (T7)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cn_brand_matcher  # noqa: E402

# ---------------------------------------------------------------------------
# Paths & model setup (weights already present from T2/T3)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
PHISHINTENTION_ROOT = REPO_ROOT / "third_party" / "PhishIntention"
PHISHINTENTION_SRC = PHISHINTENTION_ROOT / "src"

if str(PHISHINTENTION_SRC) not in sys.path:
    sys.path.insert(0, str(PHISHINTENTION_SRC))

MODELS_READY = False
MODELS_LOAD_ERROR: str | None = None

# ---------------------------------------------------------------------------
# L0: built-in brand whitelist (extended in T7 with 20-30 brands)
# ---------------------------------------------------------------------------
USER_FACING_BRAND = {  # brand display name -> official domains (host or suffix)
    "支付宝": ["alipay.com"],
    "微信": ["weixin.qq.com"],
    "淘宝": ["taobao.com"],
    "天猫": ["tmall.com"],
    "京东": ["jd.com"],
    "拼多多": ["pinduoduo.com", "yangkeduo.com"],
    "百度": ["baidu.com"],
    "知乎": ["zhihu.com"],
    "微博": ["weibo.com"],
    "QQ": ["qq.com"],
    "微信支付": ["weixin.qq.com"],
    "工商银行": ["icbc.com.cn"],
    "建设银行": ["ccb.com"],
    "农业银行": ["abchina.com"],
    "中国银行": ["boc.cn"],
    "招商银行": ["cmbchina.com"],
    "交通银行": ["bankcomm.com"],
    "邮储银行": ["psbc.com"],
    "12306": ["12306.cn"],
    "学信网": ["chsi.com.cn"],
    "PayPal": ["paypal.com"],
    "Google": ["google.com", "google.com.hk", "google.cn"],
    "Apple": ["apple.com", "icloud.com"],
    "Microsoft": ["microsoft.com", "office.com", "live.com"],
    "Amazon": ["amazon.com", "amazon.cn"],
    "Facebook": ["facebook.com"],
    "Instagram": ["instagram.com"],
    "GitHub": ["github.com"],
    "Coinbase": ["coinbase.com"],
    "MetaMask": ["metamask.io"],
    "Ledger": ["ledger.com"],
    "Xfinity": ["xfinity.com"],
    "Shopee": ["shopee.com"],
    "Steam": ["steampowered.com"],
}

# Extra domains that must never be blocked (search engines, gov, edu, etc.)
EXTRA_WHITELIST = [
    "baidu.com", "google.com", "bing.com", "qq.com", "zhihu.com", "weibo.com",
    "gov.cn", "edu.cn", "cninfo.com.cn", "sse.com.cn", "szse.cn",
    "facebook.com", "amazon.com", "paypal.com", "instagram.com", "steampowered.com",
]


class LRUCache:
    def __init__(self, capacity: int = 256):
        self.capacity = capacity
        self._store: OrderedDict[str, dict] = OrderedDict()

    def get(self, key: str):
        if key in self._store:
            self._store.move_to_end(key)
            return self._store[key]
        return None

    def put(self, key: str, value: dict):
        self._store[key] = value
        self._store.move_to_end(key)
        if len(self._store) > self.capacity:
            self._store.popitem(last=False)


RESULT_CACHE = LRUCache(256)


def hostname_matches(host: str, official: list[str]) -> bool:
    """True if host equals an official domain or is a subdomain of it."""
    h = host.lower().rstrip(".")
    for d in official:
        d = d.lower().rstrip(".")
        if h == d or h.endswith("." + d):
            return True
    return False


def load_models():
    """Lazy-load AWL detector + Siamese matcher (CPU). One-shot, cached."""
    global MODELS_READY, MODELS_LOAD_ERROR
    if MODELS_READY:
        return
    if MODELS_LOAD_ERROR:
        raise RuntimeError(MODELS_LOAD_ERROR)
    t0 = time.time()
    try:
        old_cwd = os.getcwd()
        # detectron2 config paths in configs.yaml are cwd-relative
        os.chdir(PHISHINTENTION_ROOT)
        with open(PHISHINTENTION_ROOT / "configs" / "configs.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        # patch numpy legacy aliases if needed (already patched in logo_matching.py, belt & suspenders)
        import numpy as np
        for legacy in ("int", "float", "bool", "object"):
            if not hasattr(np, legacy):
                setattr(np, legacy, getattr(np, legacy + "_", None) or getattr(np, legacy))

        # AWL: Faster R-CNN layout/element detector (logo class only)
        from phishintention.modules.awl_detector import config_rcnn, pred_rcnn, find_element_type
        from phishintention.modules.logo_matching import (
            siamese_model_config,
            ocr_model_config,
            cache_reference_list,
            check_domain_brand_inconsistency,
        )

        awl = config_rcnn(
            cfg_path=cfg["AWL_MODEL"]["CFG_PATH"],
            weights_path=cfg["AWL_MODEL"]["WEIGHTS_PATH"],
            conf_threshold=cfg["AWL_MODEL"]["DETECT_THRE"],
        )
        siamese = siamese_model_config(
            num_classes=cfg["SIAMESE_MODEL"]["NUM_CLASSES"],
            weights_path=cfg["SIAMESE_MODEL"]["WEIGHTS_PATH"],
        )
        ocr = ocr_model_config(weights_path=cfg["SIAMESE_MODEL"]["OCR_WEIGHTS_PATH"])

        # brand logo feature cache (LOGO_FEATS.npy built in T3, ~31MB)
        targetlist_dir = os.path.dirname(cfg["SIAMESE_MODEL"]["TARGETLIST_PATH"])
        zip_name = os.path.basename(cfg["SIAMESE_MODEL"]["TARGETLIST_PATH"])
        targetlist_folder = os.path.join(targetlist_dir, zip_name.replace(".zip", ""))
        logo_feats, logo_files = cache_reference_list(
            model=siamese, ocr_model=ocr, targetlist_path=targetlist_folder, reload_targetlist=False
        )
        os.chdir(old_cwd)

        # cache_reference_list returns cwd-relative logo paths, but pred_brand()
        # Image.open()s them later while the server cwd is elsewhere -> absolutize.
        def _abs_logo_path(p: str) -> str:
            if os.path.isabs(p):
                return p
            joined = os.path.abspath(os.path.join(PHISHINTENTION_ROOT, p))
            if os.name == "nt" and len(joined) > 240:
                joined = "\\\\?\\" + joined
            return joined

        logo_files = [_abs_logo_path(p) for p in logo_files]

        global _MODELS
        _MODELS = {
            "awl": awl,
            "siamese": siamese,
            "ocr": ocr,
            "logo_feats": logo_feats,
            "logo_files": logo_files,
            "siamese_thre": float(cfg["SIAMESE_MODEL"]["MATCH_THRE"]),
            # check_domain_brand_inconsistency opens this with open(domain_map_path),
            # so it must be absolute (cwd may change between the load and the call)
            "domain_map_path": str(PHISHINTENTION_ROOT / cfg["SIAMESE_MODEL"]["DOMAIN_MAP_PATH"]),
            "pred_rcnn": pred_rcnn,
            "find_element_type": find_element_type,
            "check_domain_brand_inconsistency": check_domain_brand_inconsistency,
            "load_time_s": round(time.time() - t0, 2),
        }
        MODELS_READY = True
    except Exception as exc:  # noqa: BLE001
        MODELS_LOAD_ERROR = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
        raise RuntimeError(MODELS_LOAD_ERROR) from exc


_MODELS: dict = {}


# ---------------------------------------------------------------------------
# L4: rule-based VLM review (placeholder for local Qwen2-VL)
# ---------------------------------------------------------------------------
SUSPICIOUS_TLDS = {".xyz", ".top", ".icu", ".tk", ".ml", ".ga", ".cf", ".gq", ".club", ".online", ".site", ".vip", ".cc"}


def url_suspicion_score(url: str) -> dict:
    """Cheap heuristics mirroring what a small VLM would flag. Returns score + reasons."""
    score = 0
    reasons = []
    try:
        parsed = urlparse(url if "://" in url else "http://" + url)
    except Exception:  # noqa: BLE001
        return {"score": 0, "reasons": reasons}
    host = parsed.hostname or ""
    # IP address as host
    import re
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", host):
        score += 3
        reasons.append("以 IP 地址直接访问")
    # punycode / suspicious chars
    if "xn--" in host:
        score += 2
        reasons.append("国际化域名混淆")
    if host.count("-") >= 3 or len(host) > 40:
        score += 1
        reasons.append("域名异常冗长或含多个连字符")
    # suspicious TLD
    ext = tldextract.extract(url).suffix
    if ("." + ext) in SUSPICIOUS_TLDS:
        score += 2
        reasons.append(f"可疑顶级域 .{ext}")
    # keyword stuffing
    kw = ["login", "verify", "secure", "account", "bank", "webmail", "confirm", "alipay", "update"]
    if any(k in host.lower() for k in kw) and score >= 0:
        pass  # only informational
    # http
    if parsed.scheme != "https":
        score += 1
        reasons.append("非 HTTPS 连接")
    return {"score": score, "reasons": reasons}


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="PhishLens Edge Guard local service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DomSignals(BaseModel):
    page_domain: str = ""
    has_password_input: bool = False
    has_login_form: bool = False
    form_action_domains: list[str] = Field(default_factory=list)
    title: str = ""
    favicon: str = ""
    brand_hints: list[str] = Field(default_factory=list)


class AnalyzeRequest(BaseModel):
    url: str
    screenshot_base64: str
    dom_signals: DomSignals | None = None
    trigger: str = "manual"


@app.get("/health")
def health():
    return {"status": "ok", "models_ready": MODELS_READY, "cache_size": len(RESULT_CACHE._store)}


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    t_total = time.time()
    level_times: dict[str, float] = {}
    reasons: list[str] = []
    decision = "allow"
    brand: str | None = None
    official_domain: str | None = None

    # ---------- L0: whitelist + LRU cache ----------
    t0 = time.time()
    parsed = urlparse(req.url if "://" in req.url else "http://" + req.url)
    host = (parsed.hostname or "").lower().rstrip(".")
    ext_obj = tldextract.extract(req.url)
    registered_domain = f"{ext_obj.domain}.{ext_obj.suffix}" if ext_obj.domain else ""

    cached = RESULT_CACHE.get(req.url)
    if cached:
        cached["from_cache"] = True
        cached["level_times"] = {"l0_cache_hit": round(time.time() - t_total, 3)}
        return cached

    whitelisted = False
    if hostname_matches(host, EXTRA_WHITELIST) or registered_domain in EXTRA_WHITELIST:
        whitelisted = True
    elif req.dom_signals and req.dom_signals.page_domain and hostname_matches(req.dom_signals.page_domain, EXTRA_WHITELIST):
        whitelisted = True
    level_times["l0_whitelist"] = round(time.time() - t0, 3)

    if whitelisted:
        out = {
            "decision": "allow",
            "brand": None,
            "official_domain": None,
            "reasons": ["域名在白名单内"],
            "level_times": level_times,
            "total_time_s": round(time.time() - t_total, 3),
            "from_cache": False,
        }
        RESULT_CACHE.put(req.url, out)
        return out

    # ---------- decode screenshot ----------
    try:
        if req.screenshot_base64.startswith("data:"):
            raw = req.screenshot_base64.split(",", 1)[1]
        else:
            raw = req.screenshot_base64
        img_bytes = base64.b64decode(raw)
    except Exception:  # noqa: BLE001
        img_bytes = None

    # ---------- L1-2: logo detection + brand matching (CPU) ----------
    matched_target = None
    matched_domain = None
    matched_conf = None
    visual_error = None
    if img_bytes:
        try:
            load_models()
            m = _MODELS
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(img_bytes)
                tmp_path = tmp.name
            t1 = time.time()
            boxes, classes, _ = m["pred_rcnn"](im=tmp_path, predictor=m["awl"])
            logo_boxes, _ = m["find_element_type"](boxes, classes, bbox_type="logo")
            level_times["l1_logo_det"] = round(time.time() - t1, 3)

            # T7: the AWL YOLO was trained on web-page logos; App-Store-style flat
            # icons (our CN brand icons) are often classified as `block` instead.
            # If no logo box came out, take top-area blocks as logo candidates.
            if len(logo_boxes) == 0:
                block_boxes, _ = m["find_element_type"](boxes, classes, bbox_type="block")
                if len(block_boxes) > 0:
                    from PIL import Image as _PILImage
                    with _PILImage.open(tmp_path) as _im:
                        _h = _im.height
                    top_area = [b for b in block_boxes if float(b[1]) < 0.45 * _h]
                    if len(top_area) > 0:
                        logo_boxes = __import__("torch").stack([b for b in top_area])
                        reasons.append("Logo 类未检出，改用页面顶部区域块作为候选")
            t2 = time.time()
            if len(logo_boxes) > 0:
                matched_target, matched_domain, matched_coord, matched_conf = m["check_domain_brand_inconsistency"](
                    logo_boxes, m["domain_map_path"], m["siamese"], m["ocr"],
                    m["logo_feats"], m["logo_files"], tmp_path,
                    req.url, m["siamese_thre"],
                )
                if matched_target is not None and matched_domain is not None:
                    brand = matched_target
                    if isinstance(matched_domain, list):
                        official_domain = ",".join(matched_domain)
                    else:
                        official_domain = str(matched_domain)
                    reasons.append(f"Logo 匹配品牌 {brand}（相似度 {float(matched_conf or 0):.3f}）")

                    # extra cross-check against built-in brand list
                    if matched_target in USER_FACING_BRAND:
                        official_domain = ",".join(USER_FACING_BRAND[matched_target])
                else:
                    # T7: PhishIntention covers English brands only; fall back to
                    # the MobileNet Chinese-brand matcher for the same logo boxes.
                    cn_hit = cn_brand_matcher.match_logo_boxes(tmp_path, logo_boxes)
                    if cn_hit is not None:
                        brand = cn_hit["name_cn"]
                        official_domain = ",".join(cn_hit["domains"])
                        reasons.append(
                            f"Logo 匹配中文品牌 {brand}（MobileNet 相似度 {cn_hit['sim']:.3f}）"
                        )
                        matched_conf = cn_hit["sim"]
            level_times["l2_logo_match"] = round(time.time() - t2, 3)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        except Exception as exc:  # noqa: BLE001
            visual_error = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()

    # ---------- L3: DOM credential signal ----------
    t3 = time.time()
    ds = req.dom_signals
    cred_signal = False
    if ds:
        if ds.has_password_input:
            cred_signal = True
            reasons.append("页面存在密码输入框（索要凭据）")
        if ds.has_login_form:
            reasons.append("页面存在登录表单")
        # form action pointing outside the page domain = credential exfiltration risk
        if ds.form_action_domains:
            for action_domain in ds.form_action_domains:
                if action_domain and action_domain != host and not hostname_matches(action_domain, [host]):
                    cred_signal = True
                    reasons.append(f"表单 action 指向外部域名 {action_domain}")
                    break
        # brand hint cross-check
        if brand and ds.brand_hints:
            if any(bh.lower() in brand.lower() or brand.lower() in bh.lower() for bh in ds.brand_hints):
                reasons.append("页面文本品牌提示与视觉匹配品牌一致")
    level_times["l3_dom"] = round(time.time() - t3, 3)

    # ---------- L4: rule-based review (VLM hook placeholder) ----------
    t4 = time.time()
    sus = url_suspicion_score(req.url) if not whitelisted else {"score": 0, "reasons": []}
    for r in sus["reasons"]:
        reasons.append(r)
    level_times["l4_rules"] = round(time.time() - t4, 3)

    # ---------- Fuse into three-state decision ----------
    # 三条件判定：视觉 logo 匹配 + 索要凭据 + 域名与正规域名不一致
    # brand matched by either Path (PhishIntention English / MobileNet Chinese)
    brand_domain_inconsistent = False
    if brand and official_domain:
        official_list = [d.strip() for d in official_domain.split(",") if d.strip()]
        # domain mismatch: neither the URL host nor the declared page domain is official
        page_host_ok = bool(req.dom_signals and req.dom_signals.page_domain
                            and hostname_matches(req.dom_signals.page_domain, official_list))
        brand_domain_inconsistent = not (hostname_matches(host, official_list) or page_host_ok)
        if brand_domain_inconsistent:
            reasons.append(f"当前域名 {host} 与品牌 {brand} 正规域名不一致")

    if brand and brand_domain_inconsistent:
        if cred_signal or sus["score"] >= 2:
            decision = "block"
            reasons.append("视觉品牌匹配且域名与正规域名不一致，页面索要凭据/域名可疑")
        else:
            decision = "uncertain"
            reasons.append("视觉品牌匹配但域名不一致，未确认索要凭据，建议 VLM 复核")
    elif cred_signal and sus["score"] >= 3:
        decision = "uncertain"
        reasons.append("存在凭据索要信号且域名高度可疑，建议 VLM 复核")
    elif cred_signal and visual_error is None and sus["score"] >= 2:
        decision = "uncertain"
    else:
        decision = "allow"
        if not reasons:
            reasons.append("未发现可靠钓鱼信号")

    out = {
        "decision": decision,
        "brand": brand,
        "official_domain": official_domain,
        "reasons": reasons,
        "level_times": level_times,
        "total_time_s": round(time.time() - t_total, 3),
        "from_cache": False,
        "visual_error": visual_error,
        "models_load_time_s": _MODELS.get("load_time_s") if MODELS_READY else None,
    }
    RESULT_CACHE.put(req.url, out)
    return out


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")