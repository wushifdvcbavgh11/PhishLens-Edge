# Minimal probe: does Chrome actually load the unpacked extension?
import json
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "browser-extension"

opts = Options()
opts.add_argument(f"--load-extension={EXT}")
opts.add_argument("--no-first-run")
opts.add_argument("--disable-default-apps")
opts.add_argument("--window-size=1280,900")
opts.add_experimental_option("excludeSwitches", ["enable-automation"])

driver = None
try:
    driver = webdriver.Chrome(options=opts)
    # 1) open chrome://extensions and dump visible text
    driver.get("chrome://extensions/")
    time.sleep(3)
    body = driver.find_element("tag name", "body").text
    print("EXT_PAGE_HAS_PHISHLENS:", "PhishLens" in body)
    print("EXT_PAGE_SNIPPET:", json.dumps(body[:400], ensure_ascii=False))

    # 2) open the fake page and check for the content-script probe
    driver.get("http://127.0.0.1:8001/fake_alipay.html")
    time.sleep(3)
    probe = driver.execute_script(
        "const p = document.getElementById('__pleg_probe'); return p ? Object.assign({}, p.dataset) : null;"
    )
    print("PROBE_ON_PAGE:", json.dumps(probe, ensure_ascii=False))
    # content scripts may run in the main world under newer Chrome? try window marker
    marker = driver.execute_script("return window.__plegInjected === true;")
    print("WINDOW_MARKER:", marker)
finally:
    if driver:
        driver.quit()