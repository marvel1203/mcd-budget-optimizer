"""营养模块测试。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from optimizer.models import Item  # noqa: E402
from optimizer.nutrition import (  # noqa: E402
    Nutrition, NutritionTable, nutrition_from_raw, table_from_payload,
)

FIXTURE_NUTRITION = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "fixtures", "synthetic_menu.json")


class TestParse(unittest.TestCase):
    def test_chinese_keys(self):
        n = nutrition_from_raw({"foodName": "巨无霸", "能量": 550, "蛋白质": 31,
                                "脂肪": 27, "碳水化合物": 45, "钠": 1020})
        self.assertIsNotNone(n)
        self.assertEqual(n.name, "巨无霸")
        self.assertAlmostEqual(n.kcal, 550.0)
        self.assertAlmostEqual(n.protein_g, 31.0)
        self.assertAlmostEqual(n.sodium_mg, 1020.0)

    def test_english_keys(self):
        n = nutrition_from_raw({"name": "Big Mac", "energy": 550, "protein": 31, "fat": 27})
        self.assertIsNotNone(n)
        self.assertAlmostEqual(n.kcal, 550.0)

    def test_kj_converted_to_kcal(self):
        # 2300 kJ ≈ 549.7 kcal
        n = nutrition_from_raw({"name": "X", "energy_kj": 2300})
        self.assertIsNotNone(n)
        self.assertAlmostEqual(n.kcal, 2300 / 4.184, places=1)

    def test_value_with_unit_suffix(self):
        n = nutrition_from_raw({"name": "X", "能量": "550千卡", "蛋白质": "31g"})
        self.assertAlmostEqual(n.kcal, 550.0)
        self.assertAlmostEqual(n.protein_g, 31.0)

    def test_all_empty_returns_none(self):
        self.assertIsNone(nutrition_from_raw({"name": "X"}))
        self.assertIsNone(nutrition_from_raw({"name": "X", "备注": "无数据"}))


class TestLookup(unittest.TestCase):
    def setUp(self):
        self.table = NutritionTable([
            Nutrition(name="巨无霸", kcal=550),
            Nutrition(name="麦乐鸡（5块）", kcal=220),
            Nutrition(name="中薯条", kcal=320),
        ])

    def test_exact(self):
        self.assertAlmostEqual(self.table.lookup("巨无霸").kcal, 550.0)

    def test_ignores_punctuation(self):
        self.assertIsNotNone(self.table.lookup("麦乐鸡5块"))

    def test_partial_match(self):
        self.assertIsNotNone(self.table.lookup("薯条"))

    def test_unknown_returns_none(self):
        self.assertIsNone(self.table.lookup("完全不存在的东西"))

    def test_single_char_does_not_mis_match(self):
        """单字查询不该命中一堆东西。"""
        self.assertIsNone(self.table.lookup("堡"))


class TestTotal(unittest.TestCase):
    def setUp(self):
        self.table = table_from_payload({
            "foods": [{"foodName": "吉士汉堡", "能量": 300, "蛋白质": 15},
                      {"foodName": "牛奶", "能量": 130, "蛋白质": 8}]
        })

    def test_sum_with_qty(self):
        from optimizer.models import PlanLine
        lines = [PlanLine(Item(id="a", name="吉士汉堡", price_cents=1200), 2),
                 PlanLine(Item(id="b", name="牛奶", price_cents=800), 1)]
        totals = self.table.total_for(lines)
        self.assertAlmostEqual(totals["kcal"], 730.0)   # 300*2 + 130
        self.assertAlmostEqual(totals["protein_g"], 38.0)

    def test_no_match_returns_empty(self):
        from optimizer.models import PlanLine
        lines = [PlanLine(Item(id="c", name="不存在的餐品", price_cents=100), 1)]
        self.assertEqual(self.table.total_for(lines), {})


class TestFromFixture(unittest.TestCase):
    def test_loads_all_entries(self):
        import json
        with open(FIXTURE_NUTRITION, "r", encoding="utf-8") as f:
            data = json.load(f)
        table = table_from_payload(data["nutrition"])
        self.assertGreater(len(table), 15)
        self.assertIsNotNone(table.lookup("巨无霸"))
        self.assertIsNotNone(table.lookup("麦旋风"))


if __name__ == "__main__":
    unittest.main()
