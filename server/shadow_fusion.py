"""Advisory-only URL/visual/DOM fusion. Never mutates the enforced verdict."""
import json
import math
import urllib.request
from brand_evidence import summarize_brand_evidence

VERSION = "shadow-20261020.1"
ENDPOINT = "http://127.0.0.1:8000/api/v1/analyze/url"


def fetch_url_analysis(url):
    request = urllib.request.Request(
        ENDPOINT, data=json.dumps({"url": url}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    # Do not forward local analysis through system proxy settings.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=3) as response:
            data = json.loads(response.read(512_000))
        score, threshold = data["ml_score"], data["threshold"]
        if (data.get("status") != "completed" or data.get("url") != url
                or not all(type(x) in (float, int) and math.isfinite(x)
                           and 0 <= x <= 1 for x in (score, threshold))
                or type(data.get("phishtank_match")) is not bool
                or not isinstance(data.get("model_version"), str)):
            raise ValueError("invalid response")
        return data
    except Exception as exc:
        return {"status": "unavailable", "error_type": type(exc).__name__,
                "message": "URL 服务不可用或返回异常；原视觉判定保持不变"}


def combine(original, url_result, context_valid, page_url=""):
    """A high URL score alone requests review, never a phishing verdict."""
    base = original["decision"]
    complete = original.get("analysis_status") == "completed"
    form = original.get("general_risk") or {}
    url_ok = url_result.get("status") == "completed"
    suggestion = base if complete else "uncertain"
    reasons = []
    if base == "block":
        reasons.append("ORIGINAL_BLOCK_PRESERVED")
    elif url_ok and url_result.get("phishtank_match"):
        suggestion = "block"
        reasons.append("LOCAL_PHISHTANK_MATCH")
    elif url_ok and url_result["ml_score"] >= url_result["threshold"]:
        if context_valid and form.get("level") == "high":
            suggestion = "uncertain"
            reasons.append("URL_AND_CREDENTIAL_RISK")
        else:
            suggestion = "uncertain"
            reasons.append("URL_MODEL_REVIEW")
    elif context_valid and form.get("level") == "high":
        suggestion = "uncertain"
        reasons.append("CREDENTIAL_RISK_REVIEW")
    if not complete:
        reasons.append("VISUAL_OR_CONTEXT_INCOMPLETE")
    if not url_ok:
        reasons.append("URL_UNAVAILABLE")
    return {
        "mode": "shadow", "policy_version": VERSION,
        "status": "completed" if complete and url_ok else "partial",
        "enforced_decision": base, "suggested_decision": suggestion,
        "changed": suggestion != base, "reason_codes": reasons,
        # Additive explanation fields; reason_codes remains fusion-only for
        # existing clients. Never use these display fields to change a verdict.
        "original_reason_codes": list(original.get("reason_codes") or []),
        "url_analysis": url_result,
        "visual": {**summarize_brand_evidence(original, page_url or url_result.get("url", ""), context_valid),
                   "status": ("skipped" if "WHITELIST_MATCH" in original.get("reason_codes", [])
                             else original.get("analysis_status")),
                   "evidence_status": (original.get("visual_evidence") or {}).get("status"),
                   "credential_brand_linked": original.get("credential_brand_linked", False)},
        "form_risk": form,
        "notice": "并行试运行建议，不改变原拦截行为；分数未经联合概率校准",
    }
