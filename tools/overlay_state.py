# Minimal: load fake page, wait, dump overlay state + save screenshot
import json
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.edge.options import Options

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "browser-extension"

opts = Options()
opts.add_argument(f"--load-extension={EXT}")
opts.add_argument("--no-first-run")
opts.add_argument("--disable-default-apps")
opts.add_argument("--window-size=1280,900")
opts.add_experimental_option("excludeSwitches", ["enable-automation"])

driver = webdriver.Edge(options=opts)
try:
    driver.get("http://127.0.0.1:8001/fake_alipay.html")
    time.sleep(12)  # allow cold model load (~7s) + overlay render
    state = driver.execute_script("""
        const hosts = Array.from(document.documentElement.children);
        const out = { hosts: hosts.length, found: [] };
        for (const h of hosts) {
            if (h.shadowRoot) {
                const o = h.shadowRoot.querySelector('.pleg-overlay');
                const t = h.shadowRoot.querySelector('.pls-toast');
                out.found.push({
                    tag: h.tagName,
                    overlay: !!o,
                    overlayTitle: o ? (h.shadowRoot.querySelector('.pleg-title')?.textContent || '') : '',
                    toastDisplay: t ? t.style.display : 'no-toast',
                    toastText: t ? (t.querySelector('#msg')?.textContent || '') : '',
                    toastClass: t ? t.className : '',
                });
            }
        }
        return out;
    """)
    print("STATE:", json.dumps(state, ensure_ascii=False))
    driver.save_screenshot(str(ROOT / "docs" / "e2e_overlay_check.png"))
    print("SCREENSHOT:", ROOT / "docs" / "e2e_overlay_check.png")
finally:
    driver.quit()