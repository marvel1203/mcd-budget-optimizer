"""本地 mock MCP server，仅用于测试与演示。

工具名故意起得"不像麦当劳"（mcd_store_catalog_v2 / query_promotions / ...），
用来验证 mcp_client 的语义映射确实在工作，而不是靠硬编码名字蒙对的。

数据来自 tests/fixtures/synthetic_menu.json —— 合成数据，非真实价格。

用法：python3 tests/mock_mcp_server.py
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "synthetic_menu.json")

TOOLS = [
    {
        "name": "mcd_store_catalog_v2",
        "description": "拉取指定门店的菜单与商品价格，返回菜品列表、分类、标签与可售状态",
        "inputSchema": {
            "type": "object",
            "properties": {"storeId": {"type": "string", "description": "门店 ID"}},
        },
    },
    {
        "name": "query_promotions",
        "description": "查询当前可用的优惠券与优惠活动，包含满减与随心配 rules",
        "inputSchema": {
            "type": "object",
            "properties": {"storeId": {"type": "string", "description": "门店 ID"}},
        },
    },
    {
        "name": "cart_preview",
        "description": "试算购物车总价，不做实际下单",
        "inputSchema": {"type": "object", "properties": {"lines": {"type": "array"}}},
    },
    {
        "name": "place_order",
        "description": "提交订单并支付",
        "inputSchema": {"type": "object", "properties": {"lines": {"type": "array"}}},
    },
    {
        "name": "list-nutrition-foods",
        "description": "获取麦当劳餐品的营养成分数据，包括能量、蛋白质、脂肪、碳水化合物、钠等",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "weather_today",
        "description": "查询天气，和点餐无关，用于验证无关工具不会被误匹配",
        "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}},
    },
]


def _load():
    with open(FIXTURE, "r", encoding="utf-8") as f:
        return json.load(f)


def handle(msg: dict) -> dict | None:
    method = msg.get("method")
    mid = msg.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": mid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock-mcd-mcp", "version": "0.1.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        data = _load()
        if name == "mcd_store_catalog_v2":
            payload = {"items": data["items"]}
        elif name == "query_promotions":
            payload = {"rules": data["rules"]}
        elif name == "list-nutrition-foods":
            payload = {"foods": data.get("nutrition", [])}
        elif name == "cart_preview":
            payload = {"total": 0, "note": "mock 试算未实现计价逻辑"}
        elif name == "place_order":
            payload = {"orderId": "MOCK-ORDER-1", "status": "created"}
        elif name == "weather_today":
            payload = {"city": "Zhangzhou", "weather": "sunny"}
        else:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32601, "message": f"unknown tool: {name}"}}
        return {"jsonrpc": "2.0", "id": mid,
                "result": {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}}
    if method and method.startswith("notifications/"):
        return None
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"unknown method: {method}"}}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
