# E2E: load extension -> open fake Alipay page -> expect warning overlay
# Note: Chrome 147 no longer honors --load-extension via command line for unpacked
# extensions; Edge still does. Prefer Edge, fall back to Chrome.
# Single-driver flow: verify extension, then navigate to the test page in the
# SAME driver. Re-creating the driver for a second Edge instance produces
# unstable sessions (invalid session id), so we avoid it.
import json
import sys
import time
from pathlib import Path

from selenium import webdriver

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "browser-extension"

driver = None
try:
    opts = None
    try:
        from selenium.webdriver.edge.options import Options as EdgeOptions
        opts = EdgeOptions()
        opts.add_argument(f"--load-extension={EXT}")
        opts.add_argument("--no-first-run")
        opts.add_argument("--disable-default-apps")
        opts.add_argument("--window-size=1280,900")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        driver = webdriver.Edge(options=opts)
        driver.get("chrome://extensions/")
        time.sleep(2)
        if "PhishLens" not in driver.find_element("tag name", "body").text:
            raise RuntimeError("extension not loaded in Edge")
        print("BROWSER: Edge (extension OK)")
    except Exception:  # noqa: BLE001
        if driver:
            try:
                driver.quit()
            except Exception:  # noqa: BLE001
                pass
            driver = None
        from selenium.webdriver.chrome.options import Options
        opts = Options()
        opts.add_argument(f"--load-extension={EXT}")
        opts.add_argument("--no-first-run")
        opts.add_argument("--disable-default-apps")
        opts.add_argument("--window-size=1280,900")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        driver = webdriver.Chrome(options=opts)
        print("BROWSER: Chrome (fallback)")
    driver.set_page_load_timeout(60)

    # 1. open the fake Alipay page (same driver keeps the extension alive)
    driver.get("http://127.0.0.1:8001/fake_alipay.html")
    time.sleep(3)
    print("PAGE_TITLE:", driver.title)

    # 1.5 wait for content script to load & dump the debug probe
    time.sleep(2)
    try:
        probe = driver.execute_script("""
            const p = document.getElementById('__pleg_probe');
            return p ? Object.assign({}, p.dataset) : null;
        """)
    except Exception:  # noqa: BLE001
        probe = None
    print("PROBE_AFTER_LOAD:", json.dumps(probe, ensure_ascii=False))

# 2. poll for the warning overlay / terminal toast (single loop, 90s cap)
    #    NOTE: scan ALL shadow hosts first (overlay takes priority); a host with
    #    the transient "checking" toast sits before the overlay host in DOM order,
    #    so returning per-host would never reach the overlay.
    deadline = time.time() + 90
    overlay_found = False
    last_ui = ""
    last_err = ""
    while time.time() < deadline:
        try:
            info = driver.execute_script("""
                const hosts = Array.from(document.documentElement.children);
                let toast = null;
                for (const h of hosts) {
                    if (!h.shadowRoot) continue;
                    const o = h.shadowRoot.querySelector('.pleg-overlay');
                    if (o) return { type: 'overlay', text: h.shadowRoot.querySelector('.pleg-title')?.textContent || '' };
                    const t = h.shadowRoot.querySelector('.pls-toast');
                    if (t && t.style.display !== 'none' && !toast) {
                        toast = { kind: t.className, text: t.querySelector('#msg')?.textContent || '' };
                    }
                }
                if (toast) return { type: 'toast', ...toast };
                return { type: 'none', text: '' };
            """)
        except Exception as e:  # noqa: BLE001
            last_err = str(e)[:150]
            time.sleep(3)
            continue
        if info.get("type") == "overlay":
            overlay_found = True
            last_ui = info.get("text", "")
            break
        if info.get("type") == "toast" and "pls-checking" not in info.get("kind", ""):
            last_ui = info.get("text", "")
            break
        time.sleep(2)
    if not overlay_found and not last_ui and last_err:
        print("POLL_ERR:", last_err)
    print("VERDICT_AFTER_LOAD:", json.dumps({"overlay_found": overlay_found, "ui": last_ui}, ensure_ascii=False))

    # 3. focus the password box (focusin trigger) - forced via JS since overlay
    #    may block real clicks, and focusin still fires.
    try:
        driver.execute_script("document.querySelector('input[type=\"password\"]').focus();")
        print("PASSWORD_FOCUSED: True")
    except Exception:  # noqa: BLE001
        print("PASSWORD_FOCUSED: False")

    # 4. screenshot for the record & dump result
    result = {
        "overlay_found": overlay_found,
        "overlay_title": last_ui if overlay_found else "",
        "last_status": "" if overlay_found else last_ui,
    }
    print("E2E_RESULT:" + json.dumps(result, ensure_ascii=False))
    driver.save_screenshot(str(ROOT / "docs" / "e2e_warning_overlay.png"))
    print("SCREENSHOT_SAVED")

    if not overlay_found:
        sys.exit(1)
finally:
    if driver:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            pass