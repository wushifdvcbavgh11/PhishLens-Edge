# Detailed timeline: probe + overlay + toast every 2s for 30s
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
    t0 = time.time()
    while time.time() - t0 < 40:
        state = driver.execute_script("""
            const hosts = Array.from(document.documentElement.children);
            const out = { hosts: [], probe: null };
            const p = document.getElementById('__pleg_probe');
            if (p) out.probe = Object.assign({}, p.dataset);
            for (const h of hosts) {
                if (h.shadowRoot) {
                    const o = h.shadowRoot.querySelector('.pleg-overlay');
                    const t = h.shadowRoot.querySelector('.pls-toast');
                    out.hosts.push({
                        overlay: !!o,
                        overlayTitle: o ? (h.shadowRoot.querySelector('.pleg-title')?.textContent || '') : '',
                        toastClass: t ? t.className : '',
                        toastText: t ? (t.querySelector('#msg')?.textContent || '') : '',
                    });
                }
            }
            return out;
        """)
        print(f"T+{time.time()-t0:.1f}s", json.dumps(state, ensure_ascii=False), flush=True)
        if any(h.get("overlay") for h in state.get("hosts", [])):
            print("OVERLAY_FOUND")
            break
        time.sleep(3)
finally:
    driver.quit()