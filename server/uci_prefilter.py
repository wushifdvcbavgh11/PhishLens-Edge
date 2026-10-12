"""
uci_prefilter.py — UCI phishing-website classifier as a fast feature pre-screen.

Traditional-feature signal for the PhishLens funnel, COMPLEMENTARY to the
visual brand-matching path. It extracts the 30 UCI features (where
computable from URL + DOM signals), fills unavailable ones with the neutral
value 0, and returns a phishing probability from the trained
HistGradientBoosting model (model/uci_phishing_clf.joblib).

Exposed:
    FEATURE_NAMES: list[str]  (30 names, model input order)
    extract_features(url, dom_signals=None) -> (vector, computed)
    classify(url, dom_signals=None) -> dict
"""

from __future__ import annotations

import json
import re
import socket
from pathlib import Path
from urllib.parse import urlparse

import joblib
import numpy as np

SERVER_DIR = Path(__file__).resolve().parent
REPO_ROOT = SERVER_DIR.parent
MODEL_PATH = REPO_ROOT / "model" / "uci_phishing_clf.joblib"
FEATURES_PATH = REPO_ROOT / "model" / "uci_phishing_features.json"

_NUM_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
SHORTENING_DOMAINS = {
    "bit.ly", "tinyurl.com", "goo.gl", "t.co", "is.gd", "buff.ly", "ow.ly",
    "s.id", "rb.gy", "cutt.ly", "shorturl.at", "rebrand.ly", "tiny.cc",
    "sniply.in", "shorten.rs", "bc.vc", "v.gd", "soo.gd", "shorte.st",
}


def _load_names() -> list[str]:
    data = json.loads(FEATURES_PATH.read_text(encoding="utf-8"))
    return list(data["features"])


FEATURE_NAMES: list[str] = _load_names()

_MODEL = None


def _get_model():
    global _MODEL
    if _MODEL is None:
        _MODEL = joblib.load(MODEL_PATH)
    return _MODEL


def _decode(v: str) -> str:
    # bytes-decoding safety (ARFF-derived labels), kept for robustness
    return v.decode() if isinstance(v, bytes) else v


def extract_features(url: str, dom_signals: dict | None = None):
    """Return (vector: np.ndarray[30], computed: list[str] of real features).

    Features that cannot be computed from the URL + DOM signals are set to
    the neutral value 0 and excluded from `computed`.
    """
    dom_signals = dom_signals or {}
    parsed = urlparse(url if "://" in url else "http://" + url)
    host = (parsed.hostname or "").lower().rstrip(".")
    domain_part = (parsed.netloc or "").lower()
    path = parsed.path or ""
    scheme = (parsed.scheme or "").lower()

    feats: dict[str, int] = {name: 0 for name in FEATURE_NAMES}
    computed: list[str] = []

    def setf(name: str, value: int):
        feats[name] = int(value)
        computed.append(name)

    # 1 having_IP_Address
    setf("having_IP_Address", 1 if host and _NUM_RE.fullmatch(host) else -1)

    # 2 URL_Length
    ulen = len(url)
    setf("URL_Length", 1 if ulen > 75 else (-1 if ulen < 54 else 0))

    # 3 Shortining_Service
    reg = ".".join(host.split(".")[-2:]) if host.count(".") >= 1 else host
    setf("Shortining_Service", 1 if reg in SHORTENING_DOMAINS else -1)

    # 4 having_At_Symbol
    setf("having_At_Symbol", 1 if "@" in url else -1)

    # 5 double_slash_redirecting — "//" after the protocol separator
    body = url.split("://", 1)[-1] if "://" in url else url
    setf("double_slash_redirecting", 1 if "//" in body else -1)

    # 6 Prefix_Suffix — dash inside the domain
    setf("Prefix_Suffix", 1 if "-" in host else -1)

    # 7 having_Sub_Domain — count subdomains via dot count
    dots = host.count(".")
    setf("having_Sub_Domain", 1 if dots >= 3 else (0 if dots == 2 else -1))

    # 8 SSLfinal_State — https seen -> trusted-ish; http -> unknown (0)
    setf("SSLfinal_State", -1 if scheme == "https" else 0)

    # 9 Domain_registeration_length (needs WHOIS) -> 0

    # 10 Favicon (needs DOM) -> 0

    # 11 port
    port = parsed.port
    setf("port", 1 if port is not None and port not in (80, 443) else -1)

    # 12 HTTPS_token — "https" text inside the domain part
    setf("HTTPS_token", 1 if "https" in domain_part else -1)

    # 13 Request_URL / 14 URL_of_Anchor / 15 Links_in_tags -> 0 (needs DOM)

    # 16 SFH — form action outside the page domain (DOM signal)
    form_actions = dom_signals.get("form_action_domains") or []
    page_domain = (dom_signals.get("page_domain") or host).lower().rstrip(".")
    if form_actions:
        outside = any(a and a != page_domain for a in form_actions)
        setf("SFH", 1 if outside else -1)

    # 17 Submitting_to_email -> 0 (needs DOM)

    # 18 Abnormal_URL -> 0 (needs WHOIS)

    # 19 Redirect — path contains an extra "//"
    setf("Redirect", 1 if "//" in path else 0)

    # 20-23 DOM-only JS features -> 0
    # 24 age_of_domain -> 0 (needs WHOIS)
    # 25 DNSRecord — quick DNS lookup (best-effort)
    try:
        socket.setdefaulttimeout(2.0)
        socket.gethostbyname(host)
        setf("DNSRecord", -1)
    except Exception:  # noqa: BLE001 — unresolved/unreachable, keep neutral 0
        pass

    # 26-30 external knowledge features -> 0

    vector = np.array([feats[name] for name in FEATURE_NAMES], dtype=np.float32)
    return vector, computed


def classify(url: str, dom_signals: dict | None = None) -> dict:
    """Return phishing probability + model opinion (fast, never raises on URL)."""
    vector, computed = extract_features(url, dom_signals)
    try:
        model = _get_model()
        proba = float(model.predict_proba(vector.reshape(1, -1))[0, 1])
        loaded = True
    except Exception as exc:  # noqa: BLE001
        proba = 0.5
        loaded = False
        last_error = f"{type(exc).__name__}: {exc}"
    else:
        last_error = None
    if proba >= 0.9:
        hint = "phish_likely"
    elif proba <= 0.1:
        hint = "benign_likely"
    else:
        hint = "inconclusive"
    return {
        "phish_prob": round(proba, 4),
        "hint": hint,
        "features_computed": len(computed),
        "features_computed_list": computed,
        "model_loaded": loaded,
        "error": last_error,
    }


if __name__ == "__main__":
    # quick smoke test
    tests = [
        ("https://www.paypal.com/signin", None),
        ("http://alipay-secure-login.top/verify", {"page_domain": "alipay-secure-login.top", "form_action_domains": ["evil.cc"]}),
    ]
    for u, ds in tests:
        r = classify(u, ds)
        print(f"{u}\n  -> prob={r['phish_prob']} hint={r['hint']} "
              f"computed={r['features_computed']}/{len(FEATURE_NAMES)}")