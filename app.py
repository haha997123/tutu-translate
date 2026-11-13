"""Tutu Translate — a small, cheerful Chinese / English desktop translator."""
from __future__ import annotations

import argparse
import ctypes
import queue
import re
import sys
import threading
import tkinter as tk
from pathlib import Path

import customtkinter as ctk
from PIL import Image, ImageGrab

from engine import TranslationError, Translator, _direction
from phonetics import Phonetics
from speech import SpeechReader, self_test as speech_self_test
from windows_support import NativeSupport, acquire_instance, set_app_id
from capture import ScreenshotCapture, self_test as ocr_self_test, picker_self_test
from offline import self_test as offline_self_test
from learning import LearningStore
from learning_ui import LearningPage
from dictation import DictationRecorder
from editor_menu import EditorContextMenu

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ASSETS = ROOT / "assets"
BG = "#FAF8F5"
INK = "#393348"
MUTED = "#8A8398"
PURPLE = "#8B70C9"
LILAC = "#F0EBF9"
LINE = "#EAE5EF"


class TutuApp(ctk.CTk):
    def __init__(self, *, native: bool = True, learning_state_path=None):
        super().__init__()
        self.title("兔兔翻译 · Tutu Translate")
        self.geometry("1000x740")
        self.minsize(900, 680)
        self.configure(fg_color=BG)
        self.iconbitmap(str(ASSETS / "icon.ico"))
        self.events: queue.Queue = queue.Queue()
        self.jobs: queue.Queue = queue.Queue()
        self.engine = Translator()
        self.phonetics = Phonetics(ASSETS)
        self.reader = SpeechReader(self.events, ASSETS)
        self._speech_id = None
        self._speech_area = None
        self._pronounce_word = ""
        self._phonetic_after = None
        self._result_language = None
        self.native = NativeSupport(self.events, ASSETS / "icon.png") if native else None
        self.native_status = {"tray": False, "hotkey": False}
        self._busy = False
        self._pending = None
        self._active = None
        self._debounce = None
        self._closing = False
        self._last_input = ""
        self._valid_result = False
        self._offline_ready = False
        self._current_page = "translate"
        self.learning_page = None
        self.learning_store = None
        self._learning_state_path = learning_state_path
        self.capture = ScreenshotCapture(self, self.events, ASSETS)
        self.dictation = DictationRecorder(self.events)
        self._dictation_id = None
        self._dictation_input = None
        self._dictation_state = None
        self._watch_id = None
        self._build()
        threading.Thread(target=self._worker, name="tutu-translate", daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self.hide)
        for sequence in ("<Control-Return>", "<Control-KP_Enter>"):
            self.bind(sequence, self._translate_key)
            self.source.bind(sequence, self._translate_key)
        self.bind("<Control-l>", lambda e: self.clear() if self._current_page == "translate" else None)
        self.bind("<Control-r>", self._dictation_key)
        self.bind("<Escape>", lambda e: self.hide())
        self.after(120, self._start_native)
        self.after(100, self._poll)
        self.after(220, self.source.focus_set)

    def font(self, size=14, bold=False):
        return ctk.CTkFont(family="Microsoft YaHei UI", size=size, weight="bold" if bold else "normal")

    def label(self, parent, text, size=14, color=INK, bold=False, **kw):
        return ctk.CTkLabel(parent, text=text, font=self.font(size, bold), text_color=color, **kw)

    def button(self, parent, text, command, primary=False, **kw):
        return ctk.CTkButton(parent, text=text, command=command, font=self.font(13, primary),
                            height=38, corner_radius=12, fg_color=PURPLE if primary else LILAC,
                            hover_color="#7860B4" if primary else "#E5DDF4",
                            text_color="white" if primary else "#715A9F", **kw)

    def _build(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.sidebar = ctk.CTkFrame(self, width=212, corner_radius=0, fg_color="#EEE8F8")
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_columnconfigure(0, weight=1)
        self.sidebar.grid_rowconfigure(5, weight=1)
        self.label(self.sidebar, "兔兔翻译", size=27, bold=True).grid(row=0, column=0, pady=(28, 0))
        self.label(self.sidebar, "TUTU TRANSLATE", size=11, color="#9787AE").grid(row=1, column=0, pady=(2, 16))
        mascot = Image.open(ASSETS / "mascot.png")
        self.mascot = ctk.CTkImage(mascot, size=(154, 172))
        self.label(self.sidebar, "", image=self.mascot).grid(row=2, column=0, pady=(5, 13))
        self.label(self.sidebar, "语言不同，也能聊得来", size=13, color="#756585").grid(row=3, column=0)
        chip = ctk.CTkFrame(self.sidebar, fg_color="#DDD2EF", corner_radius=14)
        chip.grid(row=4, column=0, pady=(18, 0), padx=31, sticky="ew")
        chip.configure(fg_color="transparent")
        self.translate_nav = self.button(chip, "快捷翻译", lambda: self.show_page("translate"), primary=True, width=150)
        self.translate_nav.pack(fill="x", pady=(0, 8))
        self.learning_nav = self.button(chip, "英语学习", lambda: self.show_page("learning"), width=150)
        self.learning_nav.pack(fill="x")
        shortcuts = ctk.CTkFrame(self.sidebar, fg_color="#F7F3FC", corner_radius=17)
        shortcuts.grid(row=6, column=0, sticky="ew", padx=19, pady=(8, 8))
        self.label(shortcuts, "随手就能翻译", size=13, bold=True, height=20).pack(anchor="w", padx=16, pady=(12, 8))
        for key, desc in [("Alt + Q", "呼出小兔"), ("Alt + W", "翻译剪贴板"), ("Alt + E", "框选截图翻译")]:
            self.label(shortcuts, key, size=12, color="#77628F", height=20).pack(anchor="w", padx=16)
            self.label(shortcuts, desc, size=11, color=MUTED, height=18).pack(anchor="w", padx=16, pady=(0, 5))
        self.shortcut_note = self.label(self.sidebar, "", size=10, color=MUTED, wraplength=174)
        self.shortcut_note.grid(row=7, column=0, padx=17)
        self.shortcut_note.grid_remove()
        self.sidebar_tagline = self.label(self.sidebar, "一点点可爱，一点点高效", size=11, color="#A194B1", height=18)
        self.sidebar_tagline.grid(row=8, column=0, pady=(8, 14))

        main = ctk.CTkFrame(self, fg_color="transparent")
        self.translate_page = main
        main.grid(row=0, column=1, sticky="nsew", padx=28, pady=27)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(3, weight=1)
        head = ctk.CTkFrame(main, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew")
        head.grid_columnconfigure(0, weight=1)
        self.label(head, "今天也能轻松读懂。", size=26, bold=True).grid(row=0, column=0, sticky="w")
        self.pin_var = tk.BooleanVar(value=False)
        ctk.CTkSwitch(head, text="保持在最前", variable=self.pin_var, command=self._pin,
                      font=self.font(12), text_color=MUTED, width=117, switch_width=31,
                      switch_height=17, progress_color=PURPLE).grid(row=0, column=1, sticky="e")
        self.label(main, "输入即译，截图也能懂。", color=MUTED, size=13).grid(row=1, column=0, sticky="w", pady=(4, 20))

        cards = ctk.CTkFrame(main, fg_color="transparent")
        cards.grid(row=3, column=0, sticky="nsew")
        cards.grid_columnconfigure((0, 1), weight=1, uniform="editors")
        cards.grid_rowconfigure(0, weight=1)
        self.input_card = ctk.CTkFrame(cards, fg_color="white", border_width=1, border_color=LINE, corner_radius=18)
        self.input_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self.output_card = ctk.CTkFrame(cards, fg_color="#F7F4FC", border_width=1, border_color="#E9E1F3", corner_radius=18)
        self.output_card.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        for card in (self.input_card, self.output_card):
            card.grid_columnconfigure(0, weight=1)
            card.grid_rowconfigure(1, weight=1)
        inp_head = ctk.CTkFrame(self.input_card, fg_color="transparent")
        inp_head.grid(row=0, column=0, sticky="ew", padx=18, pady=(13, 4))
        inp_head.grid_columnconfigure(0, weight=1)
        self.label(inp_head, "原文", size=14, bold=True).grid(row=0, column=0, sticky="w")
        self.read_source_btn = self.button(inp_head, "朗读", lambda: self.read_aloud("source"), width=55)
        self.read_source_btn.grid(row=0, column=1, padx=(0, 6))
        self.button(inp_head, "清空", self.clear, width=55).grid(row=0, column=2)
        self.source = ctk.CTkTextbox(self.input_card, fg_color="white", corner_radius=0, border_width=0,
                        font=self.font(16), text_color=INK, wrap="word", spacing1=3, spacing3=7,
                        scrollbar_button_color="#E0D6ED", scrollbar_button_hover_color="#CAB9E0")
        self.source.grid(row=1, column=0, sticky="nsew", padx=14, pady=(4, 0))
        self.source._textbox.bind("<<Modified>>", self._modified)
        self.source._textbox.edit_modified(False)
        self.placeholder = self.label(self.source, "输入或粘贴文字…\n停顿一下，小兔就会翻译", size=15, color="#B0A8BA", justify="left")
        self.placeholder.place(x=10, y=9)
        self.placeholder.bind("<Button-1>", lambda e: self.source.focus_set())
        self.count = self.label(self.input_card, "0 / 5000", size=11, color="#B0A8BA")
        self.count.grid(row=2, column=0, sticky="e", padx=19, pady=(8, 14))
        out_head = ctk.CTkFrame(self.output_card, fg_color="transparent")
        out_head.grid(row=0, column=0, sticky="ew", padx=18, pady=(13, 4))
        out_head.grid_columnconfigure(0, weight=1)
        self.out_title = self.label(out_head, "译文", size=14, bold=True)
        self.out_title.grid(row=0, column=0, sticky="w")
        self.read_result_btn = self.button(out_head, "朗读", lambda: self.read_aloud("result"), width=55)
        self.read_result_btn.grid(row=0, column=1, padx=(0, 6))
        self.read_result_btn.configure(state="disabled")
        self.copy_btn = self.button(out_head, "复制", self.copy, width=55)
        self.copy_btn.grid(row=0, column=2)
        self.copy_btn.configure(state="disabled")
        self.result = ctk.CTkTextbox(self.output_card, fg_color="#F7F4FC", corner_radius=0, border_width=0,
                       font=self.font(16), text_color="#594B72", wrap="word", spacing1=3, spacing3=7,
                       scrollbar_button_color="#E0D6ED", scrollbar_button_hover_color="#CAB9E0")
        self.result.grid(row=1, column=0, sticky="nsew", padx=14, pady=(4, 0))
        self.result.configure(state="disabled")
        self.empty = ctk.CTkFrame(self.result, fg_color="#F7F4FC", corner_radius=0)
        self.empty.place(relx=0.5, rely=0.45, anchor="center")
        self.small_mascot = ctk.CTkImage(mascot, size=(83, 93))
        self.label(self.empty, "", image=self.small_mascot).pack()
        self.label(self.empty, "译文会出现在这里", size=13, color="#9D8FAF").pack(pady=(11, 1))
        self.label(self.empty, "小兔已经准备好了", size=11, color="#B4A7C2").pack()
        self.result_meta = self.label(self.output_card, "等待一段新的小对话", size=11, color="#A599B7")
        self.result_meta.grid(row=2, column=0, sticky="w", padx=19, pady=(8, 14))
        self.source_menu = EditorContextMenu(self, self.source._textbox, editable=True, on_change=self._modified)
        self.source_menu.bind(self.placeholder)
        self.result_menu = EditorContextMenu(self, self.result._textbox)
        self.result_menu.bind(self.empty)
        for label in self.empty.winfo_children():
            self.result_menu.bind(label)

        pronunciation = ctk.CTkFrame(main, fg_color="#F0EBF7", corner_radius=14)
        pronunciation.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        pronunciation.grid_columnconfigure(0, weight=1)
        self.label(pronunciation, "单词发音 · 美式参考音标", size=12, color="#857494", bold=True).grid(row=0, column=0, sticky="w", padx=16, pady=(11, 0))
        self.slow_var = tk.BooleanVar(value=False)
        ctk.CTkSwitch(pronunciation, text="慢速朗读", variable=self.slow_var,
                      font=self.font(11), text_color="#8A7B9C", switch_width=29, switch_height=16,
                      progress_color=PURPLE, width=100).grid(row=0, column=1, padx=12, pady=(11, 0))
        self.read_word_btn = self.button(pronunciation, "听单词", lambda: self.read_aloud("word"), width=75)
        self.read_word_btn.grid(row=0, column=2, padx=(0, 12), pady=(8, 0))
        self.read_word_btn.configure(state="disabled")
        self.ipa_font = ctk.CTkFont(family="Segoe UI", size=16)
        self.phonetic_hint_font = self.font(13)
        self.phonetic_label = ctk.CTkLabel(pronunciation, text="选中一个英文单词，查看美式音标",
                    font=self.phonetic_hint_font, text_color="#A096B0",
                    anchor="w", justify="left", wraplength=640)
        self.phonetic_label.grid(row=1, column=0, columnspan=3, sticky="ew", padx=16, pady=(3, 12))
        for area, editor in (("source", self.source), ("result", self.result)):
            editor._textbox.bind("<<Selection>>", lambda e, a=area: self._selection_changed(a), add="+")
            editor._textbox.bind("<ButtonRelease-1>", lambda e, a=area: self._selection_changed(a), add="+")

        actions = ctk.CTkFrame(main, fg_color="transparent")
        actions.grid(row=5, column=0, sticky="ew", pady=(14, 0))
        actions.grid_columnconfigure(2, weight=1)
        self.capture_btn = self.button(actions, "截图翻译", self.capture_translate, primary=True, width=100)
        self.capture_btn.grid(row=0, column=0, sticky="w")
        self.dictation_btn = self.button(actions, "语音输入", self.toggle_dictation, width=100)
        self.dictation_btn.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.service_note = self.label(actions, "正在准备本地翻译…", size=11, color=MUTED)
        self.service_note.grid(row=0, column=2, sticky="e", padx=(8, 0))
        self.status = self.label(main, "●  准备好啦，输入文字就能开始", size=12, color="#879B8D", anchor="w", wraplength=690)
        self.status.grid(row=6, column=0, sticky="ew", pady=(12, 0))
        self.label(main, "语音、截图在本机识别 · 文字联网翻译，连接不佳时本地接替", size=10, color="#B1A9BC").grid(row=7, column=0, sticky="w", pady=(5, 0))

    def _start_native(self):
        if self.native:
            self.native_status = self.native.start()
            if not self.native_status.get("hotkey"):
                self.shortcut_note.configure(text="系统快捷键可能已被占用\n仍可从托盘或桌面打开")
                self.shortcut_note.grid()

    def _pin(self):
        self.attributes("-topmost", self.pin_var.get())

    def show_page(self, page):
        if page not in ("translate", "learning") or self._closing:
            return
        if page != self._current_page:
            self.cancel_dictation()
            self.stop_reading()
        if page == "learning":
            if self.learning_page is None:
                try:
                    self.learning_store = LearningStore(ASSETS / "learning_content.json", state_path=self._learning_state_path)
                    self.learning_page = LearningPage(self, self.learning_store, self.phonetics,
                        self.read_learning, self.stop_reading, fg_color="transparent")
                except (OSError, ValueError) as exc:
                    self._say(f"学习内容暂时打不开：{exc}", "#B57B83")
                    return
            self.translate_page.grid_remove()
            self.learning_page.grid(row=0, column=1, sticky="nsew", padx=28, pady=27)
            self.learning_page.refresh()
        else:
            if self.learning_page is not None:
                self.learning_page.grid_remove()
            self.translate_page.grid()
            self.source.focus_set()
        self._current_page = page
        for name, button in (("translate", self.translate_nav), ("learning", self.learning_nav)):
            active = page == name
            button.configure(fg_color=PURPLE if active else LILAC,
                text_color="white" if active else "#715A9F", hover_color="#7860B4" if active else "#E5DDF4")

    def read_learning(self, text, slow=False):
        self.cancel_dictation()
        if self._speech_area == "learning":
            self.stop_reading()
            return
        self.stop_reading()
        self._speech_area = "learning"
        self._speech_id = self.reader.speak(text, language="en", slow=slow)
        if self._speech_id is None:
            self._speech_area = None
        self._reading_buttons()
        if self.learning_page is not None and self._speech_id is None:
            self.learning_page.set_message("无法启动英语朗读，请检查 Windows 英语语音组件。", error=True)

    def _translate_key(self, _event):
        if self._current_page == "translate":
            self.translate()
        return "break"

    def _dictation_key(self, _event):
        if self._current_page == "translate":
            self.toggle_dictation()
        return "break"

    def toggle_dictation(self):
        if self._closing or self._current_page != "translate":
            return
        if self._dictation_id is not None:
            if self._dictation_state in ("starting", "recording"):
                self.dictation.stop()
                self._dictation_state = "recognizing"
                self.dictation_btn.configure(text="识别中…", state="disabled")
                self._say("●  正在本机识别语音…", PURPLE)
            return
        if self.dictation.busy:
            self._say("上一段语音仍在结束，请稍等再试。", "#B57B83")
            return
        self.stop_reading()
        try:
            identifier = self.dictation.start()
        except Exception:
            self._say("无法启动录音，请检查麦克风及系统权限。", "#B57B83")
            return
        if identifier is None:
            self._say("语音输入暂时忙碌，请稍后再试。", "#B57B83")
            return
        self._dictation_id = identifier
        self._dictation_input = self.text()
        self._dictation_state = "starting"
        self.dictation_btn.configure(text="结束录音", state="normal", fg_color="#E9CFE0")
        self._say("●  正在打开麦克风 · Ctrl + R 结束录音", PURPLE)

    def cancel_dictation(self):
        if self._dictation_id is None:
            return
        self._dictation_id = None
        self._dictation_input = None
        self._dictation_state = None
        self.dictation.cancel()
        self.dictation_btn.configure(text="语音输入", state="normal", fg_color=LILAC)

    def _dictation_event(self, payload):
        if self._dictation_id is None or payload.get("id") != self._dictation_id:
            return
        state = payload.get("state")
        if state in ("starting", "recording", "recognizing"):
            # A queued recording event may arrive after the user already pressed stop.
            if self._dictation_state == "recognizing" and state != "recognizing":
                return
            self._dictation_state = state
            recognizing = state == "recognizing"
            self.dictation_btn.configure(text="识别中…" if recognizing else "结束录音",
                                         state="disabled" if recognizing else "normal")
            self._say("●  正在本机识别语音…" if recognizing else
                      "●  请说中文或英文 · 再点结束录音，最长 60 秒 · Ctrl + R", PURPLE)
            return
        if state not in ("result", "error", "cancelled"):
            return
        original = self._dictation_input
        self._dictation_id = None
        self._dictation_input = None
        self._dictation_state = None
        self.dictation_btn.configure(text="语音输入", state="normal", fg_color=LILAC)
        if state == "result":
            text = payload.get("text", "").strip()
            if self.text() != original or self._current_page != "translate":
                return
            if text:
                self._set_input(text)
                self._say("●  语音已转成文字，稍后按现有设置翻译", "#879B8D")
                self.source.focus_set()
            else:
                self._say("没有听清楚，请靠近麦克风再说一次。", "#B57B83")
        elif state == "error":
            self._say(payload.get("message") or "语音识别失败，请检查麦克风后重试。", "#B57B83")
        else:
            self._say("●  已取消语音输入", MUTED)

    def text(self):
        return self.source.get("1.0", "end-1c")

    def signature(self):
        return (self.text().strip(), "auto")

    def _modified(self, _event=None):
        if not self.source._textbox.edit_modified():
            return
        self.source._textbox.edit_modified(False)
        current = self.text()
        if current == self._last_input:
            return
        self.cancel_dictation()
        self._last_input = current
        self.count.configure(text=f"{len(current)} / 5000", text_color="#C37583" if len(current) > 5000 else "#B0A8BA")
        if current:
            self.placeholder.place_forget()
        else:
            self.placeholder.place(x=10, y=9)
        self._invalidate()
        if self._pending and self._pending != self.signature():
            self._pending = None
        if self._debounce:
            self.after_cancel(self._debounce)
            self._debounce = None
        if current.strip() and len(current) <= 5000:
            self._debounce = self.after(550, self.translate)
            self._say("●  停顿一下，小兔就会翻译", "#879B8D")
        elif not current.strip():
            self._say("●  准备好啦，输入文字就能开始", "#879B8D")
        else:
            self._say("一次最多翻译 5000 个字符，请分段输入。", "#B57B83")
        self._update_phonetics()

    def _invalidate(self):
        if self._valid_result:
            self._set_result("")
            self.result_meta.configure(text="原文已更新")
        self._valid_result = False
        self.copy_btn.configure(state="disabled", text="复制")
        self._result_language = None
        self.read_result_btn.configure(state="disabled")
        if self._speech_area in ("source", "result", "word"):
            self.stop_reading()

    def _say(self, message, color=MUTED):
        self.status.configure(text=message, text_color=color)

    def _set_input(self, text):
        self.source.delete("1.0", "end")
        self.source.insert("1.0", text)
        self._modified()

    def _set_result(self, text):
        self.result.configure(state="normal")
        self.result.delete("1.0", "end")
        if text:
            self.result.insert("1.0", text)
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.45, anchor="center")
        self.result.configure(state="disabled")

    @staticmethod
    def _selected(editor):
        try:
            return editor.get("sel.first", "sel.last").strip()
        except tk.TclError:
            return ""

    def _selection_changed(self, area):
        if self._closing:
            return
        if self._phonetic_after:
            self.after_cancel(self._phonetic_after)
        self._phonetic_after = self.after(80, lambda: self._refresh_selection(area))

    def _refresh_selection(self, area):
        self._phonetic_after = None
        editor = self.source if area == "source" else self.result
        selected = self._selected(editor)
        self._update_phonetics(selected or None)

    def _update_phonetics(self, selected=None):
        candidates = [selected] if selected else [self.text()]
        if not selected and self._valid_result:
            candidates.append(self.result.get("1.0", "end-1c"))
        self._pronounce_word = ""
        for candidate in candidates:
            if not candidate:
                continue
            try:
                entry = self.phonetics.lookup(candidate)
            except (OSError, ValueError):
                self.phonetic_label.configure(text="音标词典暂不可用，仍可点击朗读", text_color="#A096B0", font=self.phonetic_hint_font)
                self.read_word_btn.configure(state="disabled")
                return
            if entry:
                self._pronounce_word = entry["word"]
                variants = "   ·   ".join(f"/{ipa}/" for ipa in entry["ipa"][:2])
                self.phonetic_label.configure(text=f"{entry['word']}   {variants}", text_color="#6B5688", font=self.ipa_font)
                self.read_word_btn.configure(state="normal")
                return
            word = candidate.strip().strip("\"'“”‘’.,!?;:()[]{}")
            if len(word) <= 64 and re.fullmatch(r"[A-Za-z]+(?:['’.-][A-Za-z]+)*", word):
                self._pronounce_word = word
                self.phonetic_label.configure(text=f"{word} · 词典暂未收录，仍可听发音", text_color="#A096B0", font=self.phonetic_hint_font)
                self.read_word_btn.configure(state="normal")
                return
        self.phonetic_label.configure(text="选中一个英文单词，查看美式音标", text_color="#A096B0", font=self.phonetic_hint_font)
        self.read_word_btn.configure(state="disabled")

    def read_aloud(self, area):
        self.cancel_dictation()
        if self._speech_area == area:
            self.stop_reading()
            return
        if area == "word":
            text, language = self._pronounce_word, "en"
        elif area == "result":
            if not self._valid_result:
                return
            text = self._selected(self.result) or self.result.get("1.0", "end-1c").strip()
            language = _direction(text, "auto")[0] if self._selected(self.result) else self._result_language
        else:
            text = self._selected(self.source) or self.text().strip()
            language = _direction(text, "auto")[0]
        if not text:
            self._say("先输入文字，或选中一个英文单词再朗读吧。", "#B57B83")
            return
        if len(text) > 5000:
            self._say("一次最多朗读 5000 个字符，请分段朗读。", "#B57B83")
            return
        self._speech_area = area
        self._speech_id = self.reader.speak(text, language=language or "en", slow=self.slow_var.get())
        if self._speech_id is None:
            self._speech_area = None
        self._reading_buttons()
        self._say("●  正在准备本地朗读…" if self._speech_id is not None else "无法启动本地朗读，请检查系统语音。",
                  PURPLE if self._speech_id is not None else "#B57B83")

    def _reading_buttons(self):
        for area, button, title in (("source", self.read_source_btn, "朗读"),
                                    ("result", self.read_result_btn, "朗读"),
                                    ("word", self.read_word_btn, "听单词")):
            button.configure(text="停止" if self._speech_area == area else title)
        if self.learning_page is not None:
            self.learning_page.set_speaking(self._speech_area == "learning")

    def stop_reading(self):
        was_reading = self._speech_area is not None
        self.reader.stop()
        self._speech_id = None
        self._speech_area = None
        self._reading_buttons()
        if was_reading:
            self._say("●  已停止朗读", "#879B8D")

    def _speech_event(self, payload):
        if payload.get("state") == "ready":
            return
        if self._speech_id is None or payload.get("id") != self._speech_id:
            return
        state = payload.get("state")
        if state == "speaking":
            self._say("●  正在离线朗读 · 再点一次可停止", PURPLE)
        elif state in ("finished", "stopped", "error"):
            learning_speech = self._speech_area == "learning"
            self._speech_area = None
            self._speech_id = None
            self._reading_buttons()
            self._say(payload.get("message") or ("●  朗读完成" if state == "finished" else "●  已停止朗读"),
                      "#B57B83" if state == "error" else "#879B8D")
            if learning_speech and state == "error" and self.learning_page is not None:
                self.learning_page.set_message(payload.get("message") or "英语朗读暂时不可用。", error=True)

    def clear(self):
        self.cancel_dictation()
        self._pending = None
        self._set_input("")
        self._set_result("")
        self.result_meta.configure(text="等待一段新的小对话")
        self.out_title.configure(text="译文")
        self._update_phonetics()
        self._say("●  清空啦，开始一段新的小对话", "#879B8D")
        self.source.focus_set()

    def paste_translate(self):
        self.cancel_dictation()
        self.show_page("translate")
        self.show()
        try:
            text = self.clipboard_get()
        except tk.TclError:
            self._say("剪贴板里没有文字，复制一段文字再试试。", "#B57B83")
            return
        if len(text) > 5000:
            self._say("剪贴板超过 5000 字，请分段粘贴。", "#B57B83")
            return
        self._set_input(text)
        self.translate()

    def copy(self):
        if not self._valid_result:
            return
        text = self.result.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.copy_btn.configure(text="已复制")
        self._say("●  译文已复制，去分享吧", "#879B8D")
        self.after(1500, lambda: self.copy_btn.configure(text="复制") if not self._closing else None)

    def capture_translate(self):
        if self._closing:
            return
        self.cancel_dictation()
        self.stop_reading()
        if self._debounce:
            self.after_cancel(self._debounce)
            self._debounce = None
        self.capture.start()

    def _ocr_event(self, payload):
        self.cancel_dictation()
        state = payload.get("state")
        if state == "working":
            self.capture_btn.configure(state="disabled", text="识别中…")
            self._say("●  正在本机识别截图文字…", PURPLE)
            return
        self.capture_btn.configure(state="normal", text="截图翻译")
        self.show()
        if state == "cancelled":
            self._say("●  已取消截图", MUTED)
            if self.text().strip() and len(self.text()) <= 5000 and not self._valid_result:
                if self._debounce:
                    self.after_cancel(self._debounce)
                self._debounce = self.after(550, self.translate)
        elif payload.get("error"):
            self._say(payload["error"], "#B57B83")
        elif payload.get("text", "").strip():
            self.show_page("translate")
            self._set_input(payload["text"])
            self.translate()
        else:
            self._say("没有识别到文字，框选清晰的文字区域再试试。", "#B57B83")

    def translate(self):
        if self._debounce:
            self.after_cancel(self._debounce)
            self._debounce = None
        sig = self.signature()
        if not sig[0]:
            self._say("先输入或粘贴一段文字吧。", "#B57B83")
            self.source.focus_set()
            return
        if len(self.text()) > 5000:
            self._say("一次最多翻译 5000 字，请分成几段。", "#B57B83")
            return
        if self._busy:
            if sig != self._active:
                self._pending = sig
                self._say("●  已准备下一条，当前请求完成后翻译…")
            return
        self._run(sig)

    def _run(self, sig):
        self._busy = True
        self._active = sig
        self._say("●  正在翻译，请稍等一下…", PURPLE)

        self.jobs.put(sig)

    def _worker(self):
        # Reuse one worker and its HTTPS connections; never call Tk from here.
        try:
            self.events.put(("offline_ready", self.engine.warmup()))
        except Exception:
            self.events.put(("offline_ready", {"available": False}))
        while True:
            sig = self.jobs.get()
            if sig is None:
                return
            try:
                value = self.engine.translate(sig[0], sig[1])
                self.events.put(("result", (sig, value, None)))
            except TranslationError as exc:
                self.events.put(("result", (sig, None, str(exc))))
            except Exception:
                self.events.put(("result", (sig, None, "翻译暂时没有完成，请重试。")))


    def _complete(self, sig, value, error):
        self._busy = False
        self._active = None
        if sig == self.signature():
            if error:
                self._say(error, "#B57B83")
            else:
                if self._debounce:
                    self.after_cancel(self._debounce)
                    self._debounce = None
                self._set_result(value["text"])
                self._valid_result = True
                self._result_language = value["target"]
                self.copy_btn.configure(state="normal")
                self.read_result_btn.configure(state="normal")
                self._update_phonetics()
                target = "English" if value["target"] == "en" else "中文"
                self.out_title.configure(text=f"译文 · {target}")
                source = "中文" if value["source"].startswith("zh") else "English"
                cache = " · 缓存" if value.get("cached") else ""
                self.result_meta.configure(text=f"{source} → {target} · {value['elapsed']:.2f}s{cache}")
                self._say(f"●  翻译完成 · {value['provider']}", "#879B8D")
        else:
            self._say("●  原文已更新，随时可以翻译", "#879B8D")
        pending, self._pending = self._pending, None
        if pending and pending == self.signature():
            self._run(pending)

    def _poll(self):
        if self._closing:
            return
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "result":
                    self._complete(*payload)
                elif kind == "show":
                    self.show()
                elif kind == "translate_clipboard":
                    self.paste_translate()
                elif kind == "capture":
                    self.capture_translate()
                elif kind == "ocr":
                    self._ocr_event(payload)
                elif kind == "offline_ready":
                    self._offline_ready = bool(payload.get("available"))
                    self.service_note.configure(text="联网优先 · 本地已就绪" if self._offline_ready else "本地模型未就绪")
                elif kind == "speech":
                    self._speech_event(payload)
                elif kind == "dictation":
                    self._dictation_event(payload)
                elif kind == "quit":
                    self.quit_app()
                    return
        except queue.Empty:
            pass
        self.after(70, self._poll)

    def show(self):
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(150, lambda: self.attributes("-topmost", self.pin_var.get()) if not self._closing else None)
        self.focus_force()
        if self._current_page == "translate":
            self.source.focus_set()
        elif self.learning_page is not None:
            self.learning_page.refresh()

    def hide(self):
        self.cancel_dictation()
        if self.native_status.get("tray"):
            self.withdraw()
        elif self.native is None:
            self.quit_app()
        else:
            self.quit_app()

    def quit_app(self):
        if self._closing:
            return
        self._closing = True
        self.dictation.close()
        self.jobs.put(None)
        self.reader.close()
        self.capture.close()
        self.engine.close()
        if self.native:
            self.native.stop()
        self.destroy()

    def smoke_test(self, report_path):
        """Exercise the real UI and service with synthetic, non-private text."""
        import json
        report_path = Path(report_path)
        self._set_input("rabbit")
        self.translate()
        attempts = [0]

        def finish():
            attempts[0] += 1
            if self._busy and attempts[0] < 550:
                self.after(100, finish)
                return
            self.update_idletasks()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            result = self.result.get("1.0", "end-1c")
            report_path.write_text(json.dumps({"translated": result, "valid": self._valid_result,
                "status": self.status.cget("text"), "native": self.native_status,
                "offline_ready": self._offline_ready, "phonetics": self.phonetic_label.cget("text"),
                "dimensions": [self.winfo_width(), self.winfo_height()]}, ensure_ascii=False, indent=2), encoding="utf-8")
            left, top = self.winfo_rootx(), self.winfo_rooty()
            ImageGrab.grab(bbox=(left, top, left + self.winfo_width(), top + self.winfo_height())).save(report_path.with_suffix(".png"))
            self.after(100, self.quit_app)
        self.after(300, finish)

    def learning_smoke_test(self, report_path):
        """Exercise bundled practice, persistence and silent English speech."""
        import json
        report_path = Path(report_path)
        report = {"ok": False, "silent_speech": True}
        self.reader.close()
        self.reader = SpeechReader(self.events, ASSETS, silent=True)
        self.show_page("learning")
        page = self.learning_page
        if page is None:
            report.update(error="学习页未打开")
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            self.quit_app()
            return
        try:
            report["content_words"] = sum(item["kind"] == "word" for item in self.learning_store.items)
            report["content_sentences"] = sum(item["kind"] == "sentence" for item in self.learning_store.items)
            page.word_goal.set("3")
            page.sentence_goal.set("2")
            page.save_btn.invoke()
            report["settings"] = self.learning_store.get_settings()
            report["initial_counts"] = page.plan["counts"]
            report["english"] = page._current["english"]
            report["phonetics"] = page.ipa_label.cget("text")
            first_id = page._current["id"]
            page.known_btn.invoke()
            reloaded = LearningStore(ASSETS / "learning_content.json", state_path=self._learning_state_path)
            report["progress_saved"] = first_id in reloaded.today_plan()["done_ids"]
            report["next_card_selected"] = page._current["id"] != first_id
            page.slow_var.set(True)
            page.read_btn.invoke()
            report["read_started"] = self._speech_id is not None and self._speech_area == "learning"
            attempts = [0]

            def finish():
                attempts[0] += 1
                if self._speech_id is not None and attempts[0] < 150:
                    self.after(100, finish)
                    return
                report["read_finished"] = self._speech_id is None
                report["voices"] = self.reader.voices
                page._change_kind("今日句子")
                report["sentence"] = page._current["english"]
                report["sentence_read_enabled"] = page.read_btn.cget("state") == "normal"
                report["example_read_disabled"] = page.example_read_btn.cget("state") == "disabled"
                self.geometry("900x680")
                self.update()
                right = self.winfo_rootx() + self.winfo_width()
                bottom = self.winfo_rooty() + self.winfo_height()
                controls = (page.save_btn, page.read_btn, page.known_btn, page.again_btn, page.status_label)
                report["minimum_layout"] = all(widget.winfo_rootx()+widget.winfo_width() <= right
                    and widget.winfo_rooty()+widget.winfo_height() <= bottom for widget in controls)
                self.show_page("translate")
                report["translation_page_returns"] = self._current_page == "translate"
                self.show_page("learning")
                page._change_kind("今日单词")
                self.geometry("1000x740")
                self.update()
                report["ok"] = bool(report["content_words"] >= 160 and report["content_sentences"] >= 80
                    and report["progress_saved"] and report["next_card_selected"] and report["read_started"]
                    and report["read_finished"] and "en" in report["voices"]
                    and report["minimum_layout"] and report["translation_page_returns"])
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                self.lift()
                self.attributes("-topmost", True)
                self.update()
                left, top = self.winfo_rootx(), self.winfo_rooty()
                ImageGrab.grab(bbox=(left, top, left+self.winfo_width(), top+self.winfo_height())).save(report_path.with_suffix(".png"))
                self.after(100, self.quit_app)
            self.after(100, finish)
        except Exception as exc:
            report.update(error=f"{type(exc).__name__}: {exc}")
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            self.quit_app()


def dictation_ui_self_test(report_path, *, menu_preview=False):
    """Exercise real widgets with fake services; never open the microphone."""
    import json
    from unittest.mock import Mock, patch

    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    recorder = Mock(recording=False, busy=False)
    recorder.start.side_effect = (1, 2)
    engine = Mock()
    engine.warmup.return_value = {"available": True}
    engine.translate.side_effect = AssertionError("UI diagnostic must not translate")
    services = (("Translator", engine), ("SpeechReader", Mock()),
                ("ScreenshotCapture", Mock()), ("DictationRecorder", recorder))
    from contextlib import ExitStack
    with ExitStack() as stack:
        module = sys.modules[__name__]
        for name, service in services:
            stack.enter_context(patch.object(module, name, return_value=service))
        ctk.set_appearance_mode("light")
        app = TutuApp(native=False)
        app.geometry("900x680")
        app.attributes("-topmost", True)
        app.lift()
        report = {"ok": False, "fake_audio": True, "network_used": False}

        def cancel_debounce():
            if app._debounce:
                app.after_cancel(app._debounce)
                app._debounce = None

        def exercise():
            try:
                app._set_input("Original text stays until speech is recognized.")
                cancel_debounce()
                app.toggle_dictation()
                app._dictation_event({"id": 1, "state": "recording"})
                report["recording_button"] = app.dictation_btn.cget("text") == "结束录音"
                app.toggle_dictation()
                report["stop_called"] = recorder.stop.call_count == 1
                app._dictation_event({"id": 1, "state": "result", "text": "Good morning! How are you today?"})
                report["text_inserted"] = app.text() == "Good morning! How are you today?"
                report["translation_scheduled"] = app._debounce is not None
                cancel_debounce()
                app.toggle_dictation()
                app._set_input("An edit must survive a late recognition result.")
                cancel_debounce()
                app._dictation_event({"id": 2, "state": "result", "text": "Stale result"})
                report["edit_preserved"] = app.text() == "An edit must survive a late recognition result."
                app._set_input("Good morning! How are you today?")
                cancel_debounce()
                app._set_result("早上好！你今天怎么样？")
                app.result_meta.configure(text="语音输入界面演示 · 译文为预设示例")
                app._say("●  语音在本机识别 · 点击语音输入，或按 Ctrl + R", "#879B8D")
                app.update_idletasks()
                controls = (app.capture_btn, app.dictation_btn, app.service_note,
                            app.read_source_btn, app.read_result_btn, app.status)
                report["dimensions"] = [app.winfo_width(), app.winfo_height()]
                report["controls_fit"] = all(w.winfo_ismapped() and w.winfo_rootx() >= app.winfo_rootx()
                    and w.winfo_rootx() + w.winfo_width() <= app.winfo_rootx() + app.winfo_width()
                    and w.winfo_rooty() + w.winfo_height() <= app.winfo_rooty() + app.winfo_height()
                    for w in controls)
                if menu_preview:
                    from types import SimpleNamespace
                    editor = app.source._textbox
                    editor.tag_add("sel", "1.0", "1.4")
                    editor.mark_set("insert", "end-1c")
                    with patch.object(editor, "clipboard_get", return_value="Hello"):
                        event = SimpleNamespace(x_root=editor.winfo_rootx() + 70,
                                                y_root=editor.winfo_rooty() + 66)
                        app.source_menu.show(event)
                        app.update()
                        app.source_menu.invoke("paste")
                        report["menu_paste"] = app.text() == "Hello morning! How are you today?"
                        report["menu_paste_scheduled"] = app._debounce is not None
                        cancel_debounce()
                        app._set_input("Good morning! How are you today?")
                        cancel_debounce()
                        editor.tag_add("sel", "1.0", "1.12")
                        app.source_menu.show(event)
                        app.update()
                        app.source_menu.hover("paste")
                        import time
                        for _ in range(6):
                            app.update()
                            time.sleep(.02)
                        report["menu_visible"] = bool(app.source_menu.popup and app.source_menu.popup.winfo_ismapped())
                        report["menu_has_no_grab"] = app.grab_current() is None
                        report["selection_preserved"] = bool(editor.tag_ranges("sel"))
                        if app.source_menu.popup:
                            p = app.source_menu.popup
                            report["menu_geometry"] = [p.winfo_rootx(), p.winfo_rooty(), p.winfo_width(), p.winfo_height()]
                left, top = app.winfo_rootx(), app.winfo_rooty()
                ImageGrab.grab(bbox=(left, top, left + app.winfo_width(), top + app.winfo_height()),
                               include_layered_windows=menu_preview).save(report_path.with_suffix(".png"))
                report["ok"] = all(report.get(key) for key in ("recording_button", "stop_called", "text_inserted",
                    "translation_scheduled", "edit_preserved", "controls_fit"))
                if menu_preview:
                    app.source_menu.close()
                    report["menu_closed"] = app.source_menu.popup is None
                    report["ok"] = report["ok"] and all(report.get(key) for key in (
                        "menu_paste", "menu_paste_scheduled", "menu_visible", "menu_has_no_grab",
                        "selection_preserved", "menu_closed"))
            except Exception as exc:
                report["error"] = str(exc)
            finally:
                report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                app.after(100, app.quit_app)

        app.after(400, exercise)
        app.mainloop()
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--learn", action="store_true", help="Open the English learning page")
    parser.add_argument("--dictation-audio-dir", metavar="DIRECTORY")
    smoke_group = parser.add_mutually_exclusive_group()
    smoke_group.add_argument("--smoke-test", metavar="REPORT")
    smoke_group.add_argument("--native-smoke-test", metavar="REPORT")
    smoke_group.add_argument("--speech-self-test", metavar="REPORT")
    smoke_group.add_argument("--ocr-self-test", metavar="REPORT")
    smoke_group.add_argument("--picker-self-test", metavar="REPORT")
    smoke_group.add_argument("--offline-self-test", metavar="REPORT")
    smoke_group.add_argument("--combined-self-test", metavar="REPORT")
    smoke_group.add_argument("--learning-self-test", metavar="REPORT")
    smoke_group.add_argument("--dictation-ui-smoke", metavar="REPORT")
    smoke_group.add_argument("--editor-menu-smoke", metavar="REPORT")
    smoke_group.add_argument("--dictation-self-test", metavar="REPORT")
    args = parser.parse_args()
    if args.editor_menu_smoke:
        return 0 if dictation_ui_self_test(args.editor_menu_smoke, menu_preview=True).get("ok") else 1
    if args.dictation_ui_smoke:
        return 0 if dictation_ui_self_test(args.dictation_ui_smoke).get("ok") else 1
    if args.dictation_self_test:
        if not args.dictation_audio_dir:
            parser.error("--dictation-self-test requires --dictation-audio-dir")
        from dictation import self_test as dictation_self_test
        return dictation_self_test(args.dictation_audio_dir, args.dictation_self_test)
    if args.speech_self_test:
        report = speech_self_test(ASSETS, args.speech_self_test)
        return 0 if report.get("ok") else 1
    if args.ocr_self_test:
        report = ocr_self_test(ASSETS, args.ocr_self_test)
        return 0 if report.get("ok") else 1
    if args.picker_self_test:
        report = picker_self_test(ASSETS, args.picker_self_test)
        return 0 if report.get("ok") else 1
    if args.offline_self_test:
        report = offline_self_test(report_path=args.offline_self_test)
        return 0 if report.get("passed") else 1
    if args.combined_self_test:
        import json
        report = {}
        try:
            report["translation"] = offline_self_test()
            report["ocr"] = ocr_self_test(ASSETS)
            report["speech"] = speech_self_test(ASSETS, None)
            report["ok"] = bool(report["translation"].get("passed") and report["ocr"].get("ok") and report["speech"].get("ok"))
            if args.dictation_audio_dir:
                import tempfile
                from dictation import self_test as dictation_self_test
                with tempfile.TemporaryDirectory(prefix="tutu-voice-check-") as temporary:
                    voice_report = Path(temporary) / "voice.json"
                    voice_ok = dictation_self_test(args.dictation_audio_dir, voice_report) == 0
                    report["dictation"] = json.loads(voice_report.read_text(encoding="utf-8"))
                    report["ok"] = report["ok"] and voice_ok
        except Exception as exc:
            report.update(ok=False, error=str(exc))
        Path(args.combined_self_test).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if report.get("ok") else 1
    if args.learning_self_test:
        import tempfile
        with tempfile.TemporaryDirectory(prefix="tutu-learning-check-") as temporary:
            ctk.set_appearance_mode("light")
            app = TutuApp(native=False, learning_state_path=Path(temporary) / "learning.json")
            app.after(300, lambda: app.learning_smoke_test(args.learning_self_test))
            app.mainloop()
        import json
        report = json.loads(Path(args.learning_self_test).read_text(encoding="utf-8"))
        return 0 if report.get("ok") else 1
    if not args.smoke_test and not acquire_instance():
        return
    set_app_id()
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    app = TutuApp(native=not bool(args.smoke_test))
    smoke_path = args.smoke_test or args.native_smoke_test
    if smoke_path:
        app.after(400, lambda: app.smoke_test(smoke_path))
    elif args.learn:
        app.after(280, lambda: app.show_page("learning"))
    app.mainloop()


if __name__ == "__main__":
    sys.exit(main())
