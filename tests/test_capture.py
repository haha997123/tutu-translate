"""Region/OCR lifecycle checks. No test captures the user's desktop.

TUTU_OCR_SELFTEST=1 opts into recognition of generated sample images only.
"""
import os
from pathlib import Path
import queue
import threading
import types
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from capture import CaptureError, LocalOcr, NativeOcr, ScreenshotCapture, choose_text, crop_box, picker_self_test, rapid_text, self_test


ASSETS = Path(__file__).resolve().parents[1] / "assets"


class RegionMappingTests(unittest.TestCase):
    def test_negative_monitor_origin(self):
        self.assertEqual(crop_box((-1800, 100), (-1400, 300), (-1920, 0, 3840, 1080), (3840, 1080)),
                         (120, 100, 520, 300))

    def test_reversed_drag_and_monitor_above(self):
        self.assertEqual(crop_box((100, -50), (-100, -400), (-1920, -1080, 5760, 3240), (5760, 3240)),
                         (1820, 680, 2020, 1030))

    def test_dpi_image_ratio_and_clamping(self):
        self.assertEqual(crop_box((-2000, 100), (5000, 2000), (-1920, 0, 3840, 1080), (7680, 2160)),
                         (0, 200, 7680, 2160))

    def test_english_and_mixed_recognizer_selection(self):
        self.assertEqual(choose_text([{"language": "zh-Hans-CN", "text": "你好 Hello world"},
                                      {"language": "en-US", "text": "IfdF Hello world"}]), "你好 Hello world")
        self.assertEqual(choose_text([{"language": "zh-Hans-CN", "text": "HeIIo world"},
                                      {"language": "en-US", "text": "Hello world"}]), "Hello world")

    def test_native_chinese_spacing_cleanup(self):
        self.assertEqual(choose_text([{"language": "zh-Hans-CN", "text": "你 好 世 界 ， hello world"}]),
                         "你好世界， hello world")

    def test_blocks_keep_reading_order_and_paragraph_gap(self):
        def item(x, y, text):
            return [[[x, y], [x + 70, y], [x + 70, y + 20], [x, y + 20]], text, .99]
        blocks = [item(100, 10, "world."), item(10, 85, "Next paragraph."),
                  item(10, 10, "Hello"), item(10, 35, "Welcome to Tutu.")]
        self.assertEqual(rapid_text(blocks), "Hello world.\nWelcome to Tutu.\n\nNext paragraph.")


class CaptureLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.events = queue.Queue()
        self.app = Mock()
        self.app.after.return_value = "scheduled-capture"
        self.capture = ScreenshotCapture(self.app, self.events, ASSETS)
        self.capture.engine = Mock()

    def tearDown(self):
        self.capture.close()

    def test_constructor_never_captures(self):
        with patch("capture.ImageGrab.grab") as grab:
            capture = ScreenshotCapture(self.app, self.events, ASSETS)
            capture.close()
            grab.assert_not_called()

    def test_start_is_explicit_and_duplicate_does_not_capture(self):
        with patch("capture.ImageGrab.grab") as grab:
            self.assertTrue(self.capture.start())
            self.assertFalse(self.capture.start())
            self.app.withdraw.assert_called_once()
            self.assertEqual(self.app.after.call_args.args[0], 180)
            grab.assert_not_called()
        self.assertEqual(self.events.get_nowait()[1]["phase"], "selecting")

    def test_escape_before_capture_cancels_scheduled_callback(self):
        self.capture.start()
        callback = self.app.after.call_args.args[1]
        self.events.get_nowait()
        self.capture.cancel()
        self.app.after_cancel.assert_called_once_with("scheduled-capture")
        with patch("capture.ImageGrab.grab") as grab:
            callback()
            grab.assert_not_called()
        self.assertEqual(self.events.get_nowait()[1]["state"], "cancelled")
        self.assertFalse(self.capture.active)

    def test_cancel_discards_in_flight_result_without_any_tk_worker_calls(self):
        entered = threading.Event()
        release = threading.Event()
        def recognize(_image):
            entered.set()
            release.wait(3)
            return "late old text"
        self.capture.engine.recognize.side_effect = recognize
        self.capture._busy = True
        self.capture._token = 5
        job = threading.Thread(target=self.capture._recognize, args=(Image.new("RGB", (100, 40)), 5))
        job.start()
        self.assertTrue(entered.wait(1))
        self.capture.cancel()
        release.set()
        job.join(2)
        self.assertFalse(job.is_alive())
        states = []
        while not self.events.empty():
            states.append(self.events.get_nowait()[1]["state"])
        self.assertEqual(states, ["cancelled"])
        self.app.after.assert_not_called()
        self.app.deiconify.assert_not_called()

    def test_success_is_queued_and_does_not_touch_tk_from_worker(self):
        self.capture._busy = True
        self.capture.engine.recognize.return_value = "Hello world"
        job = threading.Thread(target=self.capture._recognize, args=(Image.new("RGB", (100, 40)), 0))
        job.start()
        job.join(2)
        self.assertEqual(self.events.get_nowait(), ("ocr", {"state": "done", "text": "Hello world", "error": None}))
        self.assertFalse(self.capture.active)
        self.app.after.assert_not_called()
        self.app.deiconify.assert_not_called()

    def test_empty_and_failed_ocr_have_clear_status(self):
        for value in ("", CaptureError("安装中文 OCR 组件")):
            if isinstance(value, Exception):
                self.capture.engine.recognize.side_effect = value
            else:
                self.capture.engine.recognize.return_value = value
            self.capture._recognize(Image.new("RGB", (100, 40)), 0)
            payload = self.events.get_nowait()[1]
            self.assertEqual(payload["state"], "done")
            self.assertTrue(payload["error"])

    def test_tiny_region_rejected_before_starting_helper(self):
        engine = NativeOcr(ASSETS)
        with patch.object(engine, "_spawn") as spawn:
            with self.assertRaises(CaptureError):
                engine.recognize(Image.new("RGB", (5, 80)))
            spawn.assert_not_called()
        engine.close()

    def test_physical_pointer_mapping_ignores_provisional_canvas_layout(self):
        self.capture._bounds = (-1920, -1080, 5760, 3240)
        self.capture.canvas = Mock()
        self.capture.canvas.winfo_width.return_value = 1
        self.capture.canvas.winfo_height.return_value = 1
        self.assertEqual(self.capture._canvas_point((-1800, -1000)), (120, 80))
        self.capture.canvas.winfo_width.return_value = 5760
        self.capture.canvas.winfo_height.return_value = 3240
        self.assertEqual(self.capture._canvas_point((-1800, -1000)), (120, 80))
        self.capture.canvas.winfo_width.assert_not_called()
        self.capture.canvas.winfo_height.assert_not_called()


class PortableOcrTests(unittest.TestCase):
    def test_vendor_is_loaded_lazily_and_receives_pixels_in_memory(self):
        worker = Mock(return_value=([[[[0, 0], [70, 0], [70, 20], [0, 20]], "Hello world", .98]], [.1]))
        vendor = Mock(return_value=worker)
        engine = LocalOcr(ASSETS)
        engine.native = Mock()
        with patch.dict("sys.modules", {"rapidocr_onnxruntime": types.SimpleNamespace(RapidOCR=vendor)}):
            vendor.assert_not_called()
            text = engine.recognize(Image.new("RGBA", (100, 40)))
        self.assertEqual(text, "Hello world")
        self.assertIsInstance(worker.call_args.args[0], Image.Image)
        self.assertEqual(worker.call_args.args[0].mode, "RGB")
        engine.native.recognize.assert_not_called()
        engine.close()

    def test_native_fallback_when_portable_component_cannot_load(self):
        engine = LocalOcr(ASSETS)
        engine.native = Mock()
        engine.native.recognize.return_value = "你好世界"
        vendor = Mock(side_effect=ImportError("missing runtime"))
        with patch.dict("sys.modules", {"rapidocr_onnxruntime": types.SimpleNamespace(RapidOCR=vendor)}):
            self.assertEqual(engine.recognize(Image.new("RGB", (100, 40))), "你好世界")
        engine.close()

    def test_blank_crop_does_not_require_native_language_pack(self):
        engine = LocalOcr(ASSETS)
        engine._portable = Mock(return_value=(None, None))
        engine.native = Mock()
        self.assertEqual(engine.recognize(Image.new("RGB", (100, 40))), "")
        engine.native.recognize.assert_not_called()
        engine.close()


@unittest.skipUnless(os.name == "nt", "native Windows Tk picker")
class PickerLayoutTests(unittest.TestCase):
    def test_actual_tk_maps_full_background_and_restores_selected_pixels(self):
        with patch("capture.ImageGrab.grab", side_effect=AssertionError("Picker regression must use only synthetic pixels")):
            result = picker_self_test(ASSETS)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["photo_initial"], [800, 500])
        self.assertEqual(result["canvas_mapped"], result["photo_mapped"])
        self.assertTrue(result["background_readable"])
        self.assertTrue(result["selection_original_brightness"])
        self.assertTrue(result["escape_cancelled"])
        self.assertTrue(result["right_click_cancelled"])
        self.assertFalse(result["screenshot_captured"])


@unittest.skipUnless(os.environ.get("TUTU_OCR_SELFTEST") == "1", "synthetic local OCR is opt-in")
class NativeSelfTest(unittest.TestCase):
    def test_synthetic_chinese_english_and_mixed(self):
        with patch("capture.ImageGrab.grab", side_effect=AssertionError("Tests must not capture a screen")):
            result = self_test(ASSETS)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["screenshot_captured"])


if __name__ == "__main__":
    unittest.main()
