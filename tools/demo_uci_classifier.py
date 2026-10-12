"""
demo_uci_classifier.py — end-to-end demo for the UCI feature pre-screen.

Loads the trained UCI model (server.uci_prefilter.classify) and scores
(a) real phishing URLs harvested from OpenPhish (testset/openphish_urls.txt)
and (b) legitimate brand login URLs, printing a per-URL verdict table.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "server"))

from uci_prefilter import classify  # noqa: E402

OPENPHISH_TXT = REPO_ROOT / "testset" / "openphish_urls.txt"

LEGIT_URLS = [
    "https://www.paypal.com/signin",
    "https://login.alipay.com/",
    "https://www.icbc.com.cn/icbc/perbank/index.jsp",
    "https://www.baidu.com/",
    "https://github.com/login",
    "https://mail.qq.com/",
    "https://kyfw.12306.cn/otn/login/init",
]


def score_one(url: str, dom_signals: dict | None = None) -> None:
    """Single-URL demo: extract features and print verdict."""
    import time

    def _hint_cn(h: str) -> str:
        return {"phish_likely": "钓鱼倾向（phish_likely）",
                "benign_likely": "安全倾向（benign_likely）",
                "inconclusive": "不确定（inconclusive）"}.get(h, h)

    r = classify(url, dom_signals)
    print(f"URL          : {url}")
    print(f"phish_prob   : {r['phish_prob']}")
    print(f"hint         : {_hint_cn(r['hint'])}")
    print(f"features     : {r['features_computed']}/30 computed -> "
          f"{r['features_computed_list']}")
    if dom_signals:
        print(f"DOM signals  : {dom_signals}")
    print(f"model_loaded : {r['model_loaded']}")


def main() -> None:
    import sys as _sys

    if len(_sys.argv) > 1:
        url_arg = _sys.argv[1]
        dom = None
        if len(_sys.argv) > 2:
            # optional JSON string, e.g. '{"page_domain":"a.com","form_action_domains":["evil.cc"]}'
            import json as _json

            dom = _json.loads(_sys.argv[2])
        score_one(url_arg, dom)
        return

    phish_urls: list[str] = []
    if OPENPHISH_TXT.exists():
        for line in OPENPHISH_TXT.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                phish_urls.append(line)
            if len(phish_urls) >= 8:
                break

    rows = []
    for url in phish_urls:
        r = classify(url)
        rows.append((url, "phishing(OpenPhish)", r))
    for url in LEGIT_URLS:
        r = classify(url)
        rows.append((url, "legitimate", r))

    print(f"{'URL':<52} {'ground truth':<18} {'prob':>6}  hint")
    print("-" * 100)
    for url, gt, r in rows:
        print(f"{url:<52} {gt:<18} {r['phish_prob']:>6.3f}  {r['hint']}")

    # quick summary
    correct = 0
    for url, gt, r in rows:
        pred = r["hint"]
        if gt.startswith("phishing"):
            correct += int(pred == "phish_likely")
        else:
            correct += int(pred != "phish_likely")
    print(f"\nsummary: {correct}/{len(rows)} consistent with ground truth "
          f"(phish_likely for phish, not phish_likely for legit)")


if __name__ == "__main__":
    main()