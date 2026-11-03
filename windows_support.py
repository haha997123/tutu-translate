"""Windows integration for Tutu; all UI work stays on the caller's thread.

Call ``acquire_instance()`` before creating Tk. A second launch signals the
running copy and should exit. NativeSupport delivers only queue events; the
application drains the queue with Tk.after() and reads the clipboard there.
"""

from __future__ import annotations

import atexit
import ctypes
import logging
import os
from pathlib import Path
import queue
import threading

log = logging.getLogger(__name__)
_WINDOWS = os.name == "nt"
_APP_ID = "Tutu.Translate.Desktop.1"
_MUTEX_NAME = r"Local\TutuTranslate.Desktop.Instance.v1"
_SHOW_EVENT_NAME = r"Local\TutuTranslate.Desktop.Show.v1"
_instance_lock = threading.RLock()
_instance_mutex = None
_instance_show_event = None

if _WINDOWS:
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    _kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateEventW.restype = wintypes.HANDLE
    _kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    _kernel32.SetEvent.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.GetCurrentThreadId.argtypes = []
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    _user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.UnregisterHotKey.restype = wintypes.BOOL
    _user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
    _user32.PeekMessageW.restype = wintypes.BOOL
    _user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.PostThreadMessageW.restype = wintypes.BOOL
    _user32.MsgWaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD, wintypes.DWORD]
    _user32.MsgWaitForMultipleObjects.restype = wintypes.DWORD
    _shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
    _shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long


def set_app_id() -> bool:
    """Set the taskbar identity before constructing the application window."""
    if not _WINDOWS:
        return False
    return _shell32.SetCurrentProcessExplicitAppUserModelID(_APP_ID) >= 0


def acquire_instance() -> bool:
    """Keep one copy per Windows session; ask the existing copy to show itself.

    This is safe to call repeatedly in the owning process. OS handles disappear
    automatically after a crash, so no stale lock files need to be removed.
    """
    global _instance_mutex, _instance_show_event
    if not _WINDOWS:
        return True
    with _instance_lock:
        if _instance_mutex:
            return True
        # Hold the event before creating the mutex, covering a second launch
        # arriving before the original process starts its native message loop.
        show_event = _kernel32.CreateEventW(None, False, False, _SHOW_EVENT_NAME)
        if not show_event:
            raise ctypes.WinError(ctypes.get_last_error())
        ctypes.set_last_error(0)
        mutex = _kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        error = ctypes.get_last_error()
        if not mutex:
            _kernel32.CloseHandle(show_event)
            raise ctypes.WinError(error)
        if error == 183:  # ERROR_ALREADY_EXISTS
            _kernel32.SetEvent(show_event)
            _kernel32.CloseHandle(mutex)
            _kernel32.CloseHandle(show_event)
            return False
        _instance_mutex = mutex
        _instance_show_event = show_event
        return True


def release_instance() -> None:
    """Release process handles at shutdown, after NativeSupport.stop()."""
    global _instance_mutex, _instance_show_event
    if not _WINDOWS:
        return
    with _instance_lock:
        for handle in (_instance_show_event, _instance_mutex):
            if handle:
                _kernel32.CloseHandle(handle)
        _instance_mutex = _instance_show_event = None


atexit.register(release_instance)


class NativeSupport:
    """Global hotkeys, tray icon and second-launch activation.

    ``('show', None)`` opens the window. ``('translate_clipboard', None)`` asks
    the UI to read and translate clipboard text. ``('quit', None)`` exits.
    Closing the Tk window can hide it; these helpers continue running until
    ``stop()`` is called. No callback touches Tk or reads the clipboard.
    """

    def __init__(self, callback_queue: queue.Queue, icon_path: Path | str):
        self.callback_queue = callback_queue
        self.icon_path = Path(icon_path)
        self._status = {"hotkey": False, "tray": False, "single_instance": False}
        self._lock = threading.RLock()
        self._started = False
        self._stop_requested = threading.Event()
        self._native_ready = threading.Event()
        self._tray_ready = threading.Event()
        self._native_thread = None
        self._tray_thread = None
        self._thread_id = 0
        self._stop_handle = None
        self._tray_icon = None
        self.hotkeys: dict[str, bool] = {}

    def _emit(self, event: str) -> None:
        if not self._stop_requested.is_set():
            try:
                self.callback_queue.put_nowait((event, None))
            except queue.Full:
                log.warning("Application event queue is full")

    def start(self) -> dict[str, bool]:
        """Start helpers once and report whether each feature is available.

        ``hotkey`` is true when both advertised shortcuts registered. Individual
        results are also available in ``hotkeys`` for a precise status hint.
        Tray imports are optional; a missing dependency does not prevent launch.
        """
        with self._lock:
            if self._started:
                return dict(self._status)
            self._started = True
            self._stop_requested.clear()
            self._status["single_instance"] = acquire_instance()
            if not self._status["single_instance"] or not _WINDOWS:
                return dict(self._status)
            self._stop_handle = _kernel32.CreateEventW(None, True, False, None)
            if self._stop_handle:
                self._native_thread = threading.Thread(target=self._native_loop, name="Tutu hotkeys", daemon=True)
                self._native_thread.start()
                self._native_ready.wait(2.0)
            else:
                log.warning("Could not create the native stop event")
            self._start_tray()
            return dict(self._status)

    def _native_loop(self) -> None:
        registered = []
        try:
            self._thread_id = _kernel32.GetCurrentThreadId()
            message = wintypes.MSG()
            # PeekMessage creates a message queue for this windowless thread.
            _user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            modifiers = 0x0001 | 0x4000  # ALT, NOREPEAT
            bindings = ((0x5454, ord("Q"), "show", "Alt+Q"),
                        (0x5451, ord("W"), "translate_clipboard", "Alt+W"),
                        (0x5452, ord("E"), "capture", "Alt+E"))
            events = {}
            for identifier, key, event, label in bindings:
                success = bool(_user32.RegisterHotKey(None, identifier, modifiers, key))
                self.hotkeys[label] = success
                if success:
                    registered.append(identifier)
                    events[identifier] = event
                else:
                    log.info("Shortcut %s is already in use or unavailable", label)
            self._status["hotkey"] = all(self.hotkeys.values())
            self._native_ready.set()
            handles = (wintypes.HANDLE * 2)(self._stop_handle, _instance_show_event)
            while not self._stop_requested.is_set():
                result = _user32.MsgWaitForMultipleObjects(2, handles, False, 0xFFFFFFFF, 0x04FF)
                if result == 0:  # Stop event.
                    break
                if result == 1:  # Auto-reset event from a second app launch.
                    self._emit("show")
                elif result == 2:  # Windows messages, including WM_HOTKEY.
                    while _user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                        if message.message == 0x0012:  # WM_QUIT
                            return
                        if message.message == 0x0312:  # WM_HOTKEY
                            event = events.get(int(message.wParam))
                            if event:
                                self._emit(event)
                else:
                    log.warning("Native message wait failed: %s", ctypes.get_last_error())
                    break
        except Exception:
            log.exception("Native shortcut support failed")
            self._status["hotkey"] = False
        finally:
            for identifier in registered:
                _user32.UnregisterHotKey(None, identifier)
            self._native_ready.set()

    def _start_tray(self) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw

            if self.icon_path.is_file():
                with Image.open(self.icon_path) as source:
                    picture = source.convert("RGBA").copy()
            else:
                # Small built-in fallback while packaged artwork is unavailable.
                picture = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
                draw = ImageDraw.Draw(picture)
                draw.ellipse((5, 5, 59, 59), fill="#F7A9C1")
                draw.ellipse((20, 25, 25, 31), fill="#543848")
                draw.ellipse((39, 25, 44, 31), fill="#543848")
                draw.arc((24, 30, 40, 42), 0, 180, fill="#543848", width=3)

            def show(_icon, _item):
                self._emit("show")

            def translate_clipboard(_icon, _item):
                self._emit("translate_clipboard")

            def capture(_icon, _item):
                self._emit("capture")

            def quit_app(_icon, _item):
                self._emit("quit")

            menu = pystray.Menu(
                pystray.MenuItem("打开兔兔翻译", show, default=True),
                pystray.MenuItem("翻译剪贴板  Alt+W", translate_clipboard),
                pystray.MenuItem("截图翻译  Alt+E", capture),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", quit_app),
            )
            self._tray_icon = pystray.Icon("tutu_translate", picture, "兔兔翻译 · Alt+Q", menu)

            def setup(icon):
                if self._stop_requested.is_set():
                    icon.stop()
                else:
                    icon.visible = True
                    self._status["tray"] = True
                self._tray_ready.set()

            def run_tray():
                try:
                    self._tray_icon.run(setup=setup)
                except Exception:
                    log.exception("Tray icon support failed")
                    self._status["tray"] = False
                finally:
                    self._tray_ready.set()

            self._tray_thread = threading.Thread(target=run_tray, name="Tutu tray", daemon=True)
            self._tray_thread.start()
            self._tray_ready.wait(2.0)
        except Exception:
            log.exception("Tray icon could not be initialized")

    def stop(self) -> None:
        """Remove the tray icon and hotkeys, then release instance handles."""
        with self._lock:
            self._stop_requested.set()
            if _WINDOWS and self._stop_handle:
                _kernel32.SetEvent(self._stop_handle)
            if self._tray_icon is not None:
                try:
                    self._tray_icon.visible = False
                except Exception:
                    log.exception("Could not hide the tray icon")
                try:
                    self._tray_icon.stop()
                except Exception:
                    log.exception("Could not stop the tray icon")
            current = threading.current_thread()
            for thread in (self._native_thread, self._tray_thread):
                if thread is not None and thread is not current:
                    thread.join(timeout=3.0)
            native_alive = self._native_thread is not None and self._native_thread.is_alive()
            if _WINDOWS and not native_alive:
                if self._stop_handle:
                    _kernel32.CloseHandle(self._stop_handle)
                    self._stop_handle = None
                # A secondary NativeSupport never acquired the shared handles.
                if self._status["single_instance"]:
                    release_instance()
            self._status["hotkey"] = self._status["tray"] = False

