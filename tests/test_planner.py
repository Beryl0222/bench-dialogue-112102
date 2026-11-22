"""一维排料：kerf、共料整组、缺料、少用新料与保留长余料。"""

import _bootstrap  # noqa: F401  把仓库根注入 sys.path，兼容 -I discover
import unittest

from nestplan.requirements import PieceRequirement, GroupSpec, JobRequirements
from nestplan.stock import Bar, Interval
from nestplan.planner import place_group, place_single


def req(job_id="JT", grade="AL-F", kerf=3.0, min_reuse=180.0):
    return JobRequirements(
        job_id=job_id, drawing_id="RX", units_default="mm",
        kerf_mm=kerf, min_reuse_mm=min_reuse,
        pieces={}, groups={}, clarifications=[], slang_mm={})


def piece(name, blank, grade="AL-F", direction="+", held=False,
          sourcing="any_compatible"):
    return PieceRequirement(
        job_id="JT", drawing_id="RX", piece_name=name, grade=grade,
        direction=direction, finished_base_mm=blank, finished_mm=blank,
        tolerance_mm=1, trim_base_mm=0, trim_mm=0,
        sourcing=sourcing, held=held)


def bars():
    return {
        "N1": Bar("N1", "AL-F", ["+", "-"], [Interval(0, 1000)], [],
                  offcut=False),
        "O1": Bar("O1", "AL-F", ["+", "-"], [Interval(0, 600)], [],
                  offcut=True),
        "O2": Bar("O2", "AL-F", ["+", "-"],
                  [Interval(0, 300), Interval(700, 900)],
                  [Interval(300, 700)], offcut=True),
        "X1": Bar("X1", "AL-X", ["+", "-"], [Interval(0, 1000)], [],
                  offcut=True),
    }


SEQ = {"M0": 0}


class PlannerTest(unittest.TestCase):
    def test_kerf_between_segments(self):
        r = req()
        r.pieces = {"甲": piece("甲", 100), "乙": piece("乙", 100)}
        group = GroupSpec("G1", "JT", ["甲", "乙"])
        card = place_group("C1", r, group, bars(), 1, SEQ)
        self.assertEqual(card.required_mm, 203.0)
        self.assertEqual(card.segments[0].end, 100)
        self.assertEqual(card.segments[1].start, 103)

    def test_group_shortage_is_all_or_nothing(self):
        r = req()
        r.pieces = {n: piece(n, 400) for n in "甲乙丙"}
        group = GroupSpec("G1", "JT", list("甲乙丙"))
        card = place_group("C1", r, group, bars(), 1, SEQ)
        self.assertIsNone(card.bar_id)
        self.assertGreater(card.shortage_mm, 0)
        self.assertFalse(card.confirmable)

    def test_grade_mismatch_excluded(self):
        r = req()
        r.pieces = {"甲": piece("甲", 100)}
        card = place_single("C1", r, r.pieces["甲"], bars(), 1, SEQ)
        self.assertNotEqual(card.bar_id, "X1")

    def test_offcut_preferred_over_new_bar(self):
        r = req()
        r.pieces = {"甲": piece("甲", 100, sourcing="prefer_offcut")}
        card = place_single("C1", r, r.pieces["甲"], bars(), 1, SEQ)
        # 料头杆（O1/O2）必须压过新杆 N1；O2 有更短的适配段，
        # 先吃 O2 以保住 O1 的 600 长余料
        self.assertIn(card.bar_id, ("O1", "O2"))
        self.assertNotEqual(card.bar_id, "N1")
        # 只有一根料头杆时，它必然被选中而不是新杆
        single = {"O1": bars()["O1"], "N1": bars()["N1"]}
        card2 = place_single("C2", r, r.pieces["甲"], single, 1, SEQ)
        self.assertEqual(card2.bar_id, "O1")

    def test_preserve_long_remnant_uses_shorter_free_interval(self):
        # 纯料头杆 O2 有 300 和 200 两段；放 100 应优先 200 段，留住 300 长余料
        r = req()
        r.pieces = {"甲": piece("甲", 100, sourcing="prefer_offcut")}
        pure_offcut = Bar("O2", "AL-F", ["+", "-"],
                          [Interval(0, 300), Interval(700, 900)], [],
                          offcut=True)
        pool = {"O2": pure_offcut}
        card = place_single("C1", r, r.pieces["甲"], pool, 1, SEQ)
        self.assertEqual(card.bar_id, "O2")
        self.assertEqual(card.segments[0].start, 700)

    def test_reuse_threshold(self):
        r = req(min_reuse=180)
        r.pieces = {"甲": piece("甲", 450, sourcing="prefer_offcut")}
        card = place_single("C1", r, r.pieces["甲"], bars(), 1, SEQ)
        # O1 600-450=150 < 180，不算可复用余料
        self.assertEqual(card.bar_id, "O1")
        self.assertFalse(card.reusable)

    def test_alternatives_recorded(self):
        r = req()
        r.pieces = {"甲": piece("甲", 100)}
        card = place_single("C1", r, r.pieces["甲"], bars(), 1, SEQ)
        self.assertTrue(card.alternatives)
        alt_ids = {a["bar_id"] for a in card.alternatives}
        self.assertIn("N1", alt_ids)

    def test_direction_coverage_for_group(self):
        r = req()
        r.pieces = {
            "甲": piece("甲", 100, direction="+"),
            "乙": piece("乙", 100, direction="-"),
        }
        group = GroupSpec("G1", "JT", ["甲", "乙"])
        pool = {
            "P": Bar("P", "AL-F", ["+"], [Interval(0, 500)], []),
            "Q": Bar("Q", "AL-F", ["+", "-"], [Interval(0, 500)], []),
        }
        card = place_group("C1", r, group, pool, 1, SEQ)
        self.assertEqual(card.bar_id, "Q")


if __name__ == "__main__":
    unittest.main()
