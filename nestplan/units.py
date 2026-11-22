"""公制/英制短语解析。

规则：
- 只有显式带单位的短语才直接换算成毫米；图样 ``units_default`` 为 null 的工单，
  裸数字一律拒绝，要求澄清。
- 车间口语（毛、分）在本工单约定不明时返回“需要约定”的量，不静默换算。
- 所有换算结果保留原始短语与来源，供计划员核对，避免把粗坯当成品。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

# 1 英寸 = 25.4 毫米。样例数据里 4 英寸对应图样 101.6。
INCH_TO_MM = 25.4

_CN_DIGITS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}

# 形如 4、1.2、四、半公分里的“半”的数字片段
_NUMBER_PART = r"\d+(?:\.\d+)?|[一二两三四五六七八九]|半"
_MEASURE_RE = re.compile(rf"({_NUMBER_PART})([a-zA-Z一-鿿]+)")

# 明确单位词 -> 毫米换算系数
_UNIT_FACTORS = {
    "mm": 1.0,
    "毫米": 1.0,
    "公分": 10.0,
    "cm": 10.0,
    "米": 1000.0,
    "m": 1000.0,
    "英寸": INCH_TO_MM,
    "寸": INCH_TO_MM,  # 车间口语里“英寸”的简称，按英寸处理并保留原句
    "inch": INCH_TO_MM,
    "in": INCH_TO_MM,
}

# 车间口语单位，语义依赖工单约定
_SLANG_UNITS = {"毛", "分"}


@dataclass
class Measurement:
    """从文字里提取的一个量。

    ``mm`` 为 None 表示该量无法在无约定情况下落实，必须留待澄清；
    此时 ``needs_convention`` 记录需要哪类约定。
    """

    raw: str
    number: float | None
    unit_word: str | None
    mm: float | None
    explicit_unit: bool
    needs_convention: str | None = None
    note: str | None = None

    @property
    def resolved(self) -> bool:
        return self.mm is not None


def parse_number(token: str) -> float:
    """解析阿拉伯数字或单个中文数词（含“半”“两”）。"""
    if token == "半":
        return 0.5
    if token in _CN_DIGITS:
        return float(_CN_DIGITS[token])
    return float(token)


def parse_measure(
    text: str,
    *,
    default_unit: str | None = None,
    slang_mm: dict[str, float] | None = None,
) -> Measurement:
    """从一段短文本中取第一个带数量的短语。

    参数
    ----
    default_unit:
        当前工单的单位约定（如 ``"mm"``）。为 None 时裸数字不允许落实。
    slang_mm:
        本工单对“毛/分”等口语单位的毫米约定，例如 ``{"毛": 1.0}``。
    """
    match = _MEASURE_RE.search(text)
    if match is None:
        # 裸数字（无单位词）
        bare = re.search(rf"({_NUMBER_PART})", text)
        if bare is None:
            return Measurement(text, None, None, None, False,
                               note="未识别到数量")
        number = parse_number(bare.group(1))
        if default_unit is not None and default_unit in _UNIT_FACTORS:
            return Measurement(
                bare.group(1), number, default_unit,
                round(number * _UNIT_FACTORS[default_unit], 6),
                explicit_unit=False,
                note="按工单约定单位落实，原句省略单位",
            )
        return Measurement(
            bare.group(1), number, None, None, explicit_unit=False,
            needs_convention="该工单没有默认单位约定，裸数字需澄清单位",
        )

    number_token, unit_word = match.groups()
    number = parse_number(number_token)
    raw = match.group(0)

    if unit_word in _UNIT_FACTORS:
        return Measurement(
            raw, number, unit_word,
            round(number * _UNIT_FACTORS[unit_word], 6),
            explicit_unit=True,
        )

    if unit_word in _SLANG_UNITS or unit_word == "分公制":
        # “一分公制”里的“公制”只是制式说明，口语单位仍按“分”处理
        canonical = "分" if unit_word == "分公制" else unit_word
        slang_mm = slang_mm or {}
        if canonical in slang_mm:
            return Measurement(
                raw, number, unit_word,
                round(number * slang_mm[canonical], 6),
                explicit_unit=True,
                note=f"按工单约定 1{canonical}={slang_mm[canonical]}mm",
            )
        return Measurement(
            raw, number, unit_word, None, explicit_unit=True,
            needs_convention=f"口语单位“{canonical}”在本工单没有毫米约定",
        )

    return Measurement(
        raw, number, unit_word, None, explicit_unit=True,
        needs_convention=f"未登记的单位词“{unit_word}”",
    )


def iter_measures(text: str) -> list[re.Match]:
    """列出一段文字里所有数量短语的位置，供语句解析引用。"""
    return list(_MEASURE_RE.finditer(text))
