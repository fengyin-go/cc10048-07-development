"""已发表结果核对服务。

多维查询原先只能在方案表里保存查询条件，无法按“实际指标版本 + 分区日期 + 来源范围”
定位已经发布出去的事实和维度数据。本模块补齐这条链路：

1. 已发表事实与维度数据按完整口径（指标名称、实际指标版本、分区日期、来源范围、分区）
   隔离，同名维度（例如两个指标都叫“渠道”）不会跨指标混入。
2. 每次筛选生成一轮“可接续筛选结果”，带接续令牌；轮次保存事实快照和证据快照，
   证据被更正后仍可用原令牌回看变更前的原始筛选结果。
3. 预聚合任务按筛选轮次关联实际数据源，只为“已受理”的地区领取数据，返回实际重算
   范围与待核查范围；同一筛选同一合格分区只会生成一次有效受理。
4. 证据更正只追加修订，绝不回写已发表度量，历史已发表结果不会被伪装成更正后的版本。
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from typing import Any

PARTITIONS = ["华东", "华南", "华北", "西南"]
SOURCE_SCOPES = ["官方数仓", "补录批次"]
PARTITION_DATES = ["2026-08-31", "2026-09-30"]

METRIC_REGISTRY: dict[str, dict[str, Any]] = {
    "订单转化率": {"当前版本": "v2026.09-r2", "历史版本": ["v2026.08-r1"]},
    "客单价": {"当前版本": "v2026.08-r1", "历史版本": []},
}

# 维度模板按“指标 + 维度名”登记；两个指标共用“渠道”“设备”两个重名维度，
# 但取值不同，查询时必须带指标口径，不能只按维度名取数。
DIMENSION_TEMPLATES: dict[tuple[str, str], list[tuple[str, float]]] = {
    ("订单转化率", "渠道"): [("自然流量", 0.6200), ("付费流量", 0.7100), ("活动会场", 0.5500)],
    ("订单转化率", "设备"): [("移动端", 0.5800), ("桌面端", 0.6900)],
    ("客单价", "渠道"): [("自然流量", 131.50), ("付费流量", 142.80), ("活动会场", 118.60)],
    ("客单价", "设备"): [("移动端", 126.40), ("桌面端", 137.90)],
}

_PARTITION_FACTOR = {"华东": 1.00, "华南": 0.94, "华北": 0.91, "西南": 0.97}
_SOURCE_ROW_COUNTS = {"华东": 12000, "华南": 9800, "华北": 7600, "西南": 5400}
_STATUS_VALID = ("有效", "已核验")


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _normalize_partitions(value: Any) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        raw = value.replace("、", ",").replace("，", ",").split(",")
    else:
        raw = list(value)
    partitions = [str(item).strip() for item in raw if str(item).strip()]
    unknown = [item for item in partitions if item not in PARTITIONS]
    if unknown:
        raise ValueError(f"未知分区：{'、'.join(unknown)}")
    return partitions


class PublishedResultService:
    """已发表事实、可接续筛选轮次、证据修订和复算受理的内存账本。"""

    def __init__(self) -> None:
        self.reset()

    # ------------------------------------------------------------------ 初始化
    def reset(self) -> None:
        self.facts: list[dict[str, Any]] = self._seed_facts()
        self.dimensions: list[dict[str, Any]] = self._seed_dimensions(self.facts)
        self.evidence: dict[str, dict[str, Any]] = self._seed_evidence(self.facts)
        self.rounds: dict[int, dict[str, Any]] = {}
        self._next_round_id = 1
        self.acceptances: dict[tuple[str, str], dict[str, Any]] = {}
        self.pendings: dict[tuple[str, str], dict[str, Any]] = {}
        self.resolved_pendings: list[dict[str, Any]] = []
        self.claims: dict[str, dict[str, Any]] = {}

    def _seed_facts(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        def add(
            fact_id: int,
            code: str,
            metric: str,
            version: str,
            date: str,
            scope: str,
            partition: str,
            value: float,
            batch: str,
            evidence_ref: str,
            published_at: str,
        ) -> None:
            rows.append({
                "id": fact_id,
                "事实标识": code,
                "指标名称": metric,
                "指标版本": version,
                "分区日期": date,
                "来源范围": scope,
                "分区": partition,
                "度量": value,
                "发布状态": "已发布",
                "发布批次": batch,
                "发布时间": published_at,
                "证据标识": evidence_ref,
            })

        hist = "v2026.08-r1"
        add(1, "FACT-ORD-E-0831", "订单转化率", hist, "2026-08-31", "官方数仓", "华东",
            0.6421, "PUB-20260831-A", "EV-ORD-E", "2026-09-01 09:30:00")
        add(2, "FACT-ORD-S-0831", "订单转化率", hist, "2026-08-31", "官方数仓", "华南",
            0.5812, "PUB-20260831-A", "EV-ORD-S", "2026-09-01 09:30:00")
        add(3, "FACT-ORD-N-0831", "订单转化率", hist, "2026-08-31", "官方数仓", "华北",
            0.5590, "PUB-20260831-A", "EV-ORD-N", "2026-09-01 09:30:00")
        add(4, "FACT-ORD-SW-0831", "订单转化率", hist, "2026-08-31", "官方数仓", "西南",
            0.6120, "PUB-20260831-A", "EV-ORD-SW", "2026-09-01 09:30:00")
        add(5, "FACT-ORD-E-BL-0831", "订单转化率", hist, "2026-08-31", "补录批次", "华东",
            0.6502, "PUB-20260831-B", "EV-ORD-E-BL", "2026-09-02 11:00:00")
        add(6, "FACT-ORD-E-0930", "订单转化率", "v2026.09-r2", "2026-09-30", "官方数仓", "华东",
            0.6610, "PUB-20260930-A", "EV-ORD-E-09", "2026-10-01 09:30:00")
        add(7, "FACT-AOV-E-0831", "客单价", hist, "2026-08-31", "官方数仓", "华东",
            128.40, "PUB-20260831-C", "EV-AOV-E", "2026-09-01 10:00:00")
        add(8, "FACT-AOV-S-0831", "客单价", hist, "2026-08-31", "官方数仓", "华南",
            119.70, "PUB-20260831-C", "EV-AOV-S", "2026-09-01 10:00:00")
        add(9, "FACT-AOV-N-0831", "客单价", hist, "2026-08-31", "官方数仓", "华北",
            135.20, "PUB-20260831-C", "EV-AOV-N", "2026-09-01 10:00:00")
        return rows

    def _seed_dimensions(self, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        dim_id = 1
        for fact in facts:
            factor = _PARTITION_FACTOR.get(fact["分区"], 1.0)
            if fact["来源范围"] == "补录批次":
                factor *= 1.01
            if fact["指标版本"] == "v2026.09-r2":
                factor *= 1.02
            for (metric, dim_name), values in DIMENSION_TEMPLATES.items():
                if metric != fact["指标名称"]:
                    continue
                for dim_value, base in values:
                    rows.append({
                        "id": dim_id,
                        "事实标识": fact["事实标识"],
                        "指标名称": fact["指标名称"],
                        "指标版本": fact["指标版本"],
                        "分区日期": fact["分区日期"],
                        "来源范围": fact["来源范围"],
                        "分区": fact["分区"],
                        "维度名": dim_name,
                        "维度值": dim_value,
                        "度量值": round(base * factor, 4),
                    })
                    dim_id += 1
        return rows

    def _seed_evidence(self, facts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        chains: dict[str, dict[str, Any]] = {}
        for fact in facts:
            chains[fact["证据标识"]] = {
                "证据标识": fact["证据标识"],
                "指标名称": fact["指标名称"],
                "指标版本": fact["指标版本"],
                "分区日期": fact["分区日期"],
                "来源范围": fact["来源范围"],
                "分区": fact["分区"],
                "已发表事实标识": fact["事实标识"],
                # 只读镜像：证据怎么改都不会回写这里，保证历史已发表结果不被覆盖。
                "已发表度量": fact["度量"],
                "修订": [{
                    "修订号": 1,
                    "状态": "有效",
                    "佐证值": fact["度量"],
                    "提交人": "数据工程组",
                    "提交时间": fact["发布时间"],
                    "核验时间": fact["发布时间"],
                    "变更说明": "初始发布佐证",
                }],
            }
        return chains

    # ------------------------------------------------------------------ 元数据
    def scopes(self) -> dict[str, Any]:
        return {
            "指标清单": [
                {
                    "指标名称": name,
                    "当前版本": registry["当前版本"],
                    "历史版本": registry["历史版本"],
                }
                for name, registry in METRIC_REGISTRY.items()
            ],
            "来源范围": list(SOURCE_SCOPES),
            "分区日期": list(PARTITION_DATES),
            "分区地区": list(PARTITIONS),
        }

    # ------------------------------------------------------------------ 查询
    def _resolve_version(self, criteria: dict[str, Any]) -> tuple[str, str]:
        metric = str(criteria.get("指标名称") or "").strip()
        if metric not in METRIC_REGISTRY:
            raise ValueError(f"未知指标：{metric or '（空）'}")
        explicit = str(criteria.get("指标版本") or "").strip()
        if explicit:
            return explicit, "指定"
        mode = str(criteria.get("版本口径") or "实际").strip() or "实际"
        registry = METRIC_REGISTRY[metric]
        if mode == "历史":
            history = registry.get("历史版本") or []
            if not history:
                raise ValueError(f"指标 {metric} 没有历史版本")
            return history[0], "历史"
        if mode not in ("实际", "当前"):
            raise ValueError(f"未知版本口径：{mode}")
        return registry["当前版本"], "实际"

    def _normalize_criteria(self, criteria: dict[str, Any]) -> dict[str, Any]:
        metric = str(criteria.get("指标名称") or "").strip()
        version, mode = self._resolve_version(criteria)
        date = str(criteria.get("分区日期") or "").strip()
        scope = str(criteria.get("来源范围") or "").strip()
        if scope and scope not in SOURCE_SCOPES:
            raise ValueError(f"未知来源范围：{scope}")
        if date and date not in PARTITION_DATES:
            raise ValueError(f"未知分区日期：{date}")
        return {
            "指标名称": metric,
            "实际指标版本": version,
            "版本口径": mode,
            "分区日期": date,
            "来源范围": scope,
            "分区地区": _normalize_partitions(criteria.get("分区地区")),
        }

    def _match_facts(self, criteria: dict[str, Any]) -> list[dict[str, Any]]:
        partitions = criteria["分区地区"]
        matched = []
        for fact in self.facts:
            # 指标名称、指标版本、分区日期、来源范围一律精确匹配：
            # 版本不做“就近取最新”，来源范围不做子串，避免重名口径混入。
            if fact["指标名称"] != criteria["指标名称"]:
                continue
            if fact["指标版本"] != criteria["实际指标版本"]:
                continue
            if criteria["分区日期"] and fact["分区日期"] != criteria["分区日期"]:
                continue
            if criteria["来源范围"] and fact["来源范围"] != criteria["来源范围"]:
                continue
            if partitions and fact["分区"] not in partitions:
                continue
            matched.append(fact)
        return matched

    def query_facts(self, criteria: dict[str, Any]) -> dict[str, Any]:
        """按实际指标版本、分区日期、来源范围直接查询已发表事实与维度数据。"""
        normalized = self._normalize_criteria(criteria)
        facts = self._match_facts(normalized)
        return {
            "ok": True,
            "筛选条件": normalized,
            "结果": [self._fact_view(fact) for fact in facts],
            "统计": self._stats(facts),
            "提示": "" if facts else "没有匹配的已发表事实，请核对指标版本、分区日期和来源范围",
        }

    def query_dimensions(
        self,
        dimension_name: str,
        *,
        metric: str,
        version: str,
        partition_date: str,
        source_scope: str,
        partition: str | None = None,
    ) -> dict[str, Any]:
        """按完整口径取维度数据；只给维度名会跨指标混入，因此其余口径必填。"""
        missing = [
            label for label, value in (
                ("指标名称", metric),
                ("指标版本", version),
                ("分区日期", partition_date),
                ("来源范围", source_scope),
            ) if not str(value or "").strip()
        ]
        if missing:
            raise ValueError(f"维度查询缺少口径字段：{'、'.join(missing)}")
        rows = [
            row for row in self.dimensions
            if row["维度名"] == dimension_name
            and row["指标名称"] == metric
            and row["指标版本"] == version
            and row["分区日期"] == partition_date
            and row["来源范围"] == source_scope
            and (not partition or row["分区"] == partition)
        ]
        return {
            "ok": True,
            "维度名": dimension_name,
            "口径": {
                "指标名称": metric, "指标版本": version,
                "分区日期": partition_date, "来源范围": source_scope,
                "分区": partition or "全部",
            },
            "行数": len(rows),
            "结果": rows,
        }

    # ------------------------------------------------------------------ 可接续筛选
    def filter_published(self, criteria: dict[str, Any], token: str | None = None) -> dict[str, Any]:
        """创建或接续一轮筛选；接续时返回原快照，不重新取最新数据。"""
        if token:
            return self.get_round_by_token(token)
        normalized = self._normalize_criteria(criteria)
        facts = self._match_facts(normalized)
        round_fingerprint = _fingerprint({
            "指标名称": normalized["指标名称"],
            "指标版本": normalized["实际指标版本"],
            "分区日期": normalized["分区日期"],
            "来源范围": normalized["来源范围"],
            "分区地区": sorted(normalized["分区地区"]),
        })
        round_id = self._next_round_id
        self._next_round_id += 1
        round_token = f"CT-{round_id:02d}-{round_fingerprint[:8]}"
        evidence_snapshot = {
            fact["事实标识"]: self._evidence_state(self.evidence[fact["证据标识"]])
            for fact in facts
        }
        record = {
            "轮次号": round_id,
            "接续令牌": round_token,
            "筛选指纹": round_fingerprint,
            **normalized,
            "创建时间": _stamp(),
            "快照事实标识": [fact["事实标识"] for fact in facts],
            "证据快照": evidence_snapshot,
            "状态": "已筛选",
        }
        self.rounds[round_id] = record
        return self._round_view(record, resumed=False)

    def get_round_by_token(self, token: str) -> dict[str, Any]:
        for record in self.rounds.values():
            if record["接续令牌"] == token:
                return self._round_view(record, resumed=True)
        raise ValueError(f"接续令牌无效或已过期：{token}")

    def get_round(self, round_id: int, token: str | None = None) -> dict[str, Any]:
        record = self.rounds.get(int(round_id))
        if record is None:
            raise ValueError(f"筛选轮次 {round_id} 不存在")
        if token and record["接续令牌"] != token:
            raise ValueError("接续令牌与筛选轮次不匹配")
        return self._round_view(record, resumed=token is not None)

    def _round_facts(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        by_code = {fact["事实标识"]: fact for fact in self.facts}
        return [by_code[code] for code in record["快照事实标识"] if code in by_code]

    def _round_view(self, record: dict[str, Any], *, resumed: bool) -> dict[str, Any]:
        facts = self._round_facts(record)
        items = []
        for fact in facts:
            view = self._fact_view(fact)
            snapshot = record["证据快照"].get(fact["事实标识"])
            current = self._evidence_state(self.evidence[fact["证据标识"]])
            view["证据变更"] = self._evidence_change(snapshot, current)
            items.append(view)
        return {
            "ok": True,
            "接续": resumed,
            "筛选轮次": {
                "轮次号": record["轮次号"],
                "接续令牌": record["接续令牌"],
                "筛选指纹": record["筛选指纹"],
                "指标名称": record["指标名称"],
                "实际指标版本": record["实际指标版本"],
                "版本口径": record["版本口径"],
                "分区日期": record["分区日期"],
                "来源范围": record["来源范围"],
                "分区地区": record["分区地区"],
                "创建时间": record["创建时间"],
                "快照事实数": len(record["快照事实标识"]),
            },
            "结果": items,
            "分区": self._partition_summaries(record, items),
            "统计": self._stats(facts),
            "同名维度隔离": self._dimension_isolation(record),
            "提示": "" if items else "没有匹配的已发表事实，请核对指标版本、分区日期和来源范围",
        }

    def _partition_summaries(
        self, record: dict[str, Any], items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        summaries = []
        for partition in record["分区地区"]:
            partition_items = [item for item in items if item["分区"] == partition]
            latest_states = [item["证据"] for item in partition_items]
            recomputable = bool(partition_items) and all(
                state["状态"] in _STATUS_VALID for state in latest_states
            )
            if not partition_items:
                evidence_status = "无已发表事实"
            elif recomputable:
                evidence_status = "有效"
            else:
                evidence_status = next(
                    state["状态"] for state in latest_states
                    if state["状态"] not in _STATUS_VALID
                )
            summaries.append({
                "分区": partition,
                "事实数": len(partition_items),
                "事实标识": [item["事实标识"] for item in partition_items],
                "已发表度量": [item["度量"] for item in partition_items],
                "证据标识": [state["证据标识"] for state in latest_states],
                "证据状态": evidence_status,
                "可重算": recomputable,
            })
        return summaries

    def _dimension_isolation(self, record: dict[str, Any]) -> dict[str, Any]:
        same_partitions = [
            name for name in METRIC_REGISTRY
            if name != record["指标名称"]
        ]
        filtered_out = [
            name for name in same_partitions
            if any(
                fact["指标名称"] == name
                and fact["指标版本"] == record["实际指标版本"]
                and record["分区日期"] and fact["分区日期"] == record["分区日期"]
                and record["来源范围"] and fact["来源范围"] == record["来源范围"]
                and fact["分区"] in record["分区地区"]
                for fact in self.facts
            )
        ]
        shared_dimensions = sorted({
            row["维度名"] for row in self.dimensions
            if row["指标名称"] == record["指标名称"]
        } & {
            row["维度名"] for row in self.dimensions
            if row["指标名称"] != record["指标名称"]
        })
        return {
            "本次指标": record["指标名称"],
            "实际指标版本": record["实际指标版本"],
            "同名维度": shared_dimensions,
            "已排除的其他指标": filtered_out,
            "说明": (
                "维度按指标名称、指标版本、分区日期、来源范围和分区完整匹配，"
                "同名维度不会跨指标混入" if shared_dimensions
                else "本次筛选未涉及同名维度"
            ),
        }

    def _fact_view(self, fact: dict[str, Any]) -> dict[str, Any]:
        chain = self.evidence[fact["证据标识"]]
        dimensions = [dict(row) for row in self.dimensions if row["事实标识"] == fact["事实标识"]]
        state = self._evidence_state(chain)
        return {
            **deepcopy(fact),
            "维度": dimensions,
            "维度行数": len(dimensions),
            "证据": state,
            "可重算": state["状态"] in _STATUS_VALID,
            # 历史发布结果保持原值，证据更正只存在于佐证修订里。
            "已发表值是否被更正覆盖": False,
        }

    def _evidence_state(self, chain: dict[str, Any]) -> dict[str, Any]:
        latest = chain["修订"][-1]
        return {
            "证据标识": chain["证据标识"],
            "修订号": latest["修订号"],
            "状态": latest["状态"],
            "佐证值": latest["佐证值"],
            "提交人": latest["提交人"],
            "提交时间": latest["提交时间"],
            "核验时间": latest["核验时间"],
            "变更说明": latest["变更说明"],
        }

    def _evidence_change(
        self, snapshot: dict[str, Any] | None, current: dict[str, Any]
    ) -> dict[str, Any]:
        if not snapshot:
            return {"变更": False}
        changed = (
            snapshot.get("修订号") != current.get("修订号")
            or snapshot.get("状态") != current.get("状态")
            or snapshot.get("佐证值") != current.get("佐证值")
        )
        return {
            "变更": changed,
            "变更前": snapshot if changed else None,
            "变更后": current if changed else None,
            "说明": "筛选快照保留变更前结果，已发表度量不随证据修订改写" if changed else "",
        }

    def _stats(self, facts: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "事实数": len(facts),
            "指标名称": sorted({fact["指标名称"] for fact in facts}),
            "指标版本": sorted({fact["指标版本"] for fact in facts}),
            "分区日期": sorted({fact["分区日期"] for fact in facts}),
            "来源范围": sorted({fact["来源范围"] for fact in facts}),
            "分区": sorted({fact["分区"] for fact in facts}),
            "已发表度量合计": round(sum(float(fact["度量"]) for fact in facts), 4),
        }

    # ------------------------------------------------------------------ 证据
    def evidence_history(self, evidence_ref: str) -> dict[str, Any]:
        """返回证据的全部修订，变更前后都能查；已发表度量保持原值。"""
        chain = self.evidence.get(evidence_ref)
        if chain is None:
            raise ValueError(f"证据 {evidence_ref} 不存在")
        revisions = [deepcopy(item) for item in chain["修订"]]
        latest = revisions[-1]
        return {
            "ok": True,
            "证据": {
                "证据标识": chain["证据标识"],
                "指标名称": chain["指标名称"],
                "指标版本": chain["指标版本"],
                "分区日期": chain["分区日期"],
                "来源范围": chain["来源范围"],
                "分区": chain["分区"],
            },
            "变更前": revisions[0],
            "变更后": latest if len(revisions) > 1 else None,
            "修订记录": revisions,
            "已发表结果": {
                "事实标识": chain["已发表事实标识"],
                "度量": chain["已发表度量"],
                "说明": "历史已发表结果保持原值，未被证据修订覆盖",
            },
        }

    def submit_evidence(
        self, evidence_ref: str, *, operator: str = "数据工程组", note: str = ""
    ) -> dict[str, Any]:
        """补交佐证：把最新待核修订标记为已核验，不改动已发表度量。"""
        chain = self.evidence.get(evidence_ref)
        if chain is None:
            raise ValueError(f"证据 {evidence_ref} 不存在")
        latest = chain["修订"][-1]
        if latest["状态"] in _STATUS_VALID:
            return {
                "ok": True,
                "提示": "该佐证已有效，无需补交",
                "证据": self.evidence_history(evidence_ref),
            }
        if latest["状态"] != "待核":
            raise ValueError(f"佐证状态为 {latest['状态']}，不能按补交处理")
        latest["状态"] = "已核验"
        latest["核验时间"] = _stamp()
        latest["提交人"] = operator
        if note:
            latest["变更说明"] = f"{latest['变更说明']}；{note}"
        return {
            "ok": True,
            "提示": "佐证已补交核验，已发表结果保持不变，可重新提交复算",
            "证据": self.evidence_history(evidence_ref),
        }

    def correct_evidence(
        self, evidence_ref: str, *, value: float, note: str, operator: str = "上游数据源"
    ) -> dict[str, Any]:
        """上游更正佐证：追加一条待核修订，旧修订标记已更正，不碰已发表度量。"""
        chain = self.evidence.get(evidence_ref)
        if chain is None:
            raise ValueError(f"证据 {evidence_ref} 不存在")
        latest = chain["修订"][-1]
        if latest["状态"] in _STATUS_VALID:
            latest["状态"] = "已更正"
        chain["修订"].append({
            "修订号": latest["修订号"] + 1,
            "状态": "待核",
            "佐证值": value,
            "提交人": operator,
            "提交时间": _stamp(),
            "核验时间": "",
            "变更说明": note,
        })
        # 佐证被更正后，同一筛选同一分区的既有受理立即失效，必须重新核对。
        invalidated = []
        for (scope_fingerprint, partition), acceptance in self.acceptances.items():
            if acceptance["状态"] != "已受理":
                continue
            if acceptance["佐证"]["证据标识"] != evidence_ref:
                continue
            acceptance["状态"] = "失效"
            acceptance["失效原因"] = "佐证已被上游更正并进入待核"
            acceptance["失效时间"] = _stamp()
            invalidated.append(acceptance["受理号"])
        return {
            "ok": True,
            "提示": "佐证已追加更正修订并进入待核，需重新提交复算核查",
            "失效受理": invalidated,
            "证据": self.evidence_history(evidence_ref),
        }

    def round_evidence(self, round_id: int, partition: str, token: str | None = None) -> dict[str, Any]:
        record = self.rounds.get(int(round_id))
        if record is None:
            raise ValueError(f"筛选轮次 {round_id} 不存在")
        if token and record["接续令牌"] != token:
            raise ValueError("接续令牌与筛选轮次不匹配")
        facts = [fact for fact in self._round_facts(record) if fact["分区"] == partition]
        if not facts:
            raise ValueError(f"筛选轮次 {round_id} 不包含分区 {partition}")
        fact = facts[0]
        history = self.evidence_history(fact["证据标识"])
        snapshot = record["证据快照"].get(fact["事实标识"])
        current = self._evidence_state(self.evidence[fact["证据标识"]])
        history["筛选快照"] = {
            "创建时证据": snapshot,
            "当前证据": current,
            "变更": self._evidence_change(snapshot, current),
        }
        history["本次筛选结果"] = {
            "已发表度量": fact["度量"],
            "指标版本": fact["指标版本"],
            "发布批次": fact["发布批次"],
            "说明": "筛选结果中的已发表度量始终是发布时的事实值",
        }
        return history

    # ------------------------------------------------------------------ 复算受理
    def _source_descriptor(self, fact: dict[str, Any]) -> dict[str, Any]:
        rows = _SOURCE_ROW_COUNTS.get(fact["分区"], 0) + (
            400 if fact["来源范围"] == "补录批次" else 0
        )
        return {
            "数据源名称": (
                f"{fact['指标版本']}·{fact['分区日期']}·{fact['来源范围']}·{fact['分区']}"
            ),
            "存储位置": (
                f"dw.published/{fact['指标版本']}/{fact['分区日期']}/"
                f"{fact['来源范围']}/{fact['分区']}"
            ),
            "指标版本": fact["指标版本"],
            "分区日期": fact["分区日期"],
            "来源范围": fact["来源范围"],
            "分区": fact["分区"],
            "行数": rows,
        }

    def _claim(self, acceptance_key: str, facts: list[dict[str, Any]]) -> dict[str, Any]:
        existing = self.claims.get(acceptance_key)
        if existing:
            return existing
        descriptors = [self._source_descriptor(fact) for fact in facts]
        claim = {
            "领取批次": f"CLAIM-{acceptance_key}",
            "数据源": descriptors,
            "领取行数": sum(int(item["行数"]) for item in descriptors),
            "领取时间": _stamp(),
        }
        self.claims[acceptance_key] = claim
        return claim

    def _valid_partitions(self, record: dict[str, Any]) -> set[str]:
        return {fact["分区"] for fact in self._round_facts(record)}

    def list_rounds(self) -> dict[str, Any]:
        """列出已创建的筛选轮次及其分区摘要，供预聚合任务选择接续。"""
        items = []
        for record in sorted(self.rounds.values(), key=lambda item: item["轮次号"]):
            facts = self._round_facts(record)
            view = self._round_view(record, resumed=False)
            items.append({
                "轮次号": record["轮次号"],
                "接续令牌": record["接续令牌"],
                "筛选指纹": record["筛选指纹"],
                "指标名称": record["指标名称"],
                "实际指标版本": record["实际指标版本"],
                "分区日期": record["分区日期"],
                "来源范围": record["来源范围"],
                "分区地区": record["分区地区"],
                "创建时间": record["创建时间"],
                "可重算分区": [item["分区"] for item in view["分区"] if item["可重算"]],
                "待核分区": [
                    item["分区"] for item in view["分区"]
                    if item["事实数"] and not item["可重算"]
                ],
                "快照事实数": len(facts),
            })
        return {"ok": True, "轮次": items, "总数": len(items)}

    def recompute(
        self,
        round_id: int,
        *,
        token: str,
        selections: list[dict[str, Any]],
        supplied: dict[str, str] | None = None,
        operator: str = "数据工程组",
        job_id: int | None = None,
    ) -> dict[str, Any]:
        """按接续筛选结果提交复算：同一筛选同一合格分区只生成一次有效受理。"""
        record = self.rounds.get(int(round_id))
        if record is None:
            raise ValueError(f"筛选轮次 {round_id} 不存在")
        if record["接续令牌"] != token:
            raise ValueError("接续令牌与筛选轮次不匹配")
        if not selections:
            raise ValueError("至少选择一个分区提交复算")

        fact_by_partition: dict[str, list[dict[str, Any]]] = {}
        for fact in self._round_facts(record):
            fact_by_partition.setdefault(fact["分区"], []).append(fact)

        scope_fingerprint = _fingerprint({
            "指标名称": record["指标名称"],
            "指标版本": record["实际指标版本"],
            "分区日期": record["分区日期"],
            "来源范围": record["来源范围"],
        })

        supplied = supplied or {}
        receipts: list[dict[str, Any]] = []
        recompute_scope: list[dict[str, Any]] = []
        pending_scope: list[dict[str, Any]] = []
        new_count = 0

        for selection in selections:
            partition = str(selection.get("分区") or "").strip()
            if partition not in record["分区地区"]:
                raise ValueError(f"分区 {partition} 不在本轮筛选范围内")
            if partition not in fact_by_partition:
                raise ValueError(f"分区 {partition} 在本轮筛选结果中没有已发表事实")
            defer = str(selection.get("佐证") or "").strip() == "暂缓"

            facts = fact_by_partition[partition]
            chain = self.evidence[facts[0]["证据标识"]]
            if supplied.get(partition):
                ref = supplied[partition]
                if ref != chain["证据标识"]:
                    raise ValueError(f"分区 {partition} 的补交佐证与筛选证据不匹配")
                self.submit_evidence(ref, operator=operator)

            latest = self.evidence[facts[0]["证据标识"]]["修订"][-1]
            if defer:
                eligible, reason = False, "本次暂缓佐证，分区未进入复算"
            elif latest["状态"] not in _STATUS_VALID:
                eligible, reason = False, f"佐证已更正待核（{latest['状态']}），需先补交核验"
            else:
                eligible, reason = True, ""

            acceptance_key = f"{scope_fingerprint}-{partition}"
            existing = self.acceptances.get((scope_fingerprint, partition))

            if not eligible:
                # 已受理的分区若佐证被更正，原受理立即判定失效并转入待核查。
                if existing and existing["状态"] == "已受理":
                    existing["状态"] = "失效"
                    existing["失效原因"] = reason
                    existing["失效时间"] = _stamp()
                pending = self._open_pending(
                    record, scope_fingerprint, partition, facts[0], reason, latest
                )
                pending_scope.append(pending)
                receipts.append({
                    "分区": partition,
                    "核查号": pending["核查号"],
                    "状态": "待核查",
                    "重复提交": False,
                    "原因": reason,
                    "后续核对": pending["后续核对"],
                })
                continue

            pending = self.pendings.get((scope_fingerprint, partition))
            if pending and pending["状态"] == "待核查":
                pending["状态"] = "已解除"
                pending["解除时间"] = _stamp()
                pending["关联受理号"] = f"RC-{acceptance_key}"
                self.resolved_pendings.append(deepcopy(pending))
                self.pendings.pop((scope_fingerprint, partition), None)

            duplicate = existing is not None and existing["状态"] == "已受理"
            if duplicate:
                acceptance = existing
            else:
                claim = self._claim(acceptance_key, facts)
                acceptance = {
                    "受理号": f"RC-{acceptance_key}",
                    "筛选指纹": record["筛选指纹"],
                    "受理键": acceptance_key,
                    "筛选轮次": record["轮次号"],
                    "分区": partition,
                    "状态": "已受理",
                    "指标名称": record["指标名称"],
                    "指标版本": record["实际指标版本"],
                    "分区日期": record["分区日期"],
                    "来源范围": record["来源范围"],
                    "事实标识": [fact["事实标识"] for fact in facts],
                    "佐证": {
                        "证据标识": chain["证据标识"],
                        "修订号": latest["修订号"],
                        "佐证值": latest["佐证值"],
                    },
                    "数据源": claim["数据源"],
                    "领取行数": claim["领取行数"],
                    "领取批次": claim["领取批次"],
                    "受理时间": _stamp(),
                    "重复受理": False,
                }
                self.acceptances[(scope_fingerprint, partition)] = acceptance
                new_count += 1

            recompute_scope.append(self._acceptance_public(acceptance, duplicate))
            receipts.append({
                "分区": partition,
                "受理号": acceptance["受理号"],
                "状态": "已受理",
                "重复提交": duplicate,
                "重复受理": duplicate,
                "受理时间": acceptance["受理时间"],
                "领取行数": acceptance["领取行数"],
            })

        result = {
            "ok": True,
            "筛选轮次": {
                "轮次号": record["轮次号"],
                "接续令牌": record["接续令牌"],
                "筛选指纹": record["筛选指纹"],
                "指标名称": record["指标名称"],
                "实际指标版本": record["实际指标版本"],
                "分区日期": record["分区日期"],
                "来源范围": record["来源范围"],
            },
            "实际重算范围": recompute_scope,
            "待核查范围": pending_scope,
            "回执": receipts,
            "汇总": {
                "选择分区数": len(selections),
                "已受理": len(recompute_scope),
                "本次新增受理": new_count,
                "重复提交": sum(1 for item in receipts if item.get("重复提交")),
                "待核查": len(pending_scope),
                "领取行数合计": sum(int(item["领取行数"]) for item in recompute_scope),
            },
            "说明": (
                "同一筛选同一合格分区只生成一次有效受理；失效或待核分区列出了后续核对要求"
            ),
        }
        if job_id is not None:
            result["聚合任务"] = {"任务标识": f"聚合任务 {job_id}", "读取筛选轮次": record["轮次号"]}
        return result

    def _open_pending(
        self,
        record: dict[str, Any],
        scope_fingerprint: str,
        partition: str,
        fact: dict[str, Any],
        reason: str,
        latest: dict[str, Any],
    ) -> dict[str, Any]:
        key = (scope_fingerprint, partition)
        existing = self.pendings.get(key)
        if existing:
            existing["原因"] = reason
            existing["佐证"] = {
                "证据标识": fact["证据标识"],
                "修订号": latest["修订号"],
                "状态": latest["状态"],
                "佐证值": latest["佐证值"],
            }
            existing["后续核对"]["原因"] = reason
            return existing
        pending = {
            "核查号": f"CHK-{scope_fingerprint}-{partition}",
            "筛选指纹": record["筛选指纹"],
            "筛选轮次": record["轮次号"],
            "分区": partition,
            "状态": "待核查",
            "原因": reason,
            "事实标识": fact["事实标识"],
            "已发表度量": fact["度量"],
            "佐证": {
                "证据标识": fact["证据标识"],
                "修订号": latest["修订号"],
                "状态": latest["状态"],
                "佐证值": latest["佐证值"],
            },
            "后续核对": {
                "必须": True,
                "原因": reason,
                "动作": "补交更正后的佐证后，用同一接续令牌重新提交复算",
                "责任岗位": "值班员",
                "关联证据": fact["证据标识"],
            },
            "记录时间": _stamp(),
        }
        self.pendings[key] = pending
        return pending

    def _acceptance_public(self, acceptance: dict[str, Any], duplicate: bool) -> dict[str, Any]:
        return {
            **deepcopy(acceptance),
            "重复受理": duplicate,
        }

    def list_receipts(self, fingerprint: str | None = None) -> dict[str, Any]:
        items = [
            deepcopy(item) for item in self.acceptances.values()
            if not fingerprint or item["筛选指纹"] == fingerprint or fingerprint in item["受理键"]
        ]
        pending = [
            deepcopy(item) for key, item in self.pendings.items()
            if not fingerprint or item["筛选指纹"] == fingerprint or fingerprint in item["核查号"]
        ]
        return {
            "ok": True,
            "受理": items,
            "待核查": pending,
            "汇总": {
                "有效受理数": sum(1 for item in items if item["状态"] == "已受理"),
                "失效受理数": sum(1 for item in items if item["状态"] == "失效"),
                "待核查数": len(pending),
                "已解除核查数": len(self.resolved_pendings),
            },
        }

    # ------------------------------------------------------------------ 预聚合联动
    def accept_for_aggregate_job(
        self, job_id: int, round_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """预聚合任务入口：按可接续筛选结果关联数据源并领取地区数据。"""
        token = str(payload.get("接续令牌") or "")
        selections = payload.get("选择分区") or []
        supplied = payload.get("补交佐证") or {}
        if not token:
            raise ValueError("缺少接续令牌，无法关联可接续筛选结果")
        if not isinstance(supplied, dict):
            raise ValueError("补交佐证必须是分区到证据标识的映射")
        result = self.recompute(
            round_id,
            token=token,
            selections=selections,
            supplied=supplied,
            operator=str(payload.get("提交人") or "数据工程组"),
            job_id=job_id,
        )
        result["聚合任务"] = {
            "任务标识": f"聚合任务 {job_id}",
            "读取筛选轮次": result["筛选轮次"]["轮次号"],
            "接续令牌": result["筛选轮次"]["接续令牌"],
            "关联实际数据源": sorted({
                source["数据源名称"]
                for item in result["实际重算范围"]
                for source in item.get("数据源", [])
            }),
            "实际重算分区": [item["分区"] for item in result["实际重算范围"]],
            "待核查分区": [item["分区"] for item in result["待核查范围"]],
        }
        return result


published_result_service = PublishedResultService()
