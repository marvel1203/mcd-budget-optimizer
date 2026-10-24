---
name: mcd-budget-optimizer
description: "Plan a McDonald's order under headcount, budget and dietary constraints — pulls the live menu and promos through the McDonald's MCP, then solves for the cheapest combo that still feeds everyone."
description_zh: "在人数、预算、忌口约束下规划麦当劳点餐：经 MCP 拉取实时菜单与优惠，算出最省搭配"
description_en: "Budget-aware McDonald's order planner over MCP: cheapest combo for N people, honoring budget, allergies and taste preferences"
version: 1.0.0
---

# 麦当劳省钱点餐助手

给一群人点麦当劳时，真正难的不是选什么，而是算清楚「怎么凑最划算」：
1+1 随心配该组哪两件、要不要为了满减多加一份、谁忌口什么、预算够不够。
这些心算很容易错，而这个 Skill 把它变成一个可复现的求解过程。

菜单与优惠**全部经麦当劳 MCP 实时拉取**，Skill 本身不含任何价格数据。

## 什么时候用

用户说出下面这类需求时启用：

- 「三个人 90 块怎么点麦当劳」
- 「帮我算算怎么点最省钱 / 最划算」
- 「有花生过敏、不吃辣，四个人预算 120」
- 「1+1 随心配怎么组最值」

## 前置条件

需要配置麦当劳 MCP。支持两种传输，用环境变量告诉本 Skill：

```bash
# stdio（默认）
export MCD_MCP_TRANSPORT=stdio
export MCD_MCP_COMMAND="npx -y <麦当劳 MCP 包名>"

# 或者 http
export MCD_MCP_TRANSPORT=http
export MCD_MCP_URL="https://..."
```

MCP 至少需要能提供「菜单」和「优惠」两类能力。连上之后先跑一次自检，
确认工具被正确识别（工具名不需要和本 Skill 预设的一致，会按语义自动匹配）：

```bash
python3 -m optimizer.cli --from-mcp --people 1 --slots main --verbose
```

`--verbose` 会打印能力映射表。若 menu / coupon 显示「未匹配到工具」，
说明这个 MCP 没有暴露对应能力，需要换数据源或改用本地菜单缓存。

## 工作流

**第一步：问清约束。** 缺哪个问哪个，别一次丢五个问题：

| 参数 | 必须 | 说明 |
| --- | --- | --- |
| 人数 | 是 | `--people` |
| 每人要什么 | 否，默认 `main,drink` | `--slots`，如 `main,side,drink` |
| 预算 | 否 | `--budget`，单位元 |
| 忌口/过敏 | 否 | `--avoid`，如 `花生,辣` |
| 口味偏好 | 否 | `--prefer`，如 `牛肉` |
| 同款上限 | 否，默认 2 | `--max-same` |

**第二步：拉数据。** 优先走 MCP：

```bash
python3 -m optimizer.cli --from-mcp --people 3 --budget 90 --slots main,drink
```

网络不通或 MCP 没配好时，退回本地缓存（由 `scripts/fetch_menu.py` 生成）：

```bash
python3 -m optimizer.cli --menu cache/menu.json --people 3 --budget 90
```

**第三步：读结果。** 输出是一张 Markdown 表 + 结算说明：原价、实付、
省了多少、命中哪几条优惠、人均多少、预算是否超支。直接转述给用户，
不要自己重新算一遍。

**第四步：只在用户明确要求时才下单。** 本 Skill 默认只做规划，不碰 `order`
能力。用户说「就按这个下」时，先用 MCP 的 quote 能力复核一次实付，
把金额念给用户确认，再调用下单。

## 常用命令

```bash
# 最省：3 人 90 元，主食 + 饮品
python3 -m optimizer.cli --from-mcp --people 3 --budget 90 --slots main,drink

# 预算内吃最好（更丰盛，而不是更便宜）
python3 -m optimizer.cli --from-mcp --people 2 --budget 80 --slots main,side,drink --objective best

# 忌口 + 偏好
python3 -m optimizer.cli --from-mcp --people 4 --avoid 花生,辣 --prefer 牛肉

# 关闭凑满减（默认开启）
python3 -m optimizer.cli --from-mcp --people 2 --no-top-up

# 机器可读输出，便于后续处理
python3 -m optimizer.cli --from-mcp --people 3 --json
```

## 输出里的几句话怎么解释

- **凑单提示**：为了凑上满减多加了一件，且加完实付反而更低。若用户不想多拿，
  加 `--no-top-up` 重跑。
- **提示：类别 X 可选商品不足**：该店这类商品不够（或全被忌口过滤了），
  算法放宽了同款上限才凑齐份数。需要跟用户确认能不能接受同款多份。
- **提示：当前搭配实付 X 元，超出预算**：预算内已经给不出更省的方案，
  得减人数份数、降 slots 要求，或加预算。

## 必须遵守的几条

1. **不许凭记忆报价格。** 所有价格必须来自 MCP 实时返回或本地缓存文件。
   模型记忆里的价格一定过期，直接报出来就是错的。
2. **`cheapest` 就是最省。** 这个模式下口味偏好只在实付完全相同时才起作用，
   不会为了迎合口味多花一块钱。想兼顾丰盛用 `--objective best`。
3. **别替用户决定要不要凑单。** 凑单逻辑保证「加完更便宜」，但多一件东西
   仍然是多一件，把选择权留给用户。
4. **下单前必须复核并确认金额。**
5. **示例数据不能当真。** `tests/fixtures/synthetic_menu.json` 是合成假数据，
   只用于测试和演示，绝不能拿它的价格回答真实点餐问题。
