"""试排会话：话轮回放、指代澄清、确认台账与更正处理。

更正规则（不“悄悄重排”）：
- 已确认的共料承诺一旦被后续话轮改动，整组升版本并退回待确认；
  旧占用随旧版本一起释放，新版本重新试排、重新确认，释放与占用成对出现。
- 其他工单的已确认占用永远不动；新试排只能在剩余自由段里找位置。
- 每张试排单记录生成时的话轮序号与原句引用，休息后凭确认单即可回到
  当时那几句话，并能看到哪些替代曾经可行。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
import json
from pathlib import Path
import re

from .requirements import (
    build_requirements, JobRequirements, Clarification, Quote,
)
from .statements import (
    parse_message, Statement,
    KIND_ANAPHOR_QUESTION, KIND_ANAPHOR_RESOLVE, KIND_ANAPHOR_TOPIC,
    KIND_CONFIRM, KIND_UNMATCHED,
)
from .stock import Bar, Interval, load_bars
from .planner import PlanCard, place_group, place_single


@dataclass
class Reservation:
    """确认单在杆上的实际占用记录。"""

    ref: str
    card_id: str
    job_id: str
    group_id: str | None
    bar_id: str
    interval: Interval
    seq: int
    version: int
    status: str = "active"          # active / superseded
    quotes: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        data = asdict(self)
        data["interval"] = self.interval.as_pair()
        return data


@dataclass
class Anaphor:
    ordinal: int
    candidates: list[str]
    resolved_bar_id: str | None
    quote: Quote
    resolved_quote: Quote | None = None
    affected_jobs: list[str] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.resolved_bar_id is not None


class Session:
    def __init__(
        self,
        drawings: list[dict],
        stock_rows: list[dict],
        chat: list[dict],
        *,
        slang_mm: dict[str, dict[str, float]] | None = None,
    ):
        self.drawings_rows = drawings
        self.drawings = {row["id"]: row for row in drawings}
        self.chat = sorted(chat, key=lambda m: m["seq"])
        self.seq_by_message = {m["id"]: m["seq"] for m in self.chat}
        self.max_seq = max(self.seq_by_message.values())
        self.slang_mm = slang_mm or {}

        # 每轮都从原始库存重建，保证台账之外没有隐式状态
        self._stock_rows = stock_rows
        self.bars: dict[str, Bar] = load_bars(stock_rows)

        self.statements: list[Statement] = []
        for message in self.chat:
            self.statements.extend(parse_message(message))

        self.anaphors: dict[int, Anaphor] = {}
        self.ledger: list[Reservation] = []
        self.cards: dict[str, PlanCard] = {}
        self.group_versions: dict[str, int] = {}
        self.group_confirmed: dict[str, int] = {}
        self.card_counter = 0

    # ------------------------------------------------------------------
    # 库存与台账
    # ------------------------------------------------------------------
    def _fresh_bars(self) -> dict[str, Bar]:
        return load_bars(self._stock_rows)

    def _active_reservations(self) -> list[Reservation]:
        return [r for r in self.ledger if r.status == "active"]

    def _apply_ledger(self, bars: dict[str, Bar]) -> None:
        for res in self._active_reservations():
            bars[res.bar_id].claim(res.interval, res.ref)

    def _new_card_id(self, job_id: str, key: str) -> str:
        self.card_counter += 1
        return f"C-{job_id}-{key}-{self.card_counter:03d}"

    def _reset_runtime_state(self) -> None:
        """清空台账与解析缓存，准备从 seq 1 重新回放。"""
        self.anaphors = {}
        self.ledger = []
        self.cards = {}
        self.group_versions = {}
        self.group_confirmed = {}
        self.card_counter = 0

    # ------------------------------------------------------------------
    # 指代
    # ------------------------------------------------------------------
    def _track_anaphors(self, up_to_seq: int) -> None:
        for st in self.statements:
            if st.seq > up_to_seq:
                break
            if st.kind == KIND_ANAPHOR_QUESTION:
                ordinal = st.payload["ordinal"]
                if ordinal not in self.anaphors:
                    self.anaphors[ordinal] = Anaphor(
                        ordinal=ordinal,
                        candidates=self._candidate_bars_for(st),
                        resolved_bar_id=None,
                        quote=Quote.from_statement(st),
                        affected_jobs=list(st.jobs),
                    )
            elif st.kind == KIND_ANAPHOR_RESOLVE:
                if "ordinal" not in st.payload:
                    # “不是 S2”类否决句：登记到最近一个尚未落实的指代上
                    pending = [a for a in self.anaphors.values()
                               if not a.resolved]
                    if pending:
                        target = sorted(pending, key=lambda a: a.ordinal)[-1]
                        target.candidates = [
                            c for c in target.candidates
                            if c != st.payload["rejected_bar_id"]
                        ]
                    continue
                ordinal = st.payload["ordinal"]
                anaphor = self.anaphors.get(ordinal)
                if anaphor is None:
                    # 没有前置提问也登记，便于审计
                    anaphor = Anaphor(
                        ordinal=ordinal, candidates=[],
                        resolved_bar_id=None,
                        quote=Quote.from_statement(st),
                        affected_jobs=list(st.jobs),
                    )
                    self.anaphors[ordinal] = anaphor
                if st.payload.get("rejected_bar_id"):
                    anaphor.candidates = [
                        c for c in anaphor.candidates
                        if c != st.payload["rejected_bar_id"]
                    ]
                anaphor.resolved_bar_id = st.payload["bar_id"]
                anaphor.resolved_quote = Quote.from_statement(st)
                for job_id in st.jobs:
                    if job_id not in anaphor.affected_jobs:
                        anaphor.affected_jobs.append(job_id)

    def _candidate_bars_for(self, st: Statement) -> list[str]:
        """提问时点上，话轮里被称作“第 N 根”的可能杆。

        数据里没有显式编号顺序时，把当时同材料、且在话轮中出现过的杆
        作为候选；候选不唯一即不可落实。
        """
        mentioned: list[str] = []
        for earlier in self.statements:
            if earlier.seq >= st.seq:
                break
            for bar_id in re.findall(r"S\d+", earlier.raw):
                if bar_id not in mentioned:
                    mentioned.append(bar_id)
        return mentioned

    def unresolved_anaphors(self, job_id: str) -> list[Anaphor]:
        return [
            a for a in self.anaphors.values()
            if not a.resolved and job_id in a.affected_jobs
        ]

    # ------------------------------------------------------------------
    # 需求与澄清
    # ------------------------------------------------------------------
    def requirements_at(self, seq: int) -> dict[str, JobRequirements]:
        statements = [st for st in self.statements if st.seq <= seq]
        return build_requirements(
            self.drawings, statements, slang_mm=self.slang_mm)

    def _clarification_blockers(
        self, req: JobRequirements, pieces: list[str],
    ) -> list[str]:
        reasons = list(req.clarifications and [
            f"待澄清：{c.reason}（原句“{c.quote.text}”）"
            for c in req.clarifications
            if not c.blocking_pieces or set(c.blocking_pieces) & set(pieces)
        ])
        for anaphor in self.unresolved_anaphors(req.job_id):
            # 指代只阻塞“问题里实际议及的切件”，同工单其他无关切件
            # （如长短共料组）仍可继续试排与确认
            affected_pieces = self._anaphor_pieces(anaphor, req)
            if affected_pieces and not (set(affected_pieces) & set(pieces)):
                continue
            reasons.append(
                f"指代未落实：第{anaphor.ordinal}根无法确定唯一对象"
                f"（候选 {anaphor.candidates or '未知'}，原句“{anaphor.quote.text}”）"
                f"，涉及切件 {affected_pieces or '未知'}")
        return reasons

    def _anaphor_pieces(self, anaphor: Anaphor, req) -> list[str]:
        """从指代提问的两个选项文本里提取本工单实际涉及的零件名。"""
        question = anaphor.quote.text
        return [name for name in req.pieces if name in question]

    # ------------------------------------------------------------------
    # 试排
    # ------------------------------------------------------------------
    def plan_at(self, seq: int) -> dict:
        """生成截至 seq 的全部试排单，并维护确认台账。"""
        # 先从 1 到 seq 逐轮回放，让确认/释放/版本变迁完整沉淀到台账，
        # 这样休息后直接打开 seq=5 的确认单也看得到 seq=4 的旧承诺。
        self._reset_runtime_state()
        for step in range(1, seq + 1):
            self._track_anaphors(step)
            reqs = self.requirements_at(step)
            self._process_confirmations(step, reqs)

        requirements = self.requirements_at(seq)

        bars = self._fresh_bars()
        self._apply_ledger(bars)
        cards: dict[str, PlanCard] = {}

        # priority 越小越早处理；同优先级内先共料组后单件
        job_order = sorted(
            requirements.values(),
            key=lambda r: self.drawings[r.job_id]["priority"],
        )
        for req in job_order:
            groups_sorted = sorted(req.groups.values(), key=lambda g: g.group_id)
            for group in groups_sorted:
                card = self._card_for_group(req, group, bars, seq)
                cards[card.card_id] = card
            grouped = {
                name for g in req.groups.values() for name in g.pieces
            }
            for name, piece in req.pieces.items():
                if name in grouped:
                    continue
                card = self._card_for_single(req, piece, bars, seq)
                cards[card.card_id] = card

        self.cards = cards
        return {
            "seq": seq,
            "cards": [c.as_dict() for c in cards.values()],
            "ledger": [r.as_dict() for r in self.ledger],
            "clarifications": self._all_clarifications(requirements),
            "anaphors": [self._anaphor_dict(a) for a in self.anaphors.values()],
            "bars": self._bar_snapshot(bars),
        }

    def _card_for_group(self, req, group, bars, seq) -> PlanCard:
        blockers = self._clarification_blockers(req, group.pieces)
        version = self.group_versions.get(group.group_id, group.version)
        card_id = self._new_card_id(req.job_id, f"{group.group_id}-v{version}")
        card = place_group(card_id, req, group, bars, seq, self.seq_by_message)
        card.blocked_reasons = blockers + card.blocked_reasons
        confirmed_version = self.group_confirmed.get(group.group_id)

        # 该组此前确认过、当前版本又有待确认标记时，把旧版本可行方案
        # 显式标成“曾可行、需重新问人”，避免被静默改写。
        prior = [r for r in self.ledger
                 if r.group_id == group.group_id and r.status != "active"]
        if prior and not card.confirmed:
            for old in prior:
                card.alternatives.insert(0, {
                    "bar_id": old.bar_id,
                    "interval": old.interval.as_pair(),
                    "status": old.status,
                    "note": "旧版本曾确认的安排，共料承诺变更后需整组重新确认",
                    "superseded_at_seq": old.seq,
                })

        # 已确认组：卡片必须与台账实际预留一致（同杆同区间），
        # 不能在另一套库存上“另算”出一张不同的试排单。
        active = next(
            (r for r in self._active_reservations()
             if r.group_id == group.group_id and r.version == version),
            None)
        if active is not None and card.confirmable:
            card.bar_id = active.bar_id
            card.segments = self._segments_for_reservation(req, group, active)
            card.remnant_mm = round(self._free_remainder(active), 6)
            card.reusable = card.remnant_mm + 1e-9 >= req.min_reuse_mm
            card.alternatives = []
        if confirmed_version == version and card.confirmable:
            card.confirmed = True
        return card

    def _segments_for_reservation(self, req, group, reservation) -> list:
        from .planner import Segment
        pieces = [req.pieces[name] for name in group.pieces]
        segments = []
        cursor = reservation.interval.start
        for piece in pieces:
            blank = piece.blank_mm
            segments.append(Segment(piece.piece_name, blank,
                                    cursor, cursor + blank))
            cursor += blank + req.kerf_mm
        return segments

    def _free_remainder(self, reservation) -> float:
        """该预留所在自由段切完后剩下的长度。"""
        bar = self._fresh_bars()[reservation.bar_id]
        for iv in bar.free:
            if iv.start <= reservation.interval.start and \
                    reservation.interval.end <= iv.end:
                return iv.length - reservation.interval.length
        return 0.0

    def _card_for_single(self, req, piece, bars, seq) -> PlanCard:
        blockers = self._clarification_blockers(req, [piece.piece_name])
        card_id = self._new_card_id(req.job_id, piece.piece_name)
        card = place_single(card_id, req, piece, bars, seq, self.seq_by_message)
        card.blocked_reasons = blockers + card.blocked_reasons
        return card

    def _process_confirmations(self, seq: int, requirements) -> None:
        """把确认/共料变更话轮落到台账：整组确认，整组失效，整组重确认。"""
        # 先按当前需求同步组版本（共料承诺变化 -> 新版本）。
        # group.version 是“本会话内该组经历的变更次数”，只有该组此前已有
        # 台账记录（确认过或已建过版本）时，新变更才意味着整组重新确认。
        for req in requirements.values():
            for group in req.groups.values():
                known = self.group_versions.get(group.group_id, 0)
                had_ledger = any(
                    r.group_id == group.group_id for r in self.ledger)
                if group.version > known and (known or had_ledger):
                    self._supersede_group(group.group_id, req, group, seq)
                self.group_versions[group.group_id] = group.version

        for st in self.statements:
            if st.seq > seq or st.kind != KIND_CONFIRM:
                continue
            req = requirements.get(st.jobs[0]) if st.jobs else None
            if req is None:
                continue
            for group in req.groups.values():
                if set(payload_pieces(st)) <= set(group.pieces) and \
                        set(payload_pieces(st)) & set(group.pieces):
                    version = self.group_versions.get(group.group_id, group.version)
                    self.group_confirmed[group.group_id] = version
                    self._reserve_if_possible(req, group, version, st, seq)

    def _supersede_group(self, group_id, req, group, seq) -> None:
        superseded_bar = ""
        for res in self._active_reservations():
            if res.group_id == group_id:
                res.status = "superseded"
                superseded_bar = res.bar_id
        # 记录一条说明，由 plan_at 重建库存时自动只认 active 占用
        self.ledger.append(Reservation(
            ref=f"release-{group_id}-v{group.version - 1}-at{seq}",
            card_id="",
            job_id=req.job_id,
            group_id=group_id,
            bar_id=superseded_bar,
            interval=Interval(0, 0),
            seq=seq,
            version=group.version,
            status="released",
            quotes=[q.as_dict() for q in group.change_quotes],
        ))
        self.group_confirmed.pop(group_id, None)

    def _reserve_if_possible(self, req, group, version, st, seq) -> None:
        # 已存在同版本 active 占用则幂等返回
        if any(r.group_id == group.group_id and r.version == version
               and r.status == "active" for r in self.ledger):
            return
        bars = self._fresh_bars()
        self._apply_ledger(bars)
        card = place_group(
            self._new_card_id(req.job_id, f"{group.group_id}-v{version}-confirm"),
            req, group, bars, seq, self.seq_by_message,
        )
        if not card.confirmable or card.interval is None:
            return
        ref = f"reserve-{group.group_id}-v{version}"
        bars[card.bar_id].claim(card.interval, ref)
        self.ledger.append(Reservation(
            ref=ref,
            card_id=card.card_id,
            job_id=req.job_id,
            group_id=group.group_id,
            bar_id=card.bar_id,
            interval=card.interval,
            seq=seq,
            version=version,
            status="active",
            quotes=[q.as_dict() for q in group.change_quotes]
                   + [Quote.from_statement(st).as_dict()],
        ))

    # ------------------------------------------------------------------
    # 输出
    # ------------------------------------------------------------------
    def _all_clarifications(self, requirements) -> list[dict]:
        out = []
        for req in requirements.values():
            for c in req.clarifications:
                out.append({
                    "job_id": req.job_id,
                    "reason": c.reason,
                    "blocking_pieces": list(c.blocking_pieces),
                    "quote": c.quote.as_dict(),
                })
            for st in self.statements:
                if st.kind == KIND_UNMATCHED and req.job_id in st.jobs:
                    out.append({
                        "job_id": req.job_id,
                        "reason": st.clarify_reason,
                        "blocking_pieces": [],
                        "quote": Quote.from_statement(st).as_dict(),
                    })
        return out

    def _anaphor_dict(self, anaphor: Anaphor) -> dict:
        return {
            "ordinal": anaphor.ordinal,
            "candidates": list(anaphor.candidates),
            "resolved_bar_id": anaphor.resolved_bar_id,
            "resolved": anaphor.resolved,
            "affected_jobs": list(anaphor.affected_jobs),
            "quote": anaphor.quote.as_dict(),
            "resolved_quote": (anaphor.resolved_quote.as_dict()
                               if anaphor.resolved_quote else None),
        }

    def _bar_snapshot(self, bars: dict[str, Bar]) -> list[dict]:
        return [
            {
                "id": bar.id,
                "grade": bar.grade,
                "direction": list(bar.direction),
                "offcut": bar.offcut,
                "free": [iv.as_pair() for iv in bar.free],
                "occupied": [iv.as_pair() for iv in bar.occupied],
                "locked_refs": list(bar.locked_refs),
            }
            for bar in sorted(bars.values(), key=lambda b: b.id)
        ]

    def confirmation_sheet(self, seq: int) -> dict:
        """确认单：休息回来凭它回到当时那几句话。"""
        plan = self.plan_at(seq)
        return {
            "generated_at_seq": seq,
            "tool": "nestplan (offline trial only, no machine connection)",
            "active_reservations": [
                r.as_dict() for r in self.ledger if r.status == "active"
            ],
            "history": [r.as_dict() for r in self.ledger],
            "cards": plan["cards"],
            "clarifications": plan["clarifications"],
            "anaphors": plan["anaphors"],
        }

    def save_sheet(self, path: str | Path, seq: int) -> Path:
        path = Path(path)
        path.write_text(
            json.dumps(self.confirmation_sheet(seq),
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def load_sheet(self, path: str | Path) -> dict:
        """读回确认单并做一致性自检：active 占用不得与库存 locked 段重叠。"""
        sheet = json.loads(Path(path).read_text(encoding="utf-8"))
        bars = self._fresh_bars()
        for row in sheet["active_reservations"]:
            bar = bars[row["bar_id"]]
            start, end = row["interval"]
            for iv in bar.occupied:
                if not (end <= iv.start or start >= iv.end):
                    raise ValueError(
                        f"确认单占用 {row['ref']} 与杆 {bar.id} 既有占用冲突")
        return sheet


def payload_pieces(st: Statement) -> list[str]:
    return st.payload.get("pieces", [])
