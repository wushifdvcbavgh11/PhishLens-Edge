"""
Brand library builder (T7): metadata + logo downloader.

Downloads official logos for the 23 first-batch brands (per the competition
plan section 7.2) from their official websites (favicon / apple-touch-icon /
og:image), records the source URL for THIRD_PARTY_LICENSES, and writes
brands.json.

Usage: python build_brand_library.py [--site-urls-url none]  (download step)
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
LOGO_DIR = ROOT / "logos"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/147.0.0.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# brand_id: (display name, category, legit domains, homepage)
BRANDS = {
    "alipay":       ("支付宝", "payment", ["alipay.com", "alipay.cn", "alipayobjects.com"], "https://www.alipay.com/"),
    "wechat":       ("微信", "social", ["weixin.qq.com", "qq.com"], "https://weixin.qq.com/"),
    "qq":           ("QQ", "social", ["im.qq.com", "qq.com"], "https://im.qq.com/"),
    "icbc":         ("工商银行", "bank", ["icbc.com.cn"], "https://www.icbc.com.cn/"),
    "ccb":          ("建设银行", "bank", ["ccb.com", "ccb.com.cn"], "https://www.ccb.com/"),
    "abchina":      ("农业银行", "bank", ["abchina.com", "abchina.com.cn"], "https://www.abchina.com/"),
    "boc":          ("中国银行", "bank", ["boc.cn"], "https://www.boc.cn/"),
    "cmb":          ("招商银行", "bank", ["cmbchina.com"], "https://www.cmbchina.com/"),
    "bankcomm":     ("交通银行", "bank", ["bankcomm.com"], "https://www.bankcomm.com/"),
    "12306":        ("12306", "gov", ["12306.cn"], "https://www.12306.cn/"),
    "geeren":       ("个人所得税", "gov", ["itax.gov.cn", "chinatax.gov.cn"], "https://etax.chinatax.gov.cn/"),
    "gjzwfw":       ("国家政务服务平台", "gov", ["gjzwfw.www.gov.cn", "zwfw.www.gov.cn", "www.gov.cn"], "https://gjzwfw.www.gov.cn/"),
    "chsi":         ("学信网", "gov", ["chsi.com.cn"], "https://www.chsi.com.cn/"),
    "exmail":       ("腾讯企业邮", "office", ["exmail.qq.com"], "https://exmail.qq.com/"),
    "qiye163":      ("网易企业邮", "office", ["qiye.163.com"], "https://qiye.163.com/"),
    "alimail":      ("阿里邮箱", "office", ["alimail.alibaba.com", "aliyun.com"], "https://alimail.alibaba.com/"),
    "dingtalk":     ("钉钉", "office", ["dingtalk.com"], "https://www.dingtalk.com/"),
    "feishu":       ("飞书", "office", ["feishu.cn", "larksuite.com"], "https://www.feishu.cn/"),
    "microsoft365": ("Microsoft 365", "office", ["microsoft.com", "office.com", "live.com"], "https://www.microsoft365.com/"),
    "taobao":       ("淘宝", "ecommerce", ["taobao.com"], "https://www.taobao.com/"),
    "jd":           ("京东", "ecommerce", ["jd.com"], "https://www.jd.com/"),
    "pinduoduo":    ("拼多多", "ecommerce", ["pinduoduo.com", "yangkeduo.com"], "https://www.pinduoduo.com/"),
}

ICON_RE = re.compile(
    r'<link[^>]+rel=["\']([^"\']*(?:icon|apple-touch-icon)[^"\']*)["\']'
    r'[^>]+href=["\']([^"\']+)["\']', re.I
)
META_OG_RE = re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.I)


def pick_candidates(soup: BeautifulSoup, html: str) -> list[str]:
    cands: list[str] = []
    for link in soup.find_all("link"):
        rel = " ".join((link.get("rel") or [])).lower()
        href = link.get("href")
        if href and ("icon" in rel or "apple-touch" in rel):
            cands.append(href)
    if not cands:
        for m in ICON_RE.finditer(html):
            cands.append(m.group(2))
    og = soup.find("meta", property="og:image") or soup.find("meta", attrs={"property": "og:image"})
    if og and og.get("content"):
        cands.append(og["content"])
    return cands


def resolve(base: str, url: str) -> str:
    if url.startswith("http"):
        return url
    from urllib.parse import urljoin
    return urljoin(base, url)


def download_icon(brand_id: str, homepage: str) -> dict:
    out_dir = LOGO_DIR / brand_id
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {"brand_id": brand_id, "homepage": homepage, "downloaded": [], "sources": [], "error": None}
    try:
        r = requests.get(homepage, headers=HEADERS, timeout=15, allow_redirects=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        cands = pick_candidates(soup, r.text)

        # prefer apple-touch-icon (>=180px), then standard ico, then og:image
        seen = []
        for c in cands:
            url = resolve(homepage, c)
            if url in seen:
                continue
            seen.append(url)
            try:
                img = requests.get(url, headers=HEADERS, timeout=15)
                if img.status_code != 200 or len(img.content) < 300:
                    continue
                ext = os.path.splitext(urlparse(url).path)[1].lower()
                if ext not in (".png", ".jpg", ".jpeg", ".ico", ".webp", ".svg") or ext == ".svg":
                    # strip query then re-check
                    ext = ext or ".png"
                fname = f"logo{ext}" if ext in (".png", ".jpg", ".jpeg", ".ico", ".webp") else "logo.png"
                path = out_dir / fname
                path.write_bytes(img.content)
                result["downloaded"].append(str(path))
                result["sources"].append(url)
            except Exception as exc:  # noqa: BLE001
                continue
        if not result["downloaded"]:
            # fallback: favicon.ico at root
            url = resolve(homepage, "/favicon.ico")
            img = requests.get(url, headers=HEADERS, timeout=15)
            if img.status_code == 200 and len(img.content) > 100:
                (out_dir / "logo.ico").write_bytes(img.content)
                result["downloaded"].append(str(out_dir / "logo.ico"))
                result["sources"].append(url)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


if __name__ == "__main__":
    from urllib.parse import urlparse
    report = []
    ok, fail = 0, 0
    for bid, (name, cat, domains, home) in BRANDS.items():
        res = download_icon(bid, home)
        if res["downloaded"]:
            ok += 1
            print(f"[OK ] {bid:14s} -> {res['downloaded'][0]}")
        else:
            fail += 1
            print(f"[FAIL] {bid:14s} err={res['error']}")
        report.append(res)
        time.sleep(0.4)

    (ROOT / "download_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nok={ok} fail={fail} total={len(BRANDS)}")