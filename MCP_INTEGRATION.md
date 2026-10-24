# MCP 集成说明

## 使用的 MCP Server

| 项 | 值 |
| --- | --- |
| Server | 麦当劳中国 MCP Server（`mcd-mcp`） |
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP |
| 鉴权 | `Authorization: Bearer <MCP Token>` |
| Token 申请 | https://open.mcd.cn/mcp （手机号登录后控制台激活，免费） |
| 限流 | 每个 Token 600 次 / 分钟 |

配置方式见 `mcp-config.example.json`，其中 Token 只用环境变量占位符表示。

## 实际调用的 Tool

本项目的菜单、优惠、价格**全部**来自 MCP 实时调用，代码里不含任何硬编码价格。
按能力划分，实际用到的工具如下（工具名取自 M-China/mcd-mcp-server 官方清单）：

| 能力 | 工具 | 用途 | 必需 |
| --- | --- | --- | --- |
| 菜单 | `query-meals` | 拉取门店在售餐品、价格、分类、可售状态 | 是 |
| 菜单 | `query-meal-detail` | 需要单品细节时补充查询 | 否 |
| 优惠 | `available-coupons` | 拉取当前可用优惠券与活动（满减 / 随心配） | 是 |
| 优惠 | `query-store-coupons` | 按门店过滤可用券 | 否 |
| 试算 | `calculate-price` | 用官方口径复核实付金额 | 否 |
| 营养 | `list-nutrition-foods` | 附带热量、蛋白质、脂肪、碳水等营养信息 | 否（已接入渲染） |
| 下单 | `create-order` | 已识别但**默认不调用**，需用户显式确认 | 否 |

`query-meals` 和 `available-coupons` 是硬需求，缺任一则直接报错退出，
不会退回到猜测数据——宁可不给方案，也不给一个基于错误数据的方案。

## 调用流程

```
initialize
  └─> tools/list                       拿到全部工具定义
        └─> 能力语义映射（见下节）
              ├─> tools/call query-meals          -> 菜单 Item[]
              ├─> tools/call available-coupons    -> 优惠 CouponRule[]
              │
              ├─> 本地求解（models -> solver -> pricing）
              │     过滤忌口 / 贪心构造 / 1-swap 局部搜索 / 凑单判定
              │
              ├─> [可选] tools/call calculate-price  用官方口径复核实付
              └─> [需确认] tools/call create-order    仅当用户明确要求
```

一次完整的方案计算只发 **2 次** MCP 调用（菜单 + 优惠），
试算和下单都是按需触发，避免浪费 Token 配额也避免误触扣款。

## 为什么不硬编码工具名

各家 MCP 的工具命名没有统一标准。麦当劳官方用的是连字符风格
（`query-meals`、`available-coupons`），换成另一个实现可能就是
`get_menu` 或 `mcd_store_catalog_v2`。把工具名写死在 Skill 里，
对方一改名就整个失效。

本项目改成**能力契约**：只声明「我需要菜单、优惠、试算、营养、下单这五种能力」，
连上之后先要 `tools/list`，再按工具名 + 描述的语义打分，把实际工具映射到能力上。
映射结果可用 `--verbose` 打印出来核对。

```console
$ python3 -m optimizer.cli --from-mcp --people 3 --budget 90 --verbose
发现 33 个工具，能力映射如下：
  [ok] 获取门店菜单（含价格与可售状态） -> query-meals (score=1.00)
  [ok] 获取可用优惠券与活动 -> available-coupons (score=1.00)
  [ok] 试算购物车总价（不实际下单） -> calculate-price (score=1.00)
  [ok] 获取餐品营养成分 -> list-nutrition-foods (score=1.00)
  [ok] 下单 -> create-order (score=1.00)
```

关键词表是针对麦当劳官方 33 个工具名校准过的，
`tests/test_mcp.py::TestRealMcDonaldToolNames` 会把这份清单跑一遍，
防止后续改动让某个工具掉出映射。

## 业务价值

对消费者：

- 把「怎么凑最划算」从心算变成可复现的求解，避免人工凑单时
  把便宜的东西塞进随心配（正确做法是把池内**最贵**的几件塞进去）；
- 凑满减只在加完之后实付真下降时才发生，不会为了凑单反而多花钱；
- 忌口过滤前置，避免点完才发现有人过敏。

对麦当劳 MCP 生态：

- 菜单 + 优惠 + 试算三个能力组合出单看时看不到的价值，
  是一个「能力组合 > 能力之和」的示例；
- 只读调用为主，下单需显式确认，对用户的账号资金安全友好；
- 能力契约的做法对后续接入方有参考意义——不依赖工具命名细节。

## 安全

- Token 只从环境变量 `MCD_MCP_TOKEN` 读取，不写进任何文件；
- `create-order`、`draw-lottery` 这类会真实扣款 / 扣积分的工具
  **默认不调用**，必须用户明确要求并确认金额后才触发；
- 仓库内的配置文件只含环境变量占位符，不含任何真实凭证。
