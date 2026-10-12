# Compare extension loading strategies on Chrome 147 / Edge 154
import json
import sys
import tempfile
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.edge.options import Options as EdgeOptions

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "browser-extension"


def check(driver, name):
    try:
        driver.get("chrome://extensions/")
        time.sleep(2.5)
        body = driver.find_element("tag name", "body").text
        has = "PhishLens" in body
        print(f"[{name}] ext_page_has_phishlens={has}")
        if not has:
            print(f"[{name}] snippet={json.dumps(body[:200], ensure_ascii=False)}")
        driver.get("http://127.0.0.1:8001/fake_alipay.html")
        time.sleep(3)
        probe = driver.execute_script(
            "const p = document.getElementById('__pleg_probe'); return p ? Object.assign({}, p.dataset) : null;"
        )
        print(f"[{name}] probe_on_page={json.dumps(probe, ensure_ascii=False)}")
    except Exception as e:  # noqa: BLE001
        print(f"[{name}] error: {e}")
    finally:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            pass


# --- A: Chrome + --load-extension + fresh --user-data-dir
try:
    opts = ChromeOptions()
    opts.add_argument(f"--load-extension={EXT}")
    opts.add_argument("--no-first-run")
    opts.add_argument("--disable-default-apps")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    with tempfile.TemporaryDirectory() as td:
        opts.add_argument(f"--user-data-dir={td}")
        driver = webdriver.Chrome(options=opts)
        check(driver, "chrome-A-fresh-profile")
except Exception as e:  # noqa: BLE001
    print(f"[chrome-A-fresh-profile] launch error: {e}")

# --- B: Edge + --load-extension
try:
    opts = EdgeOptions()
    opts.add_argument(f"--load-extension={EXT}")
    opts.add_argument("--no-first-run")
    opts.add_argument("--disable-default-apps")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    driver = webdriver.Edge(options=opts)
    check(driver, "edge-B")
except Exception as e:  # noqa: BLE001
    print(f"[edge-B] launch error: {e}")

# --- C: Chrome + --disable-extensions-except (pair with load-extension)
try:
    opts = ChromeOptions()
    opts.add_argument(f"--load-extension={EXT}")
    opts.add_argument(f"--disable-extensions-except={EXT}")
    opts.add_argument("--no-first-run")
    opts.add_argument("--disable-default-apps")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    driver = webdriver.Chrome(options=opts)
    check(driver, "chrome-C-pair")
except Exception as e:  # noqa: BLE001
    print(f"[chrome-C-pair] launch error: {e}")

print("ALL_DONE")