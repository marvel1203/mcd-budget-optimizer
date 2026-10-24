# WorkBuddy 开发上下文

> 本文件用于核验本项目是否真实使用腾讯 WorkBuddy 智能体开发。
> 内容为开发过程中的对话上下文与决策记录，未做修饰。

- **开发者**：marvel1203（大雄）
- **开发时间**：2026-10-24 15:57 — 17:30（北京时间）
- **开发环境**：WorkBuddy（macOS，Agent 模式），Python 3.13
- **项目名称**：mcd-budget-optimizer（麦当劳省钱点餐助手）
- **参赛**：麦当劳程序员节创意开发大赛（WorkBuddy × 麦当劳）

---

## 一、需求是怎么来的

用户（大雄）让我整理 2026 年 1024 程序员节的活动。我在清单里标出了
WorkBuddy × 麦当劳创意大赛，并注意到它的时间窗口只剩一天多（10-25 23:59 截止）。

我给的建议是：

> WorkBuddy × 麦当劳大赛（明晚 23:59 截止报名）——唯一能换实打实大奖且时间还来得及的，
> 题材和你日常用 WorkBuddy 做 Skill 完全重合。要不要现在就把麦当劳大赛那个 Skill 做出来？

用户回复：**「WorkBuddy × 麦当劳大赛 你来帮我做」**

## 二、开工前遇到的障碍

我准备先查麦当劳 MCP 的工具定义，结果发现**外网整个断了**：

```
curl https://www.baidu.com     -> Connection timed out
curl https://api.github.com    -> Connection timed out
WebSearch                      -> fetch failed
ssh -T git@github.com          -> Could not resolve hostname
```

这意味着我拿不到 MCP 的工具名、参数、返回结构。如果按常规做法把工具名硬编码进
Skill，就只能瞎猜 `get_menu` / `list_dish` 之类的名字，猜错就是整个作品不可用。

**当时做的判断**：不猜。改成能力契约——Skill 只声明需要哪几种能力，
运行时先要 `tools/list`，按工具名和描述的语义自动匹配。
这样对方工具叫什么都能工作，也顺手解决了「大赛期间对方改工具名」的风险。

代价是多写一个 `mcp_client.py`，我认为值得。

## 三、关键决策（含我自己推翻自己的部分）

**1. 选题：省钱组合优化，不做点餐机器人。**
点餐机器人同质化严重；券码管理涉及账号安全；营养分析拿不到可靠数据，
硬编就是编数据。省钱组合优化有真实痛点，而且「省了多少」是可验证的，
不像「推荐好不好」那样说不清。

**2. 仓库里不放任何真实价格。**
模型记忆里的价格一定过期。所有价格必须来自 MCP 实时拉取或带时间戳的缓存。
测试用的合成数据在文件头和 `meta` 字段里都标了 disclaimer。

**3. 目标函数从加权求和改成元组分层比较。**
第一版 `cheapest` 模式把「实付」和「口味偏好」加权成一个分数，
跑出来发现偏好会盖过省钱——这跟模式名自相矛盾。改成先比实付、
实付完全相同时才看偏好和多样性。加了两个测试锁死这个行为：
`test_preference_breaks_ties` 和 `test_cheapest_never_pays_for_preference`。

**4. 凑单只在真的更便宜时才发生。**
凑单省了 8 块但多花 10 块，那是亏的，代码里这个分支直接跳过。

**5. 求解用启发式，并诚实标注不是全局最优。**
没有上整数规划求解器，这个规模用不上，引入依赖反而降低可用性。
为了不让它变成「看起来能跑但结果不对」，测试里对小规模用例做了
启发式 vs 暴力枚举的一致性比对。

## 四、网络恢复后补的课

16:40 左右网络恢复，我立刻去核对了两件事，都发现了问题：

**问题一：还缺 4 个必需文件。**
官方 `activityGuidelines.md` 要求仓库必须包含 `README.md`、
`CONTEST_DECLARATION.md`（内容不可改动）、`MCP_INTEGRATION.md`、
`mcp-config.example.json`、`workbuddy.md`。我只做了 1 个。
`CONTEST_DECLARATION.md` 我从官方仓库取了原文直接复制，MD5 校验一致。

**问题二：我的客户端连不上麦当劳 MCP。**
官方用的是 **Streamable HTTP**（`https://mcp.mcd.cn`），请求头必须带
`Authorization: Bearer <token>`。我第一版只做了裸 POST，没有鉴权头，
也处理不了 SSE 流响应。补上了鉴权头、`Accept` 头、`Mcp-Session-Id` 会话保持
和 SSE 解析。

**问题三：关键词表匹配不上真实工具名。**
拿到官方 33 个工具清单后发现，麦当劳的菜单工具叫 `query-meals`——
我的关键词表里没有 `meal`，会被漏掉。补了 `meal` / `餐品` / `菜品` 等关键词，
并加了 `TestRealMcDonaldToolNames`，把官方工具清单跑一遍防止再退化。

## 五、开发顺序

| 时间 | 做了什么 |
| --- | --- |
| 15:57 | 选题，确认 WorkBuddy Skill 的 frontmatter 规范 |
| 16:05 | 网络中断，改为能力契约方案 |
| 16:10—16:35 | models / pricing / solver / mcp_client / cli |
| 16:40 | 39 个测试通过；修偏好失效、资源泄漏、元组比较 |
| 16:45 | SKILL.md / README / DEVLOG |
| 16:50 | 网络恢复，核对官方规则，发现缺文件与三个问题 |
| 17:00—17:20 | 修鉴权、修关键词表、补齐四个必需文件 |
| 17:30 | 41 个测试通过，推送 GitHub，提交报名 Issue |

## 六、最终产出

```
SKILL.md                        Skill 定义（Agent 读这份）
README.md                       项目介绍
CONTEST_DECLARATION.md          参赛声明（官方原文，MD5 校验一致）
MCP_INTEGRATION.md              MCP 集成说明
mcp-config.example.json         脱敏 MCP 配置（仅环境变量占位符）
workbuddy.md                    本文件
DEVLOG.md                       详细开发记录
optimizer/                      models / mcp_client / solver / pricing / cli
scripts/fetch_menu.py           从 MCP 拉菜单快照
tests/                          mock MCP server + 41 个测试
```

41 个测试全绿，包含：异构数据归一化、优惠计价、约束求解、
启发式 vs 暴力枚举一致性、真实麦当劳工具名映射、端到端 MCP 调用。

## 七、没做的和已知限制

- `best` 模式的满意度分里「越贴近预算越好」是主观假设，没有营养或口碑数据支撑
- 1-swap 邻域不保证全局最优，只在小规模验证过与暴力枚举一致
- 未实现多门店比价、历史订单分析
- `create-order` 已识别但默认不调用，需用户显式确认
- 营养能力（`list-nutrition-foods`）已识别但尚未接入输出渲染

---

*本项目由参赛者在 WorkBuddy 中开发完成，代码与文档均为本次开发过程产出。*
