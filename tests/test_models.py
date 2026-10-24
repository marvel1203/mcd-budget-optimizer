"""领域模型与归一化测试。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from optimizer.models import (  # noqa: E402
    CouponRule, Item, OrderSpec, PlanLine, guess_category, item_from_raw,
    rule_from_raw, to_cents,
)


class TestToCents(unittest.TestCase):
    def test_yuan_float(self):
        self.assertEqual(to_cents(12.5), 1250)
        self.assertEqual(to_cents("12.50"), 1250)
        self.assertEqual(to_cents("¥13.9"), 1390)

    def test_cents_int(self):
        self.assertEqual(to_cents(1250), 1250)

    def test_explicit_object(self):
        self.assertEqual(to_cents({"cents": 1250}), 1250)
        self.assertEqual(to_cents({"yuan": 12.5}), 1250)

    def test_rejects_negative(self):
        with self.assertRaises(ValueError):
            to_cents(-1)


class TestCategory(unittest.TestCase):
    def test_declared_wins(self):
        self.assertEqual(guess_category("随便什么", "drink"), "drink")

    def test_chinese_keywords(self):
        self.assertEqual(guess_category("麦辣鸡腿堡"), "main")
        self.assertEqual(guess_category("中薯条"), "side")
        self.assertEqual(guess_category("中可乐"), "drink")
        self.assertEqual(guess_category("香芋派"), "dessert")

    def test_unknown_falls_back(self):
        self.assertEqual(guess_category("纸巾"), "other")


class TestItemFromRaw(unittest.TestCase):
    def test_field_aliases(self):
        it = item_from_raw({"sku": "A1", "title": "巨无霸", "sellPrice": 24.5,
                            "category": "汉堡", "labels": ["牛肉"]})
        self.assertEqual(it.id, "A1")
        self.assertEqual(it.name, "巨无霸")
        self.assertEqual(it.price_cents, 2450)
        self.assertEqual(it.category, "main")
        self.assertIn("牛肉", it.tags)

    def test_missing_price_raises(self):
        with self.assertRaises(ValueError):
            item_from_raw({"id": "X", "name": "没价格"})

    def test_sold_out(self):
        it = item_from_raw({"id": "X", "name": "售罄品", "price": 10, "status": "SOLD_OUT"})
        self.assertFalse(it.available)


class TestRuleFromRaw(unittest.TestCase):
    def test_threshold(self):
        r = rule_from_raw({"name": "满60减8", "minSpend": 60, "discount": 8})
        self.assertIsNotNone(r)
        self.assertEqual(r.kind, "threshold")
        self.assertEqual(r.min_spend_cents, 6000)
        self.assertEqual(r.discount_cents, 800)

    def test_bundle_fixed(self):
        r = rule_from_raw({"name": "1+1随心配", "size": 2, "fixedPrice": 13.9,
                           "categories": ["side", "drink"]})
        self.assertIsNotNone(r)
        self.assertEqual(r.kind, "bundle_fixed")
        self.assertEqual(r.size, 2)
        self.assertEqual(r.fixed_cents, 1390)
        self.assertIn("side", r.categories)

    def test_unparseable_returns_none(self):
        self.assertIsNone(rule_from_raw({"name": "神秘活动"}))


class TestOrderSpec(unittest.TestCase):
    def test_rejects_bad_people(self):
        with self.assertRaises(ValueError):
            OrderSpec(people=0)

    def test_rejects_bad_objective(self):
        with self.assertRaises(ValueError):
            OrderSpec(objective="rich")


if __name__ == "__main__":
    unittest.main()
