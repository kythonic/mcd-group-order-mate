#!/usr/bin/env python3
"""麦当劳 MCP 数据预处理工具。

把容易算错、且属于"确定性计算"的问题从模型手里拿走：

1. 金额单位统一 —— `query-meals` 返回"元"字符串，`calculate-price` 返回"分"整数
2. "随单购"价校正 —— `currentPrice` 若依赖随单购麦金卡，普通购买应按 `originalPrice` 计
3. 券池归一化 —— 同一 `couponId` 可对应多个 `couponCode`
4. 门店营业状态校验 —— `businessStatus=false` 时后续菜单调用会报 600057
5. 券机会挖掘 —— 按"实际下单时刻 + 取餐渠道"筛券（有效期/星期/时段/渠道），
   再把试算结果整理为券机会清单；**筛券与合规核算在同一次调用内完成**
6. 下单核对 —— 把 `create-order` 的返回整理为汇报结构，并核对实付是否等于试算价

用法（全部子命令从 stdin 读 JSON，或通过 --input 指定文件）：

    python mcd_utils.py yuan-to-fen --value 33.5
    python mcd_utils.py fen-to-yuan --value 3350
    python mcd_utils.py effective-price --input meals.json
    python mcd_utils.py normalize-coupons --input coupons.json
    python mcd_utils.py store-status --input stores.json
    python mcd_utils.py coupon-availability --input coupon_ctx.json
    python mcd_utils.py coupon-opportunities --input probes.json --now "2026-10-10 00:06" --channel pickup
    python mcd_utils.py order-summary --input order_resp.json --expected-fen 13090
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional, Tuple

FEN_PER_YUAN = Decimal("100")


# --------------------------------------------------------------------------- #
# 1. 金额单位
# --------------------------------------------------------------------------- #

def yuan_to_fen(value: Any) -> int:
    """元 -> 分。入参可为数字或字符串（如 "33.5"）。四舍五入到整数分。"""
    if value is None or value == "":
        return 0
    d = Decimal(str(value).strip())
    return int((d * FEN_PER_YUAN).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fen_to_yuan(fen: Any) -> str:
    """分 -> 元，返回保留两位小数的字符串。"""
    if fen is None or fen == "":
        return "0.00"
    d = Decimal(str(fen).strip())
    return str((d / FEN_PER_YUAN).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------- #
# 2. 随单购价校正
# --------------------------------------------------------------------------- #

def effective_price(meal: Dict[str, Any]) -> Dict[str, Any]:
    """校正单个餐品的价格口径。

    麦当劳菜单的 `currentPrice` 可能是"随单购"价——即必须同时购买麦金卡/早餐卡
    才能享受的价格。这类餐品的特征是存在 `withOrder` 字段，且 `discountType`
    含"随单购"字样（如"随单购麦金卡优惠"）。

    未随单购时，实际应付应为 `originalPrice`。
    """
    current = meal.get("currentPrice")
    original = meal.get("originalPrice")
    discount_type = meal.get("discountType")
    has_bundle = meal.get("withOrder") is not None

    is_conditional = has_bundle or (
        isinstance(discount_type, str) and "随单购" in discount_type
    )

    payable = original if is_conditional else current
    if payable in (None, ""):
        payable = current or original or "0"

    return {
        "code": meal.get("code"),
        "name": meal.get("name"),
        "displayPrice": current,
        "originalPrice": original,
        "discountType": discount_type,
        "requiresWithOrder": is_conditional,
        "payableWithoutBundle": str(payable),
        "payableFenWithoutBundle": yuan_to_fen(payable),
    }


def effective_prices(meals: Dict[str, Any]) -> List[Dict[str, Any]]:
    """批量为 `query-meals` 返回的 data.meals 做价格校正。"""
    return [effective_price({**v, "code": k}) for k, v in (meals or {}).items()]


# --------------------------------------------------------------------------- #
# 3. 券池归一化
# --------------------------------------------------------------------------- #

def normalize_coupons(raw: Any) -> List[Dict[str, Any]]:
    """把 `query-store-coupons` 的原始数组按 couponId 分组去重。

    入参既可传整个响应体（自动取 data 字段），也可直接传券数组。
    返回每张券一条，含一组 couponCode 与适用的 productCode 列表。
    """
    if isinstance(raw, dict):
        raw = raw.get("data", raw)
    if not isinstance(raw, list):
        return []

    grouped: Dict[str, Dict[str, Any]] = {}
    for c in raw:
        if not isinstance(c, dict):
            continue
        cid = c.get("couponId")
        if cid is None:
            continue
        g = grouped.setdefault(cid, {
            "couponId": cid,
            "title": c.get("title"),
            "tradeDateTime": c.get("tradeDateTime"),
            "codes": [],
            "productCodes": [],
            "productNames": [],
        })
        code = c.get("couponCode")
        if code and code not in g["codes"]:
            g["codes"].append(code)
        for p in c.get("products") or []:
            pc = p.get("productCode")
            if pc and pc not in g["productCodes"]:
                g["productCodes"].append(pc)
            pn = p.get("productName")
            if pn and pn not in g["productNames"]:
                g["productNames"].append(pn)

    out: List[Dict[str, Any]] = []
    for g in grouped.values():
        g["codeCount"] = len(g["codes"])
        g["representativeCode"] = g["codes"][0] if g["codes"] else None
        out.append(g)
    return out


# --------------------------------------------------------------------------- #
# 4. 门店营业状态
# --------------------------------------------------------------------------- #

def store_status(store: Dict[str, Any]) -> Dict[str, Any]:
    """校验门店是否可下单。`businessStatus=false` 时 menu 类接口会报 600057。"""
    open_now = bool(store.get("businessStatus"))
    start = store.get("businessStartTime")
    end = store.get("businessEndTime")
    return {
        "storeCode": store.get("storeCode"),
        "storeName": store.get("storeName"),
        "beCode": store.get("beCode"),
        "businessStatus": open_now,
        "hours": f"{start}-{end}" if start or end else None,
        "usable": open_now,
        "hint": None if open_now else "门店已打烊或不在营业时间，请另选门店或改约时间",
    }


def pick_open_stores(stores: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从门店列表中筛出当前可下单的门店，按距离升序。"""
    ok = [s for s in (stores or []) if s.get("businessStatus")]
    return sorted(ok, key=lambda s: s.get("distance") or 0)


# --------------------------------------------------------------------------- #
# 5. 券机会挖掘（Phase 2.5）
# --------------------------------------------------------------------------- #

_WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]


def _parse_dt(value: Any) -> Optional[datetime]:
    """解析 'YYYY-MM-DD'、'YYYY-MM-DD HH:MM'、'YYYY-MM-DD HH:MM:SS'。"""
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _split_range(text: Any) -> Tuple[Optional[datetime], Optional[datetime]]:
    """从 '2026-10-05 10:30:00-2026-10-09 23:59:59' 抽出起止时间。"""
    if not isinstance(text, str):
        return (None, None)
    parts = re.findall(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?", text)
    if len(parts) >= 2:
        return (_parse_dt(parts[0]), _parse_dt(parts[1]))
    if len(parts) == 1:
        return (_parse_dt(parts[0]), None)
    return (None, None)


_TIME_KEYS = ("tradeDateTime", "validFrom", "validUntil",
              "weekdays", "timeStart", "timeEnd", "channels")


def _coupon_verdict(coupon: Dict[str, Any], now_dt: datetime,
                    channel: Optional[str] = None) -> Tuple[List[str], List[str]]:
    """判定单张券在 `now_dt` + `channel` 下是否可用。

    返回 `(reasons, missing)`：`reasons` 非空即**不可用**；`missing` 是"无从判定"的
    维度（结构化字段缺失），不当作不可用，但必须交由调用方人工核对。
    """
    reasons: List[str] = []
    missing: List[str] = []

    start = _parse_dt(coupon.get("validFrom"))
    end = _parse_dt(coupon.get("validUntil"))
    if start is None or end is None:
        rs, re_ = _split_range(coupon.get("tradeDateTime"))
        start = start or rs
        end = end or re_

    if end and now_dt > end:
        reasons.append(f"已过期（止于 {end:%Y-%m-%d %H:%M}）")
    if start and now_dt < start:
        reasons.append(f"尚未开始（起于 {start:%Y-%m-%d %H:%M}）")
    if start is None and end is None:
        missing.append("有效期")

    raw_days = coupon.get("weekdays")
    if raw_days:
        days = []
        for d in raw_days:
            try:
                days.append(int(d))
            except (TypeError, ValueError):
                continue
        if days and now_dt.isoweekday() not in days:
            reasons.append(
                "限周" + "、".join(_WEEKDAY_CN[d - 1] for d in days if 1 <= d <= 7)
                + f"，今天是周{_WEEKDAY_CN[now_dt.isoweekday() - 1]}"
            )
    else:
        missing.append("可用星期")

    ts, te = coupon.get("timeStart"), coupon.get("timeEnd")
    if ts and te:
        cur = now_dt.strftime("%H:%M")
        if not (str(ts) <= cur <= str(te)):
            reasons.append(f"限 {ts}-{te} 使用，当前 {cur}")
    else:
        missing.append("可用时段")

    raw_channels = coupon.get("channels")
    if raw_channels:
        labels = [str(x) for x in raw_channels]
        want = {"pickup": "到店", "delivery": "外送"}.get(
            str(channel or "").lower(), str(channel or "")
        )
        if want and not any(want in lab for lab in labels):
            reasons.append(f"限定渠道：{'、'.join(labels)}")
    else:
        missing.append("渠道标签")

    return reasons, missing


def coupon_availability(coupons: Any,
                        now: Optional[str] = None,
                        channel: Optional[str] = None) -> Dict[str, Any]:
    """按**实际下单时刻**与**取餐渠道**筛券，输出可用券与排除原因。

    券的结构化时段字段是**可选**的——MCP 原始数据通常只给 `tradeDateTime` 区间，
    星期/时刻窗口需要调用方从券文案里提取后补进来：

        validFrom / validUntil   显式有效期（YYYY-MM-DD[ HH:MM]）
        weekdays                 [1..7]，ISO 星期（周一=1）
        timeStart / timeEnd      "HH:mm"
        channels                 ["到店", "外送"] 或 ["pickup", "delivery"]

    缺失的维度不会被"猜成可用"，而是记入 `unchecked`，交由调用方核对。
    """
    coupons = normalize_coupons(coupons)
    now_dt = _parse_dt(now) if now else datetime.now()
    if now_dt is None:
        now_dt = datetime.now()

    usable: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    unchecked: List[str] = []

    for c in coupons:
        reasons, missing = _coupon_verdict(c, now_dt, channel)

        if missing:
            unchecked.append(f"{c.get('title') or c.get('couponId')}：缺少 {'、'.join(missing)} 结构化字段")

        record = {
            "couponId": c.get("couponId"),
            "title": c.get("title"),
            "representativeCode": c.get("representativeCode"),
            "productCodes": c.get("productCodes"),
            "productNames": c.get("productNames"),
            "tradeDateTime": c.get("tradeDateTime"),
        }
        if reasons:
            excluded.append({**record, "reasons": reasons})
        else:
            usable.append(record)

    return {
        "now": now_dt.strftime("%Y-%m-%d %H:%M"),
        "channel": channel,
        "usable": usable,
        "excluded": excluded,
        "unchecked": unchecked,
        "hint": (
            "usable 中的券仍需用 calculate-price 试算其券商品价，才能得出真实节省额；"
            "unchecked 里的维度请从券文案人工核对后再采信。"
        ),
    }


def _probe_targets(probe: Dict[str, Any]) -> set:
    """该券的券商品可替代哪些菜单商品码（用于判断"是否替代用户已确定要买的"）。"""
    targets = (
        probe.get("substitutesMenuCodes")
        or probe.get("substitutes")
        or probe.get("replacesMenuCode")
        or []
    )
    if isinstance(targets, str):
        targets = [targets]
    return {str(t) for t in targets if t}


def _normalize_demand(demand: Any) -> set:
    """把 demand 归一化成菜单商品码集合。支持 ["1100", ...] 或 [{"menuCode": "1100"}, ...]。"""
    codes = set()
    for d in demand or []:
        if isinstance(d, str):
            codes.add(d)
        elif isinstance(d, dict):
            code = d.get("menuCode") or d.get("productCode") or d.get("code")
            if code:
                codes.add(str(code))
    return codes


def coupon_opportunities(probes: Any,
                         require_positive: bool = True,
                         demand_menu_codes: Any = None,
                         now: Optional[str] = None,
                         channel: Optional[str] = None,
                         coupons: Any = None) -> Dict[str, Any]:
    """把"单券试算结果"整理为券机会清单（按净收益降序）。

    `probes` 每项来自一次 `calculate-price` 试算（券商品 + 该券）：

        couponId / title / couponProductCode / productName
        originalFen            该券商品的划线价（响应的 `originalPrice`）
        payFen                 用券后实付（响应的 `productPrice`）
        substitutesMenuCodes   [可选] 该券商品能替代的菜单商品码

    节省额直接取 `originalFen - payFen`——这正是 `calculate-price` 返回的
    `discount`，无需再拿菜单同款做匹配（券商品码与菜单码本就不同域）。

    **★ 内置券可用性过滤（不可跳过）**：本函数会**无条件**按 `now` + `channel`
    对每张券做有效期 / 星期 / 时段 / 渠道判定——即整个 Phase 2.5 的"筛券"与
    "合规核算"合并在一次调用里完成，**不存在"只做合规、漏掉时段检查"的路径**。

    - 不可用的券 → `unavailable`（带 `reasons`），既不计入 `totalSavingFen`，
      也不进 `notApplicable`
    - 时间字段（`tradeDateTime`/`weekdays`/`timeStart`/`timeEnd`/`channels`）可放在
      每条 probe 上，也可用 `coupons` 传一份券池、按 `couponId` 自动合并
    - `now` 缺省取本机当前时间，但**预约/改期场景必须显式传**，返回里 `nowProvided`
      会标出是否显式给了

    **★ 合规硬约束（赛事红线）**：只有"券商品可替代用户**已确定要买**的菜单品"
    的券才算真正的机会。因此本函数要求传入 `demand_menu_codes`：

    - 落在需求内的券 → `opportunities`，计入 `totalSavingFen` / `ordersNeeded`
    - 落在需求外的券 → `notApplicable`，**不计入任何合计**（用它就得先加购，属诱导加购）
    - 无法判定的（未给 demand，或券未声明替代品）→ 出现在 `opportunities` 但
      `appliesToDemand = null`，**同样不计入合计**

    这样"用券要先多买东西"的券永远不会被汇总成一个"合计可省"数字对外输出。
    """
    if isinstance(probes, dict):
        demand_menu_codes = demand_menu_codes or probes.get("demandMenuCodes") or probes.get("demand")
        now = now or probes.get("now")
        channel = channel or probes.get("channel")
        coupons = coupons or probes.get("coupons")
        probes = probes.get("probes", [])

    probes = [p for p in (probes or []) if isinstance(p, dict)]

    demand = _normalize_demand(demand_menu_codes)
    demand_declared = bool(demand)

    # ---- 券可用性（有效期 / 星期 / 时段 / 渠道）：无条件执行 ----
    now_dt = _parse_dt(now) if now else datetime.now()
    if now_dt is None:
        now_dt = datetime.now()

    pool: Dict[str, Dict[str, Any]] = {}
    for c in coupons or []:
        if isinstance(c, dict) and c.get("couponId") is not None:
            pool[str(c.get("couponId"))] = c

    verdicts: Dict[str, List[str]] = {}
    avail_unchecked: List[str] = []
    for p in probes:
        cid = str(p.get("couponId"))
        merged = dict(pool.get(cid) or {})
        for k in _TIME_KEYS:
            if p.get(k) is not None:
                merged[k] = p.get(k)
        reasons, missing = _coupon_verdict(merged, now_dt, channel)
        verdicts[cid] = reasons
        if missing:
            avail_unchecked.append(
                f"{p.get('title') or cid}：缺少 {'、'.join(missing)} 结构化字段"
            )

    applicable: List[Dict[str, Any]] = []
    not_applicable: List[Dict[str, Any]] = []
    unverified: List[Dict[str, Any]] = []
    unavailable: List[Dict[str, Any]] = []

    for p in probes:
        orig = int(p.get("originalFen") or 0)
        pay = int(p.get("payFen") or 0)
        saving = orig - pay
        if require_positive and saving <= 0:
            continue

        cid = str(p.get("couponId"))
        reasons = verdicts.get(cid) or []
        if reasons:
            unavailable.append({
                "couponId": p.get("couponId"),
                "title": p.get("title"),
                "couponProductCode": p.get("couponProductCode"),
                "productName": p.get("productName"),
                "savingFen": saving,
                "savingYuan": fen_to_yuan(saving),
                "reasons": reasons,
            })
            continue

        targets = _probe_targets(p)
        if not demand_declared or not targets:
            applies: Optional[bool] = None
        else:
            applies = bool(targets & demand)

        name = p.get("productName") or p.get("title") or ""
        item = {
            "couponId": p.get("couponId"),
            "title": p.get("title"),
            "couponProductCode": p.get("couponProductCode"),
            "productName": p.get("productName"),
            "originalYuan": fen_to_yuan(orig),
            "payYuan": fen_to_yuan(pay),
            "savingFen": saving,
            "savingYuan": fen_to_yuan(saving),
            "appliesToDemand": applies,
            "pitch": (
                f"{name}：菜单价 ¥{fen_to_yuan(orig)}，用这张券 ¥{fen_to_yuan(pay)}，"
                f"省 ¥{fen_to_yuan(saving)}（券商品码 {p.get('couponProductCode')}，"
                f"必须点该券商品才能享受）"
            ),
        }

        if applies is True:
            applicable.append(item)
        elif applies is False:
            item["reason"] = "用户未确定要买该商品；用这张券需先加购 → 不计入机会（赛事禁止诱导加购）"
            not_applicable.append(item)
        else:
            item["reason"] = (
                "未提供 demandMenuCodes" if not demand_declared
                else "该券未声明可替代的菜单商品码，无法判定是否替代已购商品"
            )
            unverified.append(item)

    for bucket in (applicable, unverified, not_applicable, unavailable):
        bucket.sort(key=lambda x: -x["savingFen"])

    total = sum(o["savingFen"] for o in applicable)
    unverified_saving = sum(o["savingFen"] for o in unverified)

    warnings: List[str] = []
    if not now:
        warnings.append(
            "未显式提供 now（下单时刻），券的时段/渠道校验按本机当前时间判定；"
            "预约或改期场景必须传入真实下单时刻后重跑。"
        )
    if not demand_declared:
        warnings.append(
            "未提供 demandMenuCodes，无法判断这些券对应的商品是否为用户已确定要买的；"
            "已出现的机会一律 appliesToDemand=null 且不计入 totalSavingFen——"
            "禁止把它们的省额对外展示成可实现的机会。"
        )
    elif unverified:
        warnings.append(
            f"{len(unverified)} 张券因未声明可替代的菜单商品码而无法判定，已不计入合计。"
        )
    if not_applicable:
        warnings.append(
            f"{len(not_applicable)} 张券对应的商品用户并未要买，已移入 notApplicable 且不计入合计；"
            "只可在用户主动表示想要该商品时再提示。"
        )
    if unavailable:
        warnings.append(
            f"{len(unavailable)} 张券在 {now_dt:%Y-%m-%d %H:%M}"
            f"（渠道 {channel or '未指定'}）不可用，已移入 unavailable 且不计入合计。"
        )
    if avail_unchecked:
        warnings.append(
            "以下券缺少结构化时段/渠道字段，这些维度**未经校验**，需人工核对券文案："
            + "；".join(avail_unchecked)
        )

    return {
        "opportunities": applicable + unverified,
        "notApplicable": not_applicable,
        "unavailable": unavailable,
        "count": len(applicable),
        "totalSavingFen": total,
        "totalSavingYuan": fen_to_yuan(total),
        "ordersNeeded": len(applicable),
        "excludedSavingFen": unverified_saving,
        "excludedSavingYuan": fen_to_yuan(unverified_saving),
        "demandDeclared": demand_declared,
        "demandMenuCodes": sorted(demand),
        "availability": {
            "checked": True,
            "now": now_dt.strftime("%Y-%m-%d %H:%M"),
            "nowProvided": bool(now),
            "channel": channel,
            "unavailableCount": len(unavailable),
            "unchecked": avail_unchecked,
        },
        "compliance": "只优化用户已确定要买的东西；禁止以省钱为由建议加购或超量购买。",
        "note": "一张订单只能用一张券，因此每条券机会需各自成单（拆单）。",
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- #
# 6. 下单核对与汇报（Phase 6）
# --------------------------------------------------------------------------- #

def order_summary(payload: Any, expected_fen: Optional[int] = None) -> Dict[str, Any]:
    """把 `create-order` 的返回整理成"下单核对 + 汇报"结构。

    入参可以是整个响应、`data`、或直接是 `data.orderDetail`。
    `expected_fen` 是下单前 `calculate-price` 试算的应付金额（分）；传了就会做核对——
    **核对不通过必须告知用户并给出取消选项，不得默默接受**。

    订单状态一律读 `orderStatus`（实测是中文字符串，如"待支付"）；
    同响应里的 `status` 数字与官方文档枚举不一致，仅原样带出、不参与判断。
    """
    root = payload
    if isinstance(root, dict) and isinstance(root.get("data"), dict):
        root = root["data"]
    if not isinstance(root, dict):
        raise SystemExit("order-summary: 入参不是 create-order 的响应结构")

    detail = root.get("orderDetail") if isinstance(root.get("orderDetail"), dict) else root

    def _fen(value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            return yuan_to_fen(value)
        except Exception:
            return None

    real_fen = _fen(detail.get("realTotalAmount"))
    original_fen = _fen(detail.get("productPrice") or detail.get("totalAmount"))
    discount_fen = _fen(detail.get("totalDiscountAmount")) or 0

    check = None
    if expected_fen is not None:
        expected_fen = int(expected_fen)
        diff = (real_fen or 0) - expected_fen
        check = {
            "expectedFen": expected_fen,
            "expectedYuan": fen_to_yuan(expected_fen),
            "realYuan": fen_to_yuan(real_fen or 0),
            "diffFen": diff,
            "diffYuan": fen_to_yuan(diff),
            "matches": diff == 0,
            "action": (
                "与试算一致，可照模板汇报"
                if diff == 0
                else "⚠️ 与试算不一致：立即告知用户差异并给出 cancel-order 选项，不得默默接受"
            ),
        }

    # 支付时限（实测下单后 15 分钟）
    expire_dt, create_dt = _parse_dt(detail.get("expirePayTime")), _parse_dt(detail.get("createTime"))
    pay_window_min = (
        int((expire_dt - create_dt).total_seconds() // 60)
        if expire_dt and create_dt else None
    )

    coupons = [
        {"name": c.get("couponName"), "discountYuan": c.get("discountAmount")}
        for c in (detail.get("couponList") or []) if isinstance(c, dict)
    ]
    products = [
        {
            "name": p.get("productName"),
            "quantity": p.get("quantity"),
            "priceYuan": p.get("price"),
            "comboItems": [
                f"{i.get('itemName')}×{i.get('itemQuantity')}"
                for i in (p.get("comboItemList") or []) if isinstance(i, dict)
            ],
        }
        for p in (detail.get("orderProductList") or []) if isinstance(p, dict)
    ]

    pay_url = root.get("payH5Url") or detail.get("payH5Url")

    lines = [
        "订单已生成 ✅",
        f"  订单号  {detail.get('orderId')}",
        f"  门店    {detail.get('storeName')}",
        f"  取餐    {detail.get('takeWay')}",
        "  商品    " + " / ".join(f"{p['name']}×{p['quantity']}" for p in products),
        f"  原价    ¥{fen_to_yuan(original_fen or 0)}",
        f"  优惠    -¥{fen_to_yuan(discount_fen)}"
        + ("（" + "、".join(c["name"] for c in coupons if c["name"]) + "）" if coupons else ""),
        f"  实付    ¥{fen_to_yuan(real_fen or 0)}",
    ]
    if detail.get("expirePayTime"):
        suffix = f"（{pay_window_min} 分钟）" if pay_window_min else ""
        lines.append(f"  时限    {detail['expirePayTime']} 前{suffix}")
    if pay_url:
        lines.append("")
        lines.append(f"  支付：{pay_url}")

    return {
        "orderId": detail.get("orderId"),
        "orderStatus": detail.get("orderStatus"),
        "statusRaw": detail.get("status"),
        "storeName": detail.get("storeName"),
        "storeAddress": detail.get("storeAddress"),
        "takeWay": detail.get("takeWay"),
        "pickupCode": detail.get("pickupCode"),
        "payH5Url": pay_url,
        "payId": root.get("payId") or detail.get("payId"),
        "createTime": detail.get("createTime"),
        "expirePayTime": detail.get("expirePayTime"),
        "payWindowMinutes": pay_window_min,
        "originalYuan": fen_to_yuan(original_fen or 0),
        "discountYuan": fen_to_yuan(discount_fen),
        "realYuan": fen_to_yuan(real_fen or 0),
        "coupons": coupons,
        "products": products,
        "check": check,
        "note": (
            "状态判断用 orderStatus（中文），不要用 status 数字枚举；"
            "分账表口径照 productPrice / totalDiscountAmount / realTotalAmount 填。"
        ),
        "reportText": "\n".join(lines),
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _read_payload(args: argparse.Namespace) -> Any:
    if getattr(args, "value", None) is not None:
        return args.value
    if getattr(args, "input", None):
        with open(args.input, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return json.load(sys.stdin)


def _emit(obj: Any) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="麦当劳 MCP 数据预处理工具")
    sub = parser.add_subparsers(dest="action", required=True)

    p = sub.add_parser("yuan-to-fen", help="元 -> 分")
    p.add_argument("--value", required=True)

    p = sub.add_parser("fen-to-yuan", help="分 -> 元")
    p.add_argument("--value", required=True)

    p = sub.add_parser("effective-price", help="校正随单购价（入参为 data.meals 或整个响应）")
    p.add_argument("--input")

    p = sub.add_parser("normalize-coupons", help="按 couponId 分组去重券池")
    p.add_argument("--input")

    p = sub.add_parser("store-status", help="校验门店营业状态（入参为门店数组或整响应）")
    p.add_argument("--input")

    p = sub.add_parser("coupon-availability",
                       help="按下单时刻+渠道筛券；入参 {now, channel, coupons}")
    p.add_argument("--input")
    p.add_argument("--now", help="实际下单时刻，如 '2026-10-09 22:23'；缺省取本机当前时间")
    p.add_argument("--channel", help="pickup / delivery（或中文 到店/外送）")

    p = sub.add_parser("coupon-opportunities",
                       help="券机会清单：内部完成时段/渠道可用性 + 合规过滤；"
                            "入参 {probes:[...], demandMenuCodes:[...], now, channel, coupons}")
    p.add_argument("--input")
    p.add_argument("--demand",
                   help="用户已确定要买的菜单商品码，逗号分隔（强烈建议传，否则无法判定是否诱导加购）")
    p.add_argument("--now", help="实际下单时刻，如 '2026-10-10 00:06'；预约单必须传")
    p.add_argument("--channel", help="pickup / delivery（或中文 到店/外送）")

    p = sub.add_parser("order-summary",
                       help="整理 create-order 返回并核对金额；入参优先传整个响应（只给 orderDetail 会拿不到 payH5Url）")
    p.add_argument("--input")
    p.add_argument("--expected-fen",
                   help="下单前 calculate-price 试算的应付金额（分）；传了就会核对并给出差异")

    args = parser.parse_args(argv)

    if args.action == "yuan-to-fen":
        _emit({"yuan": args.value, "fen": yuan_to_fen(args.value)})
    elif args.action == "fen-to-yuan":
        _emit({"fen": args.value, "yuan": fen_to_yuan(args.value)})
    elif args.action == "effective-price":
        payload = _read_payload(args)
        if isinstance(payload, dict) and "data" in payload:
            payload = payload["data"]
        meals = payload.get("meals", payload) if isinstance(payload, dict) else payload
        _emit(effective_prices(meals))
    elif args.action == "normalize-coupons":
        _emit(normalize_coupons(_read_payload(args)))
    elif args.action == "store-status":
        payload = _read_payload(args)
        if isinstance(payload, dict):
            payload = payload.get("data", payload)
        stores = payload if isinstance(payload, list) else [payload]
        _emit({
            "all": [store_status(s) for s in stores],
            "open": pick_open_stores(stores),
        })
    elif args.action == "coupon-availability":
        payload = _read_payload(args)
        if isinstance(payload, list):
            payload = {"coupons": payload}
        coupons = payload.get("coupons", payload.get("data", []))
        _emit(coupon_availability(
            coupons,
            now=getattr(args, "now", None) or payload.get("now"),
            channel=getattr(args, "channel", None) or payload.get("channel"),
        ))
    elif args.action == "coupon-opportunities":
        payload = _read_payload(args)
        demand = getattr(args, "demand", None)
        demand = [c.strip() for c in demand.split(",") if c.strip()] if demand else None
        _emit(coupon_opportunities(
            payload,
            demand_menu_codes=demand,
            now=getattr(args, "now", None),
            channel=getattr(args, "channel", None),
        ))
    elif args.action == "order-summary":
        _emit(order_summary(
            _read_payload(args),
            expected_fen=getattr(args, "expected_fen", None),
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
