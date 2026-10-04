"""T8 - isolated screenshot capture for the test set.

Uses Chrome headless with a throwaway per-run user-data-dir (no extensions,
no persistent cookies) and captures the visible viewport. Images are saved to
testset/phishing/. A per-sample record (url, ts, size) is appended to
testset/phishing/manifest.tsv. Safe-mode: no JS execution beyond page load,
no downloads, screenshot discarded after processing.

Usage: venv/Scripts/python.exe tools/capture_screenshots.py [limit]
"""
from __future__ import annotations

import csv
import os
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "testset" / "phishing"
MANIFEST = OUT_DIR / "manifest.tsv"
URLS_FILE = ROOT / "testset" / "openphish_urls.txt"

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

NAV_TIMEOUT_S = 20
VIEWPORT = (1280, 800)


def get_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    for exe in (CHROME, EDGE):
        if os.path.exists(exe):
            opts.binary_location = exe
            break
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--window-size=%dx%d" % VIEWPORT)
    opts.add_argument("--hide-scrollbars")
    opts.add_argument("--user-data-dir=" + tempfile.mkdtemp(prefix="phishlens_cap_"))
    return webdriver.Chrome(options=opts)


def main(limit: int):
    urls = [u.strip() for u in URLS_FILE.read_text(encoding="utf-8").splitlines() if u.strip()]
    random.Random(42).shuffle(urls)
    if limit:
        urls = urls[:limit]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    driver = get_driver()
    driver.set_page_load_timeout(NAV_TIMEOUT_S)
    new_manifest = not MANIFEST.exists()
    fh = open(MANIFEST, "a", encoding="utf-8", newline="")
    writer = csv.writer(fh, delimiter="\t")
    if new_manifest:
        writer.writerow(["idx", "url", "status", "ts", "bytes", "title"])
    ok = 0
    for idx, url in enumerate(urls):
        name = f"phish_{idx:03d}.png"
        path = OUT_DIR / name
        if path.exists():
            ok += 1
            continue
        try:
            driver.get(url)
            time.sleep(2.0)  # let late JS render settle
            driver.save_screenshot(str(path))
            size = path.stat().st_size
            title = (driver.title or "")[:80]
            writer.writerow([idx, url, "ok", time.strftime("%Y-%m-%d %H:%M:%S"), size, title])
            fh.flush()
            ok += 1
            print(f"[{idx}] OK  {url[:70]}  {size} B  title={title!r}", flush=True)
        except Exception as exc:  # noqa: BLE001
            writer.writerow([idx, url, f"err:{type(exc).__name__}", time.strftime("%Y-%m-%d %H:%M:%S"), 0, ""])
            fh.flush()
            print(f"[{idx}] ERR {type(exc).__name__} {url[:60]}", flush=True)
        if ok >= limit:
            break
    fh.close()
    try:
        driver.quit()
    except Exception:  # noqa: BLE001
        pass
    print(f"\ndone: {ok} screenshots in {OUT_DIR}")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    main(limit)