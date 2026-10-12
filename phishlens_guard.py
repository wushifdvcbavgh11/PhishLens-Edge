# PhishLens Edge Guard - Desktop Floating Guard (USB edition)
# A standalone desktop program with a floating window:
#   1. Floating window always visible (top-right corner)
#   2. Shows screenshot evidence and explicitly unconfirmed website identity
#   3. Auto-screenshots the active window every N seconds
#   4. Sends to local analysis service (127.0.0.1:8765)
#   5. On "block": window turns red + Windows toast notification
#
# Usage:
#   .venv\Scripts\python.exe phishlens_guard.py
#
# Packaging:
#   .venv\Scripts\python.exe -m PyInstaller --onefile --noconsole phishlens_guard.py

import tkinter as tk
import base64
import io
import requests
from PIL import Image
import os
import sys

from guard_result import CaptureUnavailable, GuardRunner, RESULT_COLORS, screenshot_request

# Windows-specific imports
try:
    import win32gui
    import win32ui
    import win32con
    import win32api
    WIN32_OK = True
except Exception:
    WIN32_OK = False

# --------------------------------------------------------------------------- config
SERVER = os.environ.get("PHISHLENS_SERVER", "http://127.0.0.1:8765")
INTERVAL_S = float(os.environ.get("PHISHLENS_INTERVAL", "4"))

# --------------------------------------------------------------------------- window capture

def capture_active_window():
    """Capture the currently focused window, return base64 PNG string."""
    if not WIN32_OK:
        return None
    hwnd = win32gui.GetForegroundWindow()
    if hwnd == 0:
        return None
    if win32gui.GetWindowText(hwnd) == "PhishLens":
        return None  # A click on this floating window is not page evidence.
    rect = win32gui.GetWindowRect(hwnd)
    width = rect[2] - rect[0]
    height = rect[3] - rect[1]
    if width < 200 or height < 100:
        return None

    hwndDC = win32gui.GetWindowDC(hwnd)
    if not hwndDC:
        return None
    try:
        mfcDC = win32ui.CreateDCFromHandle(hwndDC)
        saveDC = mfcDC.CreateCompatibleDC()
        saveBitMap = win32ui.CreateBitmap()
        saveBitMap.CreateCompatibleBitmap(mfcDC, width, height)
        saveDC.SelectObject(saveBitMap)
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


# --------------------------------------------------------------------------- floating window

class PhishLensFloatGuard:
    WINDOW_WIDTH = 360
    NAVY = "#101e36"
    SURFACE = "#f4f7fb"
    TEXT = "#17263e"
    MUTED = "#62718a"
    BLUE = "#2563eb"
    STATE_STYLES = {
        "idle": ("#64748b", "#eef2f7", "待检测"),
        "checking": ("#2563eb", "#eff6ff", "检测中"),
        "clear": ("#2563eb", "#eff6ff", "截图结果"),
        "unknown": ("#a15c08", "#fff7e8", "未确认"),
        "uncertain": ("#a15c08", "#fff7e8", "待核验"),
        "warning": ("#c83240", "#fff1f2", "风险线索"),
    }

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PhishLens")
        self.root.overrideredirect(True)           # No window decorations
        self.root.attributes("-topmost", True)     # Always on top
        self.root.configure(bg="#dbe3ee")

        # Position: top-right corner
        screen_w = self.root.winfo_screenwidth()
        self.root.geometry(f"{self.WINDOW_WIDTH}x360+{max(0, screen_w - self.WINDOW_WIDTH - 24)}+24")

        self.runner = GuardRunner(self._analyze_once, INTERVAL_S)
        self._closed = False
        self._resize_job = None
        self._dragging = False

        # Drag state
        self._drag_x = 0
        self._drag_y = 0
        self._root_x = 0
        self._root_y = 0

        self._build_ui()
        self._bind_events()
        self._schedule_resize()
        self.root.after(100, self._consume_results)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        self.frame = tk.Frame(self.root, bg=self.SURFACE,
                              highlightbackground="#dbe3ee", highlightthickness=1)
        self.frame.pack(fill=tk.BOTH, expand=True)

        self.header = tk.Frame(self.frame, bg=self.NAVY, padx=16, pady=14,
                               cursor="fleur")
        self.header.pack(fill=tk.X)
        logo = tk.Canvas(self.header, width=32, height=34, bg=self.NAVY,
                          highlightthickness=0, cursor="fleur")
        logo.pack(side=tk.LEFT, padx=(0, 10))
        logo.create_polygon(16, 1, 30, 7, 28, 22, 23, 28, 16, 33,
                            9, 28, 4, 22, 2, 7, fill="#3467b8", outline="")
        logo.create_oval(9, 9, 21, 21, outline="#ffffff", width=2)
        logo.create_line(20, 20, 25, 25, fill="#ffffff", width=2)
        wordmark = tk.Frame(self.header, bg=self.NAVY, cursor="fleur")
        wordmark.pack(side=tk.LEFT)
        title = tk.Label(wordmark, text="PhishLens", font=("Segoe UI", 14, "bold"),
                          fg="#ffffff", bg=self.NAVY, anchor="w", cursor="fleur")
        title.pack(anchor="w")
        subtitle = tk.Label(wordmark, text="EDGE GUARD  /  桌面守护",
                             font=("Microsoft YaHei UI", 8), fg="#aabbd5",
                             bg=self.NAVY, anchor="w", cursor="fleur")
        subtitle.pack(anchor="w", pady=(1, 0))
        self.close_button = tk.Button(
            self.header, text="×", command=self._exit, font=("Segoe UI", 16),
            bg=self.NAVY, fg="#aabbd5", activebackground="#263852",
            activeforeground="#ffffff", relief=tk.FLAT, bd=0,
            highlightthickness=0, cursor="hand2", padx=7, pady=0,
            takefocus=True,
        )
        self.close_button.pack(side=tk.RIGHT, anchor="n")
        self.close_button.bind("<Enter>", lambda e: self.close_button.config(bg="#263852"))
        self.close_button.bind("<Leave>", lambda e: self.close_button.config(bg=self.NAVY))
        self._drag_widgets = (self.header, logo, wordmark, title, subtitle)

        body = tk.Frame(self.frame, bg=self.SURFACE, padx=16, pady=16)
        body.pack(fill=tk.X)
        overview = tk.Frame(body, bg=self.SURFACE)
        overview.pack(fill=tk.X, pady=(0, 12))
        tk.Label(overview, text="当前窗口检测", font=("Microsoft YaHei UI", 10, "bold"),
                 fg=self.TEXT, bg=self.SURFACE).pack(side=tk.LEFT)
        self.mode_label = tk.Label(overview, text="手动模式",
                                    font=("Microsoft YaHei UI", 8),
                                    fg=self.MUTED, bg=self.SURFACE)
        self.mode_label.pack(side=tk.RIGHT)

        self.result_card = tk.Frame(body, bg="#ffffff", padx=14, pady=14,
                                    highlightbackground="#dfe6f0", highlightthickness=1)
        self.result_card.pack(fill=tk.X)
        result_top = tk.Frame(self.result_card, bg="#ffffff")
        result_top.pack(fill=tk.X)
        self.indicator = tk.Canvas(result_top, width=10, height=10,
                                   bg="#ffffff", highlightthickness=0)
        self.indicator.pack(side=tk.LEFT, padx=(0, 6))
        self._set_indicator("#64748b")
        tk.Label(result_top, text="检测状态", font=("Microsoft YaHei UI", 8),
                 fg=self.MUTED, bg="#ffffff").pack(side=tk.LEFT)
        self.state_badge = tk.Label(result_top, text="待检测",
                                     font=("Microsoft YaHei UI", 8),
                                     fg="#64748b", bg="#eef2f7", padx=7, pady=3)
        self.state_badge.pack(side=tk.RIGHT)

        self.status_label = tk.Label(
            self.result_card, text="准备好，开始检测", font=("Microsoft YaHei UI", 13, "bold"),
            fg=self.TEXT, bg="#ffffff", wraplength=292, justify=tk.LEFT, anchor="w",
        )
        self.status_label.pack(fill=tk.X, pady=(12, 5))
        self.brand_label = tk.Label(
            self.result_card, text="检测当前窗口的视觉线索，辅助识别疑似仿冒页面。",
            font=("Microsoft YaHei UI", 9), fg=self.MUTED, bg="#ffffff",
            wraplength=292, justify=tk.LEFT, anchor="w",
        )
        self.brand_label.pack(fill=tk.X)

        notice = tk.Frame(body, bg=self.SURFACE)
        notice.pack(fill=tk.X, pady=(12, 14))
        tk.Label(notice, text="网址身份尚未核实", font=("Microsoft YaHei UI", 8, "bold"),
                 fg="#596c85", bg=self.SURFACE, anchor="w").pack(fill=tk.X)
        tk.Label(notice, text="仅截图无法确认网址，请结合浏览器插件核验。",
                 font=("Microsoft YaHei UI", 8), fg=self.MUTED, bg=self.SURFACE,
                 wraplength=322, justify=tk.LEFT, anchor="w").pack(fill=tk.X, pady=(3, 0))

        actions = tk.Frame(body, bg=self.SURFACE)
        actions.pack(fill=tk.X)
        actions.columnconfigure(0, weight=1, uniform="action")
        actions.columnconfigure(1, weight=1, uniform="action")
        self.toggle_button = tk.Button(
            actions, text="开始检测", command=self.toggle,
            font=("Microsoft YaHei UI", 9, "bold"), fg="#ffffff", bg=self.BLUE,
            activebackground="#1d4ed8", activeforeground="#ffffff",
            relief=tk.FLAT, bd=0, highlightthickness=1,
            highlightbackground=self.BLUE, highlightcolor="#93b4fb",
            padx=9, pady=9, cursor="hand2",
        )
        self.toggle_button.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.scan_button = tk.Button(
            actions, text="立即检测", command=self._analyze_once_async,
            font=("Microsoft YaHei UI", 9), fg=self.TEXT, bg="#ffffff",
            activebackground="#e8eef8", activeforeground=self.TEXT,
            relief=tk.FLAT, bd=0, highlightthickness=1,
            highlightbackground="#d5dfed", highlightcolor="#93b4fb",
            padx=9, pady=9, cursor="hand2",
        )
        self.scan_button.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        self.toggle_button.bind("<Enter>", lambda e: self.toggle_button.config(bg="#1d4ed8"))
        self.toggle_button.bind("<Leave>", lambda e: self.toggle_button.config(bg=self.BLUE))
        self.scan_button.bind("<Enter>", lambda e: self.scan_button.config(bg="#eff4fb"))
        self.scan_button.bind("<Leave>", lambda e: self.scan_button.config(bg="#ffffff"))

        footer = tk.Frame(self.frame, bg="#edf2f8", padx=16, pady=8)
        footer.pack(fill=tk.X)
        tk.Label(footer, text=f"检测间隔 {INTERVAL_S:g} 秒", font=("Microsoft YaHei UI", 8),
                 fg=self.MUTED, bg="#edf2f8").pack(side=tk.LEFT)
        tk.Label(footer, text="拖动标题栏可移动", font=("Microsoft YaHei UI", 8),
                 fg=self.MUTED, bg="#edf2f8").pack(side=tk.RIGHT)

    def _set_indicator(self, color):
        self.indicator.delete("all")
        self.indicator.create_oval(1, 1, 9, 9, fill=color, outline="")

    def _update_ui(self, status, text, brand=""):
        color, tint, badge = self.STATE_STYLES.get(
            status, (RESULT_COLORS.get(status, "#64748b"), "#eef2f7", "待核验"))
        self._set_indicator(color)
        self.state_badge.config(text="已暂停" if status == "idle" else badge,
                                fg=color, bg=tint)
        self.result_card.config(highlightbackground=color if status == "warning" else "#dfe6f0")
        self.status_label.config(text=text, fg=color if status == "warning" else self.TEXT)
        self.brand_label.config(text=brand or "点击开始检测恢复自动检测，也可以立即检测一次。")
        self._schedule_resize()

    def _schedule_resize(self):
        if self._resize_job is None and not self._closed:
            self._resize_job = self.root.after_idle(self._fit_content)

    def _fit_content(self):
        self._resize_job = None
        if not self._closed:
            self.root.update_idletasks()
            self.root.geometry(f"{self.WINDOW_WIDTH}x{self.frame.winfo_reqheight()}")

    def _sync_controls(self):
        # Do not call this from _show_result: the runner holds its event lock there.
        automatic = self.running
        self.toggle_button.config(text="暂停检测" if automatic else "开始检测")
        self.mode_label.config(text="自动检测中" if automatic else "手动模式",
                               fg=self.BLUE if automatic else self.MUTED)

    # ------------------------------------------------------------------ events
    def _bind_events(self):
        for widget in self._drag_widgets:
            widget.bind("<ButtonPress-1>", self._on_press)
            widget.bind("<B1-Motion>", self._on_drag)
            widget.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind("<Button-3>", self._on_right_click)
        self.root.bind("<Escape>", lambda e: self._exit())
        self.root.protocol("WM_DELETE_WINDOW", self._exit)

    def _on_press(self, event):
        self._dragging = True
        self._drag_x = event.x_root
        self._drag_y = event.y_root
        self._root_x = self.root.winfo_x()
        self._root_y = self.root.winfo_y()

    def _on_drag(self, event):
        if not self._dragging:
            return
        dx = event.x_root - self._drag_x
        dy = event.y_root - self._drag_y
        self.root.geometry(f"+{self._root_x + dx}+{self._root_y + dy}")

    def _on_release(self, event):
        self._dragging = False

    def _on_right_click(self, event):
        menu = tk.Menu(self.root, tearoff=0, bg="#ffffff", fg=self.TEXT,
                       activebackground="#eaf1ff", activeforeground=self.BLUE,
                       font=("Microsoft YaHei UI", 9), relief=tk.FLAT, bd=1)
        if self.running:
            menu.add_command(label="暂停检测", command=self.stop)
        else:
            menu.add_command(label="开始检测", command=self.start)
        menu.add_command(label="立即检测一次", command=self._analyze_once_async)
        menu.add_separator()
        menu.add_command(label="退出", command=self._exit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ------------------------------------------------------------------ control
    @property
    def running(self):
        return self.runner.running

    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        self.runner.start()
        self.runner.drain_events(self._show_result)
        self._sync_controls()

    def stop(self):
        self.runner.stop()
        self.runner.drain_events(self._show_result)
        self._sync_controls()

    def _exit(self):
        self._closed = True
        self.runner.close()
        self.root.destroy()

    # ------------------------------------------------------------------ analysis
    def _consume_results(self):
        # Scheduled only by Tk's main thread; the worker never touches widgets.
        if not self._closed:
            self.runner.drain_events(self._show_result)
            self.root.after(100, self._consume_results)

    def _show_result(self, result):
        detail = result.detail
        if result.brand:
            detail = f"品牌线索：{result.brand}；{detail}"
        self._update_ui(result.state, result.text, detail)
        if result.state == "warning":
            self._show_notification(result)

    def _analyze_once_async(self):
        self.runner.request_once()
        self.runner.drain_events(self._show_result)

    def _analyze_once(self):
        shot = capture_active_window()
        if not shot:
            raise CaptureUnavailable()
        payload = screenshot_request(shot, "float_window")
        r = requests.post(f"{SERVER}/analyze", json=payload, timeout=60)
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------ notification
    def _show_notification(self, result):
        try:
            from win10toast import ToastNotifier
            toaster = ToastNotifier()
            official = result.official_domain
            body = "发现可疑视觉线索。仅截图无法确认网址，请核对浏览器地址后再输入账号密码。"
            if official:
                body += f"\n正规域名: {official}"
            toaster.show_toast(
                f"PhishLens 警告：疑似仿冒 {result.brand or '未知品牌'}",
                body,
                duration=10,
                threaded=True
            )
        except Exception:
            pass

    # ------------------------------------------------------------------ main loop
    def run(self):
        self.root.mainloop()


# --------------------------------------------------------------------------- entry point

def main():
    print(f"[PhishLens Float Guard] Starting...")
    print(f"  Server: {SERVER}")

    try:
        r = requests.get(f"{SERVER}/health", timeout=5)
        print(f"  Server health: {r.text}")
    except Exception as e:
        print(f"  WARNING: cannot reach server ({e})")
        print(f"  Please start the server first with:")
        print(f"    .venv\\Scripts\\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8765 --app-dir server")

    if not WIN32_OK:
        print("ERROR: Windows API not available. This program requires Windows with pywin32.")
        sys.exit(1)

    app = PhishLensFloatGuard()
    app.run()


if __name__ == "__main__":
    main()
