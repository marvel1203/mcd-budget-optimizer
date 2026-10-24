"""组合求解：在满足人数 / 忌口 / 预算约束下，给出一套点餐搭配。

求解思路（启发式，非全局最优）：

  1. 过滤：剔除不可售、命中忌口标签的商品；
  2. 需求建模：spec.slots 里每个元素是一个「槽」，每槽一人一份；
     例如 slots=("main","drink")、people=3 => 需要 3 份主食 + 3 份饮品；
  3. 初始解：按槽逐个贪心选取（cheapest 取最便宜，best 取评分最高）；
  4. 局部搜索：反复尝试把某个槽换成同类别的另一个候选，接受使目标变好的替换，
     直到一轮内没有任何改进；
  5. 凑单：若启用 allow_top_up，检查「差一点就能满减」的情形，
     加一件最便宜的商品，只要加完实付反而更低就加上。

第 4 步用的是最朴素的 1-swap 邻域，规模是 槽数 × 同类候选数，
在麦当劳这种「十几个槽、每类几十个候选」的量级上是毫秒级的。
tests/test_solver.py 里对小规模用例做了「启发式 vs 暴力枚举」的一致性验证。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Iterable, Sequence

from .models import CouponRule, Item, OrderSpec, Plan, PlanLine
from .pricing import build_plan


class InfeasibleError(Exception):
    """约束无解（通常是某类商品的可选数量不够）。"""


@dataclass
class SolveResult:
    plan: Plan
    warnings: list[str] = field(default_factory=list)


def _allowed(item: Item, spec: OrderSpec) -> bool:
    if not item.available:
        return False
    if spec.avoid_tags and item.has_any_tag(spec.avoid_tags):
        return False
    return True


def _preference_score(item: Item, spec: OrderSpec) -> int:
    if not spec.prefer_tags:
        return 0
    return 8 * len(item.tags & spec.prefer_tags)


def _build_slot_list(spec: OrderSpec) -> list[str]:
    slots: list[str] = []
    for _ in range(spec.people):
        slots.extend(spec.slots)
    return slots


def _pool_by_category(candidates: Sequence[Item]) -> dict[str, list[Item]]:
    pool: dict[str, list[Item]] = {}
    for it in candidates:
        pool.setdefault(it.category, []).append(it)
    for v in pool.values():
        v.sort(key=lambda i: (i.price_cents, i.name))
    return pool


def _aggregate(assign: Sequence[Item | None]) -> dict[str, int]:
    qty: dict[str, int] = {}
    for it in assign:
        if it is not None:
            qty[it.id] = qty.get(it.id, 0) + 1
    return qty


def _lines(assign: Sequence[Item | None]) -> list[PlanLine]:
    qty = _aggregate(assign)
    by_id = {it.id: it for it in assign if it is not None}
    return [PlanLine(item=by_id[i], qty=n) for i, n in sorted(qty.items())]


def _evaluate(assign: Sequence[Item | None], spec: OrderSpec,
              rules: Sequence[CouponRule]) -> tuple[tuple[float, ...], Plan]:
    """目标值，返回可比较的元组（越大越好）。

    元组是分层比较的：先比主目标，主目标完全一致时才看后面的 tie-break。
    这样 cheapest 模式下「省钱」永远是第一优先级，口味偏好只在两个方案
    实付金额一模一样时才起作用 —— 不会因为你想吃牛肉就多花一块钱。

      cheapest: (-实付, 偏好命中, 多样性)
      best    : (满意度分, -实付)
    """
    plan = build_plan(_lines(assign), rules)
    over = 0
    if spec.budget_cents is not None and plan.total_cents > spec.budget_cents:
        over = plan.total_cents - spec.budget_cents

    pref = sum(_preference_score(it, spec) for it in assign if it is not None)
    distinct = len({it.id for it in assign if it is not None})
    if over:
        return (-1e9 - over, float(pref), float(distinct)), plan

    if spec.objective == "cheapest":
        return (-float(plan.total_cents), float(pref), float(distinct)), plan

    score = float(pref)
    score += 4.0 * distinct
    score += 2.0 * sum(1 for it in assign if it is not None)
    if spec.budget_cents:
        score += 10.0 * (plan.total_cents / spec.budget_cents)
    return (score, -float(plan.total_cents)), plan


def _initial(slots: Sequence[str], pool: dict[str, list[Item]],
             spec: OrderSpec, max_same: int) -> tuple[list[Item | None], list[str]]:
    warnings: list[str] = []
    assign: list[Item | None] = [None] * len(slots)
    counts: dict[str, int] = {}

    # 先给候选稀缺的类别排位，避免最后无货可挑
    order = sorted(range(len(slots)),
                   key=lambda j: len(pool.get(slots[j], [])))

    for j in order:
        cat = slots[j]
        cands = pool.get(cat, [])
        best: Item | None = None
        for it in cands:
            if counts.get(it.id, 0) >= max_same:
                continue
            if best is None:
                best = it
                continue
            if spec.objective == "cheapest":
                if it.price_cents < best.price_cents:
                    best = it
            else:
                if _preference_score(it, spec) > _preference_score(best, spec):
                    best = it
        if best is None:
            # 放宽 max_same 再试一次
            for it in cands:
                if counts.get(it.id, 0) >= max_same + 64:
                    continue
                best = it
                break
            if best is not None and cat in pool:
                warnings.append(f"类别「{cat}」可选商品不足，已放宽同款上限以凑齐份数")
        assign[j] = best
        if best is not None:
            counts[best.id] = counts.get(best.id, 0) + 1

    if any(a is None for a in assign):
        missing = sorted({slots[j] for j, a in enumerate(assign) if a is None})
        raise InfeasibleError(
            f"以下类别没有可用商品：{missing}。"
            f"可以放宽忌口标签、调整 slots，或换一家门店再试。"
        )
    return assign, warnings


def _local_search(assign: list[Item | None], slots: Sequence[str], pool: dict[str, list[Item]],
                  spec: OrderSpec, rules: Sequence[CouponRule],
                  max_same: int, max_rounds: int = 6) -> tuple[list[Item | None], Plan]:
    best_score, best_plan = _evaluate(assign, spec, rules)
    for _ in range(max_rounds):
        improved = False
        for j in range(len(slots)):
            cur = assign[j]
            if cur is None:
                continue
            for cand in pool.get(slots[j], []):
                if cand.id == cur.id:
                    continue
                if _aggregate(assign).get(cand.id, 0) >= max_same:
                    continue
                assign[j] = cand
                s, p = _evaluate(assign, spec, rules)
                if s > best_score:
                    best_score, best_plan, improved = s, p, True
                    cur = cand
                else:
                    assign[j] = cur
        if not improved:
            break
    return assign, best_plan


def _top_up(plan: Plan, candidates: Sequence[Item], spec: OrderSpec,
            rules: Sequence[CouponRule]) -> tuple[Plan, str]:
    """凑满减：只加不减。加完之后实付更低才加，绝不为了凑单多花钱。"""
    if not spec.allow_top_up:
        return plan, ""
    thresholds = [r for r in rules if r.kind == "threshold"]
    if not thresholds:
        return plan, ""

    have = {l.item.id for l in plan.lines}
    best_gain = 0
    best_line: PlanLine | None = None
    best_rule = ""

    for r in thresholds:
        gap = r.min_spend_cents - plan.total_cents
        if gap <= 0 or r.discount_cents <= gap:
            continue  # 差得太多，凑单反而亏
        for it in candidates:
            if it.id in have or not _allowed(it, spec):
                continue
            new_lines = list(plan.lines) + [PlanLine(item=it, qty=1)]
            candidate_plan = build_plan(new_lines, rules)
            if spec.budget_cents is not None and candidate_plan.total_cents > spec.budget_cents:
                continue
            gain = plan.total_cents - candidate_plan.total_cents
            if gain > best_gain:
                best_gain, best_line, best_rule = gain, PlanLine(item=it, qty=1), r.describe()

    if best_line is None:
        return plan, ""
    new_plan = build_plan(list(plan.lines) + [best_line], rules)
    note = (f"加了一份「{best_line.item.name}」({best_line.item.yuan:.2f}) 以凑上{best_rule}，"
            f"实付反而少 {best_gain/100:.2f} 元")
    return new_plan, note


def solve(items: Sequence[Item], rules: Sequence[CouponRule], spec: OrderSpec) -> SolveResult:
    candidates = [it for it in items if _allowed(it, spec)]
    if not candidates:
        raise InfeasibleError("过滤后没有任何可选商品，请检查门店菜单或放宽忌口条件。")

    pool = _pool_by_category(candidates)
    # 类别不足时用 other 兜底，避免直接崩
    for cat in set(spec.slots):
        if cat not in pool:
            pool[cat] = list(pool.get("other", []))

    slots = _build_slot_list(spec)
    assign, warnings = _initial(slots, pool, spec, spec.max_same)
    assign, plan = _local_search(assign, slots, pool, spec, rules, spec.max_same)

    plan, note = _top_up(plan, candidates, spec, rules)
    if note:
        warnings.append(note)

    # cheapest 模式下若仍超预算，明确告诉调用方
    if spec.budget_cents is not None and plan.total_cents > spec.budget_cents:
        warnings.append(
            f"当前搭配实付 {plan.total_cents/100:.2f} 元，超出预算 "
            f"{spec.budget_cents/100:.2f} 元 —— 已给出预算内可选的最省方案仍不够，"
            f"建议减少人数份数或降低 slots 要求。"
        )
    return SolveResult(plan=plan, warnings=warnings)


# --------------------------------------------------------------------------
# 精确解：仅用于小规模验证，别在生产路径上调用
# --------------------------------------------------------------------------

def solve_exact_small(items: Sequence[Item], rules: Sequence[CouponRule],
                      spec: OrderSpec, limit: int = 200_000) -> Plan:
    """暴力枚举所有满足约束的组合取最优。组合数超过 limit 直接放弃。"""
    candidates = [it for it in items if _allowed(it, spec)]
    slots = _build_slot_list(spec)

    per_cat: dict[str, list[Item]] = {}
    for it in candidates:
        per_cat.setdefault(it.category, []).append(it)

    groups: list[list[Item]] = []
    for cat in slots:
        cands = per_cat.get(cat) or per_cat.get("other", [])
        if not cands:
            raise InfeasibleError(f"类别 {cat} 无候选")
        groups.append(cands)

    total = 1
    for g in groups:
        total *= len(g)
        if total > limit:
            raise ValueError(f"组合数 {total} 超过上限 {limit}，无法暴力枚举")

    best: Plan | None = None
    best_score: tuple[float, ...] = (float("-inf"),)
    for combo in product(*groups):
        counts: dict[str, int] = {}
        for it in combo:
            counts[it.id] = counts.get(it.id, 0) + 1
        if any(c > spec.max_same for c in counts.values()):
            continue
        score, plan = _evaluate(list(combo), spec, rules)
        if score > best_score:
            best_score, best = score, plan
    if best is None:
        raise InfeasibleError("无任何可行组合")
    return best
