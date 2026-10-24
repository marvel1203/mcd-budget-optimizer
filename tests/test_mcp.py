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
