# mcd-budget-optimizer

> 给一群人点麦当劳，最难的不是选什么，是算清楚怎么凑最划算。
> 人数、预算、忌口丢进来，通过麦当劳 MCP 拉实时菜单和优惠，算出最省的那套搭配。

本项目是 [WorkBuddy × 麦当劳程序员创意大赛](https://github.com) 参赛作品。

---

## 它解决什么

三个人、90 块、一人一个堡一杯饮料，怎么点最省？

- 1+1 随心配该组哪两件？薯条 + 可乐值，还是玉米 + 牛奶值？
- 差 6 块就能满 60 减 8，要不要多拿一份？多拿的那份算不算白赚？
- 有人花生过敏、有人不吃辣，过滤完之后还剩多少可选？
- 「最省」和「吃点好的」是两回事，你想选哪个？

这些都是约束满足 + 组合优化问题，心算很容易错。这里把它变成一个
可复现、可测试、有日志的求解过程。

## 跑起来的样子

```console
$ python3 -m optimizer.cli --from-mcp --people 3 --budget 90 --slots main,drink --verbose

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

上面的演示走的是仓库自带的 mock MCP server（`tests/mock_mcp_server.py`），
数据是 `tests/fixtures/synthetic_menu.json` 里的**合成假数据**，
只为演示算法行为，不代表任何真实门店价格。接上真 MCP 后，同样的命令
拿到的就是实时菜单。

## 快速开始

```bash
git clone <你的仓库地址>
cd mcd-budget-optimizer
python3 --version   # 需要 3.9+，无第三方依赖

# 用合成数据先跑通
python3 -m optimizer.cli --menu tests/fixtures/synthetic_menu.json --people 3 --budget 90

# 跑测试
python3 -m unittest discover -s tests -t . -p "test_*.py"
```

## 接麦当劳 MCP

```bash
export MCD_MCP_TRANSPORT=stdio
export MCD_MCP_COMMAND="npx -y <麦当劳 MCP 包名>"

python3 scripts/fetch_menu.py --out cache/menu.json   # 拉快照
python3 -m optimizer.cli --from-mcp --people 3 --budget 90 --verbose
```

### 为什么工具名不用你管

各家 MCP 暴露的工具叫什么的都有：`get_menu`、`mcd_store_catalog_v2`、
`query_promotions`、`list_dish`……硬编码工具名的 Skill，对方一改名字就废了。

这里改成**能力契约**：Skill 只声明「我需要四种能力——菜单、优惠、试算、下单」，
连上之后先要一份 `tools/list`，再按名称和描述的语义把实际工具映射到这些能力上：

```console
发现 5 个工具，能力映射如下：
  [ok] 获取门店菜单（含价格与可售状态） -> mcd_store_catalog_v2 (score=1.00)
  [ok] 获取可用优惠券与活动 -> query_promotions (score=1.00)
  [ok] 试算购物车总价（不实际下单） -> cart_preview (score=1.00)
  [ok] 下单 -> place_order (score=1.00)
```

菜单和优惠是必需能力，缺了直接报错退出而不是瞎猜；试算和下单是可选的，
缺了也能正常出方案。仓库里自带一个 mock MCP server，工具名故意起得
「不像麦当劳」，就是用来验证这套映射确实在工作而不是蒙对的。

## 参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--people` | 1 | 人数 |
| `--budget` | 无 | 预算上限（元） |
| `--slots` | `main,drink` | 每人需要的类别，逗号分隔 |
| `--avoid` | 无 | 忌口 / 过敏标签 |
| `--prefer` | 无 | 口味偏好标签 |
| `--max-same` | 2 | 同一单品最多几份 |
| `--objective` | `cheapest` | `cheapest` 最省 / `best` 预算内吃最好 |
| `--no-top-up` | 关 | 关闭凑满减 |
| `--json` | 关 | 输出 JSON |
| `--store` | 无 | 门店 ID |

## 算法

约束是硬的，目标是明确的，但求解是启发式：

1. **过滤** —— 剔除不可售、命中忌口的商品；
2. **建模** —— `slots` 里每项是一个「槽」，每槽一人一份；
3. **构造** —— 按槽贪心，从候选稀缺的类别先挑；
4. **局部搜索** —— 反复尝试把某槽换成同类别另一个候选，接受改进，直到收敛；
5. **凑单** —— 只在「加一件之后实付反而更低」时才加，绝不为了凑单多花钱。

**不假装是全局最优。** 第 4 步用的是最朴素的 1-swap 邻域，没有模拟退火也没有
整数规划求解器——在「十几个槽、每类几十个候选」这个量级上，`cheapest`
要找的本来就是价格单调性很强的解，1-swap 足够收敛。

代价要说清楚：`best` 模式的满意度分里，「越贴近预算越好」这一项是主观权重
（没有营养/口碑数据可用），它代表的是一个假设而不是事实。

验证方式是对小规模用例做**启发式 vs 暴力枚举**的一致性比对，见
`tests/test_solver.py::TestAgainstBruteForce`。

## 关于价格数据

**本仓库不含任何真实价格。** `tests/fixtures/synthetic_menu.json` 是随手编的
合成数据，文件头和 `meta` 字段里都标了 disclaimer，只用于算法验证和演示。

真实数据一律通过 MCP 实时获取，或用 `scripts/fetch_menu.py` 拉快照。
这么做是为了不把过期价格写进代码——模型记忆里的价格一定是错的，
缓存文件至少带拉取时间戳可以核对。

## 项目结构

```
SKILL.md                      Skill 定义（Agent 读这份）
optimizer/
  models.py                   领域模型 + 异构数据归一化
  mcp_client.py               MCP 客户端 + 能力自适应映射
  solver.py                   组合求解（贪心 + 局部搜索 + 凑单）
  pricing.py                  优惠计价（bundle / threshold）
  cli.py                      命令行入口与渲染
scripts/fetch_menu.py         从 MCP 拉菜单快照
tests/
  mock_mcp_server.py          本地 mock MCP server
  fixtures/synthetic_menu.json  合成测试数据
  test_models.py  test_solver.py  test_mcp.py
```

## 大赛信息

WorkBuddy × 麦当劳程序员创意大赛，报名截止 **2026-10-25 23:59**，
按 GitHub 项目 Star 数排名。前三名 50 / 30 / 20 次巨无霸兑换券 + 10240 AI 积分，
前 100 名有节日实体徽章和汉堡回车键。

如果这个项目帮你算明白了一顿饭，点个 Star 就是对我最大的支持。

## License

MIT
