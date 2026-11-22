"""命令行入口：离线读取 data/ 下的合成资料，输出试排报告与确认单。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .session import Session
from .report import render_report

# 本工单对车间口语单位的明确约定：1 毛 = 1 毫米（仅在有约定时生效）
DEFAULT_SLANG_MM = {
    "J17": {"毛": 1.0, "分": 1.0},
    "J19": {"分": 1.0},
}


def _load(data_dir: Path, name: str) -> list[dict]:
    return json.loads((data_dir / f"{name}.json").read_text(encoding="utf-8"))


def build_session(data_dir: Path) -> Session:
    drawings = _load(data_dir, "drawings")
    stock = _load(data_dir, "stock")
    chat = _load(data_dir, "chat")
    return Session(drawings, stock, chat, slang_mm=DEFAULT_SLANG_MM)


def _cmd_report(args) -> int:
    session = build_session(Path(args.data_dir))
    seq = args.seq if args.seq is not None else session.max_seq
    plan = session.plan_at(seq)
    requirements = session.requirements_at(seq)
    print(render_report(plan, requirements))
    return 0


def _cmd_sheet(args) -> int:
    session = build_session(Path(args.data_dir))
    seq = args.seq if args.seq is not None else session.max_seq
    path = session.save_sheet(args.output, seq)
    print(f"确认单已写入 {path}（截至 seq {seq}）")
    return 0


def _cmd_verify(args) -> int:
    session = build_session(Path(args.data_dir))
    sheet = session.load_sheet(args.sheet)
    active = sheet["active_reservations"]
    print(f"确认单自检通过：{len(active)} 条 active 占用，"
          f"生成于 seq {sheet['generated_at_seq']}")
    for res in active:
        print(f"  {res['ref']} 杆{res['bar_id']} 区间{res['interval']}")
    return 0


def _cmd_requirements(args) -> int:
    session = build_session(Path(args.data_dir))
    seq = args.seq if args.seq is not None else session.max_seq
    requirements = session.requirements_at(seq)
    print(json.dumps(
        {job_id: req and _req_dict(req) for job_id, req in requirements.items()},
        ensure_ascii=False, indent=2))
    return 0


def _req_dict(req) -> dict:
    return {
        "job_id": req.job_id,
        "drawing_id": req.drawing_id,
        "units_default": req.units_default,
        "kerf_mm": req.kerf_mm,
        "min_reuse_mm": req.min_reuse_mm,
        "pieces": {name: p.as_dict() for name, p in req.pieces.items()},
        "groups": {gid: g.as_dict() for gid, g in req.groups.items()},
        "clarifications": [
            {"reason": c.reason,
             "blocking_pieces": c.blocking_pieces,
             "quote": c.quote.as_dict()}
            for c in req.clarifications
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nestplan",
        description="工位下料离线试排工具（标准库实现，不连接机床）")
    parser.add_argument("--data-dir", default="data",
                        help="合成资料目录（默认 data）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_report = sub.add_parser("report", help="输出中文试排报告")
    p_report.add_argument("--seq", type=int, default=None,
                          help="只回放截至该话轮序号的内容")
    p_report.set_defaults(func=_cmd_report)

    p_sheet = sub.add_parser("sheet", help="保存 JSON 确认单")
    p_sheet.add_argument("--seq", type=int, default=None)
    p_sheet.add_argument("--output", default="confirmation_sheet.json")
    p_sheet.set_defaults(func=_cmd_sheet)

    p_verify = sub.add_parser("verify", help="读回确认单并做占用一致性自检")
    p_verify.add_argument("sheet")
    p_verify.set_defaults(func=_cmd_verify)

    p_req = sub.add_parser("requirements", help="输出零件需求 JSON（含原句）")
    p_req.add_argument("--seq", type=int, default=None)
    p_req.set_defaults(func=_cmd_requirements)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
