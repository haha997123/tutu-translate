import json
from datetime import date, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from learning import LearningStore


class LearningStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.content = self.root / "学习词库.json"
        self.state = self.root / "学习记录.json"
        self.day = date(2026, 10, 14)
        levels = [{"id": i, "label": str(i), "description": "难度"} for i in range(1, 5)]
        items = [{"id": f"{level}-{kind}-{i}", "kind": kind, "level": level,
                  "topic": "daily" if i % 2 == 0 else "online", "english": f"item {level} {kind} {i}",
                  "chinese": "你好", "example": "Hello there.", "example_zh": "你好呀。"}
                 for level in range(1, 5) for kind in ("word", "sentence") for i in range(30)]
        self.content.write_text(json.dumps({"version": 1, "levels": levels, "items": items}, ensure_ascii=False),
                                encoding="utf-8")
        self.store = self.new_store()

    def new_store(self):
        return LearningStore(self.content, self.state, today=lambda: self.day)

    def ids(self, plan):
        return [card["id"] for card in plan["words"] + plan["sentences"]]

    def complete(self):
        plan = self.store.today_plan()
        for item_id in self.ids(plan):
            plan = self.store.rate(item_id)
        return plan

    def saved(self):
        return json.loads(self.state.read_text(encoding="utf-8"))

    def test_default_goals_and_daily_plan_stable_across_reopen(self):
        plan = self.store.today_plan()
        self.assertEqual(plan["counts"], {"word_total": 10, "word_done": 0, "sentence_total": 5, "sentence_done": 0})
        self.assertEqual(plan, self.store.today_plan())
        self.assertEqual(plan, self.new_store().today_plan())
        self.assertTrue(all(not card["review"] for card in plan["words"] + plan["sentences"]))

    def test_returns_copies_not_mutable_state(self):
        plan = self.store.today_plan()
        plan["words"][0]["english"] = "changed"
        plan["done_ids"].append("fake")
        settings = self.store.get_settings()
        settings["level"] = 4
        again = self.store.today_plan()
        self.assertNotEqual(again["words"][0]["english"], "changed")
        self.assertNotIn("fake", again["done_ids"])
        self.assertEqual(again["settings"]["level"], 1)

    def test_rate_is_persistent_and_duplicate_rating_does_not_advance(self):
        item_id = self.store.today_plan()["words"][0]["id"]
        self.store.rate(item_id, "known")
        state = self.saved()
        self.store.rate(item_id, "again")
        self.assertEqual(self.saved(), state)
        self.assertEqual(self.new_store().today_plan()["counts"]["word_done"], 1)
        self.assertEqual(state["progress"][item_id], {"step": 1, "seen": 1, "last_seen": "2026-10-14", "due": "2026-10-15"})

    def test_known_uses_progressive_review_intervals_and_again_resets(self):
        self.store.configure(1, 1, 1, "mixed")
        item_id = self.store.today_plan()["words"][0]["id"]
        for step, interval in enumerate((1, 3, 7, 14, 30, 30), start=1):
            plan = self.store.today_plan()
            self.assertIn(item_id, self.ids(plan))
            self.store.rate(item_id, "known")
            due = self.day + timedelta(days=interval)
            progress = self.saved()["progress"][item_id]
            self.assertEqual(progress["step"], min(step, 5))
            self.assertEqual(progress["due"], due.isoformat())
            self.day = due
        self.store.rate(item_id, "again")
        progress = self.saved()["progress"][item_id]
        self.assertEqual(progress["step"], 0)
        self.assertEqual(progress["due"], (self.day + timedelta(days=1)).isoformat())

    def test_cross_day_mix_has_due_reviews_then_new_cards(self):
        first = self.complete()
        self.day += timedelta(days=1)
        second = self.store.today_plan()
        self.assertEqual(sum(card["review"] for card in second["words"]), 3)
        self.assertEqual(sum(card["review"] for card in second["sentences"]), 2)
        self.assertEqual(second["done_ids"], [])
        self.assertEqual(second["streak"], 1)
        self.assertEqual(second["completed_days"], 1)
        self.assertTrue(set(self.ids(first)).intersection(self.ids(second)))
        self.assertTrue(set(self.ids(second)).difference(self.ids(first)))

    def test_configure_replaces_remaining_but_retains_done_cards(self):
        old = self.store.today_plan()
        retained = old["words"][0]["id"]
        self.store.rate(retained)
        plan = self.store.configure(3, 4, 2, "online")
        self.assertEqual(plan["done_ids"], [retained])
        self.assertEqual(plan["counts"]["word_total"], 4)
        self.assertEqual(plan["counts"]["word_done"], 1)
        for card in plan["words"] + plan["sentences"]:
            if card["id"] != retained:
                self.assertEqual((card["level"], card["topic"]), (3, "online"))
        self.assertEqual(plan, self.new_store().today_plan())
        self.day += timedelta(days=1)
        self.assertTrue(all(c["level"] == 3 for c in self.store.today_plan()["words"]))

    def test_lowering_target_never_discards_completed_cards(self):
        plan = self.store.today_plan()
        for card in plan["words"][:4]:
            self.store.rate(card["id"])
        plan = self.store.configure(1, 1, 1, "mixed")
        self.assertEqual(plan["counts"]["word_total"], 4)
        self.assertEqual(plan["counts"]["word_done"], 4)

    def test_goals_are_clamped_and_bad_options_rejected(self):
        plan = self.store.configure(2, 300, 200, "online")
        self.assertEqual(plan["settings"]["word_count"], 30)
        self.assertEqual(plan["settings"]["sentence_count"], 15)
        self.assertEqual(plan["counts"]["word_total"], 15)
        self.assertEqual(self.store.configure(1, 0, -1, "daily")["settings"]["word_count"], 1)
        for params in ((5, 10, 5, "mixed"), (1, 10, 5, "unknown")):
            with self.assertRaises(ValueError):
                self.store.configure(*params)

    def test_completed_days_and_streak_do_not_double_count_and_recommend_only(self):
        for _ in range(3):
            plan = self.complete()
            self.store.rate(plan["words"][0]["id"])
            self.day += timedelta(days=1)
        plan = self.store.today_plan()
        self.assertEqual((plan["completed_days"], plan["streak"]), (3, 3))
        self.assertEqual(plan["recommended_level"], 2)
        self.assertEqual(self.store.get_settings()["level"], 1)
        self.day += timedelta(days=1)
        self.assertEqual(self.store.today_plan()["streak"], 0)

    def test_increasing_goal_after_completion_updates_completed_day(self):
        self.store.configure(1, 1, 1, "mixed")
        self.assertEqual(self.complete()["completed_days"], 1)
        plan = self.store.configure(1, 2, 1, "mixed")
        self.assertFalse(plan["completed"])
        self.assertEqual(plan["completed_days"], 0)
        self.assertEqual(self.complete()["completed_days"], 1)

    def test_exhausted_library_repeats_without_duplicates_or_empty_plans(self):
        self.store.configure(1, 30, 15, "daily")
        self.complete()
        self.day += timedelta(days=1)
        plan = self.store.today_plan()
        self.assertEqual((len(plan["words"]), len(plan["sentences"])), (15, 15))
        self.assertEqual(len(self.ids(plan)), len(set(self.ids(plan))))
        self.assertTrue(all(card["review"] for card in plan["words"] + plan["sentences"]))
        self.complete()
        self.day += timedelta(days=1)
        self.assertTrue(self.store.today_plan()["words"])

    def test_broken_json_preserved_as_backup_before_recovery(self):
        self.state.write_bytes(b"{broken \xff")
        store = self.new_store()
        self.assertEqual(store.recovery_backup.read_bytes(), b"{broken \xff")
        self.assertEqual(store.get_settings()["level"], 1)
        self.assertEqual(json.loads(self.state.read_text("utf-8"))["version"], 1)
        self.assertEqual(len(list(self.root.glob("*.bak"))), 1)

    def test_invalid_state_structure_also_recovers(self):
        self.state.write_text('{"version": 1, "settings": []}', encoding="utf-8")
        store = self.new_store()
        self.assertIsNotNone(store.recovery_backup)
        self.assertEqual(len(store.today_plan()["words"]), 10)

    def test_atomic_save_failure_preserves_previous_file_and_memory(self):
        plan = self.store.today_plan()
        original_bytes = self.state.read_bytes()
        with patch("learning.os.replace", side_effect=OSError("write denied")):
            with self.assertRaises(OSError):
                self.store.rate(plan["words"][0]["id"])
        self.assertEqual(self.state.read_bytes(), original_bytes)
        self.assertEqual(self.store.today_plan(), plan)
        self.assertFalse(list(self.root.glob(".learning-*.tmp")))

    def test_invalid_rating_or_card_does_not_change_progress(self):
        plan = self.store.today_plan()
        for item_id, rating in (("missing", "known"), (plan["words"][0]["id"], "maybe")):
            with self.assertRaises(ValueError):
                self.store.rate(item_id, rating)
        self.assertEqual(self.store.today_plan()["done_ids"], [])

    def test_default_path_under_local_app_data_and_date_value_supported(self):
        with patch.dict("os.environ", {"LOCALAPPDATA": str(self.root / "local")}):
            store = LearningStore(self.content, today="2026-10-14")
            self.assertEqual(store.state_path, self.root / "local" / "TutuTranslate" / "learning.json")
            self.assertEqual(store.today_plan()["date"], "2026-10-14")


if __name__ == "__main__":
    unittest.main()
