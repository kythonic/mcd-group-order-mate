#!/usr/bin/env python3
"""多人拼单分账。

把一笔订单拆到每个人头上，处理四件容易扯皮的事：

1. 个人商品 vs 共享小食——共享部分怎么摊
2. 配送费 / 餐具费——均摊还是按小计比例
3. 优惠（券 + 满减）——归发起人，还是按各自小计比例分摊
4. ★ 券优惠的**归属商品**——券作用在谁的头上，优惠就该跟着谁摊

第 4 点是关键：一张券的优惠**不是凭空产生的**，它作用在某一个具体商品上。
所以优惠应该**跟随那个商品的归属规则**：

- 券作用在**共享小食**上 → 按 `shared` 规则摊（默认 even，三人均分）
- 券作用在**某个人的个人商品**上 → 全额归该人（B、C 不该蹭 A 的券）

如果一律用全局 `proportional`（按个人小计比例），会出现两种都说不清的情况：
把 A 个人商品上的券优惠摊给了 B、C；或把共享商品的券优惠按个人小计不均等地摊。
因此用可选的 `couponDiscounts` 逐笔声明"这笔优惠作用在哪"，比一个全局规则更准。

金额一律用「分」做整数运算，并用**最大余额法**取整，保证
`sum(每人应付) == 订单总额`，不会出现"少一分钱"的尴尬。

用法：

    python split_bill.py --input bill.json

输入：

    {
      "participants": [
        {"name": "A", "items": [{"name": "巨无霸三件套", "unitPrice": 37.5, "quantity": 1}]},
        {"name": "B", "items": [{"name": "麦辣鸡腿汉堡三件套", "unitPrice": 33.5, "quantity": 1}]}
      ],
      "sharedItems": [{"name": "麦乐鸡", "unitPrice": 14.5, "quantity": 1}],
      "fees": {"delivery": 9.0, "tableware": 1.0},
      "discount": 33.5,
      "couponDiscounts": [
        {"label": "薯薯任选", "amount": 4.6, "target": "shared"}
      ],
      "rule": {
        "shared": "even",
        "fee": "even",
        "discount": "proportional",
        "initiator": "A"
      }
    }

rule 取值：
    shared   : even | by_subtotal      （默认 even）
    fee      : even | by_subtotal      （默认 even）
    discount : proportional | initiator | even   （默认 proportional，仅作用于全局 discount）

couponDiscounts[].target 取值：
    shared    跟随 `shared` 规则摊（券作用在共享小食上时用）
    even      全员均分
    initiator 归发起人
    <姓名>    归该参与人（券作用在其个人商品上时用）
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

FEN = Decimal("100")


def to_fen(value: Any) -> int:
    if value is None or value == "":
        return 0
    return int((Decimal(str(value)) * FEN).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def to_yuan(fen: int) -> str:
    return f"{Decimal(fen) / FEN:.2f}"


def allocate(total_fen: int, weights: List[Decimal]) -> List[int]:
    """把 total_fen 按权重分配到 n 份，使用最大余额法，保证合计精确等于 total_fen。

    权重全为 0 时退化为等分。
    """
    n = len(weights)
    if n == 0:
        return []

    sign = -1 if total_fen < 0 else 1
    amount = abs(total_fen)

    w = [Decimal(x) if x is not None else Decimal(0) for x in weights]
    if sum(w) <= 0:
        w = [Decimal(1)] * n

    total_weight = sum(w)
    raw = [Decimal(amount) * x / total_weight for x in w]
    floors = [int(r.to_integral_value(rounding="ROUND_FLOOR")) for r in raw]
    remainder = amount - sum(floors)

    order = sorted(range(n), key=lambda i: (raw[i] - floors[i]), reverse=True)
    for k in range(remainder):
        floors[order[k % n]] += 1

    return [sign * f for f in floors]


def _sum_items(items: List[Dict[str, Any]]) -> int:
    total = 0
    for it in items or []:
        total += to_fen(it.get("unitPrice")) * int(it.get("quantity") or 1)
    return total


def split(bill: Dict[str, Any]) -> Dict[str, Any]:
    participants = bill.get("participants") or []
    shared_items = bill.get("sharedItems") or []
    fees = bill.get("fees") or {}
    rule = bill.get("rule") or {}

    if not participants:
        return {"ok": False, "error": "participants 不能为空"}

    names = [p.get("name") or f"成员{i + 1}" for i, p in enumerate(participants)]
    n = len(names)
    personal = [_sum_items(p.get("items")) for p in participants]
    subtotal = sum(personal)
    shared_fen = _sum_items(shared_items)
    delivery_fen = to_fen(fees.get("delivery"))
    tableware_fen = to_fen(fees.get("tableware"))
    fee_fen = delivery_fen + tableware_fen
    legacy_discount_fen = to_fen(bill.get("discount"))

    personal_weights = [Decimal(x) for x in personal]
    equal_weights = [Decimal(1)] * n

    # --- 共享小食 ---
    shared_rule = rule.get("shared", "even")
    shared_weights = personal_weights if shared_rule == "by_subtotal" else equal_weights
    shared_alloc = allocate(shared_fen, shared_weights)

    # --- 配送费 / 餐具费 ---
    fee_rule = rule.get("fee", "even")
    fee_weights = personal_weights if fee_rule == "by_subtotal" else equal_weights
    fee_alloc = allocate(fee_fen, fee_weights)

    # --- 全局优惠（无归属信息时使用，保持向后兼容）---
    discount_rule = rule.get("discount", "proportional")
    if discount_rule == "initiator":
        initiator = rule.get("initiator") or names[0]
        if initiator not in names:
            return {"ok": False, "error": f"initiator '{initiator}' 不在参与者名单中"}
        legacy_alloc = [0] * n
        legacy_alloc[names.index(initiator)] = legacy_discount_fen
    elif discount_rule == "even":
        legacy_alloc = allocate(legacy_discount_fen, equal_weights)
    else:
        legacy_alloc = allocate(legacy_discount_fen, personal_weights)

    # --- ★ 逐笔券优惠：跟随"它作用的那个商品"的归属规则 ---
    coupon_discounts = bill.get("couponDiscounts") or []
    coupon_alloc = [0] * n
    coupon_detail: List[Dict[str, Any]] = []
    coupon_total_fen = 0
    for cd in coupon_discounts:
        if not isinstance(cd, dict):
            continue
        amt = to_fen(cd.get("amount"))
        label = cd.get("label") or "券优惠"
        target = str(cd.get("target") or "shared")

        if target == "shared":
            alloc = allocate(amt, shared_weights)
        elif target == "even":
            alloc = allocate(amt, equal_weights)
        elif target == "initiator":
            initiator = rule.get("initiator") or names[0]
            if initiator not in names:
                return {"ok": False, "error": f"couponDiscounts 的 initiator '{initiator}' 不在参与者名单中"}
            alloc = [0] * n
            alloc[names.index(initiator)] = amt
        elif target in names:
            alloc = [0] * n
            alloc[names.index(target)] = amt
        else:
            return {
                "ok": False,
                "error": f"couponDiscounts target '{target}' 无效：应为 shared / even / initiator / 参与人姓名",
            }

        for i in range(n):
            coupon_alloc[i] += alloc[i]
        coupon_total_fen += amt
        coupon_detail.append({
            "label": label,
            "target": target,
            "amountFen": amt,
            "amountYuan": to_yuan(amt),
            "allocFen": alloc,
        })

    discount_alloc = [legacy_alloc[i] + coupon_alloc[i] for i in range(n)]
    discount_fen = legacy_discount_fen + coupon_total_fen

    breakdown: List[Dict[str, Any]] = []
    for i, name in enumerate(names):
        payable = personal[i] + shared_alloc[i] + fee_alloc[i] - discount_alloc[i]
        breakdown.append({
            "name": name,
            "personalFen": personal[i],
            "sharedFen": shared_alloc[i],
            "feeFen": fee_alloc[i],
            "discountFen": discount_alloc[i],
            "couponDiscountFen": coupon_alloc[i],
            "payableFen": payable,
            "personalYuan": to_yuan(personal[i]),
            "sharedYuan": to_yuan(shared_alloc[i]),
            "feeYuan": to_yuan(fee_alloc[i]),
            "discountYuan": to_yuan(discount_alloc[i]),
            "couponDiscountYuan": to_yuan(coupon_alloc[i]),
            "payableYuan": to_yuan(payable),
        })

    total_fen = subtotal + shared_fen + fee_fen - discount_fen
    sum_payable = sum(b["payableFen"] for b in breakdown)

    lines = ["【麦麦拼单分账】", f"商品合计  ¥{to_yuan(subtotal + shared_fen)}"]
    if delivery_fen:
        lines.append(f"配送费    ¥{to_yuan(delivery_fen)}")
    if tableware_fen:
        lines.append(f"餐具费    ¥{to_yuan(tableware_fen)}")
    if discount_fen:
        lines.append(f"优惠      -¥{to_yuan(discount_fen)}")
    for cd in coupon_detail:
        target_label = {
            "shared": "共享小食·按共享规则摊",
            "even": "全员均分",
            "initiator": f"归发起人 {rule.get('initiator') or names[0]}",
        }.get(cd["target"], f"归 {cd['target']}")
        lines.append(f"  ├ {cd['label']} -¥{cd['amountYuan']}（{target_label}）")
    lines.append(f"应付合计  ¥{to_yuan(total_fen)}")
    lines.append("─" * 16)
    for b in breakdown:
        lines.append(f"{b['name']}  ¥{b['payableYuan']}")
    lines.append("─" * 16)
    lines.append(f"合计      ¥{to_yuan(sum_payable)}")

    return {
        "ok": True,
        "currency": "CNY",
        "rule": {
            "shared": shared_rule,
            "fee": fee_rule,
            "discount": discount_rule,
            "initiator": rule.get("initiator"),
        },
        "totals": {
            "personalFen": subtotal,
            "sharedFen": shared_fen,
            "feeFen": fee_fen,
            "discountFen": discount_fen,
            "globalDiscountFen": legacy_discount_fen,
            "couponDiscountFen": coupon_total_fen,
            "totalFen": total_fen,
            "totalYuan": to_yuan(total_fen),
        },
        "couponDiscounts": coupon_detail,
        "breakdown": breakdown,
        "check": {
            "sumPayableFen": sum_payable,
            "expectedFen": total_fen,
            "matches": sum_payable == total_fen,
        },
        "aaText": "\n".join(lines),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="多人拼单分账")
    parser.add_argument("--input", help="输入 JSON 文件；省略则从 stdin 读取")
    args = parser.parse_args(argv)

    if args.input:
        with open(args.input, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    else:
        payload = json.load(sys.stdin)

    result = split(payload)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
