# MCP 集成说明

本文档说明「麦麦拼单官」实际调用了哪些麦当劳 MCP Server 与 Tool、调用流程如何串联，以及该集成带来的业务价值。

## 一、使用的 MCP Server

| 项 | 值 |
|---|---|
| Server | 麦当劳中国 MCP Server（`mcd-mcp`） |
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP |
| 鉴权方式 | 请求头 `Authorization: Bearer <MCD_MCP_TOKEN>` |
| 限流 | 600 次/分钟 |

配置示例见 [`mcp-config.example.json`](./mcp-config.example.json)（仅使用环境变量占位符，不含任何真实凭证）。

## 二、使用的 Tool 清单

### 主链路（核心 8 个）

| Tool | 在本项目中的作用 |
|---|---|
| `query-nearby-stores` | 定位可下单门店，并**校验营业状态** |
| `query-meals` | 拉取全菜单、价格与标签，作为选品基础 |
| `query-meal-detail` | 读取套餐组成与特调选项，用于满足"不吃辣/去冰"等偏好 |
| `query-store-coupons` | 拉取**本店可用优惠券**，是券寻优的唯一券源 |
| `available-coupons` | 查询麦麦省可领取的券，用于扩容券池 |
| `auto-bind-coupons` | 经用户同意后一键领取麦麦省券（写操作） |
| `calculate-price` | **真实计价**——券组合优劣的唯一判定依据 |
| `create-order` | 用户确认后创建订单，返回支付链接 |

### 辅助工具

- `delivery-query-addresses` / `delivery-query-stores`：外送场景的地址与门店查询（本版本以到店自取为主，外送按同一映射扩展）
- `query-meal-assistance`：企业团餐助餐服务（已调研，本版本暂未启用）
- `now-time-info`：获取服务端时间，用于判断营业时段与预约时间

### 未使用的工具及原因

积分商城、积分抽奖、主题活动（派对/品鉴会）、营养信息等 Tool 与本项目「多人拼单」的核心场景无直接关系，故未接入，以保证调用链聚焦、可控。

## 三、调用流程

```
① query-nearby-stores(beType=1, searchType=2, city, keyword)
       └─ 校验 businessStatus=true，产出 storeCode
② query-meals(storeCode, orderType=1, beType=1)
       └─ 产出候选商品与单价；用 mcd_utils 校正"随单购"价
③ query-meal-detail(storeCode, orderType=1, beType=1, code)
       └─ 读取特调选项（含套餐换品 roundList）与冰量等 modification
④ available-coupons  →（用户同意）→ auto-bind-coupons
⑤ query-store-coupons(storeCode, orderType=1, beType=1)
       └─ 产出本店可用券池；用 mcd_utils 按 couponId 归一化
⑥ now-time-info                            → 定下单时刻（券的星期/时段窗口按它筛）
⑦ calculate-price(...) × M                 → 逐券真实试算券商品价（受探测预算约束）
⑧ mcd_utils.coupon-opportunities(probes, demandMenuCodes, now, channel)
       └─ 一次调用内完成：有效期/星期/时段/渠道过滤 + 合规过滤 + 净收益排序
⑨ coupon_optimizer.split(demand, coupons)   → 最大权匹配 + 候选拆单方案（本地组合数学）
⑩ calculate-price(...) × N                 → 逐候选拆法真实试算（受探测预算约束）
⑪ coupon_optimizer.select(probeResults)    → 取真实总价最低的拆法
⑫ split_bill(...)                           → 分账（本地整数运算，券优惠按作用商品归属）
⑬ create-order(...)                         → 用户确认后下单（takeWayCode 取自计价返回的 takeWayList）
⑭ mcd_utils.order-summary(响应, 试算价)      → 核对实付 == 试算价，不一致即提示可取消
```

**流程设计要点**：组合数学与账务（⑧⑨⑪⑫⑭）留在本地脚本完成，MCP 只承担"取真实数据"与"真实计价"两件事。这样既避免把接口打爆（限流 600/分钟），也保证所有价格结论都有接口背书。

> ★ ⑧ 的设计要点：**"筛券"与"合规过滤"必须在同一次调用里完成**。券的过期/星期/时段/渠道判定无从由 `query-store-coupons` 直接给出（它只返回 `tradeDateTime` 区间），若不把可用性判断收进 `coupon-opportunities`，就很容易出现"只核了过期没有、漏掉今天不能用的券"——这既是价格错误，也会绕过合规过滤。把两者合并后，**不存在"只做合规、漏掉时段"的调用路径**。

## 四、业务价值

1. **把"多人拼单"这件麻烦事流程化**
   原本要拿计算器凑满减、按人头算钱，现在一句话交给助手完成。

2. **让优惠真正被用出来**
   麦当劳的券绑定到具体商品，且**一单只能用一张券**——"用哪张、顶替哪一份、拆成几单"直接决定最终价格。本项目把它建模成「券 × 需求份」的**二分图最大权匹配**（匈牙利算法求精确最优），并对候选拆法做真实试算，避免"手里有券却没用对"。

3. **把隐性成本显性化**
   配送费、餐具费、优惠如何在多人间分摊，提供可选规则并输出可直接转发的分账文案，减少同事之间的扯皮。

4. **对 MCP 能力的深度使用**
   单个 Tool 只能回答"有什么、多少钱"，本项目把 8 个 Tool 编排成一条完整链路，并显式处理了营业时间、随单购价、单位换算、限流、`takeWayCode` 取值、下单后金额核对等真实工程细节——这些正是"从能调到好用"之间的距离。

5. **金额全程可核对，不做任何价格承诺**
   从券试算到下单回报，金额只有两个来源：`calculate-price` 与 `create-order` 的返回。本地脚本负责把它们对齐（试算价 vs `realTotalAmount`），不一致就提示可取消订单——不存在"报一个好看的数字然后下单变价"的情况。

## 五、合规与安全

- 项目仅使用麦当劳 MCP 官方公开能力，未做任何绕过或高频压测
- 所有写操作（`auto-bind-coupons`、`create-order`）均需用户明确确认后执行
- 仓库内不含任何真实 Token、账号或个人信息，配置仅保留环境变量占位符
- 输出的餐品与价格信息均来自接口实时返回，不构成营养或任何专业建议
