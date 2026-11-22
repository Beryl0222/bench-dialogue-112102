"""班组话轮解析：句式覆盖、复式句、未匹配留痕。"""

import _bootstrap  # noqa: F401  把仓库根注入 sys.path，兼容 -I discover
import json
import unittest
from pathlib import Path

from nestplan.statements import (
    parse_message,
    KIND_SHARED_BAR, KIND_FINISHED_DELTA, KIND_TRIM, KIND_SOURCING,
    KIND_ANAPHOR_QUESTION, KIND_ANAPHOR_RESOLVE, KIND_ANAPHOR_TOPIC,
    KIND_CONFIRM, KIND_HOLD, KIND_ADVISORY, KIND_UNMATCHED,
    drawing_ref_to_id,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def messages():
    return {m["id"]: m for m in
            json.loads((DATA_DIR / "chat.json").read_text(encoding="utf-8"))}


def kinds(message):
    return [st.kind for st in parse_message(message)]


class StatementsTest(unittest.TestCase):
    def setUp(self):
        self.msg = messages()

    def test_m1_shared_bar_and_sourcing(self):
        ks = kinds(self.msg["M1"])
        self.assertIn(KIND_SHARED_BAR, ks)
        self.assertIn(KIND_FINISHED_DELTA, ks)
        self.assertIn(KIND_TRIM, ks)
        self.assertIn(KIND_SOURCING, ks)
        shared = next(st for st in parse_message(self.msg["M1"])
                      if st.kind == KIND_SHARED_BAR)
        self.assertEqual(shared.payload["pieces"], ["长件", "短件"])

    def test_m1_keeps_raw_quote(self):
        st = next(s for s in parse_message(self.msg["M1"])
                  if s.kind == KIND_FINISHED_DELTA)
        self.assertEqual(st.message_id, "M1")
        self.assertEqual(st.seq, 1)
        self.assertEqual(st.source, "班组长")
        self.assertIn("短半公分", st.raw)

    def test_m2_question_and_explicit_inch(self):
        sts = parse_message(self.msg["M2"])
        self.assertIn(KIND_ANAPHOR_QUESTION, kinds(self.msg["M2"]))
        question = next(s for s in sts if s.kind == KIND_ANAPHOR_QUESTION)
        self.assertEqual(question.payload["ordinal"], 2)
        explicit = next(s for s in sts if s.kind == "finished_explicit")
        self.assertEqual(explicit.payload["measure_text"], "四英寸")

    def test_m3_resolves_anaphor_and_corrects_trim(self):
        sts = parse_message(self.msg["M3"])
        resolves = [s for s in sts if s.kind == KIND_ANAPHOR_RESOLVE]
        self.assertTrue(any(s.payload.get("bar_id") == "S3" for s in resolves))
        self.assertTrue(
            any(s.payload.get("rejected_bar_id") == "S2" for s in resolves))
        trims = [s for s in sts if s.kind == KIND_TRIM]
        effects = {s.payload["effect"] for s in trims}
        self.assertEqual(effects, {"clarify", "change"})
        self.assertIn(KIND_ANAPHOR_TOPIC, kinds(self.msg["M3"]))

    def test_m4_confirm_and_hold(self):
        ks = kinds(self.msg["M4"])
        self.assertIn(KIND_CONFIRM, ks)
        self.assertIn(KIND_HOLD, ks)
        confirm = next(s for s in parse_message(self.msg["M4"])
                       if s.kind == KIND_CONFIRM)
        self.assertEqual(confirm.payload["pieces"], ["长件", "短件"])

    def test_m5_group_extension_and_advisory(self):
        ks = kinds(self.msg["M5"])
        self.assertIn(KIND_SHARED_BAR, ks)
        self.assertIn(KIND_ADVISORY, ks)
        extension = next(s for s in parse_message(self.msg["M5"])
                         if s.kind == KIND_SHARED_BAR)
        self.assertEqual(extension.payload["piece"], "右件")

    def test_drawing_ref(self):
        self.assertEqual(drawing_ref_to_id("图四"), "R4")

    def test_no_silent_swallow(self):
        # 所有真实话轮都不允许整句落入 unmatched；解析不了必须显式报出
        for message in self.msg.values():
            for st in parse_message(message):
                if st.kind == KIND_UNMATCHED:
                    self.fail(f"未匹配语句：{st.raw}")


if __name__ == "__main__":
    unittest.main()
