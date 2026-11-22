"""把试排结果渲染成办公室可读的中文文字报告。"""

from __future__ import annotations

from .planner import PlanCard


def render_requirements(requirements) -> list[str]:
    lines = ["一、零件需求（原句均可回溯，公差与留量分列）"]
    for req in sorted(requirements.values(), key=lambda r: r.job_id):
        lines.append(f"  工单 {req.job_id} / 图样 {req.drawing_id}"
                     f"（材料 {req.pieces and next(iter(req.pieces.values())).grade}，"
                     f"kerf {req.kerf_mm}mm，复用下限 {req.min_reuse_mm}mm，"
                     f"默认单位：{req.units_default or '无约定，裸数字须澄清'}）")
        for piece in req.pieces.values():
            lines.append(
                f"    - {piece.piece_name}：成品 {piece.finished_mm}mm"
                f"（图样 {piece.finished_base_mm}，公差 ±{piece.tolerance_mm}，"
                f"公差不参与下料长度）；留量 {piece.trim_mm}mm"
                f"（图样下限 {piece.trim_base_mm}）；"
                f"毛坯 {piece.blank_mm if piece.blank_mm is not None else '待澄清'}mm；"
                f"朝向 {piece.direction}；取料 {_sourcing_text(piece.sourcing)}"
                + (f"；共料组 {piece.group_id}" if piece.group_id else "")
                + ("；暂不确认" if piece.held else ""))
            for q in piece.quotes:
                lines.append(
                    f"        原句[{q.message_id}/seq{q.seq}/{q.source}] {q.text}")
        for group in req.groups.values():
            lines.append(
                f"    共料组 {group.group_id}（v{group.version}）："
                f"{' + '.join(group.pieces)} 必须同杆同段切出")
    return lines


def _sourcing_text(mode: str) -> str:
    return {
        "same_bar": "必须共料",
        "prefer_offcut": "优先余料/料头，无则用相容库存",
        "any_compatible": "允许任意相容库存",
    }.get(mode, mode)


def render_clarifications(plan: dict) -> list[str]:
    lines = ["二、待澄清事项（相关试排单暂不能确认）"]
    pending_anaphors = [a for a in plan["anaphors"] if not a["resolved"]]
    if not plan["clarifications"] and not pending_anaphors:
        lines.append("  （无）")
        return lines
    for item in plan["clarifications"]:
        q = item["quote"]
        lines.append(
            f"  - [{item['job_id']}] {item['reason']}"
            f"（原句[{q['message_id']}/seq{q['seq']}/{q['source']}] {q['text']}）")
    for a in pending_anaphors:
        q = a["quote"]
        lines.append(
            f"  - 指代“第{a['ordinal']}根”未落实，候选 {a['candidates'] or '未知'}，"
            f"影响工单 {', '.join(a['affected_jobs'])}"
            f"（原句[{q['message_id']}/seq{q['seq']}/{q['source']}] {q['text']}）")
    return lines


def render_anaphor_resolutions(plan: dict) -> list[str]:
    lines = ["二（续）、指代落实记录（无法唯一落实时才留待澄清）"]
    resolved = [a for a in plan["anaphors"] if a["resolved"]]
    if not resolved:
        lines.append("  （无）")
        return lines
    for a in resolved:
        rq = a["resolved_quote"]
        lines.append(
            f"  - “第{a['ordinal']}根”已落实为 {a['resolved_bar_id']}"
            f"（{rq['source']} seq{rq['seq']}：{rq['text']}），"
            f"影响工单 {', '.join(a['affected_jobs'])}")
    return lines


def render_cards(plan: dict) -> list[str]:
    lines = ["三、试排单（依图样生成的段占用、剩料与缺料）"]
    for card in plan["cards"]:
        head = (
            f"  {card['card_id']} 工单{card['job_id']} "
            f"切件[{'、'.join(card['pieces'])}]"
            + (f" 共料组{card['group_id']}" if card["group_id"] else "")
            + (" 【已确认】" if card["confirmed"] else
               " 【可确认】" if card["confirmable"] else " 【暂不能确认】"))
        lines.append(head)
        if card["blocked_reasons"]:
            for reason in card["blocked_reasons"]:
                lines.append(f"      阻塞：{reason}")
        if card["bar_id"] is None:
            if card["shortage_mm"] > 0:
                lines.append(
                    f"      缺料 {card['shortage_mm']}mm"
                    f"（共需 {card['required_mm']}mm，含 kerf "
                    f"{card['kerf_mm']}mm/次）")
        else:
            lines.append(
                f"      杆 {card['bar_id']}（{card['grade']}），"
                f"共需 {card['required_mm']}mm，段占用：")
            for seg in card["segments"]:
                lines.append(
                    f"        · {seg['piece']} 毛坯 {seg['blank_mm']}mm"
                    f" 区间[{seg['start']}, {seg['end']})")
            reuse = "可继续用的长余料" if card["reusable"] else "短于复用下限，按废料"
            lines.append(
                f"      剩料 {card['remnant_mm']}mm（{reuse}，"
                f"下限 {card['min_reuse_mm']}mm）")
            if card["alternatives"]:
                parts = []
                for a in card["alternatives"]:
                    if "note" in a:
                        parts.append(
                            f"{a['bar_id']}段{a['interval']}"
                            f"（{a['status']}，{a['note']}，"
                            f"seq{a['superseded_at_seq']} 失效）")
                    else:
                        parts.append(
                            f"{a['bar_id']}段{a['interval']}"
                            f"剩{a['remnant_after_mm']}mm")
                lines.append(f"      曾可行的替代：{'；'.join(parts)}")
    return lines


def render_ledger(plan: dict) -> list[str]:
    lines = ["四、确认台账（已确认占用不可被后续重排挪动）"]
    active = [r for r in plan["ledger"] if r["status"] == "active"]
    if not active:
        lines.append("  （暂无已确认占用）")
    for res in active:
        lines.append(
            f"  - {res['ref']}：工单{res['job_id']} "
            f"组{res['group_id']} v{res['version']} 占用杆 {res['bar_id']} "
            f"区间{res['interval']}（seq{res['seq']} 确认）")
    released = [r for r in plan["ledger"] if r["status"] != "active"]
    for res in released:
        lines.append(
            f"  - {res['ref']}：{res['status']}（组承诺变更，整组于 seq{res['seq']} 重新确认）")
    return lines


def render_bars(plan: dict) -> list[str]:
    lines = ["五、库存快照（含外部 locked 占用与本会话确认占用）"]
    for bar in plan["bars"]:
        lines.append(
            f"  - {bar['id']} {bar['grade']} 朝向{bar['direction']}"
            f"{' 料头杆' if bar['offcut'] else ''} "
            f"自由段{bar['free']} 已占用{bar['occupied']} 引用{bar['locked_refs']}")
    return lines


def render_report(plan: dict, requirements) -> str:
    parts = [
        f"离线试排报告（截至 seq {plan['seq']}，仅试排与确认用途，不连接机床）",
        "=" * 68,
        *render_requirements(requirements),
        "",
        *render_clarifications(plan),
        *render_anaphor_resolutions(plan),
        "",
        *render_cards(plan),
        "",
        *render_ledger(plan),
        "",
        *render_bars(plan),
    ]
    return "\n".join(parts)
