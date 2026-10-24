"""命令行入口。

两种数据来源：
  --menu <file>   读本地 JSON 缓存（离线可用，测试与演示走这条）
  --from-mcp      连麦当劳 MCP 实时拉取（需要配置 MCD_MCP_COMMAND 或 MCD_MCP_URL）

示例：
  python3 -m optimizer.cli --menu tests/fixtures/synthetic_menu.json \
      --people 3 --budget 90 --slots main,drink --avoid 花生 --prefer 牛肉
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Sequence

from .models import CouponRule, Item, OrderSpec, Plan, PlanLine, item_from_raw, rule_from_raw
from .solver import InfeasibleError, solve
from .mcp_client import client_from_env


def parse_yuan(s: str) -> int:
    return int(round(float(s) * 100))


def parse_slots(s: str) -> tuple[str, ...]:
    parts = [p.strip() for p in s.replace("，", ",").split(",") if p.strip()]
    if not parts:
        raise argparse.ArgumentTypeError("slots 不能为空")
    return tuple(parts)


def parse_tags(s: str | None) -> frozenset[str]:
    if not s:
        return frozenset()
    return frozenset(t.strip().lower() for t in s.replace("，", ",").split(",") if t.strip())


def collect_from_mcp(store: str | None = None, verbose: bool = False) -> tuple[list[Item], list[CouponRule], list[str]]:
    """通过 MCP 拉取菜单与优惠。返回 (商品, 规则, 过程说明)。"""
    notes: list[str] = []
    client = client_from_env()
    with client:
        bindings = client.bind_capabilities()
        notes.append(client.describe(bindings))
        missing = client.missing_required(bindings)
        if missing:
            raise InfeasibleError(
                "MCP 未提供下列必需能力，无法继续：" + "；".join(missing)
                + "。可用 --menu 改读本地缓存。"
            )

        def args_for(cap_key: str) -> dict:
            if not store:
                return {}
            binding = bindings[cap_key]
            schema = binding.input_schema or {}
            props = schema.get("properties", {}) if isinstance(schema, dict) else {}
            for hint in ("store", "store_id", "storeId", "shop", "shopId", "poi", "门店"):
                if hint in props:
                    return {hint: store}
            return {"store": store} if not props else {}

        raw_menu = client.call_tool(bindings["menu"].tool_name, args_for("menu"))
        raw_coupon = (client.call_tool(bindings["coupon"].tool_name, args_for("coupon"))
                      if "coupon" in bindings else None)

    items = _coerce_items(raw_menu)
    rules = _coerce_rules(raw_coupon)
    notes.append(f"拉取到 {len(items)} 个商品、{len(rules)} 条可用优惠规则")
    return items, rules, notes


def _coerce_items(payload) -> list[Item]:
    """MCP 返回可能是 list / {"items":[...]} / {"data":{...}} / 纯文本，逐个尝试。"""
    rows = _extract_rows(payload, keys=("items", "data", "list", "products", "menu", "result"))
    out: list[Item] = []
    for i, r in enumerate(rows):
        try:
            out.append(item_from_raw(r, i))
        except (ValueError, TypeError):
            continue  # 缺价格等字段的记录直接跳过，不阻塞整单
    return out


def _coerce_rules(payload) -> list[CouponRule]:
    if payload is None:
        return []
    rows = _extract_rows(payload, keys=("rules", "items", "data", "list", "coupons",
                                        "promotions", "activities", "result"))
    out: list[CouponRule] = []
    for i, r in enumerate(rows):
        rule = rule_from_raw(r, i)
        if rule is not None:
            out.append(rule)
    return out


def _extract_rows(payload, keys: Sequence[str]) -> list:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in keys:
            v = payload.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                inner = _extract_rows(v, keys)
                if inner:
                    return inner
        # 只有单条记录的情况
        if "name" in payload or "price" in payload:
            return [payload]
    if isinstance(payload, str):
        try:
            return _extract_rows(json.loads(payload), keys)
        except json.JSONDecodeError:
            return []
    return []


def render_markdown(plan: Plan, spec: OrderSpec, warnings: Sequence[str]) -> str:
    lines: list[str] = []
    lines.append("### 点餐方案")
    lines.append("")
    lines.append("| 商品 | 单价 | 份数 | 小计 |")
    lines.append("| --- | ---: | ---: | ---: |")
    for l in sorted(plan.lines, key=lambda x: (-x.qty, x.item.price_cents)):
        lines.append(f"| {l.item.name} | {l.item.yuan:.2f} | {l.qty} | {l.subtotal_cents/100:.2f} |")
    lines.append("")
    lines.append(f"- 原价合计：**{plan.subtotal_cents/100:.2f} 元**")
    lines.append(f"- 实付：**{plan.total_cents/100:.2f} 元**（共 {plan.item_count} 件，"
                 f"人均 {plan.per_person_cents(spec.people)/100:.2f} 元）")
    if plan.saved_cents:
        lines.append(f"- 省下：**{plan.saved_cents/100:.2f} 元**")
    if plan.applied:
        lines.append("- 命中优惠：" + "；".join(plan.applied))
    else:
        lines.append("- 命中优惠：无（当前订单未触发任何规则）")
    if spec.budget_cents:
        left = spec.budget_cents - plan.total_cents
        tail = f"剩余 {left/100:.2f} 元" if left >= 0 else f"**超支 {(-left)/100:.2f} 元**"
        lines.append(f"- 预算 {spec.budget_cents/100:.2f} 元：{tail}")
    if plan.top_up_note:
        lines.append(f"- 凑单提示：{plan.top_up_note}")
    for w in warnings:
        lines.append(f"- 提示：{w}")
    return "\n".join(lines)


def plan_to_dict(plan: Plan, spec: OrderSpec, warnings: Sequence[str]) -> dict:
    return {
        "people": spec.people,
        "budget": spec.budget_cents / 100.0 if spec.budget_cents else None,
        "objective": spec.objective,
        "items": [{"id": l.item.id, "name": l.item.name, "price": l.item.yuan,
                   "qty": l.qty, "category": l.item.category} for l in plan.lines],
        "subtotal": plan.subtotal_cents / 100.0,
        "total": plan.total_cents / 100.0,
        "saved": plan.saved_cents / 100.0,
        "per_person": plan.per_person_cents(spec.people) / 100.0,
        "applied_rules": plan.applied,
        "top_up_note": plan.top_up_note,
        "warnings": list(warnings),
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mcd-budget-optimizer",
        description="在人数 / 预算 / 忌口约束下，算出最省或最合适的麦当劳搭配。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--menu", help="本地菜单 JSON 文件路径")
    src.add_argument("--from-mcp", action="store_true", help="从麦当劳 MCP 实时拉取")

    p.add_argument("--people", type=int, default=1, help="人数（默认 1）")
    p.add_argument("--budget", type=parse_yuan, default=None, help="预算上限，单位元，如 90")
    p.add_argument("--slots", type=parse_slots, default="main,drink",
                   help="每人需要的类别，逗号分隔（默认 main,drink）")
    p.add_argument("--avoid", type=parse_tags, default=frozenset(), help="忌口/过敏标签，逗号分隔")
    p.add_argument("--prefer", type=parse_tags, default=frozenset(), help="偏好标签，逗号分隔")
    p.add_argument("--max-same", type=int, default=2, help="同一单品最多几份（默认 2）")
    p.add_argument("--objective", choices=("cheapest", "best"), default="cheapest",
                   help="cheapest=最省钱；best=预算内吃最好（默认 cheapest）")
    p.add_argument("--no-top-up", action="store_true", help="关闭凑满减")
    p.add_argument("--store", default=None, help="门店 ID，传给 MCP")
    p.add_argument("--json", action="store_true", help="输出 JSON 而非 Markdown")
    p.add_argument("--verbose", action="store_true", help="打印 MCP 能力映射过程")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        spec = OrderSpec(
            people=args.people,
            budget_cents=args.budget,
            slots=args.slots,
            avoid_tags=args.avoid,
            prefer_tags=args.prefer,
            max_same=args.max_same,
            objective=args.objective,
            allow_top_up=not args.no_top_up,
        )
    except ValueError as e:
        print(f"参数错误: {e}", file=sys.stderr)
        return 2

    warnings: list[str] = []
    try:
        if args.from_mcp:
            from .models import load_menu
            items, rules, notes = collect_from_mcp(args.store, args.verbose)
            if args.verbose:
                for n in notes:
                    print(n, file=sys.stderr)
        else:
            items, rules = _load_local(args.menu)
    except (InfeasibleError, FileNotFoundError) as e:
        print(f"无法获取菜单: {e}", file=sys.stderr)
        return 3
    except Exception as e:  # MCP 连接失败等
        print(f"MCP 调用失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 3

    if not items:
        print("菜单为空，无法计算。", file=sys.stderr)
        return 3

    try:
        result = solve(items, rules, spec)
    except InfeasibleError as e:
        print(f"无解: {e}", file=sys.stderr)
        return 4

    if args.json:
        print(json.dumps(plan_to_dict(result.plan, spec, result.warnings),
                         ensure_ascii=False, indent=2))
    else:
        print(render_markdown(result.plan, spec, result.warnings))
    return 0


def _load_local(path: str):
    from .models import load_menu
    return load_menu(path)


if __name__ == "__main__":
    raise SystemExit(main())
