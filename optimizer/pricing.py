"""优惠计算。

输入一组 PlanLine 和规则集合，输出实付金额与命中的规则说明。

两点设计取舍：
1. bundle 与 threshold 的叠加顺序会影响结果（先组 bundle 再满减 vs 反过来），
   这里两种顺序都算一遍，取更低的那个。规则数量通常是个位数，代价可忽略。
2. bundle 内部挑哪几件也有讲究：优先把池内单价最高的几件塞进 bundle，
   这样单笔抵扣最大。这是贪心，不保证全局最优，但对本场景足够。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .models import CouponRule, Item, PlanLine, Plan


@dataclass
class PriceResult:
    total_cents: int
    applied: list[str] = field(default_factory=list)
    # 参与 bundle 的行索引 -> 规则名，供渲染层标注
    bundled: dict[int, str] = field(default_factory=dict)


def _apply_bundles(lines: list[PlanLine], rules: Sequence[CouponRule]) -> tuple[list[PlanLine], PriceResult]:
    """把参与 bundle 的件数拆出来单独计价，剩余部分原样返回。"""
    remaining: list[PlanLine] = []
    bundled_total = 0
    applied: list[str] = []
    bundled: dict[int, str] = {}

    # 展开成「一件一行」，方便逐件挑选
    units: list[tuple[int, Item]] = []
    for idx, line in enumerate(lines):
        remaining.append(PlanLine(item=line.item, qty=line.qty))  # 先占位，稍后扣减
        for _ in range(line.qty):
            units.append((idx, line.item))

    used = [False] * len(units)

    # 按单件平均抵扣力度排序，力度大的先吃
    bundle_rules = [r for r in rules if r.kind in ("bundle_fixed", "bundle_percent")]
    def strength(r: CouponRule) -> float:
        if r.kind == "bundle_fixed":
            return float("inf") if r.size == 0 else 1.0
        return r.percent_off / 100.0

    for rule in sorted(bundle_rules, key=strength, reverse=True):
        while True:
            cand = [u for u in range(len(units)) if not used[u] and rule.in_pool(units[u][1])]
            if len(cand) < rule.size:
                break
            cand.sort(key=lambda u: units[u][1].price_cents, reverse=True)
            group = cand[: rule.size]
            prices = [units[u][1].price_cents for u in group]

            if rule.kind == "bundle_fixed":
                new_total = rule.fixed_cents
            else:
                new_total = int(round(sum(prices) * (100.0 - rule.percent_off) / 100.0))

            if new_total >= sum(prices):
                break  # 这套 bundle 不再省钱，停止

            for u in group:
                used[u] = True
                bundled.setdefault(units[u][0], rule.describe())
            bundled_total += new_total
            applied.append(f"{rule.describe()}（省 {(sum(prices)-new_total)/100:.2f}）")

    # 未被 bundle 消化的件数
    left_qty: dict[int, int] = {}
    for u, (idx, _) in enumerate(units):
        if not used[u]:
            left_qty[idx] = left_qty.get(idx, 0) + 1
    for idx in range(len(remaining)):
        remaining[idx].qty = left_qty.get(idx, 0)
    remaining = [l for l in remaining if l.qty > 0]

    total = bundled_total + sum(l.subtotal_cents for l in remaining)
    return remaining, PriceResult(total_cents=total, applied=applied, bundled=bundled)


def _apply_thresholds(subtotal_cents: int, rules: Sequence[CouponRule]) -> tuple[int, list[str]]:
    """满减。同一订单通常只享受一档，取抵扣最大的那档。"""
    best_cut = 0
    best_desc = ""
    for r in rules:
        if r.kind != "threshold":
            continue
        if subtotal_cents >= r.min_spend_cents and r.discount_cents > best_cut:
            best_cut = r.discount_cents
            best_desc = r.describe()
    applied = [f"{best_desc}（省 {best_cut/100:.2f}）"] if best_cut else []
    return max(0, subtotal_cents - best_cut), applied


def price_lines(lines: Sequence[PlanLine], rules: Sequence[CouponRule]) -> PriceResult:
    """计算实付。lines 是「商品 -> 数量」的聚合结果。"""
    base = list(PlanLine(item=l.item, qty=l.qty) for l in lines if l.qty > 0)
    raw_total = sum(l.subtotal_cents for l in base)

    # 顺序 A：先 bundle，剩余部分再满减
    rem_a, res_a = _apply_bundles(base, rules)
    total_a, applied_a = _apply_thresholds(res_a.total_cents, rules)

    # 顺序 B：先满减，再 bundle（少数平台按此算）
    total_b_pre, applied_b0 = _apply_thresholds(raw_total, rules)
    rem_b, res_b = _apply_bundles(base, rules)
    total_b = res_b.total_cents - (raw_total - total_b_pre)

    if total_a <= total_b:
        applied = res_a.applied + applied_a
        return PriceResult(total_cents=total_a, applied=applied, bundled=res_a.bundled)
    applied = applied_b0 + res_b.applied
    return PriceResult(total_cents=max(0, total_b), applied=applied, bundled=res_b.bundled)


def build_plan(lines: Sequence[PlanLine], rules: Sequence[CouponRule],
               top_up_note: str = "") -> Plan:
    subtotal = sum(l.subtotal_cents for l in lines)
    res = price_lines(lines, rules)
    return Plan(
        lines=[l for l in lines if l.qty > 0],
        subtotal_cents=subtotal,
        total_cents=res.total_cents,
        applied=res.applied,
        top_up_note=top_up_note,
    )
