"""A compact, offline English learning page for Tutu Translate."""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk


BG = "#FAF8F5"
INK = "#393348"
MUTED = "#8A8398"
PURPLE = "#8B70C9"
LILAC = "#F0EBF9"
LINE = "#EAE5EF"
LEVELS = {"1 · 轻松入门": 1, "2 · 基础交流": 2, "3 · 进阶表达": 3, "4 · 阅读挑战": 4}
TOPICS = {"上网 + 日常": "mixed", "日常生活": "daily", "上网交流": "online"}


class LearningPage(ctk.CTkFrame):
    """Own the learning controls, leaving persistence and speech to callers.

    ``store`` exposes get_settings/configure/today_plan/rate. ``on_read``
    receives English text and a ``slow`` keyword. Root speech events should
    call set_speaking so stopping and completion update both read buttons.
    Call refresh when showing this page, including after a day changes.
    """

    def __init__(self, master, store, phonetics, on_read, on_stop, **kwargs):
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)
        self.store = store
        self.phonetics = phonetics
        self.on_read = on_read
        self.on_stop = on_stop
        self.plan = None
        self.kind = "word"
        self.index = 0
        self._speaking = False
        self._read_area = "english"
        self._current = None
        self._last_date = None
        self._settings_ready = False
        self.slow_var = tk.BooleanVar(master=self, value=False)
        self.show_chinese = tk.BooleanVar(master=self, value=True)
        self.word_goal = tk.StringVar(master=self, value="10")
        self.sentence_goal = tk.StringVar(master=self, value="5")
        self.level_var = tk.StringVar(master=self, value=next(iter(LEVELS)))
        self.topic_var = tk.StringVar(master=self, value=next(iter(TOPICS)))
        self._build()
        self.refresh()

    @staticmethod
    def font(size=13, bold=False, english=False):
        return ctk.CTkFont(family="Segoe UI" if english else "Microsoft YaHei UI",
                           size=size, weight="bold" if bold else "normal")

    def _label(self, parent, text="", size=13, color=INK, bold=False, english=False, **kwargs):
        return ctk.CTkLabel(parent, text=text, font=self.font(size, bold, english),
                            text_color=color, **kwargs)

    def _button(self, parent, text, command, primary=False, **kwargs):
        kwargs.setdefault("height", 35)
        kwargs.setdefault("corner_radius", 11)
        return ctk.CTkButton(parent, text=text, command=command,
                             font=self.font(12, primary),
                             fg_color=PURPLE if primary else LILAC,
                             hover_color="#7860B4" if primary else "#E5DDF4",
                             text_color="white" if primary else "#715A9F", **kwargs)

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        heading = ctk.CTkFrame(self, fg_color="transparent")
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 9))
        heading.grid_columnconfigure(0, weight=1)
        self._label(heading, "每天一点英语", size=25, bold=True).grid(row=0, column=0, sticky="w")
        self.streak_label = self._label(heading, "从今天开始", size=11, color="#8F7AA6")
        self.streak_label.grid(row=0, column=1, sticky="e", padx=(8, 0))

        settings = ctk.CTkFrame(self, fg_color=LILAC, corner_radius=15)
        settings.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        settings.grid_columnconfigure(0, weight=1)
        first = ctk.CTkFrame(settings, fg_color="transparent")
        first.grid(row=0, column=0, sticky="ew", padx=13, pady=(10, 4))
        first.grid_columnconfigure(5, weight=1)
        self._label(first, "难度", size=11, color="#857494").grid(row=0, column=0, padx=(0, 7))
        self.level_select = ctk.CTkOptionMenu(first, values=list(LEVELS), variable=self.level_var,
                           width=145, height=29, font=self.font(11), dropdown_font=self.font(11),
                           fg_color="white", button_color="#D7CBEA", button_hover_color="#C9BAE1",
                           text_color="#715A9F", dropdown_fg_color="white", dropdown_text_color=INK,
                           dropdown_hover_color=LILAC, corner_radius=9)
        self.level_select.grid(row=0, column=1)
        self._label(first, "内容", size=11, color="#857494").grid(row=0, column=2, padx=(18, 7))
        self.topic_select = ctk.CTkOptionMenu(first, values=list(TOPICS), variable=self.topic_var,
                           width=133, height=29, font=self.font(11), dropdown_font=self.font(11),
                           fg_color="white", button_color="#D7CBEA", button_hover_color="#C9BAE1",
                           text_color="#715A9F", dropdown_fg_color="white", dropdown_text_color=INK,
                           dropdown_hover_color=LILAC, corner_radius=9)
        self.topic_select.grid(row=0, column=3)
        self._label(first, "随时可调整", size=10, color="#A194B1").grid(row=0, column=5, sticky="e")

        goals = ctk.CTkFrame(settings, fg_color="transparent")
        goals.grid(row=1, column=0, sticky="ew", padx=13, pady=(0, 10))
        goals.grid_columnconfigure(5, weight=1)
        self._label(goals, "每日", size=11, color="#857494").grid(row=0, column=0, padx=(0, 7))
        self.word_entry = ctk.CTkEntry(goals, textvariable=self.word_goal, width=47, height=29,
                              font=self.font(12), corner_radius=9, border_color="#DED4EB",
                              fg_color="white", text_color=INK, justify="center")
        self.word_entry.grid(row=0, column=1)
        self._label(goals, "个单词", size=11, color="#857494").grid(row=0, column=2, padx=(6, 13))
        self.sentence_entry = ctk.CTkEntry(goals, textvariable=self.sentence_goal, width=47, height=29,
                              font=self.font(12), corner_radius=9, border_color="#DED4EB",
                              fg_color="white", text_color=INK, justify="center")
        self.sentence_entry.grid(row=0, column=3)
        self._label(goals, "个句子", size=11, color="#857494").grid(row=0, column=4, padx=(6, 7))
        self._label(goals, "保存后更新今日计划", size=10, color="#A194B1").grid(row=0, column=5, sticky="e", padx=(6, 11))
        self.save_btn = self._button(goals, "保存", self.save_settings, primary=True, width=65, height=29)
        self.save_btn.grid(row=0, column=6)
        self.word_entry.bind("<Return>", lambda _event: self.save_settings())
        self.sentence_entry.bind("<Return>", lambda _event: self.save_settings())

        overview = ctk.CTkFrame(self, fg_color="transparent")
        overview.grid(row=2, column=0, sticky="ew")
        overview.grid_columnconfigure(1, weight=1)
        self.kind_tabs = ctk.CTkSegmentedButton(overview, values=["今日单词", "今日句子"],
                             command=self._change_kind, font=self.font(12), height=32,
                             fg_color=LILAC, unselected_color=LILAC,
                             unselected_hover_color="#E5DDF4", selected_color=PURPLE,
                             selected_hover_color="#7860B4", text_color=INK,
                             text_color_disabled=MUTED, corner_radius=10)
        self.kind_tabs.set("今日单词")
        self.kind_tabs.grid(row=0, column=0, sticky="w")
        self.progress_label = self._label(overview, "今日进度 0 / 0", size=12, color="#857494")
        self.progress_label.grid(row=0, column=1, sticky="e")

        progress = ctk.CTkFrame(self, fg_color="transparent")
        progress.grid(row=3, column=0, sticky="ew", pady=(7, 9))
        progress.grid_columnconfigure(0, weight=1)
        self.progress_bar = ctk.CTkProgressBar(progress, height=5, corner_radius=5,
                                             fg_color="#EAE2F3", progress_color=PURPLE)
        self.progress_bar.grid(row=0, column=0, sticky="ew")
        self.progress_bar.set(0)
        self.plan_hint = self._label(progress, "", size=10, color=MUTED, anchor="w", justify="left", wraplength=570)
        self.plan_hint.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        self.card = ctk.CTkFrame(self, fg_color="white", border_width=1, border_color=LINE, corner_radius=18)
        self.card.grid(row=4, column=0, sticky="nsew")
        self.card.grid_columnconfigure(0, weight=1)
        self.card.grid_rowconfigure(1, weight=1)
        metadata = ctk.CTkFrame(self.card, fg_color="transparent")
        metadata.grid(row=0, column=0, sticky="ew", padx=18, pady=(12, 3))
        metadata.grid_columnconfigure(1, weight=1)
        self.topic_badge = self._label(metadata, "日常生活", size=11, color="#8F7AA6")
        self.topic_badge.grid(row=0, column=0, sticky="w")
        self.card_position = self._label(metadata, "", size=11, color="#A194B1")
        self.card_position.grid(row=0, column=1, sticky="e")

        self.content = ctk.CTkScrollableFrame(self.card, fg_color="white", corner_radius=0,
                      scrollbar_button_color="#E0D6ED", scrollbar_button_hover_color="#CAB9E0",
                      border_width=0)
        self.content.grid(row=1, column=0, sticky="nsew", padx=13, pady=(0, 4))
        self.content.grid_columnconfigure(0, weight=1)
        self.english_label = self._label(self.content, size=28, bold=True, english=True,
                              anchor="w", justify="left", wraplength=550)
        self.english_label.grid(row=0, column=0, sticky="ew", padx=3, pady=(2, 3))
        self.ipa_label = self._label(self.content, size=15, color="#9A88B2", english=True,
                                   anchor="w", justify="left", wraplength=550)
        self.ipa_label.grid(row=1, column=0, sticky="ew", padx=3, pady=(0, 8))
        self.chinese_label = self._label(self.content, size=17, color="#6F6183", anchor="w", justify="left", wraplength=550)
        self.chinese_label.grid(row=2, column=0, sticky="ew", padx=3, pady=(0, 11))
        self.example_caption = self._label(self.content, "试着这样说", size=10, color="#A194B1", anchor="w")
        self.example_caption.grid(row=3, column=0, sticky="ew", padx=3)
        self.example_label = self._label(self.content, size=16, color="#655B75", english=True,
                                        anchor="w", justify="left", wraplength=550)
        self.example_label.grid(row=4, column=0, sticky="ew", padx=3, pady=(1, 3))
        self.example_chinese = self._label(self.content, size=12, color="#9A8DA9", anchor="w", justify="left", wraplength=550)
        self.example_chinese.grid(row=5, column=0, sticky="ew", padx=3, pady=(0, 7))

        reading = ctk.CTkFrame(self.card, fg_color="transparent")
        reading.grid(row=2, column=0, sticky="ew", padx=17, pady=(4, 13))
        reading.grid_columnconfigure(3, weight=1)
        self.read_btn = self._button(reading, "朗读英语", self.read_current, primary=True, width=100)
        self.read_btn.grid(row=0, column=0, sticky="w")
        self.example_read_btn = self._button(reading, "读例句", self.read_example, width=77)
        self.example_read_btn.grid(row=0, column=1, padx=(8, 12))
        self.slow_switch = ctk.CTkSwitch(reading, text="慢速", variable=self.slow_var,
                               font=self.font(11), text_color="#8A7B9C", width=78,
                               switch_width=29, switch_height=16, progress_color=PURPLE)
        self.slow_switch.grid(row=0, column=2)
        self.meaning_btn = self._button(reading, "收起中文", self.toggle_meaning, width=90)
        self.meaning_btn.grid(row=0, column=4, sticky="e", padx=(6, 0))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=5, column=0, sticky="ew", pady=(11, 0))
        actions.grid_columnconfigure(2, weight=1)
        self.prev_btn = self._button(actions, "上一张", lambda: self.navigate(-1), width=71)
        self.prev_btn.grid(row=0, column=0)
        self.next_btn = self._button(actions, "下一张", lambda: self.navigate(1), width=71)
        self.next_btn.grid(row=0, column=1, padx=(7, 0))
        self.again_btn = self._button(actions, "再练一练", lambda: self.rate_current("again"), width=109)
        self.again_btn.grid(row=0, column=3, padx=(8, 7))
        self.known_btn = self._button(actions, "记住了 ✓", lambda: self.rate_current("known"), primary=True, width=109)
        self.known_btn.grid(row=0, column=4)
        self.status_label = self._label(self, "", size=11, color="#879B8D", anchor="w", justify="left", wraplength=600)
        self.status_label.grid(row=6, column=0, sticky="ew", pady=(7, 0))
        self.bind("<Configure>", self._resize, add="+")
        self.content.bind("<Configure>", self._resize_content, add="+")

    def _resize(self, event=None):
        width = max(250, (event.width if event else self.winfo_width()) - 6)
        self.plan_hint.configure(wraplength=width)
        self.status_label.configure(wraplength=width)

    def _resize_content(self, event=None):
        width = max(220, (event.width if event else self.content.winfo_width()) - 12)
        for label in (self.english_label, self.ipa_label, self.chinese_label,
                      self.example_label, self.example_chinese):
            label.configure(wraplength=width)

    def _status(self, text, error=False):
        self.status_label.configure(text=text, text_color="#B57B83" if error else "#879B8D")

    def set_message(self, text, error=False):
        """Show an inline speech/persistence message from the host."""
        self._status(text, error=error)

    def _set_settings(self, settings):
        self.word_goal.set(str(settings.get("word_count", 10)))
        self.sentence_goal.set(str(settings.get("sentence_count", 5)))
        level = settings.get("level", 1)
        topic = settings.get("topic", "mixed")
        self.level_var.set(next((label for label, value in LEVELS.items() if value == level), next(iter(LEVELS))))
        self.topic_var.set(next((label for label, value in TOPICS.items() if value == topic), next(iter(TOPICS))))
        self._settings_ready = True

    def refresh(self):
        """Refresh progress from disk/store; preserve an existing card when possible."""
        current_id = self._current.get("id") if self._current else None
        try:
            plan = self.store.today_plan()
            if not self._settings_ready:
                self._set_settings(self.store.get_settings())
            new_day = self._last_date is not None and self._last_date != plan["date"]
            self.plan = plan
            self._last_date = plan["date"]
            if new_day:
                self._stop_speech()
            self._choose_card(None if new_day else current_id)
            self._update_overview()
            self._render_card()
            if new_day:
                self._status("新的一天，今日练习已经准备好了。")
            elif plan.get("completed"):
                self._status("今日目标完成啦！可以回看卡片，明天再来积累。")
            else:
                self._status("先听一听，再试着读出来；再练的内容会安排复习。")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self._status(f"暂时无法读取学习计划：{exc}", error=True)
            if self.plan is None:
                self._current = None
                self._render_card()

    def save_settings(self):
        try:
            word_count = int(self.word_goal.get().strip())
            sentence_count = int(self.sentence_goal.get().strip())
            if not 1 <= word_count <= 30 or not 1 <= sentence_count <= 15:
                self._status("每日单词可设 1–30 个，句子可设 1–15 个。", error=True)
                return
            plan = self.store.configure(LEVELS[self.level_var.get()], word_count,
                                        sentence_count, TOPICS[self.topic_var.get()])
            self._stop_speech()
            self.plan = plan
            self._last_date = plan["date"]
            self._set_settings(plan["settings"])
            self._choose_card()
            self._update_overview()
            self._render_card()
            self._status("计划已保存。今日学过的卡片会保留，复习会计入每日目标。")
        except ValueError as exc:
            self._status(f"目标没有保存：{exc}" if str(exc) and "invalid literal" not in str(exc)
                         else "每日目标请填写整数，例如 10 个单词、5 个句子。", error=True)
        except (OSError, KeyError, TypeError) as exc:
            self._status(f"计划没有保存：{exc}", error=True)

    def _items(self):
        return self.plan.get("words" if self.kind == "word" else "sentences", []) if self.plan else []

    def _choose_card(self, preferred_id=None, *, after_index=None):
        items = self._items()
        if not items:
            self.index = 0
            self._current = None
            return
        if preferred_id is not None:
            for index, item in enumerate(items):
                if item["id"] == preferred_id:
                    self.index = index
                    self._current = item
                    return
        done = set(self.plan.get("done_ids", []))
        start = 0 if after_index is None else after_index + 1
        for offset in range(len(items)):
            index = (start + offset) % len(items)
            if items[index]["id"] not in done:
                self.index = index
                self._current = items[index]
                return
        self.index = min(max(self.index, 0), len(items) - 1)
        self._current = items[self.index]

    def _change_kind(self, value):
        self._stop_speech()
        self.kind = "word" if value == "今日单词" else "sentence"
        self.index = 0
        self._choose_card()
        self._render_card()

    def _update_overview(self):
        if not self.plan:
            return
        counts = self.plan.get("counts", {})
        total = counts.get("word_total", len(self.plan.get("words", []))) + counts.get("sentence_total", len(self.plan.get("sentences", [])))
        done = counts.get("word_done", 0) + counts.get("sentence_done", 0)
        self.progress_label.configure(text=f"今日进度 {done} / {total}")
        self.progress_bar.set(done / total if total else 0)
        streak = self.plan.get("streak", 0)
        self.streak_label.configure(text=f"连续完成 {streak} 天" if streak else "今天开始积累")
        level = self.plan.get("settings", {}).get("level", 1)
        recommended = self.plan.get("recommended_level", level)
        if self.plan.get("completed_days", 0) >= 3 and recommended > level:
            self.plan_hint.configure(text=f"已完成 {self.plan['completed_days']} 天，准备好时可试试 {recommended} 级；难度由你决定。", text_color="#8F7AA6")
        else:
            self.plan_hint.configure(text=f"单词 {counts.get('word_done', 0)}/{counts.get('word_total', 0)} · 句子 {counts.get('sentence_done', 0)}/{counts.get('sentence_total', 0)} · 复习也计入今日目标", text_color=MUTED)

    def _render_card(self):
        item = self._current
        if item is None:
            self.topic_badge.configure(text="小兔的学习卡")
            self.card_position.configure(text="")
            self.english_label.configure(text="今天没有这一类练习", font=self.font(20, True))
            self.ipa_label.configure(text="可以在上方增加每日目标")
            self.chinese_label.configure(text="")
            self.example_label.configure(text="")
            self.example_chinese.configure(text="")
            self.example_caption.grid_remove()
            for button in (self.read_btn, self.example_read_btn, self.prev_btn, self.next_btn, self.known_btn, self.again_btn, self.meaning_btn):
                button.configure(state="disabled")
            return
        english = item.get("english", "")
        is_word = self.kind == "word"
        self.english_label.configure(text=english, font=self.font(28 if is_word else 23, True, english=True))
        topic = {"daily": "日常生活", "online": "上网交流"}.get(item.get("topic"), "上网 + 日常")
        badge = f"{topic}  ·  {item.get('level', 1)} 级"
        if item.get("review"):
            badge += "  ·  复习"
        self.topic_badge.configure(text=badge)
        done = item["id"] in set(self.plan.get("done_ids", []))
        self.card_position.configure(text=f"{'已学习 · ' if done else ''}{self.index + 1} / {len(self._items())}")
        ipa = ""
        if is_word:
            try:
                entry = self.phonetics.lookup(english)
                if entry:
                    ipa = "  ·  ".join(f"/{value}/" for value in entry.get("ipa", [])) + "  美式"
                else:
                    ipa = "听朗读，练习发音"
            except (OSError, ValueError, UnicodeError):
                ipa = "音标暂不可用，可以听朗读"
        self.ipa_label.configure(text=ipa)
        if ipa:
            self.ipa_label.grid()
        else:
            self.ipa_label.grid_remove()
        self.chinese_label.configure(text=item.get("chinese", ""))
        example = item.get("example") or ""
        self.example_label.configure(text=example)
        self.example_chinese.configure(text=item.get("example_zh") or "")
        for label in (self.example_caption, self.example_label):
            label.grid() if example else label.grid_remove()
        self._apply_meaning()
        for button in (self.read_btn, self.meaning_btn):
            button.configure(state="normal")
        for button in (self.known_btn, self.again_btn):
            button.configure(state="disabled" if done else "normal")
        self.example_read_btn.configure(state="normal" if example else "disabled")
        self.prev_btn.configure(state="normal" if self.index > 0 else "disabled")
        self.next_btn.configure(state="normal" if self.index < len(self._items()) - 1 else "disabled")
        self.known_btn.configure(text="已学习 ✓" if done else "记住了 ✓")
        self.content._parent_canvas.yview_moveto(0)
        self.set_speaking(self._speaking)

    def _apply_meaning(self):
        visible = self.show_chinese.get()
        self.meaning_btn.configure(text="收起中文" if visible else "显示中文")
        if self._current:
            self.chinese_label.grid() if visible else self.chinese_label.grid_remove()
            example_zh = self._current.get("example_zh")
            self.example_chinese.grid() if visible and example_zh else self.example_chinese.grid_remove()

    def toggle_meaning(self):
        self.show_chinese.set(not self.show_chinese.get())
        self._apply_meaning()

    def navigate(self, offset):
        items = self._items()
        if not items:
            return
        self._stop_speech()
        self.index = max(0, min(len(items) - 1, self.index + offset))
        self._current = items[self.index]
        self._render_card()

    def rate_current(self, rating):
        if not self._current:
            return
        try:
            index = self.index
            self.plan = self.store.rate(self._current["id"], rating)
            self._stop_speech()
            self._choose_card(after_index=index)
            self._update_overview()
            self._render_card()
            if self.plan.get("completed"):
                self._status("今日目标完成啦！可以回看卡片，明天再来积累。")
            elif all(item["id"] in set(self.plan.get("done_ids", [])) for item in self._items()):
                other = "句子" if self.kind == "word" else "单词"
                self._status(f"这一组学完啦，切到今日{other}继续。")
            else:
                self._status("已记下，之后会安排复习。" if rating == "again" else "记住一张，又进步一点点。")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self._status(f"进度没有保存：{exc}", error=True)

    def _stop_speech(self):
        self.on_stop()
        self.set_speaking(False)

    def read_current(self):
        self._read("english")

    def read_example(self):
        self._read("example")

    def _read(self, area):
        if not self._current:
            return
        if self._speaking:
            self._stop_speech()
            return
        text = self._current.get(area) or ""
        if text:
            self._read_area = area
            try:
                self.on_read(text, slow=self.slow_var.get())
            except (OSError, RuntimeError, ValueError) as exc:
                self.set_speaking(False)
                self._status(f"朗读暂时不可用：{exc}", error=True)

    def set_speaking(self, speaking):
        """Update playback controls from the host's speech event handler."""
        self._speaking = bool(speaking)
        self.read_btn.configure(text="停止朗读" if speaking and self._read_area == "english" else "朗读英语")
        self.example_read_btn.configure(text="停止" if speaking and self._read_area == "example" else "读例句")

