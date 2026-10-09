#!/usr/bin/env python3
"""券与拆单优化。

## 为什么是"拆单"而不是"单内多券"

2026-10-09 用 `calculate-price` 只读试算实测确认：**一张订单只能用一张券**
（两券同单 → `600022 暂不支持多张券使用`）。且券绑定的是独立的**券商品码**、
**一张券只覆盖一份**。

因此拼单真正的省钱空间不是"一单内多张券组合"，而是**拆单**：把 N 个人的需求与
M 张券拆成若干单，每单至多用一张券，求总价最低的拆分方案。

## 数学模型

- **需求侧**：把要买的商品按数量展开成"份"(unit)，每份带菜单商品码与单价
- **券侧**：每张券只能用在**它自己的券商品**上，一张券只覆盖一份
- **收益**：券价与该券可替代的菜单品原价之差（`saving = 原价 − 券价`）
- **合规硬约束**：只允许"券商品可替代用户已确定要买的菜单品"的券参与优化。
  不替代任何已购商品的券 = 诱导加购，赛事规则明令禁止，一律不纳入

于是问题化为一个**二分图最大权匹配**：

    左 = 券（每张最多配 1 份），右 = 需求份（每份最多配 1 张券），边权 = 省额
    → 用匈牙利算法（O(n³)）求精确最优的用券组合

匹配只决定"用哪些券、各顶替哪一份需求"；**拆单是匹配之后的机械动作**。

## 两种打包策略（规则未公开，两种都探测）

- `coupon_only`    ：券单只放券商品，无券商品另起一单（最保守）
- `minimal_orders` ：把无券商品塞进券单，使总单数 = max(券数, 1)（单数最少）

理论上两者总价相同（券只作用于券商品），但官方未公开"券单能否夹杂其他商品"，
所以**两种都生成候选，交给 `calculate-price` 判定**。

## 子命令

    split  —— 生成多套候选拆单方案（纯组合数学），输出可直接喂给 calculate-price 的
              逐单 items；受 `maxProbes` 调用预算约束
    select —— 消费 calculate-price 的真实试算结果，取总价最低的方案并算"省了多少"

启发式只决定"探测哪一套"，**真实价格一律以 calculate-price 返回为准**，禁止猜测。

## 用法

    python coupon_optimizer.py split  --input split_input.json
    python coupon_optimizer.py select --input probe_results.json

split 输入：

    {
      "demand": [
        {"name": "巨无霸三件套", "menuCode": "9900011056",
         "unitPriceFen": 3750, "quantity": 2, "for": ["A", "B"]},
        {"name": "薯条", "menuCode": "4810", "unitPriceFen": 1400, "quantity": 1, "for": ["C"]}
      ],
      "coupons": [
        {"couponId": "71F9...", "couponCode": "MCD69V...",
         "title": "麦旋风任选", "couponProductCode": "9900014239",
         "couponProductName": "麦旋风任选1",
         "substitutesMenuCodes": ["9900008754"],
         "payFen": 990, "available": true}
      ],
      "maxProbes": 20
    }

select 输入：

    {
      "baselineFen": 6700,
      "candidates": [
        {"label": "拆 2 单...", "orderTotalsFen": [990, 990]}
      ]
    }
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_MAX_PROBES = 20
DEFAULT_PACKINGS: Tuple[str, ...] = ("minimal_orders", "coupon_only")
PACKING_LABELS = {
    "minimal_orders": "最少单数",
    "coupon_only": "券单独立",
}
SINGLE_COUPON_FALLBACKS = 3  # 除最优方案外，额外给出 Top-N「只用一张券」的备选
MAX_UNITS = 200               # 需求展开后的最大份数，防异常输入炸矩阵
_HUGE = 10 ** 15


# --------------------------------------------------------------------------- #
# 需求展开与券匹配基础
# --------------------------------------------------------------------------- #

def expand_units(demand: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把需求按数量展开成"份"。

    每份是独立可被一张券顶替的最小单位。`for` / `assignees` 给出该份归属的人，
    用于后续分账（数量多于人数时按序循环取）。
    """
    units: List[Dict[str, Any]] = []
    for d in demand or []:
        if not d:
            continue
        try:
            qty = int(d.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1
        if qty <= 0:
            continue
        raw_price = d.get("unitPriceFen")
        try:
            price = int(raw_price) if raw_price is not None else None
        except (TypeError, ValueError):
            price = None

        persons = d.get("for") or d.get("assignees") or []
        if isinstance(persons, str):
            persons = [persons]

        menu_code = str(d.get("menuCode") or "")
        name = d.get("name") or menu_code or "未命名"
        for k in range(qty):
            person = persons[k % len(persons)] if persons else None
            units.append({
                "menuCode": menu_code,
                "name": name,
                "unitPriceFen": price,
                "person": person,
                "sourceItem": d.get("name") or name,
            })
    return units[:MAX_UNITS]


def coupon_targets(coupon: Dict[str, Any]) -> set:
    """该券的券商品可以顶替哪些菜单商品码。"""
    targets = coupon.get("substitutesMenuCodes") or coupon.get("substitutes") or []
    if isinstance(targets, str):
        targets = [targets]
    single = coupon.get("replacesMenuCode")
    if single:
        targets = list(targets) + [single]
    return {str(t) for t in targets if t}


def coupon_saving(coupon: Dict[str, Any], unit: Dict[str, Any]) -> int:
    """用该券顶替这一份需求能省多少（分）。数据不足时返回 0（视为无收益）。"""
    pay = coupon.get("payFen")
    if pay is None:
        return 0
    base = unit.get("unitPriceFen")
    if base is None:
        base = coupon.get("listFen")
    if base is None:
        return 0
    try:
        return int(base) - int(pay)
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------- #
# 匈牙利算法（最大权匹配 → 最小代价完美匹配）
# --------------------------------------------------------------------------- #

def _hungarian_min(cost: List[List[int]]) -> Tuple[List[int], int]:
    """方阵最小代价完美匹配（JV / e-maxx O(n³)）。

    返回 `(row -> col 的下标数组, 总代价)`；未匹配的行给出 -1。
    """
    n = len(cost)
    if n == 0:
        return [], 0

    u = [0] * (n + 1)
    v = [0] * (n + 1)
    p = [0] * (n + 1)
    way = [0] * (n + 1)

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [_HUGE] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            row = cost[i0 - 1]
            delta = _HUGE
            j1 = 0
            for j in range(1, n + 1):
                if used[j]:
                    continue
                cur = row[j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break

    assignment = [-1] * n
    for j in range(1, n + 1):
        if p[j]:
            assignment[p[j] - 1] = j - 1
    total = sum(cost[i][j] for i, j in enumerate(assignment) if j >= 0)
    return assignment, total


def max_weight_matching(weights: List[List[int]], n_cols: int) -> Tuple[List[Tuple[int, int]], int]:
    """最大权匹配。`weights[i][j]` 为第 i 张券顶替第 j 份需求的省额（分）。

    通过补零扩成方阵后取负求最小代价。**边权为 0 表示"不可顶替"**（无收益），
    因所有边权非负，最优解不会用 0 边换掉正收益边。
    """
    n_rows = len(weights)
    if n_rows == 0 or n_cols == 0:
        return [], 0
    n = max(n_rows, n_cols)
    cost = [[0] * n for _ in range(n)]
    for i in range(n_rows):
        row = weights[i]
        for j in range(n_cols):
            cost[i][j] = -int(row[j])

    assignment, total = _hungarian_min(cost)
    matches: List[Tuple[int, int]] = []
    for i, j in enumerate(assignment):
        if i < n_rows and 0 <= j < n_cols and weights[i][j] > 0:
            matches.append((i, j))
    return matches, -total


def greedy_matching(weights: List[List[int]],
                    n_cols: int,
                    unit_cap: Optional[List[int]] = None) -> List[Tuple[int, int]]:
    """按省额降序贪心匹配，作为最大权匹配的对照方案（探测多样性用）。"""
    edges = []
    for i, row in enumerate(weights):
        for j in range(n_cols):
            if row[j] > 0:
                edges.append((row[j], i, j))
    edges.sort(key=lambda e: (-e[0], e[1], e[2]))

    taken_coupon = set()
    taken_col = set()
    matches: List[Tuple[int, int]] = []
    for _w, i, j in edges:
        if i in taken_coupon or j in taken_col:
            continue
        taken_coupon.add(i)
        taken_col.add(j)
        matches.append((i, j))
    return matches


# --------------------------------------------------------------------------- #
# 拆单方案组装
# --------------------------------------------------------------------------- #

def _coupon_item(coupon: Dict[str, Any]) -> Dict[str, Any]:
    """券商品在 calculate-price / create-order 里的 items 条目。

    必须 `couponId` + `couponCode` **成对**传，且用**券商品码**（不是菜单码）。
    """
    return {
        "productCode": coupon.get("couponProductCode"),
        "quantity": 1,
        "name": coupon.get("couponProductName") or coupon.get("title"),
        "couponId": coupon.get("couponId"),
        "couponCode": coupon.get("couponCode"),
    }


def _regular_items(units: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把无券的份按菜单码合并成 items 条目。"""
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for u in units:
        code = str(u.get("menuCode"))
        if code not in merged:
            merged[code] = {
                "productCode": code,
                "quantity": 0,
                "name": u.get("name"),
            }
            order.append(code)
        merged[code]["quantity"] += 1
    return [merged[c] for c in order]


def _unique(seq: Iterable[Any]) -> List[Any]:
    out: List[Any] = []
    for x in seq:
        if x is not None and x not in out:
            out.append(x)
    return out


def build_orders(units: List[Dict[str, Any]],
                 matches: Sequence[Tuple[int, int]],
                 coupons: List[Dict[str, Any]],
                 packing: str) -> List[Dict[str, Any]]:
    """把匹配结果组装成若干张订单。

    `matches` 元素为 `(券下标, 需求份下标)`。
    """
    matched_units = {j for _i, j in matches}

    # 券单按省额降序排，让"最划算的券"排在第一单
    ordered = sorted(matches, key=lambda m: -coupon_saving(coupons[m[0]], units[m[1]]))
    orders: List[Dict[str, Any]] = [
        {"coupon": coupons[i], "couponUnit": units[j], "regularUnits": []}
        for i, j in ordered
    ]
    leftover = [u for j, u in enumerate(units) if j not in matched_units]

    if packing == "minimal_orders" and orders:
        # 无券商品轮转塞进券单：总单数 = max(券数, 1)
        for k, u in enumerate(leftover):
            orders[k % len(orders)]["regularUnits"].append(u)
    elif leftover:
        orders.append({"coupon": None, "couponUnit": None, "regularUnits": leftover})

    if not orders:
        orders = [{"coupon": None, "couponUnit": None, "regularUnits": leftover}]

    built: List[Dict[str, Any]] = []
    for idx, o in enumerate(orders, 1):
        items: List[Dict[str, Any]] = []
        if o["couponUnit"] is not None:
            items.append(_coupon_item(o["coupon"]))
        items.extend(_regular_items(o["regularUnits"]))

        persons = _unique(
            ([o["couponUnit"]["person"]] if o["couponUnit"] else [])
            + [u["person"] for u in o["regularUnits"]]
        )
        built.append({
            "index": idx,
            "coupon": None if o["coupon"] is None else {
                "couponId": o["coupon"].get("couponId"),
                "couponCode": o["coupon"].get("couponCode"),
                "title": o["coupon"].get("title"),
            },
            "items": items,
            "assignees": persons,
        })
    return built


def _candidate(units: List[Dict[str, Any]],
               coupons: List[Dict[str, Any]],
               matches: Sequence[Tuple[int, int]],
               packing: str) -> Dict[str, Any]:
    """由一组匹配 + 打包策略生成一套候选拆单方案。"""
    orders = build_orders(units, matches, coupons, packing)
    saving = sum(coupon_saving(coupons[i], units[j]) for i, j in matches)
    coupon_titles = [coupons[i].get("title") or coupons[i].get("couponId") for i, _ in matches]

    if matches:
        label = f"拆 {len(orders)} 单 · 用 {' + '.join(str(t) for t in coupon_titles)}（省 ¥{saving / 100:.2f}）"
    else:
        label = "baseline(不用券)"

    return {
        "label": label,
        "packing": packing,
        "packingLabel": PACKING_LABELS.get(packing, packing),
        "couponCount": len(matches),
        "orderCount": len(orders),
        "heuristicSavingFen": saving,
        "heuristicSavingYuan": f"{saving / 100:.2f}",
        "orders": orders,
        "probe": [{"orderIndex": o["index"], "items": o["items"]} for o in orders],
    }


def _signature(matches: Sequence[Tuple[int, int]],
               coupons: List[Dict[str, Any]],
               packing: str) -> Tuple:
    return (packing, tuple(sorted((str(coupons[i].get("couponId")), j) for i, j in matches)))


# --------------------------------------------------------------------------- #
# split
# --------------------------------------------------------------------------- #

def split(demand: Sequence[Dict[str, Any]],
          coupons: Sequence[Dict[str, Any]],
          max_probes: int = DEFAULT_MAX_PROBES,
          packings: Sequence[str] = DEFAULT_PACKINGS) -> Dict[str, Any]:
    """生成候选拆单方案。

    步骤：过滤不可用券 → 建权值矩阵 → 最大权匹配（最优用券组合）
    → 派生候选（最优 / 贪心 / Top-N 单券 × 打包策略）→ 按预算截断。
    """
    units = expand_units(demand)
    baseline = sum(int(u.get("unitPriceFen") or 0) for u in units)

    packings = [p for p in (packings or DEFAULT_PACKINGS) if p in PACKING_LABELS] or list(DEFAULT_PACKINGS)

    usable: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    for c in coupons or []:
        if not c:
            continue
        cid = c.get("couponId")
        title = c.get("title") or cid
        if c.get("available") is False:
            excluded.append({
                "couponId": cid, "title": title,
                "reasons": c.get("unavailableReasons") or ["当前不可用"],
            })
            continue
        if not c.get("couponProductCode"):
            excluded.append({
                "couponId": cid, "title": title,
                "reasons": ["缺少券商品码——券商品码只能从 query-store-coupons 获取"],
            })
            continue
        if c.get("payFen") is None:
            excluded.append({
                "couponId": cid, "title": title,
                "reasons": ["缺少用券价——需先用 calculate-price 试算该券商品"],
            })
            continue
        if not coupon_targets(c):
            excluded.append({
                "couponId": cid, "title": title,
                "reasons": ["未声明可替代的菜单商品——不替代已购商品即为诱导加购，不纳入优化"],
            })
            continue
        usable.append(c)

    base_result: Dict[str, Any] = {
        "mode": "no-coupon" if not usable else "matched",
        "baselineTotalFen": baseline,
        "baselineTotalYuan": f"{baseline / 100:.2f}",
        "demandUnits": len(units),
        "distinctMenuCodes": len({u["menuCode"] for u in units}),
        "couponsConsidered": len(usable),
        "couponsExcluded": excluded,
        "maxProbes": max_probes,
    }

    if not units or not usable:
        candidates = [_candidate(units, usable, [], "minimal_orders")]
        base_result["optimalSavingFen"] = 0
        base_result["optimalSavingYuan"] = "0.00"
        base_result["candidates"] = candidates
        base_result["note"] = (
            "无可用券或无需求，只能按原价下单。" if usable
            else "没有同时满足『券可用 + 券商品可替代已购商品』的券。"
        )
        return base_result

    # 权值矩阵：券 × 需求份
    weights: List[List[int]] = []
    for c in usable:
        targets = coupon_targets(c)
        row = []
        for u in units:
            if u["menuCode"] in targets:
                row.append(max(0, coupon_saving(c, u)))
            else:
                row.append(0)
        weights.append(row)

    optimal, _total = max_weight_matching(weights, len(units))
    greedy = greedy_matching(weights, len(units))

    candidates: List[Dict[str, Any]] = []
    seen = set()

    def add(matches: Sequence[Tuple[int, int]], packing: str) -> None:
        sig = _signature(matches, usable, packing)
        if sig in seen:
            return
        seen.add(sig)
        candidates.append(_candidate(units, usable, matches, packing))

    for packing in packings:
        add(optimal, packing)
    add(greedy, packings[0])

    # 单券兜底：若多券组合被接口拒绝（如券只能单独成单的限制），仍有可用方案
    rated = sorted(
        ((i, max(weights[i]) if weights[i] else 0) for i in range(len(usable))),
        key=lambda t: -t[1],
    )
    for i, w in rated[:SINGLE_COUPON_FALLBACKS]:
        if w <= 0:
            continue
        j = max(range(len(units)), key=lambda k: weights[i][k])
        add([(i, j)], "coupon_only")

    add([], packings[0])  # baseline 也要真实试算，作为"省了多少"的基准

    candidates.sort(key=lambda c: (-c["heuristicSavingFen"], c["orderCount"], c["couponCount"]))

    if len(candidates) > max_probes:
        kept = candidates[: max_probes - 1]
        base = next((c for c in candidates if c["couponCount"] == 0), None)
        ids = {id(c) for c in kept}
        if base is not None and id(base) not in ids:
            kept.append(base)
        candidates = kept[:max_probes]

    base_result["optimalSavingFen"] = sum(coupon_saving(usable[i], units[j]) for i, j in optimal)
    base_result["optimalSavingYuan"] = f"{base_result['optimalSavingFen'] / 100:.2f}"
    base_result["optimalCouponCount"] = len(optimal)
    base_result["candidates"] = candidates
    base_result["note"] = (
        "候选用匈牙利算法求『券 × 需求份』最大权匹配得到理论最优，再派生若干备选；"
        "全部需用 calculate-price 逐单真实试算，真实总价以接口返回为准。"
    )
    return base_result


# --------------------------------------------------------------------------- #
# select
# --------------------------------------------------------------------------- #

def _candidate_total(cand: Dict[str, Any]) -> Optional[int]:
    """从一条试算结果里取总价（分）。任一张单缺失/报错则整体作废。"""
    if cand.get("ok") is False:
        return None
    totals = cand.get("orderTotalsFen")
    if totals is None:
        single = cand.get("totalFen")
        totals = [single] if single is not None else None
    if not totals or any(t is None for t in totals):
        return None
    try:
        return sum(int(t) for t in totals)
    except (TypeError, ValueError):
        return None


def select(candidates: Sequence[Dict[str, Any]],
           baseline_fen: Optional[int] = None) -> Dict[str, Any]:
    """从 calculate-price 的真实试算结果里选总价最低的拆单方案。"""
    valid: List[Tuple[int, Dict[str, Any]]] = []
    skipped: List[Dict[str, Any]] = []
    for c in candidates or []:
        total = _candidate_total(c)
        if total is None:
            skipped.append({
                "label": c.get("label"),
                "reason": c.get("error") or "缺少有效的 orderTotalsFen",
            })
            continue
        valid.append((total, c))

    if not valid:
        return {"ok": False, "error": "没有任何有效的试算结果", "skipped": skipped}

    valid.sort(key=lambda t: t[0])
    best_total, best = valid[0]

    out: Dict[str, Any] = {
        "ok": True,
        "best": {
            "label": best.get("label"),
            "couponCount": best.get("couponCount"),
            "orderCount": best.get("orderCount") or len(best.get("orderTotalsFen") or []),
            "totalFen": best_total,
            "totalYuan": f"{best_total / 100:.2f}",
            "orders": best.get("orders"),
        },
        "ranking": [
            {
                "label": c.get("label"),
                "totalFen": t,
                "totalYuan": f"{t / 100:.2f}",
                "orderCount": c.get("orderCount") or len(c.get("orderTotalsFen") or []),
            }
            for t, c in valid
        ],
        "skipped": skipped,
    }

    base = baseline_fen
    if base is None:
        for t, c in valid:
            if not c.get("couponCount"):
                base = t
                break
    if base is not None:
        saved = int(base) - best_total
        out["baselineFen"] = int(base)
        out["baselineYuan"] = f"{int(base) / 100:.2f}"
        out["savedFen"] = saved
        out["savedYuan"] = f"{saved / 100:.2f}"
        out["savedPercent"] = round(saved / int(base) * 100, 2) if base else 0.0
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _read(args: argparse.Namespace) -> Any:
    if args.input:
        with open(args.input, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return json.load(sys.stdin)


def _emit(obj: Any) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="券与拆单优化")
    sub = parser.add_subparsers(dest="action", required=True)

    p = sub.add_parser("split", help="生成候选拆单方案（最大权匹配 + 打包策略）")
    p.add_argument("--input")

    p = sub.add_parser("select", help="从真实试算结果中选总价最低的拆单方案")
    p.add_argument("--input")

    args = parser.parse_args(argv)
    payload = _read(args)

    if args.action == "split":
        _emit(split(
            demand=payload.get("demand") or [],
            coupons=payload.get("coupons") or [],
            max_probes=int(payload.get("maxProbes") or DEFAULT_MAX_PROBES),
            packings=payload.get("packings") or DEFAULT_PACKINGS,
        ))
    else:
        _emit(select(
            candidates=payload.get("candidates") or [],
            baseline_fen=payload.get("baselineFen"),
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
