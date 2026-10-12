#!/usr/bin/env python3
"""
Fetch web-version logos for each brand from their official homepages.

Tries (in order):
  1. apple-touch-icon (120x120 or 180x180, usually high-quality PNG)
  2. favicon (fallback)
  3. first <img> with 'logo' in alt/src/class name

Outputs: brand_library/logos_web/<brand_id>/logo_web.png
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_DIR = REPO_ROOT / "brand_library"
OUT_DIR = LIB_DIR / "logos_web"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
TIMEOUT = 15


def load_brands() -> list[dict]:
    with open(LIB_DIR / "brands.json", "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def fetch_bytes(url: str) -> bytes | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code == 200:
            return r.content
    except Exception as exc:
        print(f"  [err] {url}: {exc}")
    return None


def save_logo(brand_id: str, data: bytes) -> Path | None:
    d = OUT_DIR / brand_id
    ensure_dir(d)
    path = d / "logo_web.png"
    # validate it's an image
    try:
        from io import BytesIO
        img = Image.open(BytesIO(data))
        img.verify()
        with open(path, "wb") as f:
            f.write(data)
        print(f"  -> saved {path} ({len(data)} bytes)")
        return path
    except Exception as exc:
        print(f"  [bad image] {brand_id}: {exc}")
        return None


def try_apple_touch_icon(html: str, base_url: str) -> str | None:
    """Look for <link rel='apple-touch-icon' ... href='...'>"""
    import re
    m = re.search(r'<link[^>]*rel=["\']apple-touch-icon[^"\']*["\'][^>]*>', html, re.I)
    if not m:
        m = re.search(r'<link[^>]*rel=["\']apple-touch-icon-precomposed["\'][^>]*>', html, re.I)
    if m:
        tag = m.group(0)
        hm = re.search(r'href=["\']([^"\']+)["\']', tag)
        if hm:
            return urljoin(base_url, hm.group(1))
    return None


def try_favicon(html: str, base_url: str) -> str | None:
    import re
    m = re.search(r'<link[^>]*rel=["\'][^"\']*icon["\'][^>]*>', html, re.I)
    if m:
        tag = m.group(0)
        hm = re.search(r'href=["\']([^"\']+)["\']', tag)
        if hm:
            return urljoin(base_url, hm.group(1))
    # fallback to /favicon.ico
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}/favicon.ico"


def fetch_web_logo(brand: dict) -> Path | None:
    brand_id = brand["id"]
    domains = brand.get("domains", [])
    if not domains:
        print(f"[skip] {brand_id}: no domains")
        return None
    homepage = f"https://{domains[0]}"
    print(f"\n[{brand_id}] {homepage}")

    # fetch homepage HTML
    html_bytes = fetch_bytes(homepage)
    if html_bytes is None:
        print(f"  [err] homepage unreachable")
        return None
    try:
        html = html_bytes.decode("utf-8", errors="ignore")
    except Exception:
        html = html_bytes.decode("gbk", errors="ignore")

    # strategy 1: apple-touch-icon
    icon_url = try_apple_touch_icon(html, homepage)
    if icon_url:
        data = fetch_bytes(icon_url)
        if data:
            print(f"  [apple-touch-icon] {icon_url}")
            return save_logo(brand_id, data)

    # strategy 2: favicon
    icon_url = try_favicon(html, homepage)
    if icon_url:
        data = fetch_bytes(icon_url)
        if data:
            print(f"  [favicon] {icon_url}")
            return save_logo(brand_id, data)

    print(f"  [miss] no logo found for {brand_id}")
    return None


def main():
    brands = load_brands()
    ok = 0
    miss = 0
    for b in brands:
        path = fetch_web_logo(b)
        if path:
            ok += 1
        else:
            miss += 1
        time.sleep(0.5)  # be polite
    print(f"\n{'='*40}")
    print(f"Done: {ok}/{len(brands)} succeeded, {miss} missed")
    print(f"Output dir: {OUT_DIR}")


if __name__ == "__main__":
    main()
