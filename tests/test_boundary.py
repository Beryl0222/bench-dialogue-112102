"""补充边界：确认单冲突自检、未匹配句式澄清、库存区间记账。"""

import _bootstrap  # noqa: F401  把仓库根注入 sys.path，兼容 -I discover
import json
import tempfile
import unittest
from pathlib import Path

from nestplan.session import Session
from nestplan.stock import Bar, Interval
from nestplan.cli import DEFAULT_SLANG_MM

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def make_session():
    load = lambda name: json.loads(
        (DATA_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return Session(load("drawings"), load("stock"), load("chat"),
                   slang_mm=DEFAULT_SLANG_MM)


class BoundaryTest(unittest.TestCase):
    def test_sheet_verify_detects_conflict(self):
        session = make_session()
        session.plan_at(4)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sheet.json"
            session.save_sheet(path, 4)
            sheet = json.loads(path.read_text(encoding="utf-8"))
            # 人为篡改成与 S2 既有 locked 段 [650,950) 重叠
            sheet["active_reservations"][0]["bar_id"] = "S2"
            sheet["active_reservations"][0]["interval"] = [700, 800]
            path.write_text(json.dumps(sheet, ensure_ascii=False),
                            encoding="utf-8")
            with self.assertRaises(ValueError):
                session.load_sheet(path)

    def test_bar_claim_and_release_roundtrip(self):
        bar = Bar("B", "AL-F", ["+"], [Interval(0, 500)], [])
        bar.claim(Interval(100, 200), "r1")
        self.assertEqual(bar.free, [Interval(0, 100), Interval(200, 500)])
        self.assertIn("r1", bar.locked_refs)
        bar.release(Interval(100, 200), "r1")
        self.assertEqual(bar.free, [Interval(0, 500)])
        self.assertNotIn("r1", bar.locked_refs)

    def test_claim_outside_free_rejected(self):
        bar = Bar("B", "AL-F", ["+"], [Interval(0, 100)], [])
        with self.assertRaises(ValueError):
            bar.claim(Interval(50, 150), "rx")

    def test_unmatched_sentence_becomes_clarification(self):
        from nestplan.statements import parse_message, KIND_UNMATCHED
        fake = {"id": "MX", "thread": "t", "seq": 99, "jobs": ["J17"],
                "source": "班组长",
                "text": "这批料先按老规矩看着办。"}
        sts = parse_message(fake)
        self.assertTrue(any(s.kind == KIND_UNMATCHED for s in sts))
        unmatched = next(s for s in sts if s.kind == KIND_UNMATCHED)
        self.assertFalse(unmatched.resolved)
        self.assertIn("澄清", unmatched.clarify_reason)

    def test_clarification_lists_blocking_pieces(self):
        session = make_session()
        plan = session.plan_at(2)
        items = [c for c in plan["clarifications"]
                 if "低于图样下限" in c["reason"]]
        self.assertTrue(items)
        self.assertIn("试样", items[0]["blocking_pieces"])
        self.assertEqual(items[0]["quote"]["message_id"], "M2")


if __name__ == "__main__":
    unittest.main()
