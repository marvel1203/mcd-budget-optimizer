"""MCP 客户端测试：连本地 mock server，验证能力自适应映射。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from optimizer.mcp_client import MCPClient, score_tool  # noqa: E402
from optimizer.cli import collect_from_mcp  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MOCK = os.path.join(HERE, "mock_mcp_server.py")


class TestScoreTool(unittest.TestCase):
    def test_matches_menu(self):
        t = {"name": "mcd_store_catalog_v2", "description": "拉取菜单与商品价格"}
        from optimizer.mcp_client import CAPABILITIES
        menu_cap = next(c for c in CAPABILITIES if c.key == "menu")
        self.assertGreater(score_tool(t, menu_cap), 0.45)

    def test_weather_not_matched(self):
        t = {"name": "weather_today", "description": "查询天气"}
        from optimizer.mcp_client import CAPABILITIES
        menu_cap = next(c for c in CAPABILITIES if c.key == "menu")
        self.assertLess(score_tool(t, menu_cap), 0.45)


class TestRealMcDonaldToolNames(unittest.TestCase):
    """用麦当劳 MCP 官方公布的真实工具名校准关键词表。

    工具清单来自 M-China/mcd-mcp-server 的 README。最初版本的关键词表里
    没有 "meal"，query-meals 会被漏掉 —— 这组测试就是防止再退回去。
    """

    CASES = [
        ("query-meals", "查询门店在售餐品与价格", "menu"),
        ("query-meal-detail", "查询单个餐品详情", "menu"),
        ("available-coupons", "查询当前可用优惠券", "coupon"),
        ("query-store-coupons", "查询门店可用券", "coupon"),
        ("calculate-price", "根据所选餐品计算价格，含优惠", "quote"),
        ("list-nutrition-foods", "获取餐品营养成分数据", "nutrition"),
        ("create-order", "创建订单", "order"),
    ]

    def test_each_maps_to_expected_capability(self):
        from optimizer.mcp_client import CAPABILITIES
        caps = {c.key: c for c in CAPABILITIES}
        for name, desc, expected in self.CASES:
            tool = {"name": name, "description": desc}
            scored = {k: score_tool(tool, c) for k, c in caps.items()}
            best = max(scored, key=lambda k: scored[k])
            self.assertEqual(best, expected,
                             f"{name} 被映射到 {best}（分数 {scored}），应为 {expected}")
            self.assertGreater(scored[expected], 0.45,
                               f"{name} 对 {expected} 的匹配分过低: {scored[expected]}")

    def test_unrelated_tools_stay_unmatched(self):
        from optimizer.mcp_client import CAPABILITIES
        caps = {c.key: c for c in CAPABILITIES}
        for name, desc in [("query-party-store", "查询派对门店"),
                           ("draw-lottery", "抽奖"),
                           ("now-time-info", "取服务器时间")]:
            tool = {"name": name, "description": desc}
            self.assertLess(score_tool(tool, caps["menu"]), 0.45,
                            f"{name} 不该被当成菜单工具")


class TestAgainstMockServer(unittest.TestCase):
    def setUp(self):
        self.client = MCPClient(transport="stdio",
                                command=[sys.executable, MOCK])
        self.client.start()
        self.addCleanup(self.client.close)

    def test_list_tools(self):
        tools = self.client.list_tools()
        names = {t["name"] for t in tools}
        self.assertIn("mcd_store_catalog_v2", names)
        self.assertIn("weather_today", names)

    def test_capability_binding(self):
        b = self.client.bind_capabilities()
        self.assertIn("menu", b)
        self.assertEqual(b["menu"].tool_name, "mcd_store_catalog_v2")
        self.assertIn("coupon", b)
        self.assertEqual(b["coupon"].tool_name, "query_promotions")
        self.assertIn("quote", b)
        self.assertIn("order", b)
        self.assertEqual(self.client.missing_required(b), [])

    def test_weather_never_bound_to_menu(self):
        b = self.client.bind_capabilities()
        self.assertNotEqual(b["menu"].tool_name, "weather_today")

    def test_end_to_end_collect(self):
        os.environ["MCD_MCP_TRANSPORT"] = "stdio"
        os.environ["MCD_MCP_COMMAND"] = f"{sys.executable} {MOCK}"
        try:
            items, rules, notes = collect_from_mcp()
        finally:
            os.environ.pop("MCD_MCP_COMMAND", None)
        self.assertGreater(len(items), 10)
        self.assertGreaterEqual(len(rules), 3)
        kinds = {r.kind for r in rules}
        self.assertIn("threshold", kinds)
        self.assertIn("bundle_fixed", kinds)
        self.assertTrue(any("能力映射" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
