"""领域模型与数据归一化。

麦当劳 MCP 返回的字段命名各家不一致，这里只做一件事：把任意形状的原始
JSON 收敛成可计算的 Item / CouponRule，并在收敛失败时保留原始载荷以便排查。

所有金额一律用整数「分」表示，避免浮点误差。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

CATEGORIES = ("main", "side", "drink", "dessert", "combo", "other")

# 类别判定的关键词表。命中即归类，未命中归为 other。
_CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "main": ("汉堡", "堡", "卷", "饭", "麦香", "巨无霸", "麦辣鸡腿", "burger", "wrap", "rice"),
    "side": ("薯条", "鸡块", "鸡翅", "玉米", "沙拉", "小食", "fries", "nugget", "wing", "snack"),
    "drink": ("可乐", "雪碧", "咖啡", "奶茶", "果汁", "饮品", "豆浆", "牛奶", "水",
              "coke", "coffee", "tea", "juice", "drink", "beverage"),
    "dessert": ("派", "冰淇淋", "甜筒", "麦旋风", "蛋糕", "甜品", "pie", "ice", "sundae", "dessert"),
    "combo": ("套餐", "超值", "随心配", "combo", "meal", "value"),
}


def guess_category(name: str, declared: str | None = None) -> str:
    """按声明优先、关键词兜底的方式推断类别。"""
    if declared:
        d = str(declared).strip().lower()
        for c in CATEGORIES:
            if d == c or d.startswith(c):
                return c
        alias = {
            "主食": "main", "汉堡": "main", "maindish": "main",
            "小食": "side", "配餐": "side", "小吃": "side",
            "饮料": "drink", "饮品": "drink", "beverage": "drink",
            "甜品": "dessert", "甜点": "dessert",
        }
        if d in alias:
            return alias[d]
    low = str(name).lower()
    for cat, hints in _CATEGORY_HINTS.items():
        if any(h in low for h in hints):
            return cat
    return "other"


def to_cents(value: Any) -> int:
    """把 12.5 / "12.50" / 1250(分) 之类的输入统一成整数分。

    约定：数值 <= 1000 且带小数点或非整数时视为元，否则视为分。
    显式传 {"cents": 1250} 或 {"yuan": 12.5} 时不走猜测。
    """
    if isinstance(value, Mapping):
        if "cents" in value:
            return int(round(float(value["cents"])))
        if "yuan" in value:
            return int(round(float(value["yuan"]) * 100))
        if "price" in value:
            return to_cents(value["price"])
        raise ValueError(f"无法解析价格对象: {value}")
    if isinstance(value, str):
        value = value.strip().replace("¥", "").replace("￥", "").replace("元", "")
        if not value:
            raise ValueError("空价格字符串")
        value = float(value)
    if isinstance(value, bool):  # bool 是 int 的子类，先排除
        raise ValueError(f"非法价格: {value}")
    if isinstance(value, (int, float)):
        f = float(value)
        if f < 0:
            raise ValueError(f"价格不能为负: {value}")
        # 整数且大于 1000：按分处理；否则按元处理（0.5 元 = 50 分）
        if float(value).is_integer() and f > 1000:
            return int(f)
        return int(round(f * 100))
    raise ValueError(f"无法解析价格: {value!r}")


@dataclass(frozen=True)
class Item:
    """一个可点单品。"""
    id: str
    name: str
    price_cents: int
    category: str = "other"
    tags: frozenset[str] = frozenset()
    available: bool = True
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def yuan(self) -> float:
        return self.price_cents / 100.0

    def has_any_tag(self, tags: Iterable[str]) -> bool:
        t = {str(x).lower() for x in tags}
        return bool(self.tags & t)


def _pick(d: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def item_from_raw(raw: Mapping[str, Any], index: int = 0) -> Item:
    """从一条原始菜单记录构造 Item。

    兼容常见字段命名：id/sku/itemId/productId、name/title/productName、
    price/priceCents/price_cents/sellPrice、category/type/tag 等。
    """
    if not isinstance(raw, Mapping):
        raise TypeError(f"菜单记录应为对象，实际为 {type(raw).__name__}")

    iid = _pick(raw, "id", "sku", "skuId", "itemId", "item_id", "productId", "code", default="")
    name = _pick(raw, "name", "title", "productName", "displayName", "itemName", default="")
    if not name:
        name = f"未命名商品#{index}"
    if not iid:
        iid = f"idx-{index}"

    price_raw = _pick(raw, "price", "priceCents", "price_cents", "sellPrice",
                      "salePrice", "amount", "cost")
    if price_raw is None:
        raise ValueError(f"商品 {name!r} 缺少价格字段: keys={sorted(raw.keys())}")

    declared = _pick(raw, "category", "type", "cat", "categoryName", "kind")
    tags_raw = _pick(raw, "tags", "labels", "allergens", "attrs", default=[])
    if isinstance(tags_raw, str):
        tags = frozenset(t.strip().lower() for t in tags_raw.replace("，", ",").split(",") if t.strip())
    elif isinstance(tags_raw, (list, tuple, set)):
        tags = frozenset(str(t).strip().lower() for t in tags_raw if str(t).strip())
    else:
        tags = frozenset()

    avail_raw = _pick(raw, "available", "inStock", "sellable", "status", default=True)
    if isinstance(avail_raw, bool):
        available = avail_raw
    elif isinstance(avail_raw, (int, float)):
        available = int(avail_raw) != 0
    else:
        s = str(avail_raw).lower()
        available = s not in ("sold_out", "soldout", "unavailable", "off", "0", "false", "停售", "售罄")

    return Item(
        id=str(iid),
        name=str(name),
        price_cents=to_cents(price_raw),
        category=guess_category(str(name), str(declared) if declared else None),
        tags=tags,
        available=available,
        raw=raw,
    )


@dataclass(frozen=True)
class CouponRule:
    """一条优惠规则。

    kind:
      - threshold     满 min_spend_cents 减 discount_cents
      - bundle_fixed  从 pool 中任选 size 件，合计 fixed_cents 元
      - bundle_percent 从 pool 中任选 size 件，整体打 (100-percent_off)/100
    pool 用「品类集合」或「商品 id 集合」表示，二者取其一或并存（并集）。
    """
    kind: str
    name: str = ""
    min_spend_cents: int = 0
    discount_cents: int = 0
    size: int = 0
    fixed_cents: int = 0
    percent_off: float = 0.0
    categories: frozenset[str] = frozenset()
    item_ids: frozenset[str] = frozenset()

    def in_pool(self, item: Item) -> bool:
        if self.categories and item.category in self.categories:
            return True
        if self.item_ids and item.id in self.item_ids:
            return True
        return False

    def describe(self) -> str:
        if self.kind == "threshold":
            return f"满 {self.min_spend_cents/100:.2f} 减 {self.discount_cents/100:.2f}"
        if self.kind == "bundle_fixed":
            scope = "/".join(sorted(self.categories)) or f"{len(self.item_ids)} 款"
            return f"{self.size} 件 {scope} 合计 {self.fixed_cents/100:.2f}"
        if self.kind == "bundle_percent":
            scope = "/".join(sorted(self.categories)) or f"{len(self.item_ids)} 款"
            return f"{self.size} 件 {scope} {self.percent_off:g}% off"
        return self.name or self.kind


def rule_from_raw(raw: Mapping[str, Any], index: int = 0) -> CouponRule:
    """从一条原始优惠记录构造 CouponRule。识别不了就返回 None（由调用方跳过）。"""
    if not isinstance(raw, Mapping):
        return None  # type: ignore[return-value]

    name = str(_pick(raw, "name", "title", "activityName", "ruleName", default=f"规则#{index}"))
    low = name.lower()
    kind_hint = str(_pick(raw, "kind", "type", "ruleType", "discountType", default="")).lower()

    cats_raw = _pick(raw, "categories", "category", "scope", "applyCategories", default=[])
    if isinstance(cats_raw, str):
        cats = frozenset(guess_category("", c.strip()) for c in cats_raw.replace("，", ",").split(",") if c.strip())
    elif isinstance(cats_raw, (list, tuple, set)):
        cats = frozenset(guess_category("", str(c)) for c in cats_raw if str(c).strip())
    else:
        cats = frozenset()

    ids_raw = _pick(raw, "itemIds", "item_ids", "skus", "products", "applicableItems", default=[])
    ids = frozenset(str(x) for x in ids_raw) if isinstance(ids_raw, (list, tuple, set)) else frozenset()

    # bundle：出现 size/count + fixed price / percent
    size = _pick(raw, "size", "count", "bundleSize", "quantity", "n", default=0)
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = 0

    fixed = _pick(raw, "fixedPrice", "fixed_price", "bundlePrice", "packagePrice", "groupPrice", "price")
    percent = _pick(raw, "percentOff", "percent_off", "discountPercent", "off", "rate")
    min_spend = _pick(raw, "minSpend", "min_spend", "threshold", "fullAmount", "condition")
    discount = _pick(raw, "discount", "discountAmount", "reduce", "cutAmount", "offAmount")

    is_bundle = (
        "bundle" in kind_hint or "bundle" in low or "随心配" in name or "1+1" in name
        or (size and (fixed is not None or percent is not None))
    )

    if is_bundle and size >= 2:
        if fixed is not None:
            return CouponRule(kind="bundle_fixed", name=name, size=size,
                              fixed_cents=to_cents(fixed), categories=cats, item_ids=ids)
        if percent is not None:
            try:
                p = float(percent)
            except (TypeError, ValueError):
                return None  # type: ignore[return-value]
            # 8 折可能写成 80 或 0.8 或 20(off)
            if p > 1:
                p = p if p <= 100 else p / 10.0
                p = 100.0 - p if p <= 100 else 0.0
            elif 0 < p < 1:
                p = p * 100.0
            return CouponRule(kind="bundle_percent", name=name, size=size,
                              percent_off=p, categories=cats, item_ids=ids)
        return None  # type: ignore[return-value]

    if min_spend is not None and discount is not None:
        return CouponRule(kind="threshold", name=name,
                          min_spend_cents=to_cents(min_spend),
                          discount_cents=to_cents(discount),
                          categories=cats, item_ids=ids)

    # 兜底：只有 threshold 语义的中文名（满30减5）
    if "满" in name and "减" in name and min_spend is None:
        return None  # 解析不出数字就不猜，宁可不用
    return None


@dataclass
class OrderSpec:
    """一次点餐的需求约束。"""
    people: int = 1
    budget_cents: int | None = None
    slots: tuple[str, ...] = ("main", "drink")   # 每人需要的类别
    avoid_tags: frozenset[str] = frozenset()
    prefer_tags: frozenset[str] = frozenset()
    max_same: int = 2                            # 同一单品最多几份
    objective: str = "cheapest"                  # cheapest | best
    allow_top_up: bool = True                    # 是否为了凑满减加一件

    def __post_init__(self) -> None:
        if self.people < 1:
            raise ValueError("人数至少为 1")
        if self.max_same < 1:
            raise ValueError("max_same 至少为 1")
        if self.objective not in ("cheapest", "best"):
            raise ValueError("objective 只能是 cheapest 或 best")
        if self.budget_cents is not None and self.budget_cents < 0:
            raise ValueError("预算不能为负")


@dataclass
class PlanLine:
    item: Item
    qty: int

    @property
    def subtotal_cents(self) -> int:
        return self.item.price_cents * self.qty


@dataclass
class Plan:
    lines: list[PlanLine]
    subtotal_cents: int
    total_cents: int
    applied: list[str]
    top_up_note: str = ""

    @property
    def saved_cents(self) -> int:
        return max(0, self.subtotal_cents - self.total_cents)

    @property
    def item_count(self) -> int:
        return sum(l.qty for l in self.lines)

    def per_person_cents(self, people: int) -> int:
        return self.total_cents // max(1, people)


def load_menu(path: str) -> tuple[list[Item], list[CouponRule]]:
    """从本地 JSON 缓存加载菜单与规则。文件格式见 scripts/fetch_menu.py。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = [item_from_raw(r, i) for i, r in enumerate(data.get("items", []))]
    rules: list[CouponRule] = []
    for i, r in enumerate(data.get("rules", [])):
        rule = rule_from_raw(r, i)
        if rule is not None:
            rules.append(rule)
    return items, rules


def dump_menu(items: Sequence[Item], rules: Sequence[CouponRule],
              meta: Mapping[str, Any] | None = None) -> str:
    payload = {
        "meta": dict(meta or {}),
        "items": [dict(i.raw) if i.raw else
                  {"id": i.id, "name": i.name, "price": i.price_cents / 100.0,
                   "category": i.category, "tags": sorted(i.tags)}
                  for i in items],
        "rules": [r.__dict__ | {"categories": sorted(r.categories), "item_ids": sorted(r.item_ids)}
                  for r in rules],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
