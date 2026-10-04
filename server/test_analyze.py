"""End-to-end smoke test for the local /analyze service using T3 sample screenshots."""
import base64
import json
import sys
import time
import urllib.request

SERVER = "http://127.0.0.1:8765"
BASE = r"C:/Users/wushi/.qianfan/workspace/sessions/458fc032984144e19e0b2a360d5a5c27/2026-09-28/new-chat/phishlens-edge/third_party/PhishIntention/datasets/test_sites"

CASES = [
    {
        "name": "cdcde (fake Google login)",
        "url": "https://accounts.g.cdcde.com/",
        "shot": f"{BASE}/accounts.g.cdcde.com/shot.png",
        "expect": "block",
        "dom": {"page_domain": "accounts.g.cdcde.com", "has_password_input": True, "has_login_form": True,
                "form_action_domains": ["accounts.g.cdcde.com"], "title": "Sign in - Google Accounts",
                "favicon": "", "brand_hints": ["Google"]},
    },
    {
        "name": "russianmastercach (fake Mastercard)",
        "url": "https://ru.russianmastercach-republick.xyz/",
        "shot": f"{BASE}/ru.russianmastercach-republick.xyz/shot.png",
        "expect": "block",
        "dom": {"page_domain": "ru.russianmastercach-republick.xyz", "has_password_input": True,
                "has_login_form": True, "form_action_domains": ["ru.russianmastercach-republick.xyz"],
                "title": "Mastercard", "favicon": "", "brand_hints": ["Mastercard"]},
    },
    {
        "name": "paypal.com (real PayPal, should pass)",
        "url": "https://www.paypal.com/",
        "shot": f"{BASE}/www.paypal.com/shot.png",
        "expect": "allow",
        "dom": {"page_domain": "www.paypal.com", "has_password_input": False, "has_login_form": False,
                "form_action_domains": [], "title": "PayPal", "favicon": "", "brand_hints": ["PayPal"]},
    },
]

for c in CASES:
    with open(c["shot"], "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    payload = json.dumps({
        "url": c["url"],
        "screenshot_base64": "data:image/png;base64," + b64,
        "dom_signals": c["dom"],
        "trigger": "test",
    }).encode()
    t0 = time.time()
    req = urllib.request.Request(SERVER + "/analyze", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            r = json.loads(resp.read().decode())
        ok = "PASS" if r["decision"] == c["expect"] else "FAIL"
        print(f"[{ok}] {c['name']}")
        print(f"   decision={r['decision']} (expect {c['expect']}) brand={r['brand']} official={r['official_domain']}")
        print(f"   total={r['total_time_s']}s L0={r['level_times'].get('l0_whitelist')} "
              f"L1={r['level_times'].get('l1_logo_det')} L2={r['level_times'].get('l2_logo_match')} "
              f"L3={r['level_times'].get('l3_dom')} L4={r['level_times'].get('l4_rules')}")
        print(f"   reasons={r['reasons']}")
        if r.get("visual_error"):
            print(f"   VISUAL_ERROR={r['visual_error']}")
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] {c['name']}: {e}")
    print()