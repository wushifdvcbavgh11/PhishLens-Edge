"""T9 (fallback) - fetch brand logos from Clearbit / Google favicon CDNs."""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

ROOT = Path("C:/Users/wushi/.qianfan/workspace/sessions/458fc032984144e19e0b2a360d5a5c27/2026-09-28/new-chat/phishlens-edge/brand_library")
LOGO_DIR = ROOT / "logos"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/147.0 Safari/537.36"}

BRANDS = {
    "coinbase": ["coinbase.com"],
    "metamask": ["metamask.io"],
    "ledger": ["ledger.com"],
    "instagram": ["instagram.com"],
    "xfinity": ["xfinity.com"],
    "shopee": ["shopee.sg"],
    "steam": ["steampowered.com"],
    "paypal": ["paypal.com"],
}

def fetch(url: str, out: Path) -> bool:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200 and len(r.content) > 500:
            out.write_bytes(r.content)
            return True
    except Exception:
        pass
    return False

report = {}
for bid, doms in BRANDS.items():
    out_dir = LOGO_DIR / bid
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "logo_appstore.png"
    ok = False
    # 1) Clearbit
    for d in doms:
        if fetch(f"https://logo.clearbit.com/{d}", target):
            ok = True
            report[bid] = f"clearbit:{d}"
            break
    # 2) Google favicon (256px)
    if not ok:
        for d in doms:
            if fetch(f"https://www.google.com/s2/favicons?domain={d}&sz=256", target):
                ok = True
                report[bid] = f"googlefav:{d}"
                break
    print(f"[{'OK ' if ok else 'FAIL'}] {bid:12s} -> {target.name if ok else 'n/a'} src={report.get(bid)}")
    time.sleep(0.4)

(ROOT / "download_report_t9.json").write_text(
    json.dumps({"src": report}, ensure_ascii=False, indent=2), encoding="utf-8")