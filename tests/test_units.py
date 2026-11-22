"""单位与数量短语解析：公制/英制、口语单位、裸数字约定。"""

import _bootstrap  # noqa: F401  把仓库根注入 sys.path，兼容 -I discover
import unittest

from nestplan.units import parse_measure, parse_number, INCH_TO_MM


class UnitsTest(unittest.TestCase):
    def test_metric_units(self):
        self.assertEqual(parse_measure("短半公分").mm, 5.0)
        self.assertEqual(parse_measure("三毫米").mm, 3.0)
        self.assertEqual(parse_measure("1.2cm").mm, 12.0)
        self.assertTrue(parse_measure("三毫米").explicit_unit)

    def test_inch_to_mm(self):
        m = parse_measure("四英寸")
        self.assertEqual(m.mm, 4 * INCH_TO_MM)
        self.assertEqual(m.mm, 101.6)

    def test_chinese_numerals(self):
        self.assertEqual(parse_number("两"), 2.0)
        self.assertEqual(parse_number("半"), 0.5)
        self.assertEqual(parse_number("四"), 4.0)

    def test_bare_number_requires_default_unit(self):
        bare = parse_measure("长度 4", default_unit=None)
        self.assertIsNone(bare.mm)
        self.assertFalse(bare.resolved)
        self.assertIn("默认单位", bare.needs_convention)

    def test_bare_number_uses_job_convention(self):
        m = parse_measure("长度 4", default_unit="mm")
        self.assertEqual(m.mm, 4.0)
        self.assertFalse(m.explicit_unit)
        self.assertIn("省略单位", m.note)

    def test_slang_needs_convention(self):
        m = parse_measure("三毛")
        self.assertIsNone(m.mm)
        self.assertIn("毛", m.needs_convention)
        resolved = parse_measure("三毛", slang_mm={"毛": 1.0})
        self.assertEqual(resolved.mm, 3.0)

    def test_gongzhi_slang(self):
        m = parse_measure("一分公制", slang_mm={"分": 1.0})
        self.assertEqual(m.mm, 1.0)
        denied = parse_measure("一分公制")
        self.assertIsNone(denied.mm)


if __name__ == "__main__":
    unittest.main()
