"""Desktop regression tests: no UI, Windows API, screenshot or real HTTP calls."""

import importlib.util
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from unittest.mock import MagicMock, patch

from guard_result import (
    CaptureUnavailable, GuardRunner, RESULT_COLORS, SCREENSHOT_LIMIT,
    interpret_result, screenshot_request,
)


def response(decision="allow", status="completed", mode="screenshot_only", **extra):
    return dict(analysis_status=status, analysis_mode=mode, decision=decision, **extra)


class ResultTests(unittest.TestCase):
    def test_only_completed_screenshot_allow_has_clear_wording(self):
        result = interpret_result(response())
        self.assertEqual(result.state, "clear")
        self.assertEqual(result.text, "未发现明确风险")
        self.assertEqual(result.detail, SCREENSHOT_LIMIT)
        self.assertNotIn("安全", result.text)
        self.assertEqual(RESULT_COLORS[result.state], "#479bd4")

    def test_failures_legacy_and_malformed_responses_never_clear(self):
        invalid = [
            None, [], "allow", {}, {"decision": "allow"},
            {"decision": "allow", "analysis_status": "completed"},
            response(status="degraded"), response(status="failed"),
            response(status="unknown"), response(mode="browser"),
            response(mode=None), response(decision="unknown"),
            response(decision=None), response(decision=["allow"]),
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                result = interpret_result(payload)
                self.assertEqual(result.state, "unknown")
                self.assertIn("未确认", result.text)
                self.assertEqual(result.detail, SCREENSHOT_LIMIT)

    def test_uncertain_and_warning_explain_screenshot_limit(self):
        uncertain = interpret_result(response("uncertain"))
        self.assertEqual(uncertain.state, "uncertain")
        self.assertIn("未确认", uncertain.text)
        warning = interpret_result(response("block", brand="Example"))
        self.assertEqual(warning.state, "warning")
        self.assertEqual(warning.brand, "Example")
        self.assertEqual(warning.detail, SCREENSHOT_LIMIT)
        for status in ("failed", "degraded"):
            self.assertEqual(interpret_result(response("block", status)).state, "unknown")

    def test_expected_screenshot_limit_is_distinct_from_analysis_failure(self):
        result = interpret_result(response(
            "uncertain", "degraded", reason_codes=["SCREENSHOT_ONLY"], visual_error=None,
        ))
        self.assertEqual(result.state, "uncertain")
        self.assertEqual(result.text, "未确认：仅完成截图分析")
        self.assertIn(SCREENSHOT_LIMIT, result.detail)
        self.assertIn("浏览器插件", result.detail)
        self.assertNotIn("失败", result.text)
        self.assertEqual(RESULT_COLORS[result.state], "#f5a623")

    def test_server_failures_show_actionable_reasons_without_raw_errors(self):
        cases = [
            ("VISUAL_ANALYSIS_FAILED", "视觉分析不可用", "稍后重试"),
            ("IMAGE_DECODE_FAILED", "截图无效", "切回待检测窗口"),
            ("INVALID_URL", "缺少有效网址", "HTTP(S)"),
            ("PAGE_DOMAIN_MISMATCH", "页面信息不一致", "刷新页面"),
            ("BROWSER_CONTEXT_MISSING", "页面信息不足", "浏览器插件"),
        ]
        for status in ("failed", "degraded"):
            for code, summary, action in cases:
                with self.subTest(status=status, code=code):
                    result = interpret_result(response(
                        "uncertain", status, reason_codes=["SCREENSHOT_ONLY", code],
                        reasons=["private traceback"], visual_error="private traceback",
                    ))
                    self.assertEqual(result.state, "unknown")
                    self.assertIn(summary, result.text)
                    self.assertIn(action, result.detail)
                    self.assertIn(SCREENSHOT_LIMIT, result.detail)
                    self.assertNotIn("private traceback", result.text + result.detail)

    def test_unexpected_or_malformed_degradation_never_claims_screenshot_completed(self):
        extra_cases = [
            dict(reason_codes=["SCREENSHOT_ONLY", "NEW_FAILURE"]),
            dict(reason_codes=["SCREENSHOT_ONLY"], visual_error="private traceback"),
            dict(reason_codes="SCREENSHOT_ONLY"), dict(reason_codes=None),
            dict(reason_codes=[{}, []]), dict(reason_codes=["SCREENSHOT_ONLY", {}]),
        ]
        for extra in extra_cases:
            with self.subTest(extra=extra):
                result = interpret_result(response("uncertain", "degraded", **extra))
                self.assertEqual(result.state, "unknown")
                self.assertEqual(result.text, "未确认：检测不完整")
        for status, mode, decision in (("failed", "screenshot_only", "uncertain"),
                                       ("degraded", "browser", "uncertain"),
                                       ("degraded", "screenshot_only", "allow")):
            with self.subTest(status=status, mode=mode, decision=decision):
                result = interpret_result(response(
                    decision, status, mode, reason_codes=["SCREENSHOT_ONLY"],
                ))
                self.assertEqual(result.state, "unknown")

    def test_request_never_invents_browser_evidence(self):
        payload = screenshot_request("pixels", "desktop_test")
        self.assertEqual(payload["analysis_mode"], "screenshot_only")
        self.assertEqual(payload["url"], "")
        self.assertIsNone(payload["dom_signals"])
        self.assertEqual(payload["screenshot_base64"], "pixels")


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.runners = []
        self.releases = []

    def tearDown(self):
        for runner in self.runners:
            runner.close()
        for release in self.releases:
            release.set()
        for runner in self.runners:
            if runner.thread:
                runner.thread.join(2)
                self.assertFalse(runner.thread.is_alive())

    def runner(self, analyze):
        runner = GuardRunner(analyze, interval=60)
        self.runners.append(runner)
        return runner

    def await_state(self, runner, state, seen):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            runner.drain_events(seen.append)
            if any(result.state == state for result in seen):
                return
            threading.Event().wait(0.005)
        self.fail(f"State {state!r} not delivered: {seen!r}")

    def test_start_is_idempotent_and_delivery_runs_on_consumer_thread(self):
        entered = threading.Event()
        release = threading.Event()
        self.releases.append(release)
        worker_ids = []

        def analyze():
            worker_ids.append(threading.get_ident())
            entered.set()
            release.wait(2)
            return response()

        runner = self.runner(analyze)
        runner.start()
        self.assertTrue(entered.wait(2))
        first_worker = runner.thread
        for _ in range(10):
            runner.start()
        self.assertIs(runner.thread, first_worker)
        release.set()
        seen = []
        self.await_state(runner, "clear", seen)
        self.assertEqual(len(worker_ids), 1)
        self.assertNotEqual(worker_ids[0], threading.get_ident())
        consumer_ids = []
        runner.stop()
        runner.drain_events(lambda result: consumer_ids.append(threading.get_ident()))
        self.assertEqual(consumer_ids, [threading.get_ident()])

    def test_stop_discards_late_result_and_preserves_paused_status(self):
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        self.releases.append(release)

        def analyze():
            entered.set()
            release.wait(2)
            finished.set()
            return response("block")

        runner = self.runner(analyze)
        runner.start()
        self.assertTrue(entered.wait(2))
        runner.stop()
        seen = []
        runner.drain_events(seen.append)
        release.set()
        self.assertTrue(finished.wait(2))
        # Allow completion to be queued if stop handling regresses.
        threading.Event().wait(0.05)
        runner.drain_events(seen.append)
        self.assertEqual([result.state for result in seen], ["idle"])
        self.assertFalse(runner.running)

    def test_restart_and_manual_requests_share_one_worker_drop_old_results(self):
        entered = threading.Event()
        release = threading.Event()
        self.releases.append(release)
        calls = []

        def analyze():
            calls.append(threading.get_ident())
            if len(calls) == 1:
                entered.set()
                release.wait(2)
                return response("block", brand="Stale")
            return response()

        runner = self.runner(analyze)
        runner.start()
        self.assertTrue(entered.wait(2))
        worker = runner.thread
        runner.stop()
        runner.start()
        for _ in range(10):
            runner.request_once()
        self.assertEqual(len(calls), 1)
        self.assertIs(runner.thread, worker)
        release.set()
        seen = []
        self.await_state(runner, "clear", seen)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(set(calls)), 1)
        self.assertNotIn("warning", [result.state for result in seen])

    def test_capture_and_http_exceptions_become_unknown(self):
        for exc in (CaptureUnavailable(), RuntimeError("HTTP 500")):
            with self.subTest(error=type(exc).__name__):
                def analyze(error=exc):
                    raise error
                runner = self.runner(analyze)
                runner.request_once()
                seen = []
                self.await_state(runner, "unknown", seen)
                self.assertFalse(runner.running)
                self.assertNotIn("clear", [result.state for result in seen])


class DesktopIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Import the real clients while replacing every external UI/network
        # dependency. Tests call only request construction and result delivery.
        cls.clients = []
        for name in ("phishlens_guard", "tray_guard"):
            replacements = {
                "requests": MagicMock(), "PIL": MagicMock(),
                "tkinter": types.ModuleType("tkinter"),
                "win32gui": None, "win32ui": None, "win32con": None,
                "win32api": None, "pystray": None, "win10toast": None,
            }
            path = Path(__file__).resolve().parents[1] / f"{name}.py"
            spec = importlib.util.spec_from_file_location(f"guard_test_{name}", path)
            module = importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules, replacements):
                spec.loader.exec_module(module)
            cls.clients.append(module)

    def request_function(self, module):
        if hasattr(module, "PhishLensFloatGuard"):
            instance = module.PhishLensFloatGuard.__new__(module.PhishLensFloatGuard)
            return instance._analyze_once
        return module._analyze_once

    def test_both_clients_send_screenshot_only_requests(self):
        for module in self.clients:
            with self.subTest(client=module.__name__):
                reply = MagicMock()
                reply.json.return_value = response()
                with patch.object(module, "capture_active_window", return_value="pixels"), \
                     patch.object(module.requests, "post", return_value=reply) as post:
                    self.assertEqual(self.request_function(module)(), response())
                payload = post.call_args.kwargs["json"]
                self.assertEqual(payload["analysis_mode"], "screenshot_only")
                self.assertEqual(payload["url"], "")
                self.assertIsNone(payload["dom_signals"])
                reply.raise_for_status.assert_called_once()

    def test_both_clients_propagate_capture_http_and_json_failures(self):
        for module in self.clients:
            call = self.request_function(module)
            with self.subTest(client=module.__name__, error="capture"):
                with patch.object(module, "capture_active_window", return_value=None), \
                     patch.object(module.requests, "post") as post:
                    with self.assertRaises(CaptureUnavailable):
                        call()
                    post.assert_not_called()
            for step in ("raise_for_status", "json"):
                with self.subTest(client=module.__name__, error=step):
                    reply = MagicMock()
                    getattr(reply, step).side_effect = ValueError("bad response")
                    with patch.object(module, "capture_active_window", return_value="pixels"), \
                         patch.object(module.requests, "post", return_value=reply):
                        with self.assertRaises(ValueError):
                            call()

    def test_tk_polling_consumes_on_main_thread_and_schedules_next_poll(self):
        module = self.clients[0]
        guard = module.PhishLensFloatGuard.__new__(module.PhishLensFloatGuard)
        guard.root = MagicMock()
        guard._closed = False
        guard.runner = GuardRunner(lambda: response(), interval=60)
        guard._update_ui = MagicMock()
        guard._show_notification = MagicMock()
        guard.runner.stop()
        guard._consume_results()
        guard._update_ui.assert_called_once_with("idle", "已暂停", "")
        guard.root.after.assert_called_once_with(100, guard._consume_results)
        guard._closed = True
        guard._consume_results()
        self.assertEqual(guard.root.after.call_count, 1)
        guard.runner.close()

    def test_tray_updates_for_uncertain_failures_and_completed_allow(self):
        module = self.clients[1]
        module.GuardState.icon = MagicMock()
        with patch.object(module, "_create_image", return_value="icon"), \
             patch.object(module, "show_warning_toast") as notify:
            for payload in (None, response("uncertain"), response(status="degraded"), response()):
                result = interpret_result(payload)
                module._show_result(result)
                self.assertIs(module.GuardState.last_decision, result)
                self.assertIn(result.text, module.GuardState.icon.title)
                self.assertIn(SCREENSHOT_LIMIT, module.GuardState.icon.title)
            notify.assert_not_called()

    def test_completed_allow_is_blue_in_both_desktop_presentations(self):
        result = interpret_result(response())
        module = self.clients[0]
        guard = module.PhishLensFloatGuard.__new__(module.PhishLensFloatGuard)
        guard._set_indicator = MagicMock()
        guard.status_label = MagicMock()
        guard.brand_label = MagicMock()
        guard.state_badge = MagicMock()
        guard.result_card = MagicMock()
        guard._schedule_resize = MagicMock()
        guard._show_notification = MagicMock()
        guard._show_result(result)
        guard._set_indicator.assert_called_once_with("#2563eb")
        guard.status_label.config.assert_called_once_with(text="未发现明确风险", fg=guard.TEXT)
        guard.brand_label.config.assert_called_once_with(text=SCREENSHOT_LIMIT)
        guard._show_notification.assert_not_called()
        tray = self.clients[1]
        tray.GuardState.icon = MagicMock()
        with patch.object(tray, "_create_image") as create_image:
            tray._show_result(result)
            create_image.assert_called_once_with("#479bd4")


if __name__ == "__main__":
    unittest.main()
