# WorkBuddy 开发上下文 · 麦麦拼单官

> **本文件的用途**：证明本项目（`mcd-group-order-mate`）系**真实使用腾讯 WorkBuddy 智能体**开发，用于核验是否符合《麦当劳程序员创意开发大赛》WorkBuddy 专项奖励的联动活动条件。
>
> 内容为开发过程的**对话上下文与工作记录**，已脱敏——**不含任何真实 Token、密钥、账号或他人个人信息**。文中出现的 Token 一律为占位符。

---

## 一、开发概况

| 项 | 内容 |
|---|---|
| 项目名称 | 麦麦拼单官（`mcd-group-order-mate`） |
| 项目形态 | **WorkBuddy Skill**（本仓库根目录即 Skill 根目录） |
| 开发工具 | 腾讯 WorkBuddy（Agent 模式，含自定义连接器 / Skill / 本地脚本执行） |
| 开发时间 | 2026-10-09 ~ 2026-10-10（北京时间） |
| 外部能力 | 麦当劳中国 MCP Server（`mcd-mcp`，Streamable HTTP） |
| 交付物 | `SKILL.md` + 3 个 Python 脚本 + 2 份 references + 参赛材料 |
| 验证强度 | **5 次端到端实跑**，其中 **3 次为真实下单闭环**（订单在麦当劳 App 正常生成） |

开发全程在 WorkBuddy 内完成：创意构思 → 查文档 → 写 Skill → 写脚本 → 连 MCP 实测 → 真实下单验证 → 逐轮修 bug → 产出参赛材料。**没有任何环节脱离 WorkBuddy**。

---

## 二、开发环境（脱敏）

### 1. WorkBuddy 侧

- 自定义连接器配置（仅环境变量占位符，与仓库 `mcp-config.example.json` 一致）：

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": { "Authorization": "Bearer ${MCD_MCP_TOKEN}" }
    }
  }
}
```

- Skill 安装位置（用户技能目录）：

```
~/.workbuddy/skills/mcd-group-order-mate/
```

- 开发即在此目录内运行验证：WorkBuddy 读取 `SKILL.md` 的编排指令驱动 MCP 调用，并执行 `scripts/` 下的脚本完成寻优与分账。

### 2. 脚本运行方式

所有组合数学与账务计算由本地脚本承担，MCP 只负责"取真实数据"与"真实计价"：

```bash
python scripts/mcd_utils.py yuan-to-fen --value 33.5
python scripts/mcd_utils.py coupon-opportunities --input probes.json \
    --demand 4810,1401 --now "2026-10-10 00:06" --channel pickup
python scripts/coupon_optimizer.py split  --input split_input.json
python scripts/coupon_optimizer.py select --input probe_results.json
python scripts/split_bill.py --input bill.json
python scripts/mcd_utils.py order-summary --input order_resp.json --expected-fen 13090
```

---

## 三、对话上下文（按开发轮次）

> 以下为开发过程的关键轮次：**用户输入 → WorkBuddy 执行 → 产出**。脱敏处理后按时间顺序记录。

### 第 1 轮 · 赛事调研与选题

- **用户**：准备参加麦当劳程序员创意开发大赛，先看看赛事规则与 MCP 文档。
- **WorkBuddy 执行**：抓取活动仓库 `M-China/mcd-developer-innovation-challenge` 与 `open.mcd.cn/mcp/doc`；分析活动仓库 `RANKING.md` 榜单，统计已有竞品选题分布。
- **产出**：结论——榜单头部高度集中在「单人热量/营养」「单人省钱/券」「单人积分运营」「通用点餐助手」四个饱和方向（已有 8~16★ 的头部队列），而**多人场景几乎空白**。据此推荐选题 **「麦麦拼单官」**。

### 第 2 轮 · 形态与目标确认

- **用户**：（1）采纳推荐；（2）交互形态就用 **WorkBuddy Skill**；（3）**要冲 WorkBuddy 专项积分**。
- **WorkBuddy 执行**：确认交付形态为 Skill 包 + 参赛必需文件清单；写入 `PLAN.md` 决策记录。
- **产出**：`mcd-group-order-mate/` 目录骨架、方案文档 `PLAN.md` 落地。

### 第 3 轮 · Skill 骨架与算法实现

- **WorkBuddy 执行**：编写 `SKILL.md`（编排主入口）、`references/mcp-tool-map.md`、`references/output-templates.md`；实现 `scripts/mcd_utils.py`、`scripts/coupon_optimizer.py`、`scripts/split_bill.py`。
- **产出**：Skill 骨架可通过 `quick_validate.py` 校验；脚本冒烟测试通过。

### 第 4 轮 · 领券与安装指导

- **用户**：帮我领一点券；安装步骤我来操作，你告诉我怎么装。
- **WorkBuddy 执行**：经用户同意后调用 `auto-bind-coupons`（写操作，先征得同意）；输出连接器配置 + 技能目录复制的分步操作指令。

### 第 5 轮 · 首次实跑（Skill 触发验证）

- **用户**：上手测试，截图反馈 Skill 是否被正确触发。
- **WorkBuddy 执行**：实跑 `query-nearby-stores` → `query-meals` → `query-store-coupons` → `calculate-price` → `split_bill`。
- **产出**：Skill 被正确触发，全链路跑通。

### 第 6 轮 · ★ 券规则实测（改写算法核心）

- **WorkBuddy 执行**：用真实券做 **10 次只读 `calculate-price` 实验**，逐条验证券的使用约束。
- **产出**：挖出官方文档未记载的核心规则 —— **一单只能用一张券**（`600022 暂不支持多张券使用`），并确认券商品码独立于菜单码（错用报 `600012`）、券码必须与券 ID 成对（缺码报 `600010`）、一张券只覆盖 1 份等 9 条规则。
- **影响**：项目技术核心据此**从"单内多券组合"重写为"券与拆单优化"**。

### 第 7 轮 · 算法重写为拆单优化

- **用户**：写成拆单优化。
- **WorkBuddy 执行**：把「券 × 需求份」建模为**二分图最大权匹配**，用**匈牙利算法**求精确最优的用券组合；派生多套候选拆法，再以 `calculate-price` 为唯一价格预言机逐单真实试算取最低总价。
- **产出**：`coupon_optimizer.py` 的 `split` / `select` 重写完成；与暴力枚举**对拍 400/400 一致**；60 份需求 × 12 张券耗时 0.26s。

### 第 8 轮 · 新增 Phase 2.5「券机会挖掘」

- **用户**：Phase 2.5 加！
- **WorkBuddy 执行**：在 `SKILL.md` 插入 Phase 2.5（按实际下单时刻筛券 + 真实试算 + 净收益排序），并新增**合规红线**"禁止诱导加购"。
- **产出**：`coupon-opportunities` 必须传 `demandMenuCodes`——不替代用户已购商品的券一律不计入省额。

### 第 9~13 轮 · 5 次端到端实跑（用户提供链接 + App 订单截图）

- **用户**：连续 5 次提供 WorkBuddy 分享链接与麦当劳 App 订单截图，要求核查实跑结果。
- **WorkBuddy 执行**：每轮逐项核查订单金额、规格、特调、分账，并修复暴露的 bug。
- **产出**：**3 次真实下单闭环成功**（详见第四节）。

---

## 四、真实使用证据（可核验）

### 1. 真实下单闭环（3 单）

> 场景：成都，3 人拼单，到店自取（外带）。订单均在麦当劳 App 正常生成，**实付与下单前 `calculate-price` 试算分毫不差**，App 账单与本项目分账表逐行对得上。

| # | 订单号 | 实付 | 用券 | 备注 |
|---|---|---|---|---|
| 1 | `1030193170000795583095383781` | ¥130.90 | 「薯薯任选」 | 本店有可用券 |
| 2 | `1030812770000754924732917280` | ¥84.30 | 「薯薯任选」 | 有可用券 |
| 3 | `1030893300000754924774863358` | ¥91.30 | 无 | **本店无可用券场景**（如实告知，未引导加购） |

### 2. 实跑中由 WorkBuddy 调用 MCP 实测出的规则（文档中没有）

| # | 实测结论 | 证据 |
|---|---|---|
| 1 | **一单只能用一张券** | `600022 暂不支持多张券使用` |
| 2 | 券绑定独立的"券商品码"，与菜单码不同域 | 用菜单码套券报 `600012` |
| 3 | 券码必须与券 ID 成对 | 缺券码报 `600010` |
| 4 | 券商品数量被强制为 1 | 传 `quantity=2` 仍返回 1 |
| 5 | 券有**星期 + 时段 + 渠道**三重约束 | 同一券在不同时刻可用性相反 |
| 6 | 门店打烊后菜单接口报 `600057` | 下单前必须校验营业状态 |
| 7 | `takeWayCode` 不在工具 schema 枚举中 | 只能从 `calculate-price` 的 `takeWayList` 取 |
| 8 | 同一特调组多个选项**共用同一个 `code`** | 可乐"标准/去冰/多冰/少冰"的 `code` 全是 `200002`，只能靠 `selectedKey` 区分 |
| 9 | 订单状态字段与文档不一致 | `orderStatus` 是中文串，`status` 数字与 schema 枚举对不上 |

### 3. 实跑暴露并由 WorkBuddy 修复的 Bug

1. **券优化提前砍搜索空间** → 改为匈牙利精确最优
2. **`coupon-opportunities` 会放行"多买多省"**（合规隐患）→ 强制传 `demandMenuCodes`，不替代已购商品的券不计入省额
3. **分账时券优惠摊错人**（共享商品的券被按个人小计摊，导致他人补贴持券者）→ 新增 `couponDiscounts` 逐笔声明优惠归属商品
4. **"省了多少"混用两个口径** → 区分**订单口径**（与 App 账单对齐）与**用户口径**（对外讲省额）
5. **共享小食与套餐内小食重复** → 方案阶段先与套餐对账，冲突时把两条路都摆给用户选
6. **最终方案未做预算对账**（漏算共享项均摊）→ 新增"预算核对"行
7. **`coupon-availability` 连续 3 次被跳过** → 不再靠"加规则"，改为**架构级修复**：把有效期/星期/时段/渠道判定收进 `coupon-opportunities` 内部，只要跑券机会核算就不可能漏筛券

### 4. 机械可核验的产出

| 项 | 位置 |
|---|---|
| Skill 主入口 | `SKILL.md`（YAML frontmatter 含 `agent_created: true`） |
| 本地脚本 | `scripts/mcd_utils.py`、`scripts/coupon_optimizer.py`、`scripts/split_bill.py` |
| 工具速查与实测坑 | `references/mcp-tool-map.md` |
| 输出模板 | `references/output-templates.md` |
| Skill 校验 | `quick_validate.py` → `Skill is valid!`；三脚本 `py_compile` 全过 |

---

## 五、本项目用到的 WorkBuddy 能力

| 能力 | 在本项目中的用途 |
|---|---|
| **Skill（技能）** | 项目本体即以 WorkBuddy Skill 形式交付 |
| **自定义连接器 / MCP** | 接入麦当劳 MCP Server（`mcd-mcp`），调用 8 个主链路 Tool |
| **Agent 模式** | 需求收集、门店定位、菜单/券拉取、真实计价、下单核对的全流程编排 |
| **本地脚本执行** | 组合数学（匈牙利匹配）与分账（最大余额法取整）在本地完成 |
| **多轮对话迭代** | 5 轮实跑反馈 → 逐轮定位并修复真实 bug |
| **联网检索** | 查赛事规则、MCP 文档、榜单竞品分布 |

---

## 六、声明

1. 本项目由腾讯 WorkBuddy 智能体全程参与开发，开发过程真实、可复现。
2. 本文件仅用于核验 WorkBuddy 联动活动的奖励条件，**不含任何真实 Token、密钥、账号或他人个人信息**。
3. 本文件中出现的订单号、门店与金额均来自真实接口返回，仅作开发过程佐证；相关订单均已过期失效，不涉及任何他人权益。
4. 项目输出仅供参考，不构成营养或其他专业建议。

> 附：本项目的合规立场 —— 优化范围严格限定在**用户已确定要买的商品**上，不对未购买的商品做任何优惠匹配，不出现"多买多省""凑单更划算"等诱导加购表述，亦不主动建议提高预算。
