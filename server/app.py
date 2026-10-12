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
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path
from threading import Lock
from typing import Literal
from urllib.parse import urlparse

import numpy as np  # T17: in-memory screenshot decoding (no temp file)

import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ConfigDict

# Sibling modules must also work when started as ``python server/app.py``.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from reliability import LRUCache, MODEL_VERSION, POLICY_VERSION  # noqa: E402
from visual_brand import BrandIndex, choose_regions, inspect_regions, rgb_image

# The saved UCI model uses unverified label/feature encodings. Do not import,
# run DNS queries for, or fuse this classifier until it has been revalidated.
UCI_DISABLED_REASON = "UCI_DISABLED_UNVALIDATED_ENCODING"

# ---------------------------------------------------------------------------
# Paths & model setup (weights already present from T2/T3)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if (REPO_ROOT / 'portable.json').is_file():
    # Use the packaged public-suffix snapshot; never fetch a list on first run.
    import tldextract
    tldextract.extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)
    # Upstream imports the submodule directly rather than the package facade.
    from tldextract import tldextract as tldextract_impl
    tldextract_impl.extract = tldextract.extract
PHISHINTENTION_ROOT = REPO_ROOT / "third_party" / "PhishIntention"
PHISHINTENTION_SRC = PHISHINTENTION_ROOT / "src"

if str(PHISHINTENTION_SRC) not in sys.path:
    sys.path.insert(0, str(PHISHINTENTION_SRC))

MODELS_READY = False
MODELS_LOAD_ERROR: str | None = None
MODEL_RETRY_DELAY_S = 30.0
_MODEL_INIT_LOCK = Lock()
_INFERENCE_LOCK = Lock()
_MODELS_LAST_FAILURE = float("-inf")
_CN_INIT_LOCK = Lock()
_CN_LAST_FAILURE = float("-inf")

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
    "邮储银行": ["psbc.com", "psbcltd.cn"],
    "平安银行": ["pingan.com", "bank.pingan.com"],
    "中信银行": ["citicbank.com", "ecitic.com"],
    "浦发银行": ["spdb.com.cn"],
    "清华大学": ["tsinghua.edu.cn", "id.tsinghua.edu.cn"],
    "北京大学": ["pku.edu.cn", "iaaa.pku.edu.cn", "portal.pku.edu.cn"],
    "复旦大学": ["fudan.edu.cn", "id.fudan.edu.cn", "ehall.fudan.edu.cn"],
    "浙江大学": ["zju.edu.cn", "zjuam.zju.edu.cn", "my.zju.edu.cn"],
    "上海交通大学": ["sjtu.edu.cn", "jaccount.sjtu.edu.cn"],
    "12306": ["12306.cn"],
    "学信网": ["chsi.com.cn"],
    "PayPal": ["paypal.com"],
    "Google": ["google.com", "google.com.hk", "google.cn"],
    "Apple": ["apple.com", "icloud.com"],
    "Microsoft": ["microsoft.com", "office.com", "microsoftonline.com", "live.com", "outlook.com", "sharepoint.com", "azure.com"],
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

# Explicit official domains. Entire public suffixes must never be exempted.
EXTRA_WHITELIST = [
    "baidu.com", "google.com", "bing.com", "qq.com", "zhihu.com", "weibo.com",
    "cninfo.com.cn", "sse.com.cn", "szse.cn",
    "facebook.com", "amazon.com", "paypal.com", "instagram.com", "steampowered.com",
    # --- frequently mis-identified brands (added 2026-10-11) ---
    "openai.com", "chatgpt.com", "deepseek.com", "microsoft.com",
    "office.com", "live.com", "outlook.com", "apple.com", "icloud.com",
    "github.com", "twitter.com", "x.com", "linkedin.com",
]


RESULT_CACHE = LRUCache(256)


def build_cache_key(req: "AnalyzeRequest", img_bytes: bytes | None = None) -> str:
    """Bind a verdict to pixels, complete DOM context, mode and policy/model versions."""
    screenshot = img_bytes if img_bytes is not None else req.screenshot_base64.encode("utf-8")
    payload = {
        "url": req.url,
        "screenshot_sha256": hashlib.sha256(screenshot).hexdigest(),
        "dom_signals": req.dom_signals.model_dump() if req.dom_signals is not None else None,
        "analysis_mode": req.analysis_mode,
        "policy_version": POLICY_VERSION,
        "model_version": MODEL_VERSION,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hostname_matches(host: str, official: list[str]) -> bool:
    """True if host equals an official domain or is a subdomain of it."""
    h = host.lower().rstrip(".")
    for d in official:
        d = d.lower().rstrip(".")
        if h == d or h.endswith("." + d):
            return True
    return False


def load_models():
    """Initialize once at a time, and retry failures after a bounded cooldown."""
    global MODELS_READY, MODELS_LOAD_ERROR, _MODELS_LAST_FAILURE, _MODELS
    with _MODEL_INIT_LOCK:
        if MODELS_READY:
            return
        if MODELS_LOAD_ERROR and time.monotonic() - _MODELS_LAST_FAILURE < MODEL_RETRY_DELAY_S:
            raise RuntimeError(f"模型初始化暂不可用，稍后重试: {MODELS_LOAD_ERROR}")
        try:
            models = _initialize_models()
        except Exception as exc:
            MODELS_LOAD_ERROR = f"{type(exc).__name__}: {exc}"
            _MODELS_LAST_FAILURE = time.monotonic()
            raise RuntimeError(MODELS_LOAD_ERROR) from exc
        _MODELS = models
        MODELS_LOAD_ERROR = None
        MODELS_READY = True


def _initialize_models():
    """Build model state without publishing partially initialized globals.

    Upstream AWL config paths are cwd-relative. The new brand cache has explicit
    ordered records and content/model hashes; it never relies on directory order.
    """
    t0 = time.time()
    old_cwd = os.getcwd()
    try:
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
            get_ocr_aided_siamese_embedding,
        )
        import torch
        torch.set_num_threads(2)

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

        brand_index = BrandIndex.load(REPO_ROOT)

        return {
            "awl": awl,
            "siamese": siamese,
            "ocr": ocr,
            "brand_index": brand_index,
            "embed_logo": lambda image: get_ocr_aided_siamese_embedding(rgb_image(image), siamese, ocr),
            "pred_rcnn": pred_rcnn,
            "find_element_type": find_element_type,
            "load_time_s": round(time.time() - t0, 2),
        }
    finally:
        os.chdir(old_cwd)


_MODELS: dict = {}


def _pred_rcnn_from_bytes(img_bytes: bytes, predictor):
    """T17: in-memory equivalent of PhishIntention's ``pred_rcnn``.

    The upstream helper reads the screenshot from disk via ``cv2.imread``;
    here we decode the same PNG bytes straight into a BGR ndarray
    (``cv2.imdecode``) so no temp file is ever written. Return values are
    identical: (pred_boxes, pred_classes, pred_scores).
    """
    import cv2

    im = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
    if im is None:
        raise ValueError("截图无法解码为图像")
    if im.shape[-1] == 4:
        im = cv2.cvtColor(im, cv2.COLOR_BGRA2BGR)
    outputs = predictor(im)
    instances = outputs["instances"]
    return (
        instances.pred_boxes.tensor.detach().cpu(),
        instances.pred_classes.detach().cpu(),
        instances.scores,
    )


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
    # Every configured suspicious suffix is one label; no online PSL refresh is
    # necessary for this inexpensive heuristic.
    ext = host.rsplit(".", 1)[-1]
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
app = FastAPI(title="PhishLens Edge Guard local service", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RegionSignal(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    box: list[float] = Field(min_length=4, max_length=4)
    interactive: bool = False


class CredentialContext(RegionSignal):
    text: str = Field(default='', max_length=600)


class ViewportSignal(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    width: float = Field(gt=0, le=32768)
    height: float = Field(gt=0, le=32768)


class DomSignals(BaseModel):
    page_domain: str = ""
    # None means unknown, rather than claiming that no password field exists.
    has_password_input: bool | None = None
    has_login_form: bool | None = None
    form_action_domains: list[str] = Field(default_factory=list)
    title: str = ""
    favicon: str = ""
    brand_hints: list[str] = Field(default_factory=list)
    page_fingerprint: str = ""
    viewport: ViewportSignal | None = None
    logo_regions: list[RegionSignal] = Field(default_factory=list, max_length=8)
    credential_contexts: list[CredentialContext] = Field(default_factory=list, max_length=8)


class AnalyzeRequest(BaseModel):
    url: str = ""
    screenshot_base64: str
    dom_signals: DomSignals | None = None
    trigger: str = "manual"
    analysis_mode: Literal["browser", "screenshot_only"] = "browser"


def _disabled_uci_result():
    return {
        "enabled": False,
        "hint": "disabled",
        "reason_code": UCI_DISABLED_REASON,
        "reason": "UCI 标签及在线特征编码尚未重新验证，已暂停推理及决策融合",
        "model_loaded": False,
        "phish_prob": None,
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "models_ready": MODELS_READY,
        "models_load_error": MODELS_LOAD_ERROR,
        "cache_size": len(RESULT_CACHE),
        "policy_version": POLICY_VERSION,
        "uci_prefilter": _disabled_uci_result(),
        "model_version": MODEL_VERSION,
        "reference_version": _MODELS['brand_index'].version if MODELS_READY else None,
    }


def _url_host(url: str) -> str:
    """Accept a real HTTP(S) URL, never a desktop sentinel or guessed scheme."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        # Accessing port validates invalid/out-of-range port strings.
        parsed.port
        if parsed.scheme.lower() not in ("http", "https") or not host:
            return ""
        if any(c.isspace() for c in url) or any(c in host for c in "_/%\\"):
            return ""
        return host.encode("idna").decode("ascii")
    except (ValueError, UnicodeError):
        return ""


def _page_host(value: str) -> str:
    try:
        return value.lower().rstrip(".").encode("idna").decode("ascii")
    except UnicodeError:
        return ""


def _decode_screenshot(value: str) -> bytes:
    """Validate both base64 and image content before allowing any fast path."""
    from PIL import Image

    raw = value.split(",", 1)[1] if value.startswith("data:") else value
    img_bytes = base64.b64decode(raw, validate=True)
    if not img_bytes:
        raise ValueError("截图为空")
    with Image.open(io.BytesIO(img_bytes)) as img:
        img.verify()
    return img_bytes


def _match_cn_logo(img_bytes, logo_boxes):
    """Keep optional torch/torchvision imports off the HTTP service startup path."""
    global _CN_LAST_FAILURE
    import cn_brand_matcher

    with _CN_INIT_LOCK:
        # The legacy matcher remembers errors indefinitely; make retries follow
        # the same cooldown as the primary model without changing its module.
        if cn_brand_matcher._load_error:
            if time.monotonic() - _CN_LAST_FAILURE < MODEL_RETRY_DELAY_S:
                raise RuntimeError(cn_brand_matcher._load_error)
            cn_brand_matcher._load_error = None
        try:
            cn_brand_matcher.load_index()
        except Exception:
            _CN_LAST_FAILURE = time.monotonic()
            raise
    return cn_brand_matcher.match_logo_boxes(io.BytesIO(img_bytes), logo_boxes)


def _analyze_visual(img_bytes: bytes, url: str, dom=None) -> dict:
    """Recognize pixels independently of URL; page metadata only locates crops."""
    from PIL import Image
    load_models()
    dom = dom or {}
    m = _MODELS
    started = time.monotonic()
    with _INFERENCE_LOCK:
        boxes, classes, scores = _pred_rcnn_from_bytes(img_bytes, m["awl"])
        logos, _ = m["find_element_type"](boxes, classes, bbox_type="logo")
        blocks, _ = m["find_element_type"](boxes, classes, bbox_type="block")
        detection_time = time.monotonic() - started
        with Image.open(io.BytesIO(img_bytes)) as image:
            regions = choose_regions(image.size, logos, blocks, dom.get('logo_regions', []), dom.get('viewport'))
            evidence = inspect_regions(image, regions, m['brand_index'], m['embed_logo'])
            evidence['image_size'] = list(image.size)
            evidence['detections'] = [{'box': [round(float(v), 2) for v in box],
                'class': int(kind), 'score': round(float(score), 5)}
                for box, kind, score in list(zip(boxes, classes, scores))[:32]]
    hit = evidence['hit']
    candidates = [c for r in evidence['regions'] for c in r['candidates']]
    candidate = max(candidates, key=lambda c: c['similarity']) if candidates else None
    messages = {
        'NO_LOGO_REGION': '当前截图未找到可用 Logo，请回到品牌标志所在位置重新检测',
        'AMBIGUOUS_BRANDS': '多个品牌外观相近，暂时无法可靠区分',
        'MULTIPLE_BRANDS': '页面包含多个品牌，尚不能确定凭据表单所属品牌',
        'BRAND_UNCONFIRMED': '已检查图像候选，但视觉证据不足以确认品牌',
        'BRAND_CONFIRMED': '视觉参考库确认品牌匹配',
    }
    return {
        'brand': hit['name'] if hit else None,
        'official_domain': ','.join(hit['domains']) if hit else None,
        'brand_unconfirmed': hit is None,
        'brand_candidate': ({'name': candidate['name'], 'similarity': candidate['similarity'],
                             'source': 'siamese_reference'} if candidate else None),
        'visual_evidence': evidence,
        'reasons': [messages[evidence['status']]],
        'level_times': {'l1_logo_det': round(detection_time, 3),
                        'l2_logo_match': round(time.monotonic()-started-detection_time, 3)},
        'l1_logo_boxes': len(logos),
        'l1_block_fallback': any(r['source'] == 'page_block' for r in regions),
        'l2_matched': hit is not None,
    }


def _credential_brand_link(ds, evidence):
    """A payment/social-login badge plus an unrelated password is insufficient.

    Require a recognized, non-control image inside a compact credential container
    that names the same brand. Text alone can never produce a visual hit.
    """
    import re
    hit = evidence.get('hit') if evidence else None
    if not hit or not ds or not ds.viewport:
        return False
    size = evidence.get('image_size', [0, 0])
    if not size[0] or not size[1]:
        return False
    sx, sy = ds.viewport.width / size[0], ds.viewport.height / size[1]
    x1, y1, x2, y2 = hit['box']
    cx, cy = (x1+x2)*sx/2, (y1+y2)*sy/2
    aliases = hit.get('aliases', [hit['name']])
    for context in ds.credential_contexts:
        a, b, c, d = context.box
        if context.interactive or not (a <= cx <= c and b <= cy <= d):
            continue
        if c <= a or d <= b or c-a > ds.viewport.width*.95:
            continue
        text = context.text.casefold()
        for alias in aliases:
            alias = alias.casefold().strip()
            if len(alias) < 2:
                continue
            if re.search(r'[\u3400-\u9fff]', alias):
                matches = alias in text
            else:
                matches = re.search(r'(?<![a-z0-9])'+re.escape(alias)+r'(?![a-z0-9])', text) is not None
            if matches:
                return True
    return False


def _record_event(req, out):
    try:
        event = {
            "ts": round(time.time(), 3), "url": req.url[:160],
            "decision": out["decision"], "analysis_status": out["analysis_status"],
            "analysis_mode": out["analysis_mode"], "policy_version": POLICY_VERSION,
            "reason_codes": out["reason_codes"], "brand": out["brand"],
            "official_domain": out["official_domain"], "level_times": out["level_times"],
            "total_time_s": out["total_time_s"], "from_cache": out["from_cache"],
            "brand_unconfirmed": out.get("brand_unconfirmed", False),
            "brand_candidate": out.get("brand_candidate"),
            "visual_evidence": out.get("visual_evidence"),
            "credential_brand_linked": out.get("credential_brand_linked", False),
        }
        log_path = Path(os.environ.get('PHISHLENS_EVENT_LOG', str(Path(__file__).resolve().parent / 'event.log')))
        with open(log_path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    t_total = time.time()
    out = {
        "decision": "uncertain", "analysis_status": "completed",
        "analysis_mode": req.analysis_mode, "policy_version": POLICY_VERSION,
        "reason_codes": [], "reasons": [], "brand": None, "official_domain": None,
        "level_times": {}, "from_cache": False, "visual_error": None,
        "models_load_time_s": None, "uci_prefilter": _disabled_uci_result(),
        "l1_logo_boxes": 0, "l1_block_fallback": False, "l2_matched": False,
        "brand_unconfirmed": False, "brand_candidate": None,
    }
    cache_key = None

    def add_reason(code, reason):
        out["reason_codes"].append(code)
        out["reasons"].append(reason)

    def finish(status=None):
        if status is not None:
            out["analysis_status"] = status
        if out["analysis_status"] != "completed":
            out["decision"] = "uncertain"
        out["total_time_s"] = round(time.time() - t_total, 3)
        _record_event(req, out)
        if cache_key and out["analysis_status"] == "completed":
            RESULT_CACHE.put(cache_key, out)
        return out

    host = _url_host(req.url)
    ds = req.dom_signals
    context_complete = False
    if req.analysis_mode == "screenshot_only":
        add_reason("SCREENSHOT_ONLY", "仅有截图，无法确认真实网址和凭据表单；本次只提供视觉线索")
        out["analysis_status"] = "degraded"
        # A screenshot client cannot establish URL/DOM authenticity by supplying
        # guessed values. Ignore them for every security decision in this mode.
        host = ""
        ds = None
    else:
        if not host:
            add_reason("INVALID_URL", "缺少有效的 HTTP(S) 网址，无法完成浏览器安全判断")
            return finish("failed")
        if ds is not None and ds.page_domain and _page_host(ds.page_domain) != host:
            add_reason("PAGE_DOMAIN_MISMATCH", "页面声明域名与请求网址不一致，无法可靠判断")
            return finish("failed")
        context_complete = bool(ds is not None and ds.page_domain and ds.has_password_input is not None)
        if not context_complete:
            add_reason("BROWSER_CONTEXT_MISSING", "缺少可信的页面域名或密码输入框状态，无法完成判断")
            out["analysis_status"] = "degraded"

    try:
        img_bytes = _decode_screenshot(req.screenshot_base64)
    except Exception as exc:
        out["visual_error"] = f"{type(exc).__name__}: {exc}"
        add_reason("IMAGE_DECODE_FAILED", "截图为空、损坏或不是有效图像，检测未完成")
        return finish("failed")

    # Only complete browser context is eligible for cache or domain shortcuts.
    if context_complete:
        cache_key = build_cache_key(req, img_bytes)
        cached = RESULT_CACHE.get(cache_key)
        if cached is not None:
            cached["from_cache"] = True
            cached["level_times"] = {"l0_cache_hit": round(time.time() - t_total, 3)}
            cached["total_time_s"] = round(time.time() - t_total, 3)
            return cached
        t0 = time.time()
        whitelisted = hostname_matches(host, EXTRA_WHITELIST)
        out["level_times"]["l0_whitelist"] = round(time.time() - t0, 3)
        if whitelisted:
            out["decision"] = "allow"
            add_reason("WHITELIST_MATCH", "请求网址的域名在明确白名单内")
            return finish()

    try:
        visual = _analyze_visual(img_bytes, req.url if host else "", ds.model_dump() if ds else None)
        out["brand"] = visual["brand"]
        out["official_domain"] = visual["official_domain"]
        out["reasons"].extend(visual["reasons"])
        out["level_times"].update(visual["level_times"])
        for field in ("l1_logo_boxes", "l1_block_fallback", "l2_matched"):
            out[field] = visual[field]
        out["brand_unconfirmed"] = visual.get("brand_unconfirmed", False)
        out["brand_candidate"] = visual.get("brand_candidate")
        out["visual_evidence"] = visual.get("visual_evidence", {})
        out["models_load_time_s"] = _MODELS.get("load_time_s") if MODELS_READY else None
    except Exception as exc:
        out["visual_error"] = f"{type(exc).__name__}: {exc}"
        add_reason("VISUAL_ANALYSIS_FAILED", "视觉模型不可用或推理失败，检测未完成，请稍后重试")
        return finish("failed")

    if not context_complete:
        return finish("degraded")

    # Only an explicitly observed password field supplies credential evidence.
    # Login-form existence, external actions and suspicious URLs do not replace it.
    t3 = time.time()
    credential_observed = ds.has_password_input is True
    if credential_observed:
        add_reason("PASSWORD_INPUT_DETECTED", "页面存在密码输入框（索要凭据）")
    external_action = any(
        action and not hostname_matches(_page_host(action), [host])
        for action in ds.form_action_domains
    )
    if external_action:
        add_reason("EXTERNAL_FORM_ACTION", "表单 action 指向外部域名，需进一步核实")
    out["level_times"]["l3_dom"] = round(time.time() - t3, 3)
    t4 = time.time()
    sus = url_suspicion_score(req.url)
    out["reasons"].extend(sus["reasons"])
    if sus["score"]:
        out["reason_codes"].append("SUSPICIOUS_URL")
    out["level_times"]["l4_rules"] = round(time.time() - t4, 3)
    out["level_times"]["l05_uci_features"] = 0.0

    brand = out["brand"]
    official = out["official_domain"]
    mismatch = bool(brand and official and not hostname_matches(
        host, [d.strip() for d in official.split(",") if d.strip()]))
    linked = _credential_brand_link(ds, out.get("visual_evidence"))
    out["credential_brand_linked"] = linked
    if out["brand_unconfirmed"]:
        out["brand"] = out["official_domain"] = None
        add_reason("BRAND_UNCONFIRMED", "视觉证据不足，无法确认品牌；请人工核对网址，勿据此认定安全")
        detail = out.get("visual_evidence", {}).get("status")
        if detail and detail != "BRAND_UNCONFIRMED":
            out["reason_codes"].append(detail)
    elif mismatch:
        add_reason("BRAND_DOMAIN_MISMATCH", f"当前域名 {host} 与品牌 {brand} 正规域名不一致")
        if credential_observed and linked:
            out["decision"] = "block"
            add_reason("THREE_CONDITION_MATCH", "确认视觉品牌匹配、域名不一致且页面存在密码输入框")
        elif credential_observed:
            add_reason("CREDENTIAL_BRAND_UNLINKED", "检测到品牌及密码框，但尚未确认两者属于同一登录表单")
        else:
            add_reason("INSUFFICIENT_CREDENTIAL_EVIDENCE", "未确认密码凭据索取，需人工复核")
    elif (credential_observed or external_action) and sus["score"] >= 2:
        add_reason("REVIEW_REQUIRED", "存在页面风险信号且网址可疑，需人工复核")
    else:
        out["decision"] = "allow"
        add_reason("NO_RELIABLE_PHISHING_SIGNAL", "本次完整检测未发现可靠钓鱼信号")
    return finish()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
