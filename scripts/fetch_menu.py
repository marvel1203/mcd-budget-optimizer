#!/usr/bin/env python3
"""把麦当劳 MCP 的实时菜单与优惠拉下来，存成本地 JSON 缓存。

用途：
  1. 网络不稳时给 Skill 提供离线数据源（--menu cache/menu.json）；
  2. 留一份快照做对账 —— 价格是会变的，缓存文件里带拉取时间戳；
  3. 生成真实数据的测试夹具，避免只靠合成数据验证。

用法：
    export MCD_MCP_COMMAND="npx -y <麦当劳 MCP 包名>"
    python3 scripts/fetch_menu.py --out cache/menu.json --store <门店ID>

注意：拉下来的数据里如果含门店/用户敏感信息，别提交进公开仓库。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from optimizer.cli import collect_from_mcp  # noqa: E402
from optimizer.models import dump_menu  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="从麦当劳 MCP 拉取菜单快照")
    p.add_argument("--out", default="cache/menu.json", help="输出路径")
    p.add_argument("--store", default=None, help="门店 ID")
    p.add_argument("--verbose", action="store_true", help="打印能力映射")
    args = p.parse_args(argv)

    try:
        items, rules, notes = collect_from_mcp(args.store, args.verbose)
    except Exception as e:
        print(f"拉取失败: {type(e).__name__}: {e}", file=sys.stderr)
        print("请确认 MCD_MCP_COMMAND / MCD_MCP_URL 已正确配置。", file=sys.stderr)
        return 1

    if args.verbose:
        for n in notes:
            print(n)

    if not items:
        print("拉取到 0 个商品，拒绝覆盖已有缓存。", file=sys.stderr)
        return 2

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    payload = dump_menu(items, rules, meta={
        "source": "mcp",
        "store": args.store,
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "item_count": len(items),
        "rule_count": len(rules),
    })
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(payload)

    print(f"已保存 {len(items)} 个商品、{len(rules)} 条优惠 -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
