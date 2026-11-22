"""一维试排：在图样约束下把毛坯排进杆的自由段。

排料口径（与 README 口径一致，全部以毫米计）：
- 毛坯 = 成品 + 下料留量；公差只用于验收，不参与长度。
- 一根杆上放 n 段时，总占用 = 各毛坯之和 + (n-1) 次 kerf 切耗。
- 切完剩余长度 >= 图样 min_reuse_mm 才算可继续用的长余料，否则为废料。

选择顺序（同一工单优先级内）：
1. 先吃已有余料/料头（offcut 杆与已开杆的自由段），尽量少用新料；
2. 同等条件下保留更长的可复用余料：优先把占用放进能放下的最短自由段，
   更长的自由段留作他用；
3. 再同等则按杆号、段起点确定，保证结果可复现。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .requirements import JobRequirements, PieceRequirement, GroupSpec
from .stock import Bar, Interval


@dataclass
class Segment:
    piece: str
    blank_mm: float
    start: float
    end: float

    def as_dict(self) -> dict:
        return {
            "piece": self.piece,
            "blank_mm": self.blank_mm,
            "start": self.start,
            "end": self.end,
        }


@dataclass
class PlanCard:
    """一张试排单。confirmed=False 时只代表试排，不能据此动料。"""

    card_id: str
    job_id: str
    pieces: list[str]
    group_id: str | None
    bar_id: str | None
    grade: str
    segments: list[Segment]
    kerf_mm: float
    required_mm: float
    remnant_mm: float
    reusable: bool
    min_reuse_mm: float
    shortage_mm: float
    blocked_reasons: list[str] = field(default_factory=list)
    alternatives: list[dict] = field(default_factory=list)
    confirmed: bool = False
    quote_refs: list[dict] = field(default_factory=list)

    @property
    def confirmable(self) -> bool:
        return (
            self.bar_id is not None
            and self.shortage_mm <= 0
            and not self.blocked_reasons
        )

    @property
    def interval(self) -> Interval | None:
        if not self.segments:
            return None
        return Interval(self.segments[0].start, self.segments[-1].end)

    def as_dict(self) -> dict:
        return {
            "card_id": self.card_id,
            "job_id": self.job_id,
            "pieces": list(self.pieces),
            "group_id": self.group_id,
            "bar_id": self.bar_id,
            "grade": self.grade,
            "segments": [s.as_dict() for s in self.segments],
            "kerf_mm": self.kerf_mm,
            "required_mm": self.required_mm,
            "remnant_mm": self.remnant_mm,
            "reusable": self.reusable,
            "min_reuse_mm": self.min_reuse_mm,
            "shortage_mm": self.shortage_mm,
            "blocked_reasons": list(self.blocked_reasons),
            "alternatives": list(self.alternatives),
            "confirmed": self.confirmed,
            "confirmable": self.confirmable,
            "quote_refs": list(self.quote_refs),
        }


def _compatible_bars(
    bars: dict[str, Bar], grade: str, directions: set[str], seq: int,
    seq_by_message: dict[str, int],
) -> list[Bar]:
    """同料且可用朝向能容纳组内全部零件朝向的杆。"""
    out = []
    for bar in bars.values():
        if not bar.available_at(seq, seq_by_message):
            continue
        if bar.grade_ok(grade) and directions <= set(bar.direction):
            out.append(bar)
    return sorted(out, key=lambda b: b.id)


def _gap_needed(blanks: list[float], kerf: float) -> float:
    if not blanks:
        return 0.0
    return sum(blanks) + kerf * (len(blanks) - 1)


def _rank_candidates(
    bars: list[Bar], required: float, prefer_offcut: bool,
) -> list[tuple[Bar, Interval, float]]:
    """返回排序后的 (杆, 自由段, 段内剩余) 候选。

    同一优先级内的次序：
    1. 料头杆 offcut 最优先（消化剩头、不开新杆）；其次是已开过料的杆；
       最后才是未动用的新杆——尽量少用新料；
    2. 再优先更短的自由段，把更长的可复用余料留住；
    3. 杆号、段起点兜底，保证结果可复现。
    """
    candidates: list[tuple[Bar, Interval, float]] = []
    for bar in bars:
        for iv in bar.free:
            if iv.length + 1e-9 < required:
                continue
            candidates.append((bar, iv, iv.length - required))

    def new_material_rank(bar: Bar) -> int:
        if bar.offcut:
            return 0  # 料头杆，先消化
        if bar.occupied:
            return 1  # 已开杆，继续吃它的自由段
        return 2      # 未动用的新杆

    # prefer_offcut=False 时仍保持同一层级：料头/已开杆先于新杆，
    # 差别只在上层是否把“优先余料”写进卡片语义。
    candidates.sort(key=lambda c: (
        new_material_rank(c[0]),
        c[1].length,                        # 段长升序：先吃短段，留住长余料
        c[0].id,
        c[1].start,
    ))
    return candidates


def _build_card(
    card_id: str,
    req: JobRequirements,
    pieces: list[PieceRequirement],
    group: GroupSpec | None,
    bars: dict[str, Bar],
    seq: int,
    seq_by_message: dict[str, int],
    prefer_offcut: bool,
    blocked_reasons: list[str],
) -> PlanCard:
    names = [p.piece_name for p in pieces]
    grade = pieces[0].grade
    directions = {p.direction for p in pieces}
    quotes: list[dict] = []
    for p in pieces:
        quotes.extend(q.as_dict() for q in p.quotes)

    card = PlanCard(
        card_id=card_id,
        job_id=req.job_id,
        pieces=names,
        group_id=group.group_id if group else None,
        bar_id=None,
        grade=grade,
        segments=[],
        kerf_mm=req.kerf_mm,
        required_mm=0.0,
        remnant_mm=0.0,
        reusable=False,
        min_reuse_mm=req.min_reuse_mm,
        shortage_mm=0.0,
        blocked_reasons=list(blocked_reasons),
        quote_refs=quotes,
    )

    missing = [p.piece_name for p in pieces if p.blank_mm is None]
    if missing:
        card.blocked_reasons.append(
            f"切件 {missing} 的成品尺寸或留量尚未澄清，无法计算毛坯")
    if card.blocked_reasons:
        return card

    blanks = [p.blank_mm for p in pieces]
    required = _gap_needed(blanks, req.kerf_mm)
    card.required_mm = required
    usable = _compatible_bars(
        bars, grade, directions, seq, seq_by_message)
    candidates = _rank_candidates(usable, required, prefer_offcut)

    if not candidates:
        best = max((b.longest_free() for b in usable), default=0.0)
        card.shortage_mm = round(required - best, 6)
        if not usable:
            card.blocked_reasons.append(
                f"没有同时满足材料 {grade} 且可用朝向覆盖 "
                f"{sorted(directions)} 的可用杆（含到货时序限制）")
        return card

    bar, interval, leftover = candidates[0]
    for alt_bar, alt_iv, alt_left in candidates[1:4]:
        card.alternatives.append({
            "bar_id": alt_bar.id,
            "interval": alt_iv.as_pair(),
            "interval_mm": alt_iv.length,
            "remnant_after_mm": round(alt_left, 6),
        })

    cursor = interval.start
    for piece, blank in zip(pieces, blanks):
        seg = Segment(piece.piece_name, blank, cursor, cursor + blank)
        card.segments.append(seg)
        cursor += blank + req.kerf_mm
    card.bar_id = bar.id
    card.remnant_mm = round(leftover, 6)
    card.reusable = leftover + 1e-9 >= req.min_reuse_mm
    return card


def place_group(
    card_id: str,
    req: JobRequirements,
    group: GroupSpec,
    bars: dict[str, Bar],
    seq: int,
    seq_by_message: dict[str, int],
) -> PlanCard:
    """整组共料排料：组内任一切件放不下，则整组缺料、整组待确认。

    “暂不确认”只作为阻塞确认的标记保留，不阻止生成试排段占用，
    让计划员仍能看到如果确认会怎么排、剩多少料。
    """
    pieces = [req.pieces[name] for name in group.pieces]
    blocked: list[str] = []
    if any(p.held for p in pieces):
        blocked.append(
            "组内切件被话轮要求暂不确认："
            + ", ".join(p.piece_name for p in pieces if p.held))
    card = _build_card(
        card_id, req, pieces, group, bars, seq, seq_by_message,
        prefer_offcut=True, blocked_reasons=[])
    card.blocked_reasons = blocked + card.blocked_reasons
    return card


def place_single(
    card_id: str,
    req: JobRequirements,
    piece: PieceRequirement,
    bars: dict[str, Bar],
    seq: int,
    seq_by_message: dict[str, int],
) -> PlanCard:
    """单件排料：prefer_offcut 时优先料头，any_compatible 时允许任意相容杆。"""
    blocked: list[str] = []
    if piece.held:
        blocked.append(f"{piece.piece_name} 被话轮要求暂不确认")
    if piece.group_id is not None:
        blocked.append(
            f"{piece.piece_name} 属于共料组 {piece.group_id}，不能单独排料")
    return _build_card(
        card_id, req, [piece], None, bars, seq, seq_by_message,
        prefer_offcut=piece.sourcing == "prefer_offcut",
        blocked_reasons=blocked)
