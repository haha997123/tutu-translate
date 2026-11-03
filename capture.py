"""Local screenshot OCR with a frozen region picker and bundled Chinese/English OCR.

Nothing is captured until ``ScreenshotCapture.start()`` is explicitly invoked.
Images and recognized text only live in memory; the helper is local, with no
network calls, clipboard writes, screenshot files, or content logging.
"""
from __future__ import annotations

import atexit
import base64
import ctypes
from ctypes import wintypes
import io
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
import tkinter as tk

from PIL import Image, ImageDraw, ImageFont, ImageGrab, ImageTk


class CaptureError(RuntimeError):
    """An OCR error suitable for the app status line."""


def crop_box(start, end, bounds, image_size):
    """Convert physical desktop points to image pixels, including negative origins."""
    left, top, width, height = bounds
    image_width, image_height = image_size
    x1, x2 = sorted((start[0], end[0]))
    y1, y2 = sorted((start[1], end[1]))
    def x(value):
        return max(0, min(image_width, round((value - left) * image_width / width)))
    def y(value):
        return max(0, min(image_height, round((value - top) * image_height / height)))
    return x(x1), y(y1), x(x2), y(y2)


def choose_text(results):
    """Prefer Chinese for mixed text; use English spacing for English-only text."""
    texts = {item.get("language", ""): str(item.get("text", "")).strip()
             for item in results if isinstance(item, dict)}
    chinese = next((v for k, v in texts.items() if k.startswith("zh")), "")
    english = next((v for k, v in texts.items() if k.startswith("en")), "")
    text = chinese if re.search(r"[\u3400-\u9fff]", chinese) else english or chinese
    # Windows' Chinese recognizer inserts spaces between each Han character.
    text = re.sub(r"(?<=[\u3400-\u9fff]) +(?=[\u3400-\u9fff])", "", text)
    text = re.sub(r"(?<=[\u3400-\u9fff]) +(?=[，。！？；：、])", "", text)
    return text.strip()


class NativeOcr:
    """Persistent hidden Windows OCR helper, lazy-started on a background thread.

    ``recognize(image)`` is synchronous for callers to use on their own worker.
    ``probe()`` reports installed OCR languages. No image data goes in argv.
    """
    def __init__(self, assets_path):
        self.assets = Path(assets_path)
        self.languages = []
        self.max_dimension = 10000
        self._process = None
        self._inbox = None
        self._lock = threading.Lock()
        self._closed = False
        atexit.register(self.close)

    @staticmethod
    def _read(process, inbox):
        try:
            for line in process.stdout:
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        inbox.put(value)
                except (ValueError, TypeError):
                    pass
        except (OSError, ValueError):
            pass
        finally:
            inbox.put({"state": "error", "error": "worker_closed"})

    def _spawn(self):
        if self._closed:
            raise CaptureError("截图识别已关闭。")
        if self._process is not None and self._process.poll() is None:
            return
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        worker = self.assets / "ocr_worker.ps1"
        if os.name != "nt" or not worker.is_file() or not powershell.is_file():
            raise CaptureError("截图识别需要 Windows 10 或更新版本的 OCR 组件。")
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        process = subprocess.Popen(
            [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", str(worker)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=startup,
            shell=False,
        )
        self._process = process
        self._inbox = queue.Queue()
        threading.Thread(target=self._read, args=(process, self._inbox),
                         name="tutu-ocr-output", daemon=True).start()
        ready = self._receive(15)
        if ready.get("state") != "ready":
            self.cancel_current()
            raise CaptureError("Windows OCR 未就绪，请在系统语言设置中安装中文或英文的文字识别组件。")
        self.languages = ready.get("languages", [])
        self.max_dimension = max(100, int(ready.get("max_dimension", 10000)))

    def _receive(self, timeout):
        try:
            result = self._inbox.get(timeout=timeout)
        except queue.Empty:
            self.cancel_current()
            raise CaptureError("本地识别超时，请缩小截图范围后重试。") from None
        return result

    def probe(self):
        with self._lock:
            self._spawn()
            return {"languages": list(self.languages), "max_dimension": self.max_dimension}

    def recognize(self, image):
        if image.width < 12 or image.height < 12:
            raise CaptureError("截图范围太小啦，请框选完整的文字。")
        with self._lock:
            self._spawn()
            if not self.languages:
                raise CaptureError("未安装文字识别语言，请在 Windows 语言设置中添加中文或英文 OCR 组件。")
            bitmap = image.convert("RGB")
            # Small desktop text benefits from doubling; the native dimension
            # limit is queried rather than assuming every Windows build matches.
            largest = max(bitmap.size)
            scale = min(2.0 if largest < 1800 else 1.0,
                        self.max_dimension / largest, 5000 / largest)
            if scale != 1:
                bitmap = bitmap.resize((max(1, round(bitmap.width * scale)),
                                        max(1, round(bitmap.height * scale))), Image.Resampling.LANCZOS)
            data = io.BytesIO()
            bitmap.save(data, format="PNG")
            command = json.dumps({"action": "recognize", "image": base64.b64encode(data.getvalue()).decode("ascii")},
                                 separators=(",", ":"))
            try:
                self._process.stdin.write(command + "\n")
                self._process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError, AttributeError):
                self.cancel_current()
                raise CaptureError("本地识别组件已重启，请再截一次。") from None
            result = self._receive(25)
            if result.get("state") != "recognized":
                code = result.get("error")
                if code == "no_language":
                    raise CaptureError("请先在 Windows 语言设置中安装中文或英文的文字识别组件。")
                raise CaptureError("没能识别这张截图，请框选清晰的中英文文字后重试。")
            return choose_text(result.get("results", []))

    def cancel_current(self):
        """Interrupt OCR without taking its operation lock or blocking Tk."""
        process, self._process = self._process, None
        if process is None:
            return
        try:
            process.terminate()
        except OSError:
            pass
        # Pipe cleanup may block behind a read: finish on a daemon, never on Tk.
        def reap():
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            for stream in (process.stdin, process.stdout):
                try:
                    stream.close()
                except (AttributeError, OSError, ValueError):
                    pass
        threading.Thread(target=reap, name="tutu-ocr-cleanup", daemon=True).start()

    def close(self):
        self._closed = True
        self.cancel_current()
        try:
            atexit.unregister(self.close)
        except Exception:
            pass


def rapid_text(results):
    """Reassemble detected blocks in reading order, preserving paragraph gaps."""
    blocks = []
    for result in results or []:
        if len(result) < 3 or float(result[2]) < .5 or not str(result[1]).strip():
            continue
        box, text = result[0], str(result[1]).strip()
        xs, ys = [point[0] for point in box], [point[1] for point in box]
        blocks.append((min(xs), min(ys), max(xs), max(ys), text))
    rows = []
    for block in sorted(blocks, key=lambda b: (b[1], b[0])):
        height = max(1, block[3] - block[1])
        row = next((r for r in rows
                    if min(r["bottom"], block[3]) - max(r["top"], block[1]) >=
                    .5 * min(height, r["bottom"] - r["top"])), None)
        if row is None:
            rows.append({"top": block[1], "bottom": block[3], "blocks": [block]})
        else:
            row["blocks"].append(block)
            row["top"] = min(row["top"], block[1])
            row["bottom"] = max(row["bottom"], block[3])
    lines = []
    previous = None
    for row in sorted(rows, key=lambda r: r["top"]):
        if previous is not None and row["top"] - previous["bottom"] > 1.25 * max(1, previous["bottom"] - previous["top"]):
            lines.append("")
        line = " ".join(item[4] for item in sorted(row["blocks"], key=lambda b: b[0]))
        line = re.sub(r"(?<=[\u3400-\u9fff]) +(?=[\u3400-\u9fff，。！？；：、])", "", line)
        lines.append(line)
        previous = row
    return "\n".join(lines).strip()


class LocalOcr:
    """Bundled CPU-only RapidOCR, with native Windows OCR as a safe fallback.

    The wheel contains all three ONNX models. It never downloads at runtime,
    needs no OS language packs, and is imported only after OCR is requested.
    """
    def __init__(self, assets_path):
        self.native = NativeOcr(assets_path)
        self._portable = None
        self._portable_failed = False
        self._closed = False
        self._lock = threading.Lock()
        self.backend = None

    def _load(self):
        if self._closed:
            raise CaptureError("截图识别已关闭。")
        if self._portable is not None or self._portable_failed:
            return
        try:
            # Keep this import in the worker: ONNX/cv2 initialization must not
            # delay opening the main window or run on Tk's thread.
            from rapidocr_onnxruntime import RapidOCR
            self._portable = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1,
                                      use_cls=False, print_verbose=False,
                                      max_side_len=3200, det_limit_side_len=2560,
                                      det_limit_type="max")
            self.backend = "RapidOCR / ONNX CPU"
        except Exception:
            self._portable_failed = True

    def probe(self):
        with self._lock:
            self._load()
            if self._portable is not None:
                return {"engine": self.backend, "languages": ["zh", "en"],
                        "bundled_models": True, "max_dimension": 3200}
            details = self.native.probe()
            self.backend = "Windows.Media.Ocr"
            return {"engine": self.backend, "bundled_models": False, **details}

    def recognize(self, image):
        if image.width < 12 or image.height < 12:
            raise CaptureError("截图范围太小啦，请框选完整的文字。")
        with self._lock:
            self._load()
            engine = self._portable
            if engine is not None:
                try:
                    # A PIL input uses in-memory pixels in RapidOCR.LoadImage.
                    pixels = image.convert("RGB")
                    if max(pixels.size) < 1280:
                        pixels = pixels.resize((pixels.width * 2, pixels.height * 2), Image.Resampling.LANCZOS)
                    result, _timings = engine(pixels, use_cls=False)
                    text = rapid_text(result)
                    if text:
                        return text
                    return ""
                except Exception:
                    # A native component failure still leaves installed Windows
                    # OCR available; never send an image to a network service.
                    pass
            self.backend = "Windows.Media.Ocr"
            try:
                return self.native.recognize(image)
            except CaptureError as exc:
                raise CaptureError("本地 OCR 组件未就绪，请完整解压软件包后重试；也可在 Windows 语言设置中安装中英文文字识别组件。") from exc

    def cancel_current(self):
        # ONNX calls are bounded local CPU tasks. The picker invalidates their
        # result token immediately; do not wait for inference on the UI thread.
        self.native.cancel_current()

    def close(self):
        self._closed = True
        self.native.close()


class ScreenshotCapture:
    """Main-thread region picker; only the OCR worker runs outside Tk's thread.

    Queue events are ``('ocr', {'state': 'working'|'done'|'cancelled',
    'text': str, 'error': str|None})``. The app restores its window on completion.
    ``working`` includes ``phase='selecting'|'recognizing'`` for status text.
    """
    def __init__(self, app, callback_queue, assets_path):
        self.app = app
        self.events = callback_queue
        self.engine = LocalOcr(assets_path)
        self.overlay = None
        self.canvas = None
        self._image = None
        self._photo = None
        self._selection_photo = None
        self._start_point = None
        self._bounds = None
        self._pending_after = None
        self._token = 0
        self._busy = False
        self._closed = False
        self._guard = threading.Lock()

    @property
    def active(self):
        return self._busy

    def _emit(self, state, text="", error=None, **extra):
        if not self._closed:
            self.events.put(("ocr", {"state": state, "text": text, "error": error, **extra}))

    def start(self):
        """Invoke from the app's Tk thread, only following the user's command."""
        if self._closed or self._busy:
            return False
        self._token += 1
        token = self._token
        self._busy = True
        self._emit("working", phase="selecting")
        self.app.withdraw()
        self.app.update_idletasks()
        self._pending_after = self.app.after(180, lambda: self._take_screen(token))
        return True

    @staticmethod
    def _cursor():
        point = wintypes.POINT()
        user = ctypes.windll.user32
        position = getattr(user, "GetPhysicalCursorPos", user.GetCursorPos)
        position(ctypes.byref(point))
        return point.x, point.y

    def _take_screen(self, token):
        self._pending_after = None
        if self._closed or token != self._token:
            return
        try:
            user = ctypes.windll.user32
            context = None
            if hasattr(user, "SetThreadDpiAwarenessContext"):
                user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
                user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
                context = user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            try:
                self._bounds = tuple(user.GetSystemMetrics(i) for i in (76, 77, 78, 79))
                self._image = ImageGrab.grab(all_screens=True).convert("RGB")
            finally:
                if context:
                    user.SetThreadDpiAwarenessContext(context)
            # Use Tk's original DPI context when creating its window. CTk uses
            # per-monitor v1; creating just this window under v2 can trigger Tk
            # resize behavior that disagrees with the physical screen geometry.
            self._show_picker()
        except Exception:
            self._destroy_picker()
            self._busy = False
            self._emit("done", error="截图没有成功，请关闭安全桌面或远程限制后重试。")

    def _show_picker(self):
        left, top, width, height = self._bounds
        overlay = tk.Toplevel(self.app)
        self.overlay = overlay
        overlay.withdraw()
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        overlay.configure(bg="#362A45")
        overlay.geometry(f"{width}x{height}+0+0")
        canvas = tk.Canvas(overlay, width=width, height=height,
                           highlightthickness=0, borderwidth=0, cursor="crosshair", bg="#362A45")
        self.canvas = canvas
        canvas.pack(fill="both", expand=True)
        overlay.update_idletasks()
        overlay.deiconify()
        # geometry '-1920+0' means distance from the right in Tk. SetWindowPos
        # uses the actual physical virtual-screen origin, including left monitors.
        user = ctypes.windll.user32
        user.GetParent.argtypes = [wintypes.HWND]
        user.GetParent.restype = wintypes.HWND
        user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     wintypes.UINT]
        user.SetWindowPos.restype = wintypes.BOOL
        hwnd = user.GetParent(overlay.winfo_id()) or overlay.winfo_id()
        if not user.SetWindowPos(hwnd, ctypes.c_void_p(-1), left, top, width, height, 0x0040):
            raise CaptureError("截图选区窗口未能覆盖屏幕，请重试。")
        overlay.update_idletasks()
        # A newly mapped canvas can still report 1x1 until Tk processes its
        # Configure event. Build the frozen background at known physical screen
        # dimensions, never at those provisional widget dimensions.
        display = self._image.resize((width, height), Image.Resampling.BILINEAR)
        dim = Image.blend(display, Image.new("RGB", display.size, "#241C35"), .14)
        self._photo = ImageTk.PhotoImage(dim, master=overlay)
        canvas.create_image(0, 0, image=self._photo, anchor="nw", tags="background")
        # Put the hint beside the cursor's current monitor rather than between
        # monitors or at the unused corner of a virtual-screen bounding box.
        cx, cy = self._canvas_point(self._cursor())
        hint_width = min(490, width - 20)
        hx = max(10, min(width - hint_width - 10, cx - hint_width / 2))
        hy = max(10, min(height - 60, cy - 75))
        canvas.create_rectangle(hx, hy, hx + hint_width, hy + 44, fill="#FAF6FF", outline="#D9C8F2", width=2, tags="hint")
        canvas.create_text(hx + hint_width / 2, hy + 22, text="框选要翻译的文字  ·  Esc / 右键取消", fill="#6F4DA5",
                           font=("Microsoft YaHei UI", 13), tags="hint")
        canvas.bind("<ButtonPress-1>", self._press)
        canvas.bind("<B1-Motion>", self._drag)
        canvas.bind("<ButtonRelease-1>", self._release)
        canvas.bind("<Button-3>", lambda event: self.cancel())
        overlay.bind("<Escape>", lambda event: self.cancel())
        overlay.protocol("WM_DELETE_WINDOW", self.cancel)
        overlay.focus_force()
        overlay.grab_set()

    def _canvas_point(self, physical):
        left, top, _width, _height = self._bounds
        # The canvas and its frozen image use physical screen pixels. Keeping
        # this independent of winfo_width also prevents a first-drag mismatch
        # while the native window's Configure event is still pending.
        return physical[0] - left, physical[1] - top

    def _press(self, _event=None):
        self._start_point = self._cursor()
        self.canvas.delete("hint")
        self._drag()

    def _drag(self, _event=None):
        if self._start_point is None or self.canvas is None:
            return
        current = self._cursor()
        box = crop_box(self._start_point, current, self._bounds, self._image.size)
        x1, y1 = self._canvas_point(self._start_point)
        x2, y2 = self._canvas_point(current)
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        self.canvas.delete("selection")
        if box[2] > box[0] and box[3] > box[1]:
            clear = self._image.crop(box).resize((max(1, round(x2 - x1)), max(1, round(y2 - y1))), Image.Resampling.BILINEAR)
            self._selection_photo = ImageTk.PhotoImage(clear, master=self.overlay)
            self.canvas.create_image(x1, y1, image=self._selection_photo, anchor="nw", tags="selection")
        self.canvas.create_rectangle(x1, y1, x2, y2, outline="#A46ADA", width=3, tags="selection")
        for x, y in ((x1, y1), (x1, y2), (x2, y1), (x2, y2)):
            self.canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill="#FAF6FF", outline="#A87DDB", tags="selection")
        self._draw_dimensions(x1, y1, x2, y2, box[2] - box[0], box[3] - box[1])

    def _draw_dimensions(self, x1, y1, x2, y2, pixel_width, pixel_height):
        """Place a size badge outside the selected text, when there is room."""
        _left, _top, width, height = self._bounds
        label_width, label_height = 132, 25
        if y1 >= label_height + 7:
            bx, by = x1, y1 - label_height - 6
        elif y2 + label_height + 7 <= height:
            bx, by = x1, y2 + 6
        elif x2 + label_width + 7 <= width:
            bx, by = x2 + 6, max(0, y1)
        elif x1 >= label_width + 7:
            bx, by = x1 - label_width - 6, max(0, y1)
        else:
            # A nearly full-screen selection has no outside space. Keep every
            # selected pixel unobscured rather than overlaying a text badge.
            return
        bx = max(2, min(width - label_width - 2, bx))
        self.canvas.create_rectangle(bx, by, bx + label_width, by + label_height,
                                     fill="#FAF6FF", outline="#A46ADA", tags=("selection", "dimensions"))
        self.canvas.create_text(bx + label_width / 2, by + label_height / 2,
                                text=f"{pixel_width} × {pixel_height} px", fill="#6F4DA5",
                                font=("Microsoft YaHei UI", 10), tags=("selection", "dimensions"))

    def _release(self, _event=None):
        if self._start_point is None or self._image is None:
            return
        box = crop_box(self._start_point, self._cursor(), self._bounds, self._image.size)
        if box[2] - box[0] < 12 or box[3] - box[1] < 12:
            self.canvas.delete("selection")
            x, y = self._canvas_point(self._cursor())
            self.canvas.create_text(max(130, min(self._bounds[2] - 130, x)),
                                    max(25, min(self._bounds[3] - 25, y + 30)),
                                    text="范围有点小，请重新框选完整文字", fill="#FAF6FF",
                                    font=("Microsoft YaHei UI", 12), tags="hint")
            self._start_point = None
            return
        image = self._image.crop(box)
        token = self._token
        self._destroy_picker()
        self._emit("working", phase="recognizing")
        threading.Thread(target=self._recognize, args=(image, token), name="tutu-ocr", daemon=True).start()

    def _recognize(self, image, token):
        error = None
        text = ""
        try:
            text = self.engine.recognize(image)
            if not text:
                error = "没有识别到文字，再框选清晰一点的中英文试试吧。"
        except CaptureError as exc:
            error = str(exc)
        except Exception:
            error = "本地文字识别暂时不可用，请再试一次。"
        finally:
            image.close()
        # No Tk access here. The application's queue poll restores the window.
        with self._guard:
            if not self._closed and token == self._token:
                self._busy = False
                self._emit("done", text=text, error=error)

    def _destroy_picker(self):
        if self.overlay is not None:
            try:
                self.overlay.grab_release()
                self.overlay.destroy()
            except tk.TclError:
                pass
        self.overlay = self.canvas = None
        self._image = self._photo = self._selection_photo = None
        self._start_point = None

    def cancel(self):
        """Cancel from Tk's thread. Results from an old worker are discarded."""
        if not self._busy:
            return
        with self._guard:
            self._token += 1
            self._busy = False
        if self._pending_after is not None:
            try:
                self.app.after_cancel(self._pending_after)
            except tk.TclError:
                pass
            self._pending_after = None
        self._destroy_picker()
        self.engine.cancel_current()
        self._emit("cancelled")

    def close(self):
        self._closed = True
        self.cancel()
        self.engine.close()


def self_test(assets_path, report_path=None):
    """Recognize generated English/Chinese images; never capture a real screen."""
    engine = LocalOcr(assets_path)
    report = {"ok": False, "screenshot_captured": False}
    try:
        startup = time.monotonic()
        report.update(engine.probe())
        report["startup_seconds"] = round(time.monotonic() - startup, 3)
        fonts = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts"
        samples = [("english", "Hello world. Welcome to Tutu Translate.", "segoeui.ttf", 36),
                   ("english_arial", "A small local translator can read this sentence.", "arial.ttf", 32),
                   ("english_consolas", "Please translate this English paragraph.", "consola.ttf", 30),
                   ("chinese", "你好世界，欢迎使用兔兔翻译。", "msyh.ttc", 42),
                   ("mixed", "截图翻译 Hello world 123", "msyh.ttc", 42),
                   ("paragraph", "Reading should be easy.\nThis is the second line.", "segoeui.ttf", 30)]
        results = {}
        for name, text, face, size in samples:
            font = ImageFont.truetype(str(fonts / face), size)
            image = Image.new("RGB", (1400, 210 if "\n" in text else 135), "white")
            ImageDraw.Draw(image).multiline_text((30, 30), text, font=font, fill="black", spacing=12)
            start = time.monotonic()
            recognized = engine.recognize(image)
            compact = re.sub(r"[\W_]+", "", recognized).lower()
            expected = re.sub(r"[\W_]+", "", text).lower()
            results[name] = {"ok": expected == compact, "text": recognized,
                             "seconds": round(time.monotonic() - start, 3)}
            image.close()
        report["samples"] = results
        report["ok"] = bool(report.get("bundled_models")) and all(item["ok"] for item in results.values())
        try:
            report["native_languages"] = engine.native.probe()["languages"]
        except CaptureError:
            report["native_languages"] = []
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        engine.close()
    if report_path is not None:
        Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def picker_self_test(assets_path, report_path=None):
    """Verify actual Tk mapping with a synthetic image, never a desktop grab.

    This diagnostic owns its small test window and sends only local Tk events.
    The saved report contains geometry and pixel values, never screen contents.
    """
    import customtkinter as ctk

    report = {"ok": False, "screenshot_captured": False, "synthetic_image": True}
    root = None
    picker = None
    picture = Image.new("RGB", (800, 500), "#FAF8F5")
    draw = ImageDraw.Draw(picture)
    draw.rectangle((0, 0, 70, 70), fill="#F2AFC9")
    draw.rectangle((730, 430, 799, 499), fill="#A8D8CA")
    font_path = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts/msyh.ttc"
    font = ImageFont.truetype(str(font_path), 26)
    draw.text((100, 140), "Hello world. 截图文字保持清楚。", font=font, fill="#393348")

    def settle_until(predicate, timeout=.8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            root.update()
            if predicate():
                return True
            time.sleep(.01)
        return bool(predicate())

    def photo_size(photo):
        return [photo.width(), photo.height()]

    def photo_pixel(photo, x, y):
        value = photo.tk.call(str(photo), "get", x, y)
        return [int(part) for part in value] if isinstance(value, tuple) else [int(part) for part in value.split()]

    try:
        root = ctk.CTk()
        root.withdraw()
        events = queue.Queue()
        picker = ScreenshotCapture(root, events, assets_path)
        picker._bounds = (80, 80, 800, 500)
        picker._image = picture.copy()
        picker._busy = True
        cursor = [200, 200]
        picker._cursor = lambda: tuple(cursor)
        picker._show_picker()
        report["canvas_initial"] = [picker.canvas.winfo_width(), picker.canvas.winfo_height()]
        report["photo_initial"] = photo_size(picker._photo)
        mapped = settle_until(lambda: picker.canvas.winfo_width() == 800 and picker.canvas.winfo_height() == 500)
        report["canvas_mapped"] = [picker.canvas.winfo_width(), picker.canvas.winfo_height()]
        report["photo_mapped"] = photo_size(picker._photo)
        report["background_pixel"] = photo_pixel(picker._photo, 600, 350)
        report["background_source_pixel"] = list(picture.getpixel((600, 350)))
        report["background_readable"] = min(report["background_pixel"]) >= 210
        report["escape_bound"] = bool(picker.overlay.bind("<Escape>"))
        report["right_click_bound"] = bool(picker.canvas.bind("<Button-3>"))

        cursor[:] = [180, 200]
        picker._press()
        cursor[:] = [640, 400]
        picker._drag()
        root.update_idletasks()
        report["selection_size"] = photo_size(picker._selection_photo)
        report["selection_pixel"] = photo_pixel(picker._selection_photo, 240, 90)
        report["selection_source_pixel"] = list(picture.getpixel((340, 210)))
        report["selection_original_brightness"] = report["selection_pixel"] == report["selection_source_pixel"]
        badge = picker.canvas.bbox("dimensions")
        report["dimension_badge"] = list(badge) if badge else None
        report["dimension_badge_outside_selection"] = bool(badge) and (
            badge[2] <= 100 or badge[0] >= 560 or badge[3] <= 120 or badge[1] >= 320)

        picker.overlay.event_generate("<Escape>")
        escaped = settle_until(lambda: picker.overlay is None)
        report["escape_cancelled"] = escaped and not picker.active
        picker._bounds = (80, 80, 800, 500)
        picker._image = picture.copy()
        picker._busy = True
        picker._show_picker()
        settle_until(lambda: picker.canvas.winfo_width() == 800)
        picker.canvas.event_generate("<Button-3>", x=100, y=100)
        clicked = settle_until(lambda: picker.overlay is None)
        report["right_click_cancelled"] = clicked and not picker.active
        report["cancelled_events"] = []
        while not events.empty():
            _kind, payload = events.get_nowait()
            report["cancelled_events"].append(payload.get("state"))
        report["ok"] = bool(mapped and report["photo_initial"] == [800, 500]
                            and report["photo_mapped"] == [800, 500]
                            and report["background_readable"]
                            and report["selection_size"] == [460, 200]
                            and report["selection_original_brightness"]
                            and report["dimension_badge_outside_selection"]
                            and report["escape_bound"] and report["right_click_bound"]
                            and report["escape_cancelled"] and report["right_click_cancelled"]
                            and report["cancelled_events"] == ["cancelled", "cancelled"])
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if picker is not None:
            picker.close()
        if root is not None:
            try:
                for callback in root.tk.call("after", "info"):
                    root.after_cancel(callback)
                root.destroy()
            except tk.TclError:
                pass
        picture.close()
    if report_path is not None:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
