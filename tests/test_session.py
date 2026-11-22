"""会话时序：指代澄清、确认台账、更正整组重确认、到货时序、确认单往返。"""

import _bootstrap  # noqa: F401  把仓库根注入 sys.path，兼容 -I discover
import json
import tempfile
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


def card_by_pieces(plan, piece_set):
    for card in plan["cards"]:
        if set(card["pieces"]) == set(piece_set):
            return card
    raise KeyError(piece_set)


class SessionTimelineTest(unittest.TestCase):
    def setUp(self):
        self.session = make_session()

    def test_anaphor_unresolved_blocks_only_affected_cards(self):
        plan = self.session.plan_at(2)
        group_card = card_by_pieces(plan, ["长件", "短件"])
        right_card = card_by_pieces(plan, ["右件"])
        sample_card = card_by_pieces(plan, ["试样"])
        # 无关的长短共料仍可试排确认；右件与试样因指代/留量待澄清
        self.assertTrue(group_card["confirmable"])
        self.assertFalse(right_card["confirmable"])
        self.assertFalse(sample_card["confirmable"])
        blockers = " ".join(right_card["blocked_reasons"])
        self.assertIn("第2根", blockers)

    def test_anaphor_resolution_at_seq3(self):
        plan = self.session.plan_at(3)
        anaphor = next(a for a in plan["anaphors"] if a["ordinal"] == 2)
        self.assertTrue(anaphor["resolved"])
        self.assertEqual(anaphor["resolved_bar_id"], "S3")
        sample = card_by_pieces(plan, ["试样"])
        self.assertTrue(sample["confirmable"])

    def test_confirmation_creates_ledger_reservation(self):
        plan = self.session.plan_at(4)
        group = card_by_pieces(plan, ["长件", "短件"])
        self.assertTrue(group["confirmed"])
        self.assertEqual(group["bar_id"], "S1")
        active = [r for r in plan["ledger"] if r["status"] == "active"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["bar_id"], "S1")
        self.assertEqual(active[0]["interval"], [0, 554.0])

    def test_locked_refs_never_moved(self):
        plan = self.session.plan_at(4)
        s2 = next(b for b in plan["bars"] if b["id"] == "S2")
        self.assertIn("card-old", s2["locked_refs"])
        self.assertIn([650, 950], s2["occupied"])

    def test_no_two_cards_claim_same_interval(self):
        plan = self.session.plan_at(4)
        claimed = []
        for r in plan["ledger"]:
            if r["status"] == "active":
                claimed.append((r["bar_id"], tuple(r["interval"])))
        self.assertEqual(len(claimed), len(set(claimed)))

    def test_correction_supersedes_whole_group_and_releases(self):
        plan = self.session.plan_at(5)
        group = card_by_pieces(plan, ["长件", "短件", "右件"])
        self.assertFalse(group["confirmed"])
        self.assertEqual(group["group_id"], "G17-1")
        self.assertEqual(group["bar_id"], "S1")
        self.assertEqual(group["required_mm"], 801.0)
        # 旧确认被整组失效并释放，没有静默挪动
        active = [r for r in plan["ledger"] if r["status"] == "active"]
        self.assertEqual(active, [])
        statuses = {r["ref"].split("-")[0]: r["status"]
                    for r in plan["ledger"]}
        self.assertIn("reserve", statuses)
        self.assertIn("release", statuses)
        # 旧方案作为“曾可行、需重新问人”的替代保留
        self.assertTrue(any("曾确认" in a.get("note", "")
                            for a in group["alternatives"]))

    def test_other_confirmed_work_order_immutable(self):
        # seq4 确认后，seq5 重排只动 G17-1 自己；若组仍能放下，
        # 台账里不会出现其他工单的新占用
        plan4 = self.session.plan_at(4)
        s1_at_4 = next(b for b in plan4["bars"] if b["id"] == "S1")
        plan5 = self.session.plan_at(5)
        s1_at_5 = next(b for b in plan5["bars"] if b["id"] == "S1")
        # 释放后 S1 回到全自由，说明旧占用是成对释放而非被改写
        self.assertEqual(s1_at_4["occupied"], [[0, 554.0]])
        self.assertEqual(s1_at_5["occupied"], [])

    def test_s5_not_available_before_arrival(self):
        plan4 = self.session.plan_at(4)
        # seq4 时 S5 尚未到货（arrives_after=M5），不应出现在任何备选里
        for card in plan4["cards"]:
            self.assertNotIn("S5", [a["bar_id"] for a in card["alternatives"]])
            self.assertNotEqual(card["bar_id"], "S5")
        plan5 = self.session.plan_at(5)
        # seq5 仍不满足 seq > 5，S5 依旧不可用
        for card in plan5["cards"]:
            self.assertNotEqual(card["bar_id"], "S5")

    def test_s5_becomes_available_after_seq5(self):
        plan6 = self.session.plan_at(6)
        bars = {b["id"]: b for b in plan6["bars"]}
        self.assertIn("S5", bars)

    def test_confirmation_sheet_roundtrip(self):
        self.session.plan_at(4)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sheet.json"
            self.session.save_sheet(path, 4)
            sheet = self.session.load_sheet(path)
        self.assertEqual(sheet["generated_at_seq"], 4)
        self.assertEqual(len(sheet["active_reservations"]), 1)
        # 确认单带原句，休息后能回到当时那几句话
        ref = sheet["active_reservations"][0]
        self.assertTrue(ref["quotes"])
        self.assertIn("确认长短共料",
                      " ".join(q["text"] for q in ref["quotes"]))

    def test_sheet_keeps_history_for_break_resumption(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sheet5.json"
            self.session.save_sheet(path, 5)
            sheet = self.session.load_sheet(path)
        refs = [r["ref"] for r in sheet["history"]]
        self.assertTrue(any(r.startswith("reserve-G17-1") for r in refs))
        self.assertTrue(any(r.startswith("release-G17-1") for r in refs))
        self.assertEqual(sheet["active_reservations"], [])

    def test_quote_provenance_on_every_card(self):
        plan = self.session.plan_at(5)
        for card in plan["cards"]:
            self.assertTrue(card["quote_refs"])
            for q in card["quote_refs"]:
                self.assertIn(q["message_id"],
                              {"M1", "M2", "M3", "M4", "M5"})


if __name__ == "__main__":
    unittest.main()
