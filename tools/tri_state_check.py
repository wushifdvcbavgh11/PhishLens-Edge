# Verify the three UI states end-to-end:
#   1. uncertain: generic portal page (no brand logo) -> orange dot + "存在疑点"
#   2. server offline: stop 8765 -> red dot + "服务未启动"
# Uses a single Edge driver per scenario (Edge still honors --load-extension).
import json
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.edge.options import Options

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "browser-extension"


def read_ui(driver):
    """Return {overlay, toast_kind, toast_text, toast_btn} across all shadow hosts."""
    return driver.execute_script("""
        const hosts = Array.from(document.documentElement.children);
        let toast = null;
        for (const h of hosts) {
            if (!h.shadowRoot) continue;
            const o = h.shadowRoot.querySelector('.pleg-overlay');
            if (o) return { overlay: true, title: h.shadowRoot.querySelector('.pleg-title')?.textContent || '' };
            const t = h.shadowRoot.querySelector('.pls-toast');
            if (t && t.style.display !== 'none' && !toast) {
                toast = { kind: t.className, text: t.querySelector('#msg')?.textContent || '',
                          btn: t.querySelector('.pls-act')?.textContent || '' };
            }
        }
        return { overlay: false, toast: toast };
    """)


def new_driver():
    opts = Options()
    opts.add_argument(f"--load-extension={EXT}")
    opts.add_argument("--no-first-run")
    opts.add_argument("--disable-default-apps")
    opts.add_argument("--window-size=1280,900")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    d = webdriver.Edge(options=opts)
    d.set_page_load_timeout(60)
    return d


def poll(driver, what, secs=45):
    deadline = time.time() + secs
    while time.time() < deadline:
        try:
            u = read_ui(driver)
        except Exception as e:  # noqa: BLE001
            time.sleep(3)
            continue
        if what(u):
            return u
        time.sleep(2)
    return None


ok = True
driver = None
try:
    if len(sys.argv) > 1 and sys.argv[1] == "offline":
        # ---- Scenario 2: server offline -> offline state ----
        driver = new_driver()
        driver.get("http://127.0.0.1:8001/fake_generic_portal.html")
        time.sleep(4)
        u = poll(driver, lambda x: (x.get("toast") or {}).get("kind", "").find("pls-offline") >= 0, secs=35)
        print("SCENARIO2_OFFLINE:", json.dumps(u, ensure_ascii=False) if u else "NOT_FOUND")
        if not u:
            ok = False
        else:
            driver.save_screenshot(str(ROOT / "docs" / "e2e_state_offline.png"))
            print("SCREENSHOT_OFFLINE_SAVED")
    else:
        # ---- Scenario 1: generic portal -> uncertain ----
        driver = new_driver()
        driver.get("http://127.0.0.1:8001/fake_generic_portal.html")
        time.sleep(4)
        u = poll(driver, lambda x: (x.get("toast") or {}).get("kind", "").find("pls-uncertain") >= 0)
        print("SCENARIO1_UNCERTAIN:", json.dumps(u, ensure_ascii=False) if u else "NOT_FOUND")
        if not u:
            ok = False
        else:
            driver.save_screenshot(str(ROOT / "docs" / "e2e_state_uncertain.png"))
            print("SCREENSHOT_UNCERTAIN_SAVED")
finally:
    if driver:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            pass

print("TRI_STATE_RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)