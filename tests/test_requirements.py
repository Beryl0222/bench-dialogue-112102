"""零件需求构建：公差/留量分离、共料组、取料约束、澄清边界。"""

import _bootstrap  # noqa: F401  把仓库根注入 sys.path，兼容 -I discover
import json
import unittest
from pathlib import Path

from nestplan.session import Session
from nestplan.cli import DEFAULT_SLANG_MM

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def make_session():
    load = lambda name: json.loads(
        (DATA_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return Session(load("drawings"), load("stock"), load("chat"),
                   slang_mm=DEFAULT_SLANG_MM)


class RequirementsTest(unittest.TestCase):
    def setUp(self):
        self.session = make_session()

    def test_tolerance_never_changes(self):
        reqs = self.session.requirements_at(5)
        for req in reqs.values():
            for piece in req.pieces.values():
                drawing = next(
                    p for p in
                    self.session.drawings[req.drawing_id]["pieces"]
                    if p["name"] == piece.piece_name)
                self.assertEqual(piece.tolerance_mm,
                                 drawing["tolerance_mm"])

    def test_short_piece_finished_and_trim(self):
        req = self.session.requirements_at(5)["J17"]
        short = req.pieces["短件"]
        self.assertEqual(short.finished_mm, 245.0)      # 250 - 半公分
        self.assertEqual(short.trim_mm, 3.0)            # 三毛，改回三毫米
        self.assertEqual(short.blank_mm, 248.0)
        self.assertEqual(short.tolerance_mm, 2)

    def test_right_piece_trim_keeps_drawing_floor(self):
        req = self.session.requirements_at(5)["J17"]
        right = req.pieces["右件"]
        self.assertEqual(right.trim_mm, 4)              # 不被口语改小
        self.assertEqual(right.blank_mm, 244)

    def test_sample_inch_and_corrected_trim(self):
        req = self.session.requirements_at(5)["J19"]
        sample = req.pieces["试样"]
        self.assertEqual(sample.finished_mm, 101.6)     # 四英寸
        self.assertEqual(sample.trim_mm, 2.0)           # 一毫米改成两毫米
        self.assertEqual(sample.blank_mm, 103.6)

    def test_sourcing_modes(self):
        req = self.session.requirements_at(1)["J17"]
        self.assertEqual(req.pieces["右件"].sourcing,
                         "prefer_offcut")
        req5 = self.session.requirements_at(5)["J17"]
        for name in ("长件", "短件", "右件"):
            self.assertEqual(req5.pieces[name].sourcing, "same_bar")
            self.assertEqual(req5.pieces[name].group_id, "G17-1")

    def test_group_grows_and_versions(self):
        req4 = self.session.requirements_at(4)["J17"]
        self.assertEqual(req4.groups["G17-1"].pieces, ["长件", "短件"])
        self.assertEqual(req4.groups["G17-1"].version, 1)
        req5 = self.session.requirements_at(5)["J17"]
        self.assertEqual(
            req5.groups["G17-1"].pieces, ["长件", "短件", "右件"])
        self.assertEqual(req5.groups["G17-1"].version, 2)

    def test_held_flag(self):
        req = self.session.requirements_at(4)["J17"]
        self.assertTrue(req.pieces["右件"].held)

    def test_every_value_has_quote(self):
        for req in self.session.requirements_at(5).values():
            for piece in req.pieces.values():
                self.assertTrue(piece.quotes)
                for q in piece.quotes:
                    self.assertTrue(q.text)
                    self.assertIn(q.message_id, {"M1", "M2", "M3", "M4", "M5"})

    def test_bare_number_blocked_without_default_unit(self):
        # R5 没有 units_default：裸数字工单约定缺失时必须留澄清
        from nestplan.units import parse_measure
        m = parse_measure("留 3", default_unit=None)
        self.assertFalse(m.resolved)


if __name__ == "__main__":
    unittest.main()
