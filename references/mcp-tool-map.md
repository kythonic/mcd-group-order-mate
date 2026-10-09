# 麦当劳 MCP 工具速查

供 `SKILL.md` 编排时按需查阅。**标注 ★ 的为本项目实测结论**，非官方文档原文。

## Server 接入

| 项 | 值 |
|---|---|
| 地址 | `https://mcp.mcd.cn` |
| 协议 | Streamable HTTP |
| 鉴权 | 请求头 `Authorization: Bearer <MCD_MCP_TOKEN>` |
| 限流 | 600 次/分钟 |
| 错误码 | `401` Token 无效或过期；`429` 触发限流 |

## 取餐方式 → 调用参数映射

这张表贯穿全流程，**所有工具调用的 `beType` / `orderType` / `beCode` 都由它决定**。

| 取餐方式 | beType | orderType | beCode | 门店来源 |
|---|---|---|---|---|
| 到店自取 | 1 | 1 | 不传 | `query-nearby-stores` |
| 麦乐送到家 | 2 | 2 | 必传 | `delivery-query-stores` |
| 得来速 | 5 | 1 | 必传 | `query-nearby-stores` |
| 企业团餐 | 6 | 2 | 必传 | `delivery-query-stores` |

> ★ 到店自取场景**不要传 `beCode`**，传了会报错。

### ★ `takeWayCode`（`orderType=1` 时必传，2026-10-09 实测）

取值**只能**来自 `calculate-price` 返回的 `data.takeWayList[].code`（每次计价都会带回，**不要硬编码**）：

| 用户说法 | `code` | 官方 `subtitle` |
|---|---|---|
| 堂食 / 在店里吃 | `eat-in` | 店内用餐 |
| 外带 / 打包带走 / 自提 | `take-in-store` | 店内自提 |
| 得来速车道取餐 | `take-dt-quick` | 车道自提 |

> ⚠️ **命名有坑**：`take-in-store` 字面像"在店内"，实际是**外带**；`eat-in` 才是堂食。**按 `subtitle` 判断，不要按 code 字面猜**。

## 拼单主链路（本期核心 8 个工具）

```
query-nearby-stores   → 选门店（校验 businessStatus）
query-meals           → 全菜单 + 价格 + 标签
query-meal-detail     → 套餐组成 / 特调选项
query-store-coupons   → 本店可用券（寻优的唯一券源）
available-coupons     → 麦麦省可领券
auto-bind-coupons     → 一键领券（写操作，需用户同意）
calculate-price       → 真实计价（价格唯一可信来源）
create-order          → 下单（写操作，需用户确认）
```

## 其余工具（按场景）

| 场景 | 工具 |
|---|---|
| 外送地址 | `delivery-query-addresses`、`delivery-create-address` |
| 外送门店 | `delivery-query-stores` |
| 订单管理 | `cancel-order`、`query-order`、`order-list` |
| 我的券 | `query-my-coupons` |
| 积分账户 | `query-my-account` |
| 麦麦商城 | `mall-points-products`、`mall-product-detail`、`mall-create-order`、`mall-order-list`、`mall-order-detail` |
| 积分抽奖 | `query-lottery-info`、`draw-lottery`、`query-my-prizes` |
| 主题活动 | `campaign-calendar`、`query-party-city`、`query-party-store`、`query-partystore-date`、`query-partystore-session`、`party-order-create` |
| 团餐 | `query-meal-assistance`（助餐服务码，团餐场景必传 `gmServiceCode`） |
| 其他 | `list-nutrition-foods`、`now-time-info` |

## ★ 已知坑（2026-10-09 实测）

| # | 现象 | 处理方式 |
|---|---|---|
| 1 | 门店 `businessStatus=false` 时调 `query-meals` 报 **`600057 门店可能已关闭或不在营业时间`** | 进菜单前先用 `mcd_utils.py store-status` 过滤，只对 `usable=true` 的门店继续 |
| 2 | `query-meals` 的 `currentPrice` 单位是**元**（字符串 `"33.5"`） | 用 `mcd_utils.py yuan-to-fen` 转换 |
| 3 | `calculate-price` 返回单位是**分**（整数） | 展示前 `fen-to-yuan`，**不要手算** |
| 4 | ★ `currentPrice` 可能是**随单购价**：该餐品存在 `withOrder` 字段、`discountType` 含"随单购" | 用 `mcd_utils.py effective-price` 校正；未随单购时按 `originalPrice` 计，否则会虚假承诺低价 |
| 5 | 同一 `couponId` 可对应多个 `couponCode` | 用 `mcd_utils.py normalize-coupons` 分组去重 |
| 6 | `query-store-coupons` 与 `query-my-coupons` 语义不同：前者带回门店/渠道校验，后者仅是卡包资产 | **券寻优只用 `query-store-coupons`** |
| 7 | `query-nearby-stores` 按位置搜必须 `searchType=2` + `city` + `keyword` 同时给 | 缺一不可 |
| 8 | `calculate-price` / `create-order` 的 `items[].modification.values[].key` 规则隐晦 | 见下节「特调传参规则」 |
| 9 | 同一商品在不同接口名称可能不同（菜单 `4810`="薯条"，`calculate-price` 里="中薯条"） | 一律以 `productCode` 为准，不要用名称匹配 |
| 10 | 券的 `productCode` 与菜单 `productCode` 不同域，且**一单只能用一张券** | ★ 见下节「券使用规则」，直接决定寻优算法形态 |
| 11 | ★ 套餐在计价接口里**只传 `productCode`+`quantity` 会走默认选配**，要换套餐内商品必须传 `roundList` | 见下节「套餐换品规则」 |
| 12 | ★ **同一商品在不同套餐里的 `diffPrice` 不同**（换"高达吉士双牛堡"在 `9900015568` 是 `+¥0`、在 `9900013304` 是 `-¥1`） | 不能按菜单标价推断哪套划算，必须 `calculate-price` 真实试算 |
| 13 | `query-nearby-stores` 传 `searchType=1`（读收藏门店）且账号无收藏 → **`600050 收藏餐厅列表为空`** | 除非用户明确说"我的收藏门店"，否则一律用 `searchType=2` + `city` + `keyword` |
| 14 | ★ `create-order` 到店场景漏传 `takeWayCode` 会失败，且该值**不在**工具 schema 里枚举 | 见上「`takeWayCode`」节，从 `calculate-price` 的 `takeWayList` 取 |
| 15 | ★ `orderDetail.orderStatus` 实测返回**中文字符串**（`"待支付"`），与 schema 写的"1待支付/2配餐中…"数字枚举**不一致**；同一响应里 `status="10"`（按文档是"餐厅确认配餐中"，可订单明明是待支付） | 判断订单状态**用 `orderStatus` 文本**，不要按数字枚举写逻辑 |
| 16 | ★ 券商品可能与它替代的菜单品**规格不同**：「薯薯任选」券商品实为**大薯条**（划线 ¥16.00），而用户要买的是中薯条 `4810`（¥14.50） | 于是"省多少"有两个口径（6.10 / 4.60），**两个都要交代**，见「下单返回值」节 |

## ★ 券使用规则（2026-10-09 用 `calculate-price` 只读试算实测确认）

| # | 规则 | 证据 / 复现 |
|---|---|---|
| 1 | **一张订单只能用一张券** | 两张券同单 → `600022 暂不支持多张券使用` |
| 2 | **券绑定"券商品码"**：券的 `products[].productCode`（如麦旋风任选 `9900014239`）通常是一个**独立商品码**，与菜单里的商品码不同域 | 菜单麦旋风 `9900008754` + 该券 → `600012 促销规则不支持该商品` |
| 2b | ⚠️ 但**存在两者相同的特例**：`507411` 既是菜单"麦咖啡™美式"，也是券"9.9元中杯冰美式"的券商品"麦咖啡™冰美式中杯" | 别把"不同域"当铁律做硬校验；正确做法是**永远用券自己返回的 `products[].productCode`**，不做域判断 |
| 3 | **券商品码不带券时无效** | `9900014239` 单独计价 → `price=0`、`productList=[]` |
| 4 | **`couponId` 与 `couponCode` 必须成对传** | 只传 `couponId` → `600010 使用优惠券需要couponId和couponCode` |
| 5 | **一张券只覆盖一份** | 券商品 `quantity=2` + 1 张券 → 返回 `quantity=1`，券商品数量被强制为 1 |
| 6 | **一张券可与普通商品同单**（券只作用于券商品，其余按原价计） | 券商品 + 薯条 `4810` → 总价 = 券价 + 其他商品原价。2026-10-09 再次实测：单笔含券商品 `9900016370` + 巨无霸+板烧+麦辣鸡腿堡+麦乐鸡+麦辣鸡翅，总价 ¥112.40 = 券价 ¥9.90 + 其余 ¥102.50，**与拆成 2 单完全等价** ⇒ **券单允许夹杂普通商品，拆单非必需**（`minimal_orders` 可用）。第三次实测（真实下单）：券商品 `9900016370` + 3 份套餐 → 1 单 ¥84.30 = ¥9.90 + ¥74.40，App 账单逐行一致 |
| 7 | **券商品码只能从 `query-store-coupons` 拿** | `query-my-coupons` 不返回 `productCode` |
| 8 | **券有时段约束** | `query-my-coupons` 有效期含星期+时段，如"周一~五 10:30-23:59""周六 05:00-10:29"（早餐券） |
| 9 | **券有渠道标签** | `到店专用` / `外送专用` / 两者兼有；与取餐方式不匹配则不可用 |

> ⚠️ **这条结论改写了方案的技术核心**：**"一单内多券组合寻优"不成立**（规则 1）。拼单真正的省钱空间来自**拆单**——把 N 个人 + M 张券拆成若干单、每单用一张券，在"券商品码固定 + 每单一张券 + 券时段/渠道约束"下求**总价最低的拆分方案**。
>
> 实测样例（到店自取、无配送费，故拆单无额外成本）：
> - 麦旋风任选：`9900014239` ¥14 → **¥9.9**（省 ¥4.1）
> - 薯薯任选：`9900016370` ¥16 → **¥9.9**（省 ¥6.1）
> - 两券合单不可行 → 必须拆成 2 单，合计 ¥19.8（合单假设 ¥30.0）

## ★ 下单返回值怎么用（2026-10-09 首次真实下单实测）

`create-order` 成功后，`data.orderDetail` 里已有一批可直接引用的字段，**不要自己另算一遍**：

| 字段 | 用途 |
|---|---|
| `orderId` | 订单号（也是 `pickupCode` 的来源） |
| `payH5Url` / `payId` | 支付入口（`https://m.mcd.cn/mcp/scanToPay?orderId=…`） |
| `expirePayTime` | 支付时限（实测下单后 **15 分钟**） |
| `realTotalAmount` | **实付** —— 必须与试算价核对，不一致要报警而非默默接受 |
| `productPrice` / `totalDiscountAmount` | 商品原价 / 总优惠 —— **分账表口径以此为准** |
| `couponList[].discountAmount` | 每张券的真实优惠额 |
| `orderProductList[].comboItemList` | 套餐子项明细（用于向用户解释套餐里到底是什么） |
| `takeWay` | 取餐方式文案（如"外带"） |

## ★ "省了多少"的两个口径（券商品与菜单品规格不一致时必看）

当券商品**与它替代的菜单品规格不同**时，会出现两个都正确、但数值不同的"省额"：

| 口径 | 算法 | 实测（薯薯任选替代中薯条） | 用在哪 |
|---|---|---|---|
| **订单口径** | 券商品划线价 − 券价 | ¥16.00 − ¥9.90 = **¥6.10** | 与 App 账单/订单详情对齐（`totalDiscountAmount`） |
| **用户口径** | 被替代菜单品菜单价 − 券价 | ¥14.50 − ¥9.90 = **¥4.60** | 回答"相比原本要花的钱，实际少花多少" |

> ★ **规格差异已被 App 账单证实**（2026-10-09 第二次真实下单）：App 的订单详情把券商品展开成「薯薯任选 / 1 × **大薯条**」，商品小计 ¥90.4、已优惠 ¥6.1、合计 ¥84.3 —— 与「订单口径」逐行一致。所以下单后要拿 App 截图核对时，**用订单口径**；而对用户讲"比原计划少花多少"用**用户口径**。
>
> 两种口径下**每人均摊完全相同**（券商品净额恒为 ¥9.90），差别只在展示。要求：
> 1. **分账表的"商品原价/优惠/实付"必须用订单口径**，否则群里对着 App 账单（¥137.00 / -¥6.10 / ¥130.90）会对不上
> 2. "省了多少"另行单列，注明用的是**用户口径**及其基线
> 3. **必须显式告知规格变化**（"券给的是大薯条，比你原计划的中薯条分量更大"），不要让用户以为完全等价

## 特调传参规则（modification）

用于满足"A 不吃辣""B 去冰"这类偏好，即**组内换选项**（如"少冰"→"去冰"）。
（**换掉套餐中的整个商品**用 `roundList`，见下一节。）

`query-meal-detail` 返回的每个特调组内：

- **用户选中的特调项** → `key` 取该组的 `selectedKey`
- **用户未选中的特调项** → 若该项 `unselectedKey` 不为空，`key` 取 `unselectedKey`，**并且也必须传入**

即：**对于含 `unselectedKey` 的特调组，组内所有项都要传**——选中的用 `selectedKey`，未选中的用 `unselectedKey`。漏传会导致计价/下单失败。

### ★ 同一特调组内多个选项可能共用同一个 `code`，必须靠 `key` 区分（2026-10-10 实测）

实测「龙焰鸡腿堡三件套」第 3 轮饮料，`可乐中杯`（`3050`）的冰量组，**四个选项的 `code` 全是 `200002`**，只靠 `selectedKey` 区分：

| 选项 | `code` | `selectedKey` | `unselectedKey` |
|---|---|---|---|
| 标准（默认） | `200002` | `0-1` | 无 |
| **去冰** | `200002` | **`0-0`** | 无 |
| 多冰 | `200002` | `0-2` | 无 |
| 少冰 | `200002` | `1-1` | 无 |

所以「要一份去冰可乐」的正确传法是 **`{"code": "200002", "key": "0-0", "quantity": 1}`**：

```json
{"round":"3","comboItemList":[{"code":"3050","quantity":1,
  "modification":{"values":[{"code":"200002","key":"0-0","quantity":1}]}}]}
```

⚠️ 要点：
- **别按 `code` 猜**——同一个 `code` 对应多个语义完全不同的选项，只看 `code` 无法区分"标准 / 去冰 / 多冰"
- **别把 `key` 写死**——`0-0` 之类是随分组变化的，必须每次从 `query-meal-detail` 的 `selectedKey` 现取
- `unselectedKey` 为空（`null`）的组**只需传选中的那一项**；只有 `unselectedKey` 非空的组才要求整组都传（见上）

## 套餐换品规则（roundList）★ 2026-10-09 实测

`query-meal-detail` 的套餐返回里有一个 `rounds[]` 数组（"第 N 轮选择"，如第 1 轮选主食、第 2 轮选饮料/小食），每轮下有 `choices[]` 候选项。

**规则**：

- `calculate-price` / `create-order` 里，套餐**只传 `productCode` + `quantity` 时走默认选配**
- 要换掉套餐内的整个商品，必须在 item 上加 `roundList`：

```json
{"productCode": "9900015568", "quantity": 3,
 "roundList": [
   {"round": "1", "comboItemList": [{"code": "521316", "quantity": 1}]},
   {"round": "2", "comboItemList": [{"code": "3050",  "quantity": 1}]}
 ]}
```

- `round` 取 `rounds[].id` 的**字符串**形式（不是下标、不是 name）
- `comboItemList[].code` 取自 `rounds[].choices[].code`；`quantity` 是该轮要几份
- ★ **每个候选项都带 `diffPrice`（如 `+ ¥0` / `- ¥1` / `+ ¥1.5`）**，换品可能加价也可能减价 —— 必须按 `diffPrice` 判断性价比，不能假定同价

**实测样例（同一汉堡，两个套餐差价不同）**：

| 套餐 | 菜单价 | 换"高达吉士双牛堡"`521316` 的 `diffPrice` | 换后单价 |
|---|---|---|---|
| 精选超值随心配 `9900015568` | ¥13.9 | **+ ¥0** | ¥13.90 |
| 人气经典随心配 `9900013304` | ¥14.9 | **- ¥1** | ¥13.90 |

可见**同一个更贵的汉堡在贵的套餐里反而"减价"**。所以判断"哪套最划算"必须跑 `calculate-price` 真实试算（上表两行试算结果均为 ¥41.70 = 3×¥13.90），不能看菜单标价。

## 输出展示的强制要求

- `calculate-price` 返回的价格单位为**分**，展示前 ÷100
- 门店展示必须**完整给出 `storeCode`**；得来速场景还需给出 `beCode`
- `reservation=true` 时，`reservationTimeOptions` 的每一条都要展示（`today=true` 标星号），漏一条会导致无法预约
- 券展示按 `couponId` 分组，多张时显示"首个(共 N 张)"
