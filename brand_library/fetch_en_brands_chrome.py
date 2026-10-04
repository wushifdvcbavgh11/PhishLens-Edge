"""T9 - fetch remaining EN brand logos via headless Chrome (uses system proxy,
which plain requests does not). Downloads favicon/apple-touch-icon into
brand_library/logos/<id>/.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

ROOT = Path("C:/Users/wushi/.qianfan/workspace/sessions/458fc032984144e19e0b2a360d5a5c27/2026-09-28/new-chat/phishlens-edge/brand_library")
LOGO_DIR = ROOT / "logos"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

BRANDS = {
    "coinbase": ["https://www.coinbase.com/apple-touch-icon.png", "https://www.coinbase.com/favicon.ico"],
    "ledger":   ["https://www.ledger.com/apple-touch-icon.png", "https://www.ledger.com/favicon.ico"],
    "instagram": ["https://www.instagram.com/apple-touch-icon.png", "https://www.instagram.com/favicon.ico"],
    "steam":    ["https://store.steampowered.com/apple-touch-icon.png", "https://store.steampowered.com/favicon.ico"],
}

import selenium.webdriver as wd  # noqa: E402
from selenium.webdriver.chrome.options import Options  # noqa: E402


def download_with_chrome(bid: str, urls: list[str]) -> Path | None:
    dl = LOGO_DIR / bid / "_dl"
    dl.mkdir(parents=True, exist_ok=True)
    opts = Options()
    opts.binary_location = CHROME
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--ignore-certificate-errors")
    prefs = {
        "download.default_directory": str(dl),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": False,
    }
    opts.add_experimental_option("prefs", prefs)
    drv = wd.Chrome(options=opts)
    try:
        for u in urls:
            try:
                drv.get(u)
                time.sleep(2.5)
            except Exception:
                continue
            files = list(dl.iterdir())
            if files:
                f = max(files, key=lambda x: x.stat().st_size)
                if f.stat().st_size > 500:
                    return f
    finally:
        drv.quit()
    return None


def to_png(src: Path, dst: Path) -> bool:
    from PIL import Image
    try:
        im = Image.open(src)
        im.convert("RGBA").save(dst, "PNG")
        return True
    except Exception:
        return False


if __name__ == "__main__":
    for bid, urls in BRANDS.items():
        out_dir = LOGO_DIR / bid
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / "logo_appstore.png"
        src = download_with_chrome(bid, urls)
        if src and to_png(src, target):
            print(f"[OK ] {bid:12s} {src.name} -> {target.name} ({target.stat().st_size}B)")
        else:
            print(f"[FAIL] {bid:12s} src={'n/a' if src is None else src}")
        time.sleep(0.5)