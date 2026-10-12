"""Desktop screenshot results and serialized scan lifecycle; no UI dependencies."""

from dataclasses import dataclass
import queue
import threading
import time


SCREENSHOT_LIMIT = "仅截图无法确认网址"
# "clear" reports only an absence of visible risk cues, never verified safety.
# Keep it blue in both desktop clients; no screenshot-only state is green.
RESULT_COLORS = {
    "idle": "#888888", "checking": "#f5a623", "clear": "#479bd4",
    "unknown": "#f5a623", "uncertain": "#f5a623", "warning": "#f44336",
}
_FAILURE_DETAILS = {
    "VISUAL_ANALYSIS_FAILED": ("视觉分析不可用", "请稍后重试；持续失败请检查本机服务"),
    "IMAGE_DECODE_FAILED": ("截图无效", "请切回待检测窗口后重新检测"),
    "INVALID_URL": ("缺少有效网址", "请在浏览器中打开 HTTP(S) 页面后重新检测"),
    "PAGE_DOMAIN_MISMATCH": ("页面信息不一致", "请刷新页面后重新检测"),
    "BROWSER_CONTEXT_MISSING": ("页面信息不足", "请使用浏览器插件重新检测"),
}


@dataclass(frozen=True)
class GuardResult:
    state: str
    text: str
    detail: str = SCREENSHOT_LIMIT
    brand: str = ""
    official_domain: str = ""


class CaptureUnavailable(Exception):
    """No usable foreground-window screenshot was captured."""


def screenshot_request(screenshot: str, trigger: str) -> dict:
    return {
        "analysis_mode": "screenshot_only",
        "url": "",
        "screenshot_base64": screenshot,
        "dom_signals": None,
        "trigger": trigger,
    }


def interpret_result(result) -> GuardResult:
    """An absent, legacy, incomplete or invalid response is never a clear result."""
    if not isinstance(result, dict):
        return GuardResult("unknown", "未确认：响应无效")
    status = result.get("analysis_status")
    if status in ("failed", "degraded"):
        codes = result.get("reason_codes")
        codes = (set(codes) if isinstance(codes, list)
                 and all(isinstance(code, str) for code in codes) else set())
        # Use stable reason codes; raw errors/reasons may contain server internals.
        for code, (summary, action) in _FAILURE_DETAILS.items():
            if code in codes:
                return GuardResult("unknown", f"未确认：{summary}", f"{action}；{SCREENSHOT_LIMIT}")
        if (status == "degraded" and result.get("analysis_mode") == "screenshot_only"
                and result.get("decision") == "uncertain" and codes == {"SCREENSHOT_ONLY"}
                and not result.get("visual_error")):
            return GuardResult(
                "uncertain", "未确认：仅完成截图分析",
                f"{SCREENSHOT_LIMIT}；请结合浏览器插件检测",
            )
    if status == "failed":
        return GuardResult("unknown", "未确认：检测失败")
    if status == "degraded":
        return GuardResult("unknown", "未确认：检测不完整")
    if status != "completed" or result.get("analysis_mode") != "screenshot_only":
        return GuardResult("unknown", "未确认：响应无效或版本不兼容")
    decision = result.get("decision")
    if decision == "allow":
        return GuardResult("clear", "未发现明确风险")
    if decision == "uncertain":
        return GuardResult("uncertain", "未确认")
    if decision == "block":
        brand = result.get("brand")
        official = result.get("official_domain")
        return GuardResult(
            "warning", "发现疑似仿冒线索", SCREENSHOT_LIMIT,
            brand[:40] if isinstance(brand, str) else "",
            official[:200] if isinstance(official, str) else "",
        )
    return GuardResult("unknown", "未确认：结果无效")


class GuardRunner:
    """One worker for periodic and manual scans; only current results reach the UI.

    UI code consumes the queue with drain_events. Tk callers must do so through
    root.after on the main thread. Stopping or restarting invalidates in-flight
    requests without creating another network/capture worker.
    """

    def __init__(self, analyze, interval=4):
        self._analyze = analyze
        self._interval = max(0.1, float(interval))
        self._condition = threading.Condition()
        self._events = queue.Queue()
        self._generation = 0
        self._running = False
        self._pending = False
        self._closed = False
        self._next_due = 0.0
        self.thread = None

    @property
    def running(self):
        with self._condition:
            return self._running

    def _ensure_worker(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._work, daemon=True)
            self.thread.start()

    def _enqueue(self, result):
        self._events.put((self._generation, result))

    def start(self):
        with self._condition:
            if self._closed or self._running:
                return
            self._generation += 1
            self._running = True
            self._pending = True
            self._enqueue(GuardResult("checking", "正在检测..."))
            self._ensure_worker()
            self._condition.notify_all()

    def stop(self):
        with self._condition:
            self._generation += 1
            self._running = False
            self._pending = False
            self._enqueue(GuardResult("idle", "已暂停", ""))
            self._condition.notify_all()

    def request_once(self):
        with self._condition:
            if self._closed:
                return
            self._generation += 1
            self._pending = True
            self._enqueue(GuardResult("checking", "正在检测..."))
            self._ensure_worker()
            self._condition.notify_all()

    def close(self):
        with self._condition:
            self._closed = True
            self._generation += 1
            self._running = False
            self._pending = False
            self._condition.notify_all()

    def drain_events(self, consume):
        # Serialize result delivery with stop/start, including the tray consumer.
        with self._condition:
            while True:
                try:
                    generation, result = self._events.get_nowait()
                except queue.Empty:
                    return
                if not self._closed and generation == self._generation:
                    consume(result)

    def _work(self):
        while True:
            with self._condition:
                while True:
                    if self._closed:
                        return
                    delay = self._next_due - time.monotonic()
                    if self._pending or (self._running and delay <= 0):
                        self._pending = False
                        generation = self._generation
                        self._enqueue(GuardResult("checking", "正在检测..."))
                        break
                    self._condition.wait(max(0, delay) if self._running else None)
            try:
                result = interpret_result(self._analyze())
            except CaptureUnavailable:
                result = GuardResult("unknown", "未确认：无法采集窗口")
            except Exception:
                result = GuardResult("unknown", "未确认：检测请求失败")
            with self._condition:
                if not self._closed and generation == self._generation:
                    self._enqueue(result)
                    self._next_due = time.monotonic() + self._interval
