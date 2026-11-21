"""检查交换资料的标识、文本和跨表引用。"""

import json
from pathlib import Path
import unittest


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


class SourceDataTest(unittest.TestCase):
    def setUp(self):
        self.tables = {}
        for path in sorted(DATA_DIR.glob("*.json")):
            rows = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsInstance(rows, list)
            self.assertTrue(rows, path.name)
            by_id = {}
            for row in rows:
                identifier = row["id"]
                self.assertIsInstance(identifier, str, path.name)
                self.assertNotIn(identifier, by_id, path.name)
                if "text" in row:
                    self.assertIsInstance(row["text"], str, path.name)
                by_id[identifier] = row
            self.tables[path.stem] = by_id

    def test_exchange_tables_present(self):
        self.assertEqual(set(self.tables), {'chat', 'drawings', 'stock'})

    def test_related_source_records(self):
        data = self.tables
        drawings = data["drawings"]
        for key in ("J17", "J19"):
            self.assertIn(drawings[key]["drawing"], drawings)
        self.assertIsNone(drawings["R5"]["units_default"])
        for row in data["stock"].values():
            for left, right in row["free"]:
                self.assertLess(left, right)
                for start, end in row["occupied"]:
                    self.assertTrue(right <= start or left >= end)
        self.assertTrue(set(data["chat"]["M2"]["jobs"]) <= drawings.keys())
        self.assertIn(data["stock"]["S5"]["arrives_after"], data["chat"])
