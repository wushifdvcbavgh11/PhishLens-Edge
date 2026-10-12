# PhishLens Edge Guard - System Tray Standalone Guard (USB-starter edition)
# Screenshot-only desktop assistant; cannot verify the browser's URL or DOM:
#   1. Lives in system tray (click to start/stop, right-click to exit)
#   2. Monitors the active foreground window every N seconds
#   3. Captures a screenshot of the active window
#   4. POSTs to the local analysis service (127.0.0.1:8765 /analyze)
#   5. On "block": shows a native Windows toast notification with brand + official domain
#
# Usage:
#   .venv\Scripts\python.exe tray_guard.py
#
# Packaging (optional, for USB stick):
#   .venv\Scripts\python.exe -m PyInstaller --onefile --noconsole tray_guard.py

from __future__ import annotations

import base64
import io
import os
import sys
import threading

import requests
from PIL import Image, ImageDraw

from guard_result import CaptureUnavailable, GuardRunner, RESULT_COLORS, screenshot_request

# Windows-specific: pywin32 for window enumeration & capture, pystray for tray icon
try:
    import win32gui
    import win32ui
    import win32con
    import win32api
    WIN32_OK = True
except Exception:
    WIN32_OK = False

try:
    import pystray
    from pystray import MenuItem as item
    PYSTRAY_OK = True
except Exception:
    PYSTRAY_OK = False

# --------------------------------------------------------------------------- config
SERVER = os.environ.get("PHISHLENS_SERVER", "http://127.0.0.1:8765")
INTERVAL_S = float(os.environ.get("PHISHLENS_INTERVAL", "4"))   # scan every 4s
MIN_WINDOW_WIDTH = 200
MIN_WINDOW_HEIGHT = 100

# --------------------------------------------------------------------------- helpers


def capture_active_window() -> str | None:
    """Capture the currently focused window, return base64 PNG string.
    Returns None if the window is too small or capture fails."""
    if not WIN32_OK:
        return None
    hwnd = win32gui.GetForegroundWindow()
    if hwnd == 0:
        return None
    # Skip our own tiny notification windows / task manager etc.
    rect = win32gui.GetWindowRect(hwnd)
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    if width < MIN_WINDOW_WIDTH or height < MIN_WINDOW_HEIGHT:
        return None

    # Capture via BitBlt (more reliable than PrintWindow on most systems)
    hwndDC = win32gui.GetWindowDC(hwnd)
    if not hwndDC:
        return None
    try:
        mfcDC = win32ui.CreateDCFromHandle(hwndDC)
        saveDC = mfcDC.CreateCompatibleDC()
        saveBitMap = win32ui.CreateBitmap()
        saveBitMap.CreateCompatibleBitmap(mfcDC, width, height)
        saveDC.SelectObject(saveBitMap)

        # BitBlt from window DC to memory DC
        saveDC.BitBlt((0, 0), (width, height), mfcDC, (0, 0), win32con.SRCCOPY)

        bmpinfo = saveBitMap.GetInfo()
        bmpstr = saveBitMap.GetBitmapBits(True)
        im = Image.frombuffer(
            "RGB",
            (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
            bmpstr, "raw", "BGRX", 0, 1,
        )
    except Exception:
        return None
    finally:
        try:
            win32gui.DeleteObject(saveBitMap.GetHandle())
        except Exception:
            pass
        try:
            saveDC.DeleteDC()
        except Exception:
            pass
        try:
            mfcDC.DeleteDC()
        except Exception:
            pass
        win32gui.ReleaseDC(hwnd, hwndDC)

    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


# --------------------------------------------------------------------------- Windows toast

try:
    from win10toast import ToastNotifier
    _toaster = ToastNotifier()
    TOAST_OK = True
except Exception:
    TOAST_OK = False
    _toaster = None


def show_warning_toast(brand: str, official_domain: str) -> None:
    title = f"PhishLens 警告：疑似仿冒 {brand}"
    body = (
        "仅截图无法确认网址，请核对浏览器地址。\n"
        f"正规域名: {official_domain}\n"
        f"请勿在此页面输入账号密码。"
    )
    if TOAST_OK and _toaster:
        try:
            _toaster.show_toast(title, body, duration=10, threaded=True)
            return
        except Exception:
            pass
    # Keep the warning in the tray if toasts are unavailable; do not block the
    # result consumer with a modal MessageBox while stop/start is waiting.


# --------------------------------------------------------------------------- analysis loop

class GuardState:
    icon = None  # pystray.Icon
    last_decision = None
    consumer_stop = threading.Event()


def _analyze_once() -> dict:
    shot = capture_active_window()
    if not shot:
        raise CaptureUnavailable()
    payload = screenshot_request(shot, "tray_guard")
    r = requests.post(f"{SERVER}/analyze", json=payload, timeout=60)
    r.raise_for_status()
    return r.json()


GuardState.runner = GuardRunner(_analyze_once, INTERVAL_S)


def _show_result(result):
    GuardState.last_decision = result
    detail = f"；{result.detail}" if result.detail else ""
    _update_icon_text(f"PhishLens {result.text}{detail}")
    if GuardState.icon:
        GuardState.icon.icon = _create_image(RESULT_COLORS.get(result.state, "#888888"))
    if result.state == "warning":
        show_warning_toast(result.brand or "未知品牌", result.official_domain or "未知")


def _consume_results():
    while not GuardState.consumer_stop.wait(0.1):
        GuardState.runner.drain_events(_show_result)


def start_guard():
    GuardState.runner.start()
    GuardState.runner.drain_events(_show_result)


def stop_guard():
    GuardState.runner.stop()
    GuardState.runner.drain_events(_show_result)


def _update_icon_text(text: str):
    if GuardState.icon:
        try:
            GuardState.icon.title = text
        except Exception:
            pass


# --------------------------------------------------------------------------- tray UI

def _create_image(color="#888888"):
    """Draw the PhishLens shield with an analysis-colored status dot."""
    size, scale = 64, 4
    image = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    dc = ImageDraw.Draw(image)

    def points(coords):
        return [(x * scale, y * scale) for x, y in coords]

    def box(coords):
        return tuple(value * scale for value in coords)

    # A constant brand silhouette keeps the small icon recognizable in every state.
    dc.polygon(
        points([(31, 3), (55, 12), (54, 31), (51, 41), (44, 51),
                (31, 60), (18, 51), (11, 41), (8, 31), (7, 12)]),
        fill="#7594bf",
    )
    dc.polygon(
        points([(31, 5), (53, 13), (52, 31), (49, 40), (42, 49),
                (31, 57), (20, 49), (13, 40), (10, 31), (9, 13)]),
        fill="#101e36",
    )
    dc.polygon(points([(31, 7), (50, 14), (49, 22), (31, 16), (12, 22), (11, 14)]),
               fill="#223e69")
    # A bold lens remains legible at Windows' 16px and 24px tray sizes.
    dc.ellipse(box((18, 17, 38, 37)), outline="#ffffff", width=4 * scale)
    dc.line(points([(36, 35), (44, 43)]), fill="#ffffff", width=5 * scale)
    dc.ellipse(box((41.5, 40.5, 46.5, 45.5)), fill="#ffffff")
    # Retain RESULT_COLORS exactly: a blue result is never presented as green safety.
    dc.ellipse(box((38, 37, 62, 61)), fill="#ffffff")
    dc.ellipse(box((40.5, 39.5, 59.5, 58.5)), fill=color)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def _on_start(icon, item):
    start_guard()


def _on_stop(icon, item):
    stop_guard()


def _on_exit(icon, item):
    stop_guard()
    GuardState.runner.close()
    GuardState.consumer_stop.set()
    icon.stop()


def _on_test(icon, item):
    """Manual trigger for a single analysis (useful for demo)."""
    GuardState.runner.request_once()
    GuardState.runner.drain_events(_show_result)


def main():
    if not WIN32_OK:
        print("ERROR: win32 modules not available. Run on Windows with pywin32 installed.")
        sys.exit(1)
    if not PYSTRAY_OK:
        print("ERROR: pystray not available. Install: python -m pip install pystray")
        sys.exit(1)

    print(f"[PhishLens Tray Guard] Starting...")
    print(f"  Server: {SERVER}")
    print(f"  Interval: {INTERVAL_S}s")
    print(f"  Toast available: {TOAST_OK}")

    # Test connectivity once at startup
    try:
        r = requests.get(f"{SERVER}/health", timeout=5)
        print(f"  Server health: {r.text}")
    except Exception as e:
        print(f"  WARNING: cannot reach server ({e}). Start the server first with:")
        print(f"    .venv\\Scripts\\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8765 --app-dir server")

    icon = pystray.Icon(
        "PhishLensGuard",
        _create_image(),
        "PhishLens Edge Guard",
        menu=pystray.Menu(
            item("开始守护", _on_start),
            item("暂停守护", _on_stop),
            item("立即检测一次", _on_test),
            item("退出", _on_exit),
        ),
    )
    GuardState.icon = icon
    threading.Thread(target=_consume_results, daemon=True).start()
    # Auto-start on launch
    start_guard()
    try:
        icon.run()
    finally:
        GuardState.runner.close()
        GuardState.consumer_stop.set()


if __name__ == "__main__":
    main()
