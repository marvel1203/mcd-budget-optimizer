"""营养信息接入（可选能力）。

麦当劳 MCP 的 `list-nutrition-foods` 能给出餐品的能量、蛋白质、脂肪等数据。
本模块把它归一化成 NutritionTable，再按餐品名模糊匹配到菜单项上，
让点餐方案除了「多少钱」还能回答「多少卡」。

两个现实约束：

1. 营养数据的餐品名和菜单里的餐品名经常对不齐（「麦香鱼」vs「麦香鱼堡」），
   所以匹配走「归一化后精确匹配 -> 双向包含匹配」，匹配不上就返回 None，
   宁可留空也不填一个相近但错误的值。
2. 这个能力是可选的。MCP 没提供、或者用户没要求，就不显示营养列，
   不影响主流程。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

# 各字段的候选键名。单位的坑在这里处理：带 kj/千焦 的能量值换算成大卡。
FIELD_KEYS: dict[str, tuple[str, ...]] = {
    "kcal": ("kcal", "energy", "energy_kcal", "calorie", "calories", "kilocalorie",
             "能量", "热量", "千卡", "大卡", "卡路里"),
    "protein_g": ("protein", "protein_g", "蛋白质"),
    "fat_g": ("fat", "fat_g", "脂肪"),
    "carb_g": ("carb", "carbohydrate", "carb_g", "carbs", "碳水化合物", "碳水"),
    "sodium_mg": ("sodium", "sodium_mg", "钠"),
    "calcium_mg": ("calcium", "calcium_mg", "钙"),
}

_ENERGY_KJ_KEYS = ("kj", "kilojoule", "千焦")

_PUNCT = re.compile(r"[\s（）()【】\[\]·・、,，\-—_/]+")


def _norm(name: str) -> str:
    """名称归一化：去空白、括号内容和标点，转小写。"""
    s = str(name).strip().lower()
    s = _PUNCT.sub("", s)
    return s


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    return float(m.group())


@dataclass(frozen=True)
class Nutrition:
    name: str
    kcal: float | None = None
    protein_g: float | None = None
    fat_g: float | None = None
    carb_g: float | None = None
    sodium_mg: float | None = None
    calcium_mg: float | None = None

    def is_empty(self) -> bool:
        return all(v is None for v in (self.kcal, self.protein_g, self.fat_g,
                                       self.carb_g, self.sodium_mg, self.calcium_mg))


class NutritionTable:
    """餐品名 -> 营养信息 的查询表。"""

    def __init__(self, entries: Iterable[Nutrition]):
        self._by_name: dict[str, Nutrition] = {}
        for e in entries:
            self._by_name[_norm(e.name)] = e

    def __len__(self) -> int:
        return len(self._by_name)

    def lookup(self, item_name: str) -> Nutrition | None:
        key = _norm(item_name)
        if not key:
            return None
        hit = self._by_name.get(key)
        if hit is not None:
            return hit
        # 双向包含匹配。短名至少 2 个字，避免「堡」这种单字误伤一片。
        if len(key) >= 2:
            for k, v in self._by_name.items():
                if len(k) >= 2 and (key in k or k in key):
                    return v
        return None

    def total_for(self, lines: Sequence[Any]) -> dict[str, float | None]:
        """汇总一份搭配的总营养。lines 是有 .item 和 .qty 的 PlanLine。"""
        acc: dict[str, float] = {}
        seen: dict[str, int] = {}
        for l in lines:
            n = self.lookup(l.item.name)
            if n is None:
                seen[l.item.name] = seen.get(l.item.name, 0) + 0
                continue
            for f in ("kcal", "protein_g", "fat_g", "carb_g", "sodium_mg", "calcium_mg"):
                v = getattr(n, f)
                if v is None:
                    continue
                acc[f] = acc.get(f, 0.0) + v * l.qty
        if not acc:
            return {}
        return {k: round(v, 1) for k, v in acc.items()}


def nutrition_from_raw(raw: Mapping[str, Any]) -> Nutrition | None:
    """从一条营养记录构造 Nutrition。取不到任何字段就返回 None。"""
    if not isinstance(raw, Mapping):
        return None
    name = ""
    for k in ("name", "foodName", "productName", "title", "餐品", "名称"):
        v = raw.get(k)
        if v:
            name = str(v)
            break
    if not name:
        return None

    values: dict[str, float | None] = {}
    for field, keys in FIELD_KEYS.items():
        found: float | None = None
        for k, v in raw.items():
            kl = str(k).lower()
            if any(str(key).lower() == kl for key in keys):
                found = _to_float(v)
                # 能量值可能以千焦为单位
                if field == "kcal" and found is not None:
                    if any(u in kl for u in _ENERGY_KJ_KEYS):
                        found = found / 4.184
                break
        if found is None:
            # 键名没精确命中时，退一步做包含匹配
            for k, v in raw.items():
                kl = str(k).lower()
                if any(str(key).lower() in kl for key in keys):
                    found = _to_float(v)
                    if field == "kcal" and found is not None:
                        if any(u in kl for u in _ENERGY_KJ_KEYS):
                            found = found / 4.184
                    break
        values[field] = found

    n = Nutrition(name=name, **values)
    return None if n.is_empty() else n


def table_from_payload(payload: Any) -> NutritionTable:
    """从 MCP 返回构造查询表。payload 可能是 list / {"foods":[...]} / 文本。"""
    rows = _extract(payload)
    entries = []
    for r in rows:
        n = nutrition_from_raw(r)
        if n is not None:
            entries.append(n)
    return NutritionTable(entries)


def _extract(payload: Any) -> list:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        # nutrition / foods 要排在 items 前面：完整菜单快照里也有 items，
        # 顺序反了会把菜单当成营养数据。
        for k in ("foods", "nutrition", "nutritions", "foodList",
                  "items", "data", "list", "result"):
            v = payload.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                inner = _extract(v)
                if inner:
                    return inner
        if "name" in payload:
            return [payload]
    if isinstance(payload, str):
        try:
            return _extract(json.loads(payload))
        except json.JSONDecodeError:
            return []
    return []


def load_nutrition_file(path: str) -> NutritionTable:
    with open(path, "r", encoding="utf-8") as f:
        return table_from_payload(json.load(f))
