# 演示场景

以下命令均可直接复现（仓库根目录执行）。数据来自 mock MCP server 的
**合成假数据**，只看算法行为，不要当真价格。

准备：

```bash
export MCD_MCP_TRANSPORT=stdio
export MCD_MCP_COMMAND="python3 tests/mock_mcp_server.py"
```

---

## 场景一：三人 90 元，主食 + 饮品

```bash
python3 -m optimizer.cli --from-mcp --people 3 --budget 90 --slots main,drink --verbose
```

输出：

```
发现 5 个工具，能力映射如下：
  [ok] 获取门店菜单（含价格与可售状态） -> mcd_store_catalog_v2 (score=1.00)
  [ok] 获取可用优惠券与活动 -> query_promotions (score=1.00)
  [ok] 试算购物车总价（不实际下单） -> cart_preview (score=1.00)
  [ok] 下单 -> place_order (score=1.00)
拉取到 21 个商品、3 条可用优惠规则
### 点餐方案

| 商品 | 单价 | 份数 | 小计 |
| --- | ---: | ---: | ---: |
| 吉士汉堡 | 12.00 | 2 | 24.00 |
| 牛奶 | 8.00 | 1 | 8.00 |
| 中可乐 | 9.50 | 1 | 9.50 |
| 橙汁 | 10.00 | 1 | 10.00 |
| 麦香鸡 | 15.00 | 1 | 15.00 |

- 原价合计：66.50 元
- 实付：52.90 元（共 6 件，人均 17.63 元）
- 省下：13.60 元
- 命中优惠：2 件 drink/side 合计 13.90（省 5.60）；满 60.00 减 8.00（省 8.00）
- 预算 90.00 元：剩余 37.10 元
```

看点：三条饮品选了三种而不是两份牛奶。两份牛奶同样能到 52.90 元，
但实付相同时算法偏好多样性更高的组合。

---

## 场景二：忌口 + 偏好

```bash
python3 -m optimizer.cli --menu tests/fixtures/synthetic_menu.json \
    --people 2 --slots main,side,drink --avoid 花生 --prefer 鸡肉
```

输出：

```
| 商品 | 单价 | 份数 | 小计 |
| --- | ---: | ---: | ---: |
| 吉士汉堡 | 12.00 | 2 | 24.00 |
| 牛奶 | 8.00 | 1 | 8.00 |
| 中可乐 | 9.50 | 1 | 9.50 |
| 麦乐鸡（5块） | 13.00 | 1 | 13.00 |
| 麦辣鸡翅（2块） | 14.00 | 1 | 14.00 |

- 原价合计：68.50 元
- 实付：43.80 元（共 6 件，人均 21.90 元）
- 省下：24.70 元
- 命中优惠：满 60.00 减 8.00（省 8.00）；
  2 件 drink/side 合计 13.90（省 13.10）；2 件 drink/side 合计 13.90（省 3.60）
```

看点三个：

1. **花生派被排除了**，尽管它是小食里第二便宜的；
2. **随心配被用了两次** —— 鸡翅 + 可乐一组（原价 23.5 压到 13.9，省 13.10），
   麦乐鸡 + 牛奶一组（省 3.60）。这种「把最贵的几件塞进 bundle」的挑法
   正是省钱的关键，人工凑单最容易在这里吃亏；
3. **偏好鸡肉没有让我们多花钱** —— 主食仍是吉士汉堡（12 元最便宜，牛肉），
   鸡肉只出现在实付相同的可选位置。`cheapest` 模式下省钱优先级最高，
   想兼顾口味请换 `--objective best`。

---

## 场景三：机器可读输出

```bash
python3 -m optimizer.cli --menu tests/fixtures/synthetic_menu.json \
    --people 2 --slots main,drink --json
```

输出：

```json
{
  "people": 2,
  "budget": null,
  "objective": "cheapest",
  "items": [
    {"id": "d-04", "name": "牛奶", "price": 8.0, "qty": 2, "category": "drink"},
    {"id": "m-07", "name": "吉士汉堡", "price": 12.0, "qty": 2, "category": "main"}
  ],
  "subtotal": 40.0,
  "total": 37.9,
  "saved": 2.1,
  "per_person": 18.95,
  "applied_rules": ["2 件 drink/side 合计 13.90（省 2.10）"],
  "top_up_note": "",
  "warnings": []
}
```

适合接进别的 Agent 流程继续处理。

---

## 场景四：MCP 缺能力时会怎样

把 mock server 的菜单工具改名，再跑一次，会看到：

```
  [缺失] 获取门店菜单（含价格与可售状态） -> 未匹配到工具
无法获取菜单: MCP 未提供下列必需能力，无法继续：获取门店菜单（含价格与可售状态）。
可用 --menu 改读本地缓存。
```

必需能力缺失直接报错退出，不会用错误数据算出一个看起来正常的方案。
