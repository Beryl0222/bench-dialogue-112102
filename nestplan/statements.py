"""把班组话轮拆成带原句的结构化语句。

设计原则：宁可标成“未匹配/需澄清”，也不凭猜测吞掉文字。每条语句都保留
话轮 id、顺序号、角色和原文片段，下游所有数量、尺寸、对象、材料限制都能
回溯到原句。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

# ---- 词表与正则 -------------------------------------------------------

PIECE_ALIASES = {
    "长": "长件", "长件": "长件",
    "短": "短件", "短件": "短件",
    "右": "右件", "右件": "右件",
    "试样": "试样",
}

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}

_SPLIT_RE = re.compile(r"[。；？!?]")
_PIECE_GROUP = r"(?:长件|短件|右件|试样|长|短)"
_DRAWING_REF = r"图[一二三四五六七八九\d]+"
_BAR_REF = r"S\d+"
# 句首可能带的承接词，如“还有短件留量千万别当成公差”
_LEAD_IN = r"(?:还有|另外|此外)?"


@dataclass
class Statement:
    """一条从话轮中切出的语句。"""

    kind: str                       # 见 KIND_* 常量
    raw: str                        # 原文片段
    message_id: str
    seq: int
    source: str
    jobs: list[str]
    payload: dict = field(default_factory=dict)
    resolved: bool = True           # False 表示该语句本身要求澄清
    clarify_reason: str | None = None

    def quote(self) -> dict:
        """供确认单引用的原句出处。"""
        return {
            "message_id": self.message_id,
            "seq": self.seq,
            "source": self.source,
            "text": self.raw,
        }


# 语句种类
KIND_SHARED_BAR = "shared_bar"            # 必须共料的切件组
KIND_FINISHED_DELTA = "finished_delta"    # 成品尺寸相对图样的增减
KIND_FINISHED_EXPLICIT = "finished_explicit"   # 显式成品尺寸
KIND_TRIM = "trim"                        # 下料额外留量（不是公差）
KIND_SOURCING = "sourcing"                # 取料约束：优先余料/任意相容
KIND_ANAPHOR_QUESTION = "anaphor_question"     # “第二根”指代提问
KIND_ANAPHOR_RESOLVE = "anaphor_resolve"       # “第二根”落实到库存号
KIND_ANAPHOR_TOPIC = "anaphor_topic"           # 指代问题所指对象的回答
KIND_CONFIRM = "confirm"                  # 确认共料/计划
KIND_HOLD = "hold"                        # 暂不确认
KIND_ADVISORY = "advisory"                # 提示语（如留量≠公差）
KIND_UNMATCHED = "unmatched"


def _norm_piece(token: str) -> str:
    return PIECE_ALIASES.get(token, token)


def _expand_group(text: str) -> list[str]:
    """把“长短”“长、短件”这类并列词展开成零件名列表。"""
    names: list[str] = []
    for token in re.findall(_PIECE_GROUP, text):
        name = _norm_piece(token)
        if name not in names:
            names.append(name)
    return names


def _ordinal(token: str) -> int:
    if token in _CN_NUM:
        return _CN_NUM[token]
    return int(token)


# 每条规则：(kind, 编译后的正则, 载荷构造函数, 是否复式规则)
_RULES: list[tuple[str, re.Pattern, callable, bool]] = []


def _rule(pattern: str, *, multi: bool = False):
    def register(fn):
        _RULES.append((pattern and re.compile(pattern), fn, multi))
        return fn
    return register


@_rule(rf"^第(?P<ord>[一二两三四五六七八九\d]+)根是库存\s*(?P<bar>{_BAR_REF})(?:\s*，不是\s*(?P<wrong>{_BAR_REF}))?$",
       multi=True)
def _anaphor_resolve(m):
    return {
        "ordinal": _ordinal(m.group("ord")),
        "bar_id": m.group("bar"),
        "rejected_bar_id": m.group("wrong"),
    }


@_rule(rf"^第(?P<ord>[一二两三四五六七八九\d]+)根是库存\s*(?P<bar>{_BAR_REF})$")
def _anaphor_resolve_short(m):
    return {
        "ordinal": _ordinal(m.group("ord")),
        "bar_id": m.group("bar"),
        "rejected_bar_id": None,
    }


@_rule(rf"^不是\s*(?P<wrong>{_BAR_REF})$")
def _anaphor_reject(m):
    # 与同话轮前半句的落实动作配对，载荷里只登记被否决的杆
    return {"rejected_bar_id": m.group("wrong")}


@_rule(rf"^第(?P<ord>[一二两三四五六七八九\d]+)根是要留给(?P<option_a>.+?)还是(?P<option_b>.+?)$")
def _anaphor_question(m):
    return {
        "ordinal": _ordinal(m.group("ord")),
        "option_a": m.group("option_a"),
        "option_b": m.group("option_b"),
    }


@_rule(r"^说的是(?P<topic>.+?)$")
def _anaphor_topic(m):
    return {"topic": _norm_piece(m.group("topic"))}


@_rule(rf"^修边说的是(?P<old>.+?)，改成(?P<new>.+?)$", multi=True)
def _trim_correction(m):
    # 两个留量事件：先落实旧说法的含义，再生效新值
    return {
        "events": [
            {"measure_text": m.group("old"), "effect": "clarify"},
            {"measure_text": m.group("new"), "effect": "change"},
        ],
    }


@_rule(rf"^(?P<piece>{_PIECE_GROUP})成品比(?P<drawing>{_DRAWING_REF})短(?P<measure>.+?)$")
def _finished_delta(m):
    return {
        "piece": _norm_piece(m.group("piece")),
        "drawing_ref": m.group("drawing"),
        "delta_text": m.group("measure"),
        "direction": "shorter",
    }


@_rule(rf"^(?P<piece>{_PIECE_GROUP})(?P<measure>.+?)说的是成品$")
def _finished_explicit(m):
    return {
        "piece": _norm_piece(m.group("piece")),
        "measure_text": m.group("measure"),
    }


@_rule(rf"^仍留(?P<measure>.+?)$")
@_rule(rf"^另留(?P<measure>.+?)(?:修边)?$")
@_rule(rf"^留量改回(?P<measure>.+?)$")
def _trim_simple(m):
    return {"measure_text": m.group("measure"), "effect": "set"}


@_rule(rf"^修边说的是(?P<measure>.+?)$")
def _trim_clarify_only(m):
    return {"measure_text": m.group("measure"), "effect": "clarify"}


@_rule(rf"^改成(?P<measure>.+?)$")
def _trim_change_only(m):
    return {"measure_text": m.group("measure"), "effect": "change"}


@_rule(rf"^{_LEAD_IN}(?P<piece>{_PIECE_GROUP})只要求用相容材料，能从剩头出更好$",
       multi=True)
def _sourcing_prefer_offcut(m):
    return {"piece": _norm_piece(m.group("piece")),
            "mode": "prefer_offcut"}


@_rule(r"^能从剩头出更好$")
def _offcut_preference_only(m):
    return {"mode": "prefer_offcut", "piece": None}


@_rule(rf"^该组(?P<piece>{_PIECE_GROUP})也要取自同一根$")
@_rule(rf"^(?P<piece>{_PIECE_GROUP})也要取自同一根$")
def _also_same_bar(m):
    return {"piece": _norm_piece(m.group("piece"))}


_PIECE_LIST = rf"{_PIECE_GROUP}(?:[、,]{_PIECE_GROUP})*"
# “长短”这类无顿号连写：把相邻单字零件词整体展开
_PIECE_RUN = r"(?:长|短|右)+(?:件)?"


@_rule(rf"^确认(?P<what>{_PIECE_LIST}|{_PIECE_RUN})共料$")
def _confirm_shared(m):
    return {"pieces": _expand_group(m.group("what"))}


@_rule(rf"^(?:{_DRAWING_REF})?(?P<group>{_PIECE_LIST}|{_PIECE_RUN})同取一根$")
def _shared_bar(m):
    return {"pieces": _expand_group(m.group("group"))}


@_rule(rf"^(?P<piece>{_PIECE_GROUP})按更正后的成品尺寸走$")
def _use_corrected_finished(m):
    return {"piece": _norm_piece(m.group("piece")),
            "advisory": "use_latest_finished"}


@_rule(rf"^(?P<piece>{_PIECE_GROUP})先不确认$")
def _hold(m):
    return {"piece": _norm_piece(m.group("piece"))}


@_rule(rf"^{_LEAD_IN}(?:(?P<piece>{_PIECE_GROUP}))?留量千万别当成公差$")
def _advisory(m):
    payload = {"advisory": "trim_is_not_tolerance"}
    if m.group("piece"):
        payload["piece"] = _norm_piece(m.group("piece"))
    return payload


def _classify(clause: str) -> tuple[str, dict, bool]:
    for pattern, fn, multi in _RULES:
        m = pattern.match(clause)
        if m:
            kind = fn.__name__  # 临时名，下面映射成 KIND_*
            payload = fn(m)
            return kind, payload, multi
    return KIND_UNMATCHED, {}, False


# 处理函数名 -> 对外语句种类
_KIND_MAP = {
    "_anaphor_resolve": KIND_ANAPHOR_RESOLVE,
    "_anaphor_question": KIND_ANAPHOR_QUESTION,
    "_anaphor_topic": KIND_ANAPHOR_TOPIC,
    "_trim_correction": KIND_TRIM,
    "_finished_delta": KIND_FINISHED_DELTA,
    "_finished_explicit": KIND_FINISHED_EXPLICIT,
    "_trim_simple": KIND_TRIM,
    "_sourcing_prefer_offcut": KIND_SOURCING,
    "_also_same_bar": KIND_SHARED_BAR,
    "_confirm_shared": KIND_CONFIRM,
    "_shared_bar": KIND_SHARED_BAR,
    "_hold": KIND_HOLD,
    "_advisory": KIND_ADVISORY,
    "_use_corrected_finished": KIND_ADVISORY,
    "_anaphor_resolve_short": KIND_ANAPHOR_RESOLVE,
    "_anaphor_reject": KIND_ANAPHOR_RESOLVE,
    "_trim_clarify_only": KIND_TRIM,
    "_trim_change_only": KIND_TRIM,
    "_offcut_preference_only": KIND_SOURCING,
}


def parse_message(message: dict) -> list[Statement]:
    """解析一条话轮记录，返回语句列表。

    入参字段：id/thread/seq/jobs/source/text（与 data/chat.json 一致）。
    “修边说的是一毫米，改成两毫米”这类复式语句会展开成多条 Statement。
    """
    text = message["text"]
    base = {
        "message_id": message["id"],
        "seq": message["seq"],
        "source": message["source"],
        "jobs": list(message.get("jobs", [])),
    }
    statements: list[Statement] = []
    # 先按句末标点切成完整句；整句匹配不上时再退回逗号切分，
    # 这样“右件只要求用相容材料，能从剩头出更好”能作为一条复式规则命中，
    # 而“第二根是库存 S3，不是 S2”会退化成两条落实/否决语句。
    sentences = [c.strip() for c in _SPLIT_RE.split(text) if c.strip()]
    clauses: list[str] = []
    for sentence in sentences:
        func_name, _, multi = _classify(sentence)
        matched = _KIND_MAP.get(func_name, func_name) != KIND_UNMATCHED
        # 带逗号的句子只有被复式规则整体命中才不拆；否则按逗号回退，
        # 避免宽松单句规则（如“说的是…”）把整句吞掉。
        if matched and (multi or "，" not in sentence):
            clauses.append(sentence)
        else:
            clauses.extend(p.strip() for p in sentence.split("，") if p.strip())

    for clause in clauses:
        func_name, payload, _multi = _classify(clause)
        kind = _KIND_MAP.get(func_name, func_name)

        if kind == KIND_TRIM and "events" in payload:
            for event in payload["events"]:
                statements.append(Statement(
                    KIND_TRIM, clause, **base,
                    payload={"measure_text": event["measure_text"],
                             "effect": event["effect"]},
                ))
            continue

        if kind == KIND_UNMATCHED:
            statements.append(Statement(
                KIND_UNMATCHED, clause, **base,
                resolved=False,
                clarify_reason="没有匹配的试排句式，需人工澄清这句话的含义",
            ))
            continue

        statements.append(Statement(kind, clause, **base, payload=payload))
    return statements


def drawing_ref_to_id(ref: str) -> str:
    """“图四” -> “R4”。数字部分直接沿用，中文数词转阿拉伯数字。"""
    num = ref.removeprefix("图")
    digit = _CN_NUM.get(num, num)
    return f"R{digit}"
