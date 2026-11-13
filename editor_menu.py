"""Rounded editor menus that follow Tutu's light purple interface."""
from __future__ import annotations

import sys
import tkinter as tk

import customtkinter as ctk

BACKGROUND = "#FFFDFF"
INK = "#544564"
MUTED = "#A89AB6"
DISABLED = "#C6BDCF"
HOVER = "#EEE6FA"
PURPLE = "#8968BE"
TRANSPARENT = "#010203"


def _work_area(widget, x, y):
    """Use the nearest monitor's work area, including negative coordinates."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class MonitorInfo(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

            user32 = ctypes.windll.user32
            user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
            user32.MonitorFromPoint.restype = wintypes.HANDLE
            user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
            user32.GetMonitorInfoW.restype = wintypes.BOOL
            info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
            monitor = user32.MonitorFromPoint(wintypes.POINT(x, y), 2)
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                r = info.rcWork
                return r.left, r.top, r.right, r.bottom
        except (OSError, AttributeError):
            pass
    left, top = widget.winfo_vrootx(), widget.winfo_vrooty()
    return left, top, left + widget.winfo_vrootwidth(), top + widget.winfo_vrootheight()


class EditorContextMenu:
    """One focusable popup at a time, without a global or pointer grab."""

    _shown = None

    def __init__(self, master, text, *, editable=False, on_change=None):
        self.master = master.winfo_toplevel()
        self.text = text
        self.editable = editable
        self.on_change = on_change
        commands = [("copy", "复制", "Ctrl+C"), ("all", "全选", "Ctrl+A")]
        if editable:
            commands = [("cut", "剪切", "Ctrl+X"), ("copy", "复制", "Ctrl+C"),
                        ("paste", "粘贴", "Ctrl+V"), ("all", "全选", "Ctrl+A")]
        self.items = [{"action": a, "label": label, "shortcut": key, "enabled": False}
                      for a, label, key in commands]
        self.popup = None
        self.canvas = None
        self._active = None
        self._focus_after = None
        self._rows = []
        self._scale = 1.0
        self._owner_bounds = None
        self.bind(text)
        self.master.bind("<ButtonPress-1>", self._outside_click, add="+")
        self.master.bind("<ButtonPress-3>", self._outside_click, add="+")
        self.master.bind("<Unmap>", self._owner_unmap, add="+")
        self.master.bind("<Configure>", self._owner_configure, add="+")

    def bind(self, widget):
        # Placeholder labels cover the actual Text while it is empty.
        widget.bind("<Button-3>", self.show, add="+")
        widget.bind("<Shift-F10>", self.show_at_caret, add="+")
        widget.bind("<KeyPress-Menu>", self.show_at_caret, add="+")

    def _selection(self):
        selected = self.text.tag_ranges("sel")
        return selected if len(selected) == 2 else ()

    def _clipboard(self):
        try:
            return self.text.clipboard_get()
        except tk.TclError:
            return ""

    def refresh(self):
        selected = bool(self._selection())
        states = {"copy": selected, "cut": selected, "all": bool(self.text.get("1.0", "end-1c"))}
        if self.editable:
            states["paste"] = bool(self._clipboard())
        for item in self.items:
            item["enabled"] = states.get(item["action"], False)
        if self._active is not None and not self.items[self._active]["enabled"]:
            self._active = None
        self._draw()

    def enabled(self, action):
        return next((item["enabled"] for item in self.items
                     if action in (item["action"], item["label"])), False)

    def show(self, event):
        if EditorContextMenu._shown is not None:
            EditorContextMenu._shown.close()
        self.text.focus_set()
        self.refresh()
        try:
            self._scale = max(.75, float(self.master._get_widget_scaling()))
        except AttributeError:
            self._scale = 1.0
        s = self._scale
        width, height = round(234 * s), round((26 + len(self.items) * 36) * s)
        popup = tk.Toplevel(self.master, takefocus=True)
        self.popup = popup
        EditorContextMenu._shown = self
        popup.withdraw()
        popup.overrideredirect(True)
        popup.transient(self.master)
        # An override-redirect window does not inherit the owner's topmost flag
        # on Windows. Preserve the menu above a pinned Tutu window as well.
        popup.attributes("-topmost", bool(self.master.attributes("-topmost")))
        popup.configure(background=TRANSPARENT)
        if sys.platform == "win32":
            popup.attributes("-transparentcolor", TRANSPARENT)
        popup.geometry(f"{width}x{height}+0+0")
        frame = ctk.CTkFrame(popup, corner_radius=12, border_width=1,
                             border_color="#E3D8EE", fg_color=BACKGROUND, bg_color=TRANSPARENT)
        frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(frame, highlightthickness=0, borderwidth=0,
                                background=BACKGROUND, takefocus=True)
        self.canvas.place(x=round(8 * s), y=round(8 * s),
                          width=width - round(16 * s), height=height - round(16 * s))
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", lambda _event: self.hover(None))
        self.canvas.bind("<ButtonRelease-1>", self._click)
        popup.bind("<KeyPress>", self._key)
        popup.bind("<FocusOut>", lambda _event, p=popup: self._schedule_focus_check(p))
        popup.bind("<Destroy>", lambda e, p=popup: self._popup_destroyed(e, p))
        popup.update_idletasks()
        left, top, right, bottom = _work_area(self.master, int(event.x_root), int(event.y_root))
        x = max(left + 4, min(int(event.x_root) + 2, right - width - 4))
        y = max(top + 4, min(int(event.y_root) + 3, bottom - height - 4))
        # '+-100' preserves an absolute negative position in Tk geometry.
        popup.geometry(f"{width}x{height}+{x}+{y}")
        self._owner_bounds = self._current_owner_bounds()
        self._draw()
        popup.deiconify()
        popup.lift()
        popup.focus_force()
        self.canvas.focus_set()
        return "break"

    def show_at_caret(self, _event):
        location = self.text.bbox("insert")
        x, y = (location[0], location[1] + location[3]) if location else (8, 8)
        event = type("MenuLocation", (), {"x_root": self.text.winfo_rootx() + x,
                                          "y_root": self.text.winfo_rooty() + y})()
        self.show(event)
        self._navigate(1)
        return "break"

    def close(self, restore_focus=False):
        popup = self.popup
        self.popup, self.canvas = None, None
        self._active = None
        if EditorContextMenu._shown is self:
            EditorContextMenu._shown = None
        if self._focus_after is not None:
            try:
                self.master.after_cancel(self._focus_after)
            except tk.TclError:
                pass
            self._focus_after = None
        if popup is not None:
            try:
                popup.destroy()
            except tk.TclError:
                pass
        if restore_focus:
            self.text.focus_set()

    def _popup_destroyed(self, event, popup):
        if event.widget is popup and self.popup is popup:
            self.close()

    def _schedule_focus_check(self, popup):
        if self.popup is popup and self._focus_after is None:
            self._focus_after = self.master.after_idle(lambda: self._check_focus(popup))

    def _check_focus(self, popup):
        self._focus_after = None
        if self.popup is not popup:
            return
        focused = self.master.focus_get()
        if focused is None or focused.winfo_toplevel() is not popup:
            self.close()

    def _outside_click(self, event):
        if self.popup is not None and event.widget.winfo_toplevel() is not self.popup:
            self.close()

    def _owner_unmap(self, event):
        if event.widget is self.master:
            self.close()

    def _owner_configure(self, event):
        if event.widget is self.master and self.popup is not None:
            # Changing the pin style can queue a Configure with unchanged
            # geometry; dismiss only when the owner really moves or resizes.
            if self._current_owner_bounds() != self._owner_bounds:
                self.close()

    def _current_owner_bounds(self):
        return (self.master.winfo_rootx(), self.master.winfo_rooty(),
                self.master.winfo_width(), self.master.winfo_height())

    def _navigate(self, step):
        available = [i for i, item in enumerate(self.items) if item["enabled"]]
        if available:
            current = available.index(self._active) if self._active in available else (-1 if step > 0 else 0)
            self._active = available[(current + step) % len(available)]
            self._draw()

    def _key(self, event):
        if event.keysym == "Escape":
            self.close(restore_focus=True)
        elif event.keysym in ("Down", "Up"):
            self._navigate(1 if event.keysym == "Down" else -1)
        elif event.keysym in ("Return", "KP_Enter"):
            if self._active is not None:
                self.invoke(self.items[self._active]["action"])
        elif event.state & 4:
            action = {"x": "cut", "c": "copy", "v": "paste", "a": "all"}.get(event.keysym.lower())
            if action:
                self.invoke(action)
        else:
            return
        return "break"

    def _row_at(self, y):
        return next((i for i, (start, end) in enumerate(self._rows) if start <= y < end), None)

    def _motion(self, event):
        index = self._row_at(event.y)
        self.hover(self.items[index]["action"] if index is not None else None)

    def _click(self, event):
        index = self._row_at(event.y)
        if index is not None:
            self.invoke(self.items[index]["action"])

    def hover(self, action):
        self._active = next((i for i, item in enumerate(self.items)
                             if action in (item["action"], item["label"]) and item["enabled"]), None)
        self._draw()

    def invoke(self, action):
        self.refresh()
        item = next((item for item in self.items if action in (item["action"], item["label"])), None)
        if item and item["enabled"]:
            self.close()
            self.perform(item["action"])

    def _draw(self):
        if self.canvas is None:
            return
        canvas, s = self.canvas, self._scale
        canvas.delete("all")
        width = round(218 * s)
        y = 0
        self._rows = []
        for index, item in enumerate(self.items):
            if item["action"] == "all":
                canvas.create_line(9 * s, y + 5 * s, width - 9 * s, y + 5 * s, fill="#EEE7F4")
                y += 10 * s
            self._rows.append((y, y + 36 * s))
            active = index == self._active and item["enabled"]
            if active:
                self._rounded(canvas, 0, y + s, width, y + 35 * s, 8 * s, fill=HOVER)
            color = PURPLE if active else INK if item["enabled"] else DISABLED
            self._icon(item["action"], 17 * s, y + 18 * s, color)
            canvas.create_text(39 * s, y + 18 * s, anchor="w", text=item["label"], fill=color,
                               font=("Microsoft YaHei UI", -round(13 * s)))
            canvas.create_text(width - 12 * s, y + 18 * s, anchor="e", text=item["shortcut"],
                               fill=PURPLE if active else MUTED if item["enabled"] else DISABLED,
                               font=("Segoe UI", -round(11 * s)))
            y += 36 * s

    @staticmethod
    def _rounded(canvas, x1, y1, x2, y2, radius, **kw):
        canvas.create_polygon(x1 + radius, y1, x2 - radius, y1, x2, y1,
                              x2, y1 + radius, x2, y2 - radius, x2, y2,
                              x2 - radius, y2, x1 + radius, y2, x1, y2,
                              x1, y2 - radius, x1, y1 + radius, x1, y1,
                              smooth=True, splinesteps=24, outline="", **kw)

    def _icon(self, action, x, y, color):
        canvas, s = self.canvas, self._scale
        line = {"fill": color, "width": max(1, 1.2 * s), "capstyle": "round", "joinstyle": "round"}
        if action == "copy":
            canvas.create_rectangle(x - 3*s, y - 6*s, x + 6*s, y + 4*s, outline=color, width=1.2*s)
            canvas.create_line(x - 6*s, y - 3*s, x - 6*s, y + 7*s, x + 3*s, y + 7*s, **line)
        elif action == "paste":
            canvas.create_line(x - 3*s, y - 6*s, x - 6*s, y - 6*s, x - 6*s, y + 7*s,
                               x + 6*s, y + 7*s, x + 6*s, y - 6*s, x + 3*s, y - 6*s, **line)
            canvas.create_rectangle(x - 3*s, y - 8*s, x + 3*s, y - 4*s, outline=color, width=1.2*s)
            canvas.create_line(x - 3*s, y, x + 3*s, y, **line)
            canvas.create_line(x - 3*s, y + 3*s, x + 2*s, y + 3*s, **line)
        elif action == "cut":
            canvas.create_oval(x - 7*s, y + s, x - s, y + 7*s, outline=color, width=1.2*s)
            canvas.create_oval(x + s, y + s, x + 7*s, y + 7*s, outline=color, width=1.2*s)
            canvas.create_line(x - 4*s, y + s, x + 5*s, y - 7*s, **line)
            canvas.create_line(x + 4*s, y + s, x - 5*s, y - 7*s, **line)
        else:
            for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                canvas.create_line(x + dx*3*s, y + dy*7*s, x + dx*7*s, y + dy*7*s,
                                   x + dx*7*s, y + dy*3*s, **line)
            canvas.create_line(x - 3*s, y - 2*s, x + 3*s, y - 2*s, **line)
            canvas.create_line(x - 3*s, y + 2*s, x + 3*s, y + 2*s, **line)

    def perform(self, action):
        self.text.focus_set()
        selected = self._selection()
        if action == "all":
            self.text.tag_add("sel", "1.0", "end-1c")
            self.text.mark_set("insert", "end-1c")
            return
        if action in ("copy", "cut"):
            if not selected or (action == "cut" and not self.editable):
                return
            try:
                value = self.text.get(*selected)
                self.text.clipboard_clear()
                self.text.clipboard_append(value)
            except tk.TclError:
                return
            if action == "copy":
                return
            self.text.delete(*selected)
        elif action == "paste" and self.editable:
            value = self._clipboard()
            if not value:
                return
            if selected:
                self.text.mark_set("insert", selected[0])
                self.text.delete(*selected)
            self.text.insert("insert", value)
            self.text.see("insert")
        else:
            return
        if self.on_change:
            self.on_change()
