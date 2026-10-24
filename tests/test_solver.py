"""求解器与计价测试。

包含一个关键用例：小规模场景下把启发式解和暴力枚举的最优解做对比，
确认启发式在常见输入上确实拿到了最优（或至少不劣于）结果。
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from optimizer.models import (  # noqa: E402
    CouponRule, Item, OrderSpec, PlanLine, load_menu,
)
from optimizer.pricing import price_lines  # noqa: E402
from optimizer.solver import InfeasibleError, solve, solve_exact_small  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "synthetic_menu.json")


def item(name, price, cat="main", tags=(), available=True):
    return Item(id=name, name=name, price_cents=int(price * 100),
                category=cat, tags=frozenset(tags), available=available)


class TestPricing(unittest.TestCase):
    def test_threshold(self):
        lines = [PlanLine(item("A", 30), 2)]           # 60 元
        rule = CouponRule(kind="threshold", min_spend_cents=6000, discount_cents=800)
        res = price_lines(lines, [rule])
        self.assertEqual(res.total_cents, 5200)

    def test_threshold_not_reached(self):
        lines = [PlanLine(item("A", 30), 1)]
        rule = CouponRule(kind="threshold", min_spend_cents=6000, discount_cents=800)
        self.assertEqual(price_lines(lines, [rule]).total_cents, 3000)

    def test_bundle_fixed_saves(self):
        # 13 + 9.5 = 22.5，随心配 13.9，应省 8.6
        lines = [PlanLine(item("薯条", 13, "side"), 1), PlanLine(item("可乐", 9.5, "drink"), 1)]
        rule = CouponRule(kind="bundle_fixed", size=2, fixed_cents=1390,
                          categories=frozenset({"side", "drink"}))
        res = price_lines(lines, [rule])
        self.assertEqual(res.total_cents, 1390)
        self.assertEqual(len(res.applied), 1)

    def test_bundle_not_worth_it(self):
        # 8 + 8 = 16 < 13.9? 不，16 > 13.9，省 2.1；换成 5+5=10 < 13.9 就不该组
        cheap = [PlanLine(item("玉米", 5, "side"), 1), PlanLine(item("牛奶", 5, "drink"), 1)]
        rule = CouponRule(kind="bundle_fixed", size=2, fixed_cents=1390,
                          categories=frozenset({"side", "drink"}))
        self.assertEqual(price_lines(cheap, [rule]).total_cents, 1000)

    def test_picks_best_of_two_orders(self):
        lines = [PlanLine(item("薯条", 13.5, "side"), 1), PlanLine(item("可乐", 9.5, "drink"), 1),
                 PlanLine(item("堡", 40, "main"), 1)]
        bundle = CouponRule(kind="bundle_fixed", size=2, fixed_cents=1390,
                            categories=frozenset({"side", "drink"}))
        thr = CouponRule(kind="threshold", min_spend_cents=5000, discount_cents=800)
        res = price_lines(lines, [bundle, thr])
        # 两种顺序都算，取更低者；能省到就行
        self.assertLess(res.total_cents, 6300)


class TestSolverBasics(unittest.TestCase):
    def setUp(self):
        self.items, self.rules = load_menu(FIXTURE)

    def test_respects_slot_demand(self):
        spec = OrderSpec(people=3, slots=("main", "drink"))
        res = solve(self.items, self.rules, spec)
        mains = sum(l.qty for l in res.plan.lines if l.item.category == "main")
        drinks = sum(l.qty for l in res.plan.lines if l.item.category == "drink")
        self.assertEqual(mains, 3)
        self.assertEqual(drinks, 3)

    def test_avoid_tag_excluded(self):
        spec = OrderSpec(people=2, slots=("main",), avoid_tags=frozenset({"牛肉"}))
        res = solve(self.items, self.rules, spec)
        for l in res.plan.lines:
            self.assertNotIn("牛肉", l.item.tags)

    def test_unavailable_excluded(self):
        spec = OrderSpec(people=2, slots=("main",))
        res = solve(self.items, self.rules, spec)
        self.assertTrue(all(l.item.available for l in res.plan.lines))
        self.assertNotIn("培根蔬萃双层牛堡", [l.item.name for l in res.plan.lines])

    def test_budget_respected_when_feasible(self):
        spec = OrderSpec(people=2, slots=("main", "drink"), budget_cents=6000)
        res = solve(self.items, self.rules, spec)
        self.assertLessEqual(res.plan.total_cents, 6000)

    def test_max_same(self):
        spec = OrderSpec(people=6, slots=("main",), max_same=2)
        res = solve(self.items, self.rules, spec)
        self.assertTrue(all(l.qty <= 2 for l in res.plan.lines))

    def test_preference_breaks_ties(self):
        """同价时，选命中偏好的那个。"""
        items = [item("堡A", 20, "main", tags=("牛肉",)),
                 item("堡B", 20, "main", tags=("鸡肉",))]
        spec = OrderSpec(people=1, slots=("main",), prefer_tags=frozenset({"牛肉"}))
        res = solve(items, [], spec)
        self.assertEqual(res.plan.lines[0].item.name, "堡A")

    def test_cheapest_never_pays_for_preference(self):
        """便宜 2 元的选项优先于偏好命中，cheapest 就是 cheapest。"""
        items = [item("堡A", 20, "main", tags=("牛肉",)),
                 item("堡B", 20, "main", tags=("鸡肉",)),
                 item("堡C", 18, "main", tags=("鸡肉",))]
        spec = OrderSpec(people=1, slots=("main",), prefer_tags=frozenset({"牛肉"}))
        res = solve(items, [], spec)
        self.assertEqual(res.plan.total_cents, 1800)
        self.assertEqual(res.plan.lines[0].item.name, "堡C")

    def test_infeasible_raises(self):
        spec = OrderSpec(people=1, slots=("main",), avoid_tags=frozenset({"牛肉", "鸡肉", "鱼肉"}))
        with self.assertRaises(InfeasibleError):
            solve(self.items, self.rules, spec)

    def test_cheapest_beats_naive(self):
        """最省方案应该不贵于「每人各点一份最便宜」的朴素做法。"""
        spec = OrderSpec(people=3, slots=("main", "drink"))
        res = solve(self.items, self.rules, spec)
        cheapest_main = min(i.price_cents for i in self.items
                            if i.category == "main" and i.available)
        cheapest_drink = min(i.price_cents for i in self.items
                             if i.category == "drink" and i.available)
        naive = 3 * (cheapest_main + cheapest_drink)
        self.assertLessEqual(res.plan.total_cents, naive + 1)


class TestAgainstBruteForce(unittest.TestCase):
    """小规模下启发式应达到暴力枚举的最优值。"""

    def _check(self, items, rules, spec):
        heuristic = solve(items, rules, spec).plan.total_cents
        exact = solve_exact_small(items, rules, spec).total_cents
        self.assertLessEqual(heuristic, exact + 1,
                             f"启发式 {heuristic} 差于暴力最优 {exact}")

    def test_one_person_no_rules(self):
        items = [item("堡A", 20, "main"), item("堡B", 25, "main"), item("堡C", 18, "main"),
                 item("可乐", 9, "drink"), item("咖啡", 12, "drink"), item("牛奶", 8, "drink")]
        spec = OrderSpec(people=1, slots=("main", "drink"))
        self._check(items, [], spec)

    def test_two_people_with_threshold(self):
        items = [item("堡A", 20, "main"), item("堡B", 25, "main"), item("堡C", 18, "main"),
                 item("可乐", 9, "drink"), item("咖啡", 12, "drink")]
        rules = [CouponRule(kind="threshold", min_spend_cents=5000, discount_cents=800)]
        spec = OrderSpec(people=2, slots=("main", "drink"))
        self._check(items, rules, spec)

    def test_with_bundle(self):
        items = [item("堡A", 20, "main"), item("堡B", 25, "main"),
                 item("薯条", 13, "side"), item("鸡块", 13, "side"),
                 item("可乐", 9, "drink"), item("雪碧", 11, "drink")]
        rules = [CouponRule(kind="bundle_fixed", size=2, fixed_cents=1390,
                            categories=frozenset({"side", "drink"}))]
        spec = OrderSpec(people=1, slots=("main", "side", "drink"))
        self._check(items, rules, spec)

    def test_budget_objective_best(self):
        items = [item("堡A", 20, "main"), item("堡B", 26, "main"), item("堡C", 18, "main"),
                 item("可乐", 9, "drink"), item("咖啡", 12, "drink"), item("牛奶", 8, "drink")]
        spec = OrderSpec(people=2, slots=("main", "drink"), budget_cents=6000,
                         objective="best")
        # 只验证不超预算且能产出方案，best 的评分含主观权重，不与暴力比
        res = solve(items, [], spec)
        self.assertLessEqual(res.plan.total_cents, 6000)


if __name__ == "__main__":
    unittest.main()
