"""Offline daily English practice and progress, separate from translation history."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


DEFAULT_SETTINGS = {"level": 1, "word_count": 10, "sentence_count": 5, "topic": "mixed"}
REVIEW_DAYS = (1, 3, 7, 14, 30)


class LearningStore:
    """Maintain a stable daily plan using only bundled item IDs and local progress.

    ``today`` may be a date, an ISO date, or a callable returning either. It is
    injectable so midnight transitions and review schedules can be tested.
    Methods that change progress return the same dictionary as ``today_plan``.
    File errors propagate as OSError; writes leave the previous file intact.
    """

    def __init__(self, content_path, state_path=None, today=None):
        self.content_path = Path(content_path)
        self.state_path = Path(state_path) if state_path is not None else (
            Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            / "TutuTranslate" / "learning.json"
        )
        self._today = today
        self.recovery_backup = None
        with self.content_path.open("r", encoding="utf-8-sig") as source:
            content = json.load(source)
        self._validate_content(content)
        self.levels = deepcopy(content["levels"])
        self.items = deepcopy(content["items"])
        self.all_items = self.items
        self._by_id = {item["id"]: item for item in self.items}
        self._level_ids = {level["id"] for level in self.levels}
        self._state = self._load()

    @staticmethod
    def _validate_content(content):
        if not isinstance(content, dict) or content.get("version") != 1:
            raise ValueError("学习词库版本无效")
        levels, items = content.get("levels"), content.get("items")
        if not isinstance(levels, list) or not levels or not isinstance(items, list) or not items:
            raise ValueError("学习词库不能为空")
        level_ids = set()
        for level in levels:
            if not isinstance(level, dict) or type(level.get("id")) is not int:
                raise ValueError("学习词库难度无效")
            if level["id"] not in range(1, 5) or level["id"] in level_ids:
                raise ValueError("学习词库难度重复或超出范围")
            level_ids.add(level["id"])
        item_ids = set()
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                raise ValueError("学习词卡 ID 无效")
            if item["id"] in item_ids or item.get("level") not in level_ids:
                raise ValueError("学习词卡 ID 重复或难度无效")
            if item.get("kind") not in ("word", "sentence") or item.get("topic") not in ("daily", "online"):
                raise ValueError("学习词卡类别无效")
            if not all(isinstance(item.get(key), str) and item[key].strip() for key in ("english", "chinese")):
                raise ValueError("学习词卡文字无效")
            item_ids.add(item["id"])

    def _date(self):
        value = self._today() if callable(self._today) else self._today
        if value is None:
            return date.today()
        if isinstance(value, datetime):
            return value.date()
        return value if isinstance(value, date) else date.fromisoformat(value)

    def _empty_state(self):
        settings = deepcopy(DEFAULT_SETTINGS)
        if settings["level"] not in self._level_ids:
            settings["level"] = min(self._level_ids)
        return {"version": 1, "settings": settings, "plans": {}, "progress": {}, "completed_dates": []}

    def _load(self):
        if not self.state_path.exists():
            return self._empty_state()
        try:
            with self.state_path.open("r", encoding="utf-8-sig") as source:
                state = json.load(source)
            self._validate_state(state)
        except (ValueError, TypeError, KeyError, UnicodeError):
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup = self.state_path.with_name(f"{self.state_path.name}.corrupt-{stamp}.bak")
            shutil.copy2(self.state_path, backup)
            self.recovery_backup = backup
            state = self._empty_state()
            self._write(state)
        return state

    def _validate_state(self, state):
        if not isinstance(state, dict) or state.get("version") != 1:
            raise ValueError("学习进度版本无效")
        settings = state["settings"]
        if settings != self._settings(**settings):
            raise ValueError("学习设置无效")
        if not isinstance(state["plans"], dict) or not isinstance(state["progress"], dict):
            raise ValueError("学习进度无效")
        completed = state["completed_dates"]
        if not isinstance(completed, list) or len(completed) != len(set(completed)):
            raise ValueError("学习日期无效")
        for day in completed:
            date.fromisoformat(day)
        for day, plan in state["plans"].items():
            date.fromisoformat(day)
            if not isinstance(plan, dict) or plan["date"] != day:
                raise ValueError("学习计划无效")
            if plan["settings"] != self._settings(**plan["settings"]):
                raise ValueError("学习计划设置无效")
            for key in ("word_ids", "sentence_ids", "review_ids", "done_ids"):
                ids = plan[key]
                if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(ids) != len(set(ids)):
                    raise ValueError("学习计划词卡无效")
            planned = set(plan["word_ids"] + plan["sentence_ids"])
            if not set(plan["done_ids"]).issubset(planned) or not set(plan["review_ids"]).issubset(planned):
                raise ValueError("学习计划完成状态无效")
            if not isinstance(plan["ratings"], dict) or set(plan["ratings"]) != set(plan["done_ids"]):
                raise ValueError("学习计划评分无效")
            if any(rating not in ("known", "again") for rating in plan["ratings"].values()):
                raise ValueError("学习计划评分无效")
        for item_id, progress in state["progress"].items():
            if not isinstance(item_id, str) or not isinstance(progress, dict):
                raise ValueError("学习复习进度无效")
            if type(progress["step"]) is not int or not 0 <= progress["step"] <= len(REVIEW_DAYS):
                raise ValueError("学习复习次数无效")
            if type(progress["seen"]) is not int or progress["seen"] < 1:
                raise ValueError("学习复习次数无效")
            date.fromisoformat(progress["due"])
            date.fromisoformat(progress["last_seen"])

    def _settings(self, level, word_count, sentence_count, topic):
        if isinstance(level, bool) or int(level) not in self._level_ids:
            raise ValueError("请选择有效的学习难度")
        if topic not in ("mixed", "daily", "online"):
            raise ValueError("请选择有效的学习主题")
        return {"level": int(level), "word_count": min(30, max(1, int(word_count))),
                "sentence_count": min(15, max(1, int(sentence_count))), "topic": topic}

    def _write(self, state):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", prefix=".learning-",
                                             suffix=".tmp", dir=self.state_path.parent, delete=False) as target:
                temporary = Path(target.name)
                json.dump(state, target, ensure_ascii=False, indent=2)
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.state_path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def _commit(self, state):
        self._write(state)
        self._state = state

    def get_settings(self):
        return deepcopy(self._state["settings"])

    def configure(self, level, word_count, sentence_count, topic):
        settings = self._settings(level, word_count, sentence_count, topic)
        if settings == self._state["settings"]:
            return self.today_plan()
        state = deepcopy(self._state)
        state["settings"] = settings
        day = self._date().isoformat()
        previous = state["plans"].get(day)
        state["plans"][day] = self._make_plan(state, day, previous)
        self._update_completion(state, day)
        self._commit(state)
        return self._render(day)

    def _make_plan(self, state, day, previous=None):
        settings = deepcopy(state["settings"])
        done_ids = [i for i in (previous or {}).get("done_ids", []) if i in self._by_id]
        review_ids = [i for i in (previous or {}).get("review_ids", []) if i in done_ids]
        plan = {"date": day, "settings": settings, "word_ids": [], "sentence_ids": [],
                "done_ids": done_ids, "review_ids": review_ids,
                "ratings": {i: previous["ratings"][i] for i in done_ids}}
        for kind, count_key in (("word", "word_count"), ("sentence", "sentence_count")):
            completed = [i for i in done_ids if self._by_id[i]["kind"] == kind]
            eligible = [item["id"] for item in self.items if item["kind"] == kind
                        and item["level"] == settings["level"]
                        and (settings["topic"] == "mixed" or item["topic"] == settings["topic"])
                        and item["id"] not in done_ids]
            remaining = max(0, settings[count_key] - len(completed))
            chosen, reviews = self._choose(eligible, remaining, day, state["progress"])
            plan[kind + "_ids"] = completed + chosen
            plan["review_ids"].extend(reviews)
        return plan

    @staticmethod
    def _choose(eligible, target, day, progress):
        # Stable ordering prevents reopening from replacing a day's cards.
        tie = lambda item_id: hashlib.sha256(f"{day}:{item_id}".encode("utf-8")).digest()
        new = sorted((i for i in eligible if i not in progress), key=tie)
        due = sorted((i for i in eligible if i in progress and progress[i]["due"] <= day),
                     key=lambda i: (progress[i]["due"], tie(i)))
        chosen = due[:min(target, max(1, round(target * .3)))] if due and target else []
        chosen.extend(new[:max(0, target - len(chosen))])
        chosen.extend(i for i in due if i not in chosen and len(chosen) < target)
        history = sorted((i for i in eligible if i in progress and i not in chosen),
                         key=lambda i: (progress[i]["last_seen"], tie(i)))
        chosen.extend(history[:max(0, target - len(chosen))])
        return chosen, [i for i in chosen if i in progress]

    def today_plan(self):
        day = self._date().isoformat()
        if day not in self._state["plans"]:
            state = deepcopy(self._state)
            state["plans"][day] = self._make_plan(state, day)
            self._commit(state)
        return self._render(day)

    def rate(self, item_id, rating="known"):
        if rating not in ("known", "again"):
            raise ValueError("请选择已掌握或再练习")
        day = self.today_plan()["date"]
        plan = self._state["plans"][day]
        if item_id not in plan["word_ids"] + plan["sentence_ids"]:
            raise ValueError("该词卡不属于今天的学习计划")
        if item_id in plan["done_ids"]:
            return self._render(day)
        state = deepcopy(self._state)
        plan = state["plans"][day]
        plan["done_ids"].append(item_id)
        plan["ratings"][item_id] = rating
        previous = state["progress"].get(item_id, {"step": 0, "seen": 0})
        step = min(len(REVIEW_DAYS), previous["step"] + 1) if rating == "known" else 0
        interval = REVIEW_DAYS[step - 1] if step else 1
        state["progress"][item_id] = {"step": step, "seen": previous["seen"] + 1,
                                      "last_seen": day,
                                      "due": (date.fromisoformat(day) + timedelta(days=interval)).isoformat()}
        self._update_completion(state, day)
        self._commit(state)
        return self._render(day)

    def _update_completion(self, state, day):
        plan = state["plans"][day]
        planned = plan["word_ids"] + plan["sentence_ids"]
        completed = bool(planned) and set(planned).issubset(plan["done_ids"])
        dates = set(state["completed_dates"])
        if completed:
            dates.add(day)
        else:
            dates.discard(day)
        state["completed_dates"] = sorted(dates)

    def _render(self, day):
        plan = self._state["plans"][day]
        done = set(plan["done_ids"])
        words = [dict(deepcopy(self._by_id[i]), review=i in plan["review_ids"])
                 for i in plan["word_ids"] if i in self._by_id]
        sentences = [dict(deepcopy(self._by_id[i]), review=i in plan["review_ids"])
                     for i in plan["sentence_ids"] if i in self._by_id]
        completed_dates = set(self._state["completed_dates"])
        cursor = date.fromisoformat(day)
        if day not in completed_dates:
            cursor -= timedelta(days=1)
        streak = 0
        while cursor.isoformat() in completed_dates:
            streak += 1
            cursor -= timedelta(days=1)
        completed_days = len(completed_dates)
        current_level = self._state["settings"]["level"]
        recommended = min(max(self._level_ids), max(current_level, 1 + completed_days // 3))
        return {"date": day, "settings": deepcopy(plan["settings"]), "words": words, "sentences": sentences,
                "done_ids": [i for i in plan["done_ids"] if i in self._by_id],
                "completed": bool(words or sentences) and all(c["id"] in done for c in words + sentences),
                "counts": {"word_total": len(words), "word_done": sum(c["id"] in done for c in words),
                           "sentence_total": len(sentences), "sentence_done": sum(c["id"] in done for c in sentences)},
                "streak": streak, "completed_days": completed_days, "recommended_level": recommended}
