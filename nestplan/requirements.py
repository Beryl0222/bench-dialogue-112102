"""由图样与班组语句构建零件需求（含共料组与澄清项）。

关键边界：
- ``tolerance_mm`` 只来自图样，任何话轮都不能改动它；留量是下料毛坯的
  额外长度，单独记在 ``trim_mm``，毛坯 = 成品 + 留量（kerf 由排料另计）。
- 无法落实的量（口语单位无约定、无默认单位工单里的裸数字）进入 ``clarify``，
  对应零件的试排单保持不可确认，而不是猜一个值。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .statements import (
    Statement,
    KIND_SHARED_BAR,
    KIND_FINISHED_DELTA,
    KIND_FINISHED_EXPLICIT,
    KIND_TRIM,
    KIND_SOURCING,
    KIND_CONFIRM,
    KIND_HOLD,
    KIND_ADVISORY,
    KIND_ANAPHOR_TOPIC,
    drawing_ref_to_id,
)
from .units import parse_measure


@dataclass
class Quote:
    """一处数量/尺寸/对象/材料限制的原句出处。"""

    message_id: str
    seq: int
    source: str
    text: str

    @classmethod
    def from_statement(cls, st: Statement) -> "Quote":
        return cls(st.message_id, st.seq, st.source, st.raw)

    def as_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "seq": self.seq,
            "source": self.source,
            "text": self.text,
        }


@dataclass
class Clarification:
    reason: str
    quote: Quote
    blocking_pieces: list[str] = field(default_factory=list)


@dataclass
class PieceRequirement:
    job_id: str
    drawing_id: str
    piece_name: str
    grade: str
    direction: str
    finished_base_mm: float          # 图样成品尺寸
    finished_mm: float | None        # 当前有效成品尺寸（可能被话轮更正）
    tolerance_mm: float              # 图样公差，永不被话轮修改
    trim_base_mm: float              # 图样留量下限
    trim_mm: float | None            # 当前有效下料留量
    sourcing: str = "any_compatible"  # same_bar / prefer_offcut / any_compatible
    group_id: str | None = None
    held: bool = False
    quotes: list[Quote] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)

    @property
    def blank_mm(self) -> float | None:
        """下料毛坯长度 = 成品 + 留量。任一未定则为 None。"""
        if self.finished_mm is None or self.trim_mm is None:
            return None
        return self.finished_mm + self.trim_mm

    def as_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "drawing_id": self.drawing_id,
            "piece": self.piece_name,
            "grade": self.grade,
            "direction": self.direction,
            "finished_mm": self.finished_mm,
            "finished_base_mm": self.finished_base_mm,
            "tolerance_mm": self.tolerance_mm,
            "trim_mm": self.trim_mm,
            "trim_base_mm": self.trim_base_mm,
            "blank_mm": self.blank_mm,
            "sourcing": self.sourcing,
            "group_id": self.group_id,
            "held": self.held,
            "quotes": [q.as_dict() for q in self.quotes],
            "history": self.history,
        }


@dataclass
class GroupSpec:
    """必须共料（同杆同段切出）的切件组。"""

    group_id: str
    job_id: str
    pieces: list[str]
    version: int = 1
    change_quotes: list[Quote] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "group_id": self.group_id,
            "job_id": self.job_id,
            "pieces": list(self.pieces),
            "version": self.version,
            "change_quotes": [q.as_dict() for q in self.change_quotes],
        }


@dataclass
class JobRequirements:
    job_id: str
    drawing_id: str
    units_default: str | None
    kerf_mm: float
    min_reuse_mm: float
    pieces: dict[str, PieceRequirement]
    groups: dict[str, GroupSpec]
    clarifications: list[Clarification]
    slang_mm: dict[str, float]

    def group_of_piece(self, piece: str) -> GroupSpec | None:
        gid = self.pieces[piece].group_id
        return self.groups.get(gid) if gid else None


def _job_index(drawings: dict) -> dict[str, dict]:
    return {
        row["id"]: row
        for row in drawings.values()
        if row.get("kind") == "job"
    }


def _superseded_trim_quotes(
    statements: list[Statement], piece_names_by_job: dict[str, set[str]],
) -> dict[str, set[tuple]]:
    """预扫：被同工单后续“改成…”更正覆盖的留量原句。

    key 为零件名，value 为 (message_id, raw) 集合。这些临时说法只留痕，
    不再作为“低于图样下限”的阻塞澄清。
    """
    topic: dict[str, str] = {}
    topic_seq: dict[str, int] = {}
    superseded: dict[str, set[tuple]] = {}
    ordered = sorted(statements, key=lambda s: s.seq)

    for st in ordered:
        if st.kind == KIND_ANAPHOR_TOPIC:
            for job_id in st.jobs:
                if st.payload["topic"] in piece_names_by_job.get(job_id, set()):
                    topic[job_id] = st.payload["topic"]
                    topic_seq[job_id] = st.seq
        elif st.kind == KIND_TRIM:
            for job_id in st.jobs:
                piece = topic.get(job_id)
                if piece is None or topic_seq.get(job_id) != st.seq:
                    continue
                if st.payload.get("effect") == "change":
                    bag = superseded.setdefault(piece, set())
                    for older in ordered:
                        if older.seq >= st.seq or older.kind != KIND_TRIM:
                            continue
                        if job_id in older.jobs:
                            bag.add((older.message_id, older.raw))
    return superseded


def build_requirements(
    drawings: dict,
    statements: list[Statement],
    *,
    slang_mm: dict[str, dict[str, float]] | None = None,
) -> dict[str, JobRequirements]:
    """按 seq 顺序回放语句，生成每个工单当前有效的零件需求。"""
    slang_mm = slang_mm or {}
    jobs = _job_index(drawings)
    result: dict[str, JobRequirements] = {}

    # 初始化：每个工单按图样建立零件需求
    for job_id, job in jobs.items():
        drawing = drawings[job["drawing"]]
        pieces: dict[str, PieceRequirement] = {}
        for p in drawing["pieces"]:
            pieces[p["name"]] = PieceRequirement(
                job_id=job_id,
                drawing_id=drawing["id"],
                piece_name=p["name"],
                grade=drawing["grade"],
                direction=p["direction"],
                finished_base_mm=p["finished_mm"],
                finished_mm=p["finished_mm"],
                tolerance_mm=p["tolerance_mm"],
                trim_base_mm=p["trim_min_mm"],
                trim_mm=p["trim_min_mm"],
            )
        result[job_id] = JobRequirements(
            job_id=job_id,
            drawing_id=drawing["id"],
            units_default=drawing.get("units_default"),
            kerf_mm=drawing["kerf_mm"],
            min_reuse_mm=drawing["min_reuse_mm"],
            pieces=pieces,
            groups={},
            clarifications=[],
            slang_mm=dict(slang_mm.get(job_id, {})),
        )

    # 每个工单最近被谈及的零件，供无主语语句（如“仍留三毛”）挂靠
    topic: dict[str, str] = {}
    # 话题是在哪条话轮建立的：无主语语句只能挂靠同话轮的话题
    topic_seq: dict[str, int] = {}
    group_seq = {job_id: 0 for job_id in jobs}
    # 预扫：被后续“改成…”更正覆盖的临时留量原句，只留痕不阻塞
    piece_names_by_job = {
        job_id: set(req.pieces) for job_id, req in result.items()
    }
    superseded_trim_quotes = _superseded_trim_quotes(
        statements, piece_names_by_job)

    def target_jobs(st: Statement, piece: str | None = None):
        """语句实际作用到的工单：指定了零件时，只发给含该零件的工单。"""
        for job_id in st.jobs:
            if job_id not in result:
                continue
            if piece is not None and piece not in result[job_id].pieces:
                continue
            yield job_id

    for st in sorted(statements, key=lambda s: s.seq):
        kind, payload = st.kind, st.payload

        if kind == KIND_ANAPHOR_TOPIC:
            # “说的是试样”同时是同一话轮后续无主语留量语句的话题锚点
            for job_id in target_jobs(st, payload["topic"]):
                topic[job_id] = payload["topic"]
                topic_seq[job_id] = st.seq
            continue

        if kind == KIND_ADVISORY:
            # 提示语只留痕；“留量不是公差”由数据结构本身保证
            target = payload.get("piece")
            for job_id in target_jobs(st, target):
                if target is not None:
                    result[job_id].pieces[target].quotes.append(
                        Quote.from_statement(st))
                else:
                    for piece in result[job_id].pieces.values():
                        piece.quotes.append(Quote.from_statement(st))
            continue

        if kind == KIND_SHARED_BAR:
            names = payload.get("pieces") or [payload["piece"]]
            for job_id in target_jobs(st):
                req = result[job_id]
                members = [n for n in names if n in req.pieces]
                if not members:
                    continue
                existing = {
                    req.pieces[n].group_id
                    for n in members
                    if req.pieces[n].group_id is not None
                }
                existing.discard(None)
                if existing:
                    gid = next(iter(existing))
                    group = req.groups[gid]
                    added = [n for n in members if n not in group.pieces]
                    if added:
                        group.pieces.extend(added)
                        group.version += 1
                        group.change_quotes.append(Quote.from_statement(st))
                elif req.groups:
                    # “该组右件也要取自同一根”：并入本工单最近建立的共料组，
                    # 并提升版本——共料承诺变了，整组要重新确认。
                    group = sorted(
                        req.groups.values(), key=lambda g: g.group_id)[-1]
                    gid = group.group_id
                    added = [n for n in members if n not in group.pieces]
                    if added:
                        group.pieces.extend(added)
                        group.version += 1
                        group.change_quotes.append(Quote.from_statement(st))
                else:
                    group_seq[job_id] += 1
                    gid = f"G{job_id[1:]}-{group_seq[job_id]}"
                    group = GroupSpec(
                        group_id=gid, job_id=job_id, pieces=list(members),
                        change_quotes=[Quote.from_statement(st)],
                    )
                    req.groups[gid] = group
                for n in members:
                    req.pieces[n].group_id = gid
                    req.pieces[n].sourcing = "same_bar"
                    req.pieces[n].quotes.append(Quote.from_statement(st))
                    # 共料语句谈及多个零件，不建立单一话题，
                    # 防止后续无主语留量误挂到组内最后一个零件
            continue

        if kind == KIND_CONFIRM:
            # 确认动作由 Session 记录；这里只保证原句可回溯到零件
            for job_id in target_jobs(st):
                for name in payload["pieces"]:
                    if name in result[job_id].pieces:
                        result[job_id].pieces[name].quotes.append(
                            Quote.from_statement(st))
            continue

        if kind == KIND_HOLD:
            for job_id in target_jobs(st, payload["piece"]):
                piece = result[job_id].pieces[payload["piece"]]
                piece.held = True
                piece.quotes.append(Quote.from_statement(st))
                topic[job_id] = piece.piece_name
            continue

        if kind == KIND_SOURCING:
            piece_name = payload.get("piece")
            for job_id in target_jobs(st, piece_name):
                req = result[job_id]
                if piece_name is None:
                    # 无主语的“能从剩头出更好”：挂到当前话题零件
                    piece_name = topic.get(job_id)
                if piece_name is None or piece_name not in req.pieces:
                    req.clarifications.append(Clarification(
                        reason="取料偏好语句缺少可挂靠的零件，需澄清",
                        quote=Quote.from_statement(st),
                    ))
                    continue
                piece = req.pieces[piece_name]
                piece.sourcing = payload["mode"]
                piece.quotes.append(Quote.from_statement(st))
                topic[job_id] = piece.piece_name
            continue

        if kind in (KIND_FINISHED_DELTA, KIND_FINISHED_EXPLICIT, KIND_TRIM):
            for job_id in target_jobs(st):
                req = result[job_id]
                if kind == KIND_TRIM:
                    # 无主语留量语句只能挂到“同一话轮”里明确谈及的零件；
                    # 更早话轮的话题不能跨话轮延续，避免“改回三毫米”
                    # 顺着别的零件的旧话题误挂。
                    piece_name = topic.get(job_id)
                    if piece_name is not None and topic_seq.get(job_id) != st.seq:
                        piece_name = None
                else:
                    piece_name = payload["piece"]
                    if piece_name not in req.pieces:
                        # 跨工单语句（如 J17/J19 共议话轮里谈的是试样），
                        # 与本工单无关，不制造误报澄清
                        continue
                if piece_name is None or piece_name not in req.pieces:
                    if kind == KIND_TRIM:
                        # 没有话题零件的工单直接忽略，不制造无关“待澄清”
                        continue
                    req.clarifications.append(Clarification(
                        reason="语句缺少可挂靠的零件，需澄清针对哪个切件",
                        quote=Quote.from_statement(st),
                    ))
                    continue
                piece = req.pieces[piece_name]
                piece.quotes.append(Quote.from_statement(st))
                topic[job_id] = piece_name
                topic_seq[job_id] = st.seq

                if kind == KIND_FINISHED_DELTA:
                    drawing_id = drawing_ref_to_id(payload["drawing_ref"])
                    if drawing_id != req.drawing_id:
                        req.clarifications.append(Clarification(
                            reason=f"原句引用 {drawing_id} 与工单图样 "
                                   f"{req.drawing_id} 不一致",
                            quote=Quote.from_statement(st),
                            blocking_pieces=[piece_name],
                        ))
                        continue
                    measure = parse_measure(
                        payload["delta_text"],
                        default_unit=req.units_default,
                        slang_mm=req.slang_mm,
                    )
                    if not measure.resolved:
                        req.clarifications.append(Clarification(
                            reason=measure.needs_convention or "尺寸无法落实",
                            quote=Quote.from_statement(st),
                            blocking_pieces=[piece_name],
                        ))
                        continue
                    if payload["direction"] == "shorter":
                        piece.finished_mm = piece.finished_mm - measure.mm
                    piece.history.append({
                        "field": "finished_mm",
                        "value_mm": piece.finished_mm,
                        "quote": Quote.from_statement(st).as_dict(),
                    })

                elif kind == KIND_FINISHED_EXPLICIT:
                    measure = parse_measure(
                        payload["measure_text"],
                        default_unit=req.units_default,
                        slang_mm=req.slang_mm,
                    )
                    if not measure.resolved:
                        req.clarifications.append(Clarification(
                            reason=measure.needs_convention or "尺寸无法落实",
                            quote=Quote.from_statement(st),
                            blocking_pieces=[piece_name],
                        ))
                        continue
                    piece.finished_mm = measure.mm
                    piece.history.append({
                        "field": "finished_mm",
                        "value_mm": measure.mm,
                        "quote": Quote.from_statement(st).as_dict(),
                    })

                else:  # KIND_TRIM
                    effect = payload["effect"]
                    measure = parse_measure(
                        payload["measure_text"],
                        default_unit=req.units_default,
                        slang_mm=req.slang_mm,
                    )
                    if not measure.resolved:
                        req.clarifications.append(Clarification(
                            reason=measure.needs_convention or "留量无法落实",
                            quote=Quote.from_statement(st),
                            blocking_pieces=[piece_name],
                        ))
                        continue
                    if effect == "clarify":
                        # 只解释旧说法（如“修边说的是一毫米”），不改当前留量
                        piece.history.append({
                            "field": "trim_mm_clarified",
                            "value_mm": measure.mm,
                            "quote": Quote.from_statement(st).as_dict(),
                        })
                        continue
                    if measure.mm < piece.trim_base_mm - 1e-9:
                        quote_key = (st.message_id, st.raw)
                        superseded = quote_key in superseded_trim_quotes.get(
                            piece_name, set())
                        if superseded:
                            # 该临时说法已被同工单后续话轮明确更正，
                            # 只留痕，不再阻塞确认
                            piece.history.append({
                                "field": "trim_mm_superseded",
                                "value_mm": measure.mm,
                                "quote": Quote.from_statement(st).as_dict(),
                            })
                            continue
                        # 下料留量不得低于图样 trim_min：这是材料约束，
                        # 不能被一句口语悄悄改小
                        req.clarifications.append(Clarification(
                            reason=(
                                f"{piece_name} 留量 {measure.mm}mm 低于图样下限 "
                                f"{piece.trim_base_mm}mm，需澄清是否放宽图样约束"),
                            quote=Quote.from_statement(st),
                            blocking_pieces=[piece_name],
                        ))
                        continue
                    piece.trim_mm = measure.mm
                    piece.history.append({
                        "field": "trim_mm",
                        "value_mm": measure.mm,
                        "quote": Quote.from_statement(st).as_dict(),
                    })

    return result
