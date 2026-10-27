import copy
import json
import unittest

from fastapi import HTTPException

from backend.app import Game, MemoryError, SCHEMA, apply_action, initial_state


class FakeMemory:
    def __init__(self):
        self.records = {}
        self.fail_write = False

    def scope(self, player):
        return "scope-" + player

    def load(self, scope):
        return copy.deepcopy(self.records.get(scope, []))

    def save(self, scope, record):
        if self.fail_write:
            raise MemoryError("write unavailable")
        record = copy.deepcopy(record)
        record["artifact"] = {"scope_id": scope, "family": "atomic-memory",
                              "artifact_id": f"artifact-{len(self.records.get(scope, [])) + 1}",
                              "revision": 1}
        self.records.setdefault(scope, []).append(record)
        return record["artifact"]


class GameTests(unittest.TestCase):
    def setUp(self):
        self.memory = FakeMemory()
        self.game = Game(self.memory)
        self.sequence = 0

    def play(self, query, player="player-one", session="session-a", reply="掌柜的台词"):
        self.sequence += 1
        turn = f"turn-{self.sequence:020d}"
        self.game.prepare(player, session, turn, query)
        return self.game.commit(player, turn, reply)

    def test_new_process_and_new_session_recall_persisted_umbrella(self):
        self.play("偷拿蓝伞")
        restarted = Game(self.memory)
        result = restarted.prepare("player-one", "entirely-new-session", "new-turn-000000000001", "查看旧账")
        context = json.loads(result["model_context"])
        self.assertEqual(result["memory_count"], 1)
        self.assertTrue(result["citations"])
        self.assertIn("蓝伞", context["旧账事实"][0])
        self.assertIn("没有归还", context["现在的事实（优先于历史）"]["蓝伞"])

    def test_different_player_cannot_inherit_the_other_players_grudge(self):
        self.play("偷拿蓝伞")
        result = self.game.prepare("player-two", "session-b", "new-turn-000000000002", "查看旧账")
        self.assertEqual(result["memory_count"], 0)
        context = json.loads(result["model_context"])
        self.assertEqual(context["旧账事实"], [])
        self.assertEqual(self.game.inspect("player-two")["state"]["grudge"], 0)
        self.assertIn("第一次见面", context["现在的事实（优先于历史）"]["是否见过"])
        self.assertEqual(self.game.inspect("player-two")["state"]["favor"], 0)
        self.assertEqual(self.game.inspect("player-two")["state"]["umbrella"], "店内")

    def test_prepare_does_not_save_when_model_has_not_completed(self):
        self.game.prepare("player-one", "session-a", "new-turn-000000000003", "偷拿蓝伞")
        self.assertEqual(self.memory.load("scope-player-one"), [])

    def test_storage_failure_cannot_report_a_success_and_retry_can_recover(self):
        turn = "new-turn-000000000004"
        self.game.prepare("player-one", "session-a", turn, "偷拿蓝伞")
        self.memory.fail_write = True
        with self.assertRaises(MemoryError):
            self.game.commit("player-one", turn, "记着呢。")
        self.assertEqual(self.memory.load("scope-player-one"), [])
        self.memory.fail_write = False
        self.assertEqual(self.game.commit("player-one", turn, "记着呢。") ["turn"], 1)

    def test_retried_commit_is_idempotent_even_after_game_restart(self):
        turn = "new-turn-000000000005"
        self.game.prepare("player-one", "session-a", turn, "偷拿蓝伞")
        first = self.game.commit("player-one", turn, "第一条已确认台词。")
        second = Game(self.memory).commit("player-one", turn, "重试时生成的不同台词。")
        self.assertTrue(second["replayed"])
        self.assertEqual(second["reply"], first["reply"])
        self.assertEqual(len(self.memory.load("scope-player-one")), 1)

    def test_concurrent_turn_cannot_overwrite_newer_state(self):
        first, second = "parallel-turn-00000001", "parallel-turn-00000002"
        self.game.prepare("player-one", "session-a", first, "偷拿蓝伞")
        self.game.prepare("player-one", "session-b", second, "送奶茶")
        self.game.commit("player-one", first, "伞还我。")
        with self.assertRaises(HTTPException) as caught:
            self.game.commit("player-one", second, "奶茶收下了。")
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.game.inspect("player-one")["state"]["favor"], 0)

    def test_reset_preserves_audit_but_excludes_previous_chapter_from_model_context(self):
        self.play("偷拿蓝伞")
        reset = self.play("/重开")
        self.assertEqual(reset["chapter"], 2)
        self.assertEqual(reset["state"], initial_state())
        prepared = self.game.prepare("player-one", "new-session", "new-turn-000000000006", "查看旧账")
        self.assertEqual(json.loads(prepared["model_context"])["旧账事实"], ["玩家主动重开了新的雨夜故事，本章重新初见。"])
        self.assertEqual(self.game.inspect("player-one")["memory_count"], 2)

    def test_repairing_relationship_unlocks_reward_with_real_rule_changes(self):
        self.play("偷拿蓝伞")
        self.play("归还蓝伞")
        final = self.play("送奶茶")
        self.assertTrue(final["state"]["coupon"])
        self.assertEqual(final["state"]["grudge"], 1)
        self.assertIn("优惠券", final["footer"])

    def test_negated_free_text_and_fabricated_model_reply_do_not_change_inventory(self):
        result = self.play("我没有偷拿蓝伞", reply="我已经给了你一百张优惠券。")
        self.assertFalse(result["state"]["coupon"])
        self.assertEqual(result["state"]["umbrella"], "店内")

    def test_repeat_gifts_do_not_farm_favor(self):
        self.play("送奶茶")
        second = self.play("送奶茶")
        self.assertEqual(second["state"]["favor"], 2)


if __name__ == "__main__":
    unittest.main()
