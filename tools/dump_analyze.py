# Dump what /analyze actually returns (and what the cached verdict is)
import base64
import json
import urllib.request

from pathlib import Path

IMG = Path(__file__).resolve().parents[1] / "testset/fake/fake_alipay.png"

b64 = base64.b64encode(IMG.read_bytes()).decode()
payload = {
    "url": "http://127.0.0.1:8001/fake_alipay.html",
    "screenshot_base64": "data:image/png;base64," + b64,
    "dom_signals": {
        "page_domain": "127.0.0.1",
        "has_password_input": True,
        "has_login_form": True,
        "form_action_domains": [],
        "title": "支付宝 - 安全中心",
        "favicon": "",
        "brand_hints": ["支付宝"],
        "page_fingerprint": "dom|支付宝 - 安全中心|1|首页"
    },
    "trigger": "debug_dump",
}
req = urllib.request.Request(
    "http://127.0.0.1:8765/analyze",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
t0 = __import__("time").time()
with urllib.request.urlopen(req, timeout=120) as r:
    body = r.read().decode("utf-8")
print("ELAPSED:", round(__import__("time").time() - t0, 2), "s")
print("STATUS:", r.status)
obj = json.loads(body)
print(json.dumps({k: obj.get(k) for k in ("decision", "brand", "official_domain", "reasons", "l1_logo_det", "l2_logo_match", "l3", "l4", "from_cache", "cache_key") if k in obj}, ensure_ascii=False, indent=1))