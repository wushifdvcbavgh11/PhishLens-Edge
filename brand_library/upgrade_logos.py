"""Brand logo fetch v3 (quality-first, no destructive bug).

Pipeline per brand:
  A. restore from download_report.json sources (favicon baseline)
  B. Wikimedia Commons known SVG filename -> render 512px PNG (clean official logo)
  C. Commons search fallback (SVG preferred, rendered at 512)
  D. official apple-touch-icon (180px)
  E. keep the LARGEST valid image in the dir at the end.
NEVER delete an existing file on failure.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import requests
from PIL import Image

urllib3 = None

ROOT = Path("C:/Users/wushi/.qianfan/workspace/sessions/458fc032984144e19e0b2a360d5a5c27/2026-09-28/new-chat/phishlens-edge/brand_library")
LOGO_DIR = ROOT / "logos"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/147.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# Commons known filenames (tried in order)
COMMONS_FILES = {
    "alipay":    ["Alipay_logo.svg", "Alipay logo.svg"],
    "wechat":    ["WeChat_logo.svg", "WeChat logo.svg", "Wechat_logo.svg"],
    "qq":        ["QQ_logo.svg", "Tencent_QQ_logo.svg", "QQ logo.svg"],
    "icbc":      ["Industrial_and_Commercial_Bank_of_China_logo.svg", "ICBC_logo.svg", "ICBC logo.svg"],
    "ccb":       ["China_Construction_Bank_logo.svg", "CCB_logo.svg", "China Construction Bank logo.svg"],
    "abchina":   ["Agricultural_Bank_of_China_logo.svg", "ABC_logo.svg"],
    "boc":       ["Bank_of_China_logo.svg", "Bank_of_China_logo.svg"],
    "cmb":       ["China_Merchants_Bank_logo.svg", "CMB_logo.svg"],
    "bankcomm":  ["Bank_of_Communications_logo.svg", "Bank of Communications logo.svg"],
    "12306":     ["China_Railway_12306_logo.png", "China Railway 12306 logo.png"],
    "geeren":    [],
    "gjzwfw":    [],
    "chsi":      ["CHSI_logo.png", "学信网logo.png"],
    "exmail":    [],
    "qiye163":   ["NetEase_logo.svg", "NetEase_Mail_logo.png"],
    "alimail":   ["Alibaba_logo.svg", "Alimail_logo.png"],
    "dingtalk":  ["DingTalk_logo.svg", "DingTalk logo.png"],
    "feishu":    ["Feishu_logo.svg", "Feishu logo.png", "Lark_software_logo.png", "Lark_(software)_logo.svg"],
    "microsoft365": ["Microsoft_365_logo.svg", "Microsoft_365.svg"],
    "taobao":    ["Taobao_logo.svg", "Taobao logo.svg"],
    "jd":        ["JD.com_logo.svg", "JD_logo.svg", "JD.com logo.svg"],
    "pinduoduo": ["Pinduoduo_logo.svg", "Pinduoduo logo.svg"],
}

# search terms for Commons API fallback
COMMONS_TERMS = {
    "alipay": "Alipay logo", "wechat": "WeChat logo", "qq": "Tencent QQ logo",
    "icbc": "Industrial and Commercial Bank of China logo",
    "ccb": "China Construction Bank logo", "abchina": "Agricultural Bank of China logo",
    "boc": "Bank of China logo", "cmb": "China Merchants Bank logo",
    "bankcomm": "Bank of Communications logo", "12306": "China Railway 12306",
    "geeren": "Individual Income Tax China app", "gjzwfw": "National Government Service Platform China",
    "chsi": "CHSI China Higher Education Student Information", "exmail": "Tencent Exmail",
    "qiye163": "NetEase Mail", "alimail": "Alibaba Mail",
    "dingtalk": "DingTalk", "feishu": "Feishu LARK", "microsoft365": "Microsoft 365",
    "taobao": "Taobao", "jd": "JD.com", "pinduoduo": "Pinduoduo",
}

HOMES = {
    "alipay": "https://www.alipay.com/", "wechat": "https://weixin.qq.com/",
    "qq": "https://im.qq.com/", "icbc": "https://www.icbc.com.cn/",
    "ccb": "https://www.ccb.com/cn/home/indexv3.html",
    "abchina": "https://www.abchina.com/cn/", "boc": "https://www.boc.cn/",
    "cmb": "https://www.cmbchina.com/", "bankcomm": "https://www.bankcomm.com/",
    "12306": "https://www.12306.cn/", "geeren": "https://www.chinatax.gov.cn/",
    "gjzwfw": "https://gjzwfw.www.gov.cn/", "chsi": "https://www.chsi.com.cn/",
    "exmail": "https://exmail.qq.com/", "qiye163": "https://qiye.163.com/",
    "alimail": "https://alimail.alibaba.com/", "dingtalk": "https://www.dingtalk.com/",
    "feishu": "https://www.feishu.cn/", "microsoft365": "https://www.office.com/",
    "taobao": "https://www.taobao.com/", "jd": "https://www.jd.com/",
    "pinduoduo": "https://www.pinduoduo.com/",
}


def img_max_size(img_path: Path) -> int:
    try:
        im = Image.open(img_path)
        return max(im.size)
    except Exception:  # noqa: BLE001
        return 0


def fetch_bytes(url: str, timeout: int = 25) -> bytes | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, verify=False, allow_redirects=True)
        # reject non-image payloads (html error pages etc.)
        ct = r.headers.get("Content-Type", "")
        if "image" not in ct and not url.lower().endswith((".svg", ".png", ".jpg", ".jpeg", ".ico", ".webp")):
            if len(r.content) < 512 or b"<html" in r.content[:1024].lower():
                return None
        if r.status_code == 200 and len(r.content) > 200:
            return r.content
    except Exception:  # noqa: BLE001
        pass
    return None


def save_image(data: bytes, dest: Path) -> bool:
    """Write data to dest, validate it is an image. NEVER unlink dest on failure."""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(data)
        im = Image.open(tmp)
        im.verify()
        tmp.rename(dest)
        return True
    except Exception:  # noqa: BLE001
        try:
            tmp = dest.with_suffix(".tmp")
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


def official_candidates(home: str):
    """Yield (url, kind) for og:image / apple-touch-icon / icon."""
    try:
        r = requests.get(home, headers=HEADERS, timeout=20, verify=False, allow_redirects=True)
        if r.status_code != 200:
            return []
        html = r.text
        final_url = str(r.url)
        out = []
        for m in re.finditer(r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']', html, re.I):
            out.append((urljoin(final_url, m.group(1)), "og"))
        for m in re.finditer(r'<link[^>]+rel=["\'][^"\']*apple-touch-icon[^"\']*["\'][^>]+href=["\']([^"\']+)["\']', html, re.I):
            out.append((urljoin(final_url, m.group(1)), "apple"))
        for m in re.finditer(r'<link[^>]+rel=["\'][^"\']*icon[^"\']*["\'][^>]+href=["\']([^"\']+)["\']', html, re.I):
            out.append((urljoin(final_url, m.group(1)), "icon"))
        return out
    except Exception:  # noqa: BLE001
        return []


def commons_filepath(filename: str) -> str | None:
    """Render a Commons file (SVG->PNG 512). Returns URL or None."""
    url = f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(filename, safe='')}?width=512"
    data = fetch_bytes(url, timeout=30)
    if not data:
        return None
    return url


def commons_search(term: str) -> str | None:
    api = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query", "format": "json",
        "generator": "search", "gsrsearch": term, "gsrlimit": 10,
        "prop": "imageinfo", "iiprop": "url|mime|size",
    }
    try:
        r = requests.get(api, params=params, headers=HEADERS, timeout=25)
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
        cands = []
        for p in pages.values():
            ii = (p.get("imageinfo") or [{}])[0]
            url = ii.get("url", "")
            mime = ii.get("mime", "")
            title = p.get("title", "")
            w, h = ii.get("width", 0), ii.get("height", 0)
            low = title.lower()
            if not url or any(k in low for k in ("_icon", "icon-", "screenshot", "favicon", ".ico")):
                continue
            if mime == "image/svg+xml":
                cands.append((2, max(w, h), f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(title, safe='')}?width=512"))
            elif mime in ("image/png", "image/jpeg") and max(w, h) >= 128:
                cands.append((1, max(w, h), url))
        cands.sort(key=lambda x: (-x[0], -x[1]))
        if cands:
            return cands[0][2]
    except Exception:  # noqa: BLE001
        pass
    return None


def main():
    # restore baseline from download_report if exists
    rep_path = ROOT / "download_report.json"
    if rep_path.exists():
        try:
            rep = json.loads(rep_path.read_text(encoding="utf-8"))
            for item in rep:
                srcs = item.get("downloaded", [])
                if not srcs:
                    continue
                bid = item["brand_id"]
                d = LOGO_DIR / bid
                d.mkdir(parents=True, exist_ok=True)
                # fetch the SECOND source first (usually the icon/logo)
                for src in reversed(srcs):
                    dest = Path(src)
                    if dest.exists() and dest.name != "logo.png":
                        continue
                    data = fetch_bytes(src.replace("\\", "/"))
                    if data and save_image(data, dest):
                        break
        except Exception as e:  # noqa: BLE001
            print("restore warn:", e)

    report = {}
    for bid, files in COMMONS_FILES.items():
        d = LOGO_DIR / bid
        d.mkdir(parents=True, exist_ok=True)

        # A. already good?
        cur = max((img_max_size(f) for f in d.iterdir() if f.is_file()), default=0)
        if cur >= 128:
            report[bid] = {"status": "keep", "size": cur}
            continue

        # B. known Commons filename
        for fn in files:
            url = commons_filepath(fn)
            if url:
                for suffix in (".png", ".jpg"):
                    data = fetch_bytes(url)
                    if data and save_image(data, d / f"logo{suffix}"):
                        break
                if img_max_size(d / "logo.png") >= 128 or img_max_size(d / "logo.jpg") >= 128:
                    break

        cur = max((img_max_size(f) for f in d.iterdir() if f.is_file()), default=0)
        # C. Commons search
        if cur < 128 and COMMONS_TERMS.get(bid):
            u = commons_search(COMMONS_TERMS[bid])
            if u:
                data = fetch_bytes(u)
                if data and save_image(data, d / "logo.png"):
                    cur = max(cur, img_max_size(d / "logo.png"))

        # D. official apple-touch-icon (skip homepage og for banner risk)
        if cur < 128 and HOMES.get(bid):
            for url, kind in official_candidates(HOMES[bid]):
                if kind != "apple":
                    continue
                data = fetch_bytes(url)
                if data and save_image(data, d / "logo_apple.png"):
                    cur = max(cur, img_max_size(d / "logo_apple.png"))
                if cur >= 128:
                    break

        report[bid] = {"status": "ok" if cur >= 64 else "fail", "size": cur}
        time.sleep(0.2)

    (ROOT / "upgrade_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for b, r in report.items():
        print(f"{b:14s} {r['status']:5s} max={r['size']}px")


if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings()
    main()