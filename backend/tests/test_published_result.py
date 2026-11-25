"""已发表事实筛选与复算受理测试：版本/日期/来源定位、重名维度隔离、可接续快照、
同一筛选同一分区的唯一有效受理、失效分区后续核对、证据变更前后可查。"""
import pytest

from app.services.published_result import PublishedResultService


HIST_CRITERIA = {
    "指标名称": "订单转化率",
    "版本口径": "历史",
    "分区日期": "2026-08-31",
    "来源范围": "官方数仓",
    "分区地区": ["华东", "华南", "华北"],
}


@pytest.fixture()
def svc():
    service = PublishedResultService()
    service.reset()
    return service


class TestPublishedFactQuery:
    def test_filters_by_actual_version_date_and_scope(self, svc):
        result = svc.query_facts({
            "指标名称": "订单转化率",
            "版本口径": "历史",
            "分区日期": "2026-08-31",
            "来源范围": "官方数仓",
            "分区地区": ["华东", "华南", "华北"],
        })
        matched = result["结果"]
        assert len(matched) == 3
        assert {row["指标版本"] for row in matched} == {"v2026.08-r1"}
        assert {row["分区日期"] for row in matched} == {"2026-08-31"}
        assert {row["来源范围"] for row in matched} == {"官方数仓"}
        assert {row["分区"] for row in matched} == {"华东", "华南", "华北"}

    def test_actual_version_excludes_history(self, svc):
        # 默认“实际”口径：订单转化率取当前 v2026.09-r2，历史 v2026.08-r1 不得混入
        result = svc.query_facts({
            "指标名称": "订单转化率",
            "分区日期": "2026-09-30",
            "来源范围": "官方数仓",
            "分区地区": ["华东"],
        })
        assert result["结果"]
        assert all(row["指标版本"] == "v2026.09-r2" for row in result["结果"])

    def test_source_scope_is_exact(self, svc):
        official = svc.query_facts({
            "指标名称": "订单转化率", "版本口径": "历史",
            "分区日期": "2026-08-31", "来源范围": "官方数仓", "分区地区": ["华东"],
        })["结果"]
        backfill = svc.query_facts({
            "指标名称": "订单转化率", "版本口径": "历史",
            "分区日期": "2026-08-31", "来源范围": "补录批次", "分区地区": ["华东"],
        })["结果"]
        assert [row["事实标识"] for row in official] == ["FACT-ORD-E-0831"]
        assert [row["事实标识"] for row in backfill] == ["FACT-ORD-E-BL-0831"]

    def test_unknown_partition_rejected(self, svc):
        with pytest.raises(ValueError):
            svc.query_facts({**HIST_CRITERIA, "分区地区": ["海外"]})

    def test_unknown_metric_rejected(self, svc):
        with pytest.raises(ValueError):
            svc.query_facts({**HIST_CRITERIA, "指标名称": "不存在的指标"})


class TestDuplicateDimensionIsolation:
    def test_dimension_requires_full_scope(self, svc):
        with pytest.raises(ValueError):
            svc.query_dimensions("渠道", metric="订单转化率", version="",
                                 partition_date="2026-08-31", source_scope="官方数仓")

    def test_same_named_dimension_not_mixed_across_metrics(self, svc):
        result = svc.query_dimensions(
            "渠道", metric="订单转化率", version="v2026.08-r1",
            partition_date="2026-08-31", source_scope="官方数仓",
        )
        assert result["行数"] > 0
        assert all(row["指标名称"] == "订单转化率" for row in result["结果"])
        values = {row["维度值"] for row in result["结果"]}
        # 订单转化率的“渠道”取值是比率，客单价同名维度取值是金额，不能混入
        assert values <= {"自然流量", "付费流量", "活动会场"}
        assert all(row["度量值"] < 1 for row in result["结果"])

    def test_dimension_rows_follow_fact_scope(self, svc):
        result = svc.query_dimensions(
            "渠道", metric="订单转化率", version="v2026.08-r1",
            partition_date="2026-08-31", source_scope="官方数仓", partition="华南",
        )
        assert all(row["分区"] == "华南" for row in result["结果"])


class TestResumableFilter:
    def test_round_snapshot_and_token(self, svc):
        first = svc.filter_published(HIST_CRITERIA)
        token = first["筛选轮次"]["接续令牌"]
        assert first["筛选轮次"]["实际指标版本"] == "v2026.08-r1"
        resumed = svc.filter_published({}, token=token)
        assert resumed["接续"] is True
        assert resumed["筛选轮次"]["轮次号"] == first["筛选轮次"]["轮次号"]
        assert len(resumed["结果"]) == len(first["结果"])

    def test_resumed_round_keeps_snapshot_after_evidence_correction(self, svc):
        first = svc.filter_published(HIST_CRITERIA)
        token = first["筛选轮次"]["接续令牌"]
        svc.correct_evidence("EV-ORD-S", value=0.6034, note="上游更正")
        resumed = svc.filter_published({}, token=token)
        south = next(item for item in resumed["结果"] if item["分区"] == "华南")
        # 已发表度量保持历史发布值，不被更正覆盖
        assert south["度量"] == first["结果"][1]["度量"] == 0.5812
        assert south["证据变更"]["变更"] is True
        assert south["证据变更"]["变更前"]["状态"] == "有效"
        assert south["证据变更"]["变更后"]["状态"] == "待核"

    def test_invalid_token_rejected(self, svc):
        with pytest.raises(ValueError):
            svc.filter_published({}, token="CT-NOT-EXIST")

    def test_token_must_match_round(self, svc):
        a = svc.filter_published(HIST_CRITERIA)
        b = svc.filter_published({
            "指标名称": "客单价", "版本口径": "实际",
            "分区日期": "2026-08-31", "来源范围": "官方数仓", "分区地区": ["华东"],
        })
        with pytest.raises(ValueError):
            svc.get_round(a["筛选轮次"]["轮次号"], token=b["筛选轮次"]["接续令牌"])


class TestRecomputeAcceptance:
    def test_one_acceptance_per_round_partition(self, svc):
        first = svc.filter_published(HIST_CRITERIA)
        token = first["筛选轮次"]["接续令牌"]
        rid = first["筛选轮次"]["轮次号"]
        out = svc.recompute(rid, token=token, selections=[{"分区": "华东"}, {"分区": "华北"}])
        assert {item["分区"] for item in out["实际重算范围"]} == {"华东", "华北"}
        assert out["汇总"]["本次新增受理"] == 2

        # 同一筛选同一分区重复点击：不再新增受理
        again = svc.recompute(rid, token=token, selections=[{"分区": "华东"}, {"分区": "华北"}])
        assert again["汇总"]["本次新增受理"] == 0
        assert all(item["重复提交"] for item in again["回执"])
        receipts = svc.list_receipts(first["筛选轮次"]["筛选指纹"])
        assert receipts["汇总"]["有效受理数"] == 2

    def test_deferred_partition_enters_pending(self, svc):
        first = svc.filter_published(HIST_CRITERIA)
        token = first["筛选轮次"]["接续令牌"]
        rid = first["筛选轮次"]["轮次号"]
        out = svc.recompute(
            rid, token=token,
            selections=[{"分区": "华东"}, {"分区": "华南", "佐证": "暂缓"}, {"分区": "华北"}],
        )
        assert {item["分区"] for item in out["实际重算范围"]} == {"华东", "华北"}
        pending = out["待核查范围"]
        assert len(pending) == 1
        assert pending[0]["分区"] == "华南"
        assert pending[0]["后续核对"]["必须"] is True
        assert pending[0]["后续核对"]["关联证据"] == "EV-ORD-S"

    def test_correction_invalidates_existing_acceptance(self, svc):
        first = svc.filter_published(HIST_CRITERIA)
        token = first["筛选轮次"]["接续令牌"]
        rid = first["筛选轮次"]["轮次号"]
        svc.recompute(rid, token=token, selections=[{"分区": "华东"}, {"分区": "华南"}, {"分区": "华北"}])
        assert svc.list_receipts()["汇总"]["有效受理数"] == 3

        correction = svc.correct_evidence("EV-ORD-S", value=0.6034, note="提交前上游更正")
        assert correction["失效受理"]
        receipts = svc.list_receipts(first["筛选轮次"]["筛选指纹"])
        assert receipts["汇总"]["有效受理数"] == 2
        assert receipts["汇总"]["失效受理数"] == 1

        # 重新提交：华南只能进入待核查，不能再生成有效受理
        retry = svc.recompute(rid, token=token, selections=[{"分区": "华东"}, {"分区": "华南"}])
        south = next(item for item in retry["回执"] if item["分区"] == "华南")
        assert south["状态"] == "待核查"
        assert retry["汇总"]["本次新增受理"] == 0
        assert retry["汇总"]["重复提交"] == 1
        pending = svc.list_receipts(first["筛选轮次"]["筛选指纹"])["待核查"]
        assert {item["分区"] for item in pending} == {"华南"}

    def test_supplement_then_retry_accepts_once(self, svc):
        first = svc.filter_published(HIST_CRITERIA)
        token = first["筛选轮次"]["接续令牌"]
        rid = first["筛选轮次"]["轮次号"]
        svc.recompute(rid, token=token, selections=[{"分区": "华东"}, {"分区": "华南", "佐证": "暂缓"}])
        svc.correct_evidence("EV-ORD-S", value=0.6034, note="上游更正")
        pending = svc.list_receipts(first["筛选轮次"]["筛选指纹"])["待核查"]
        assert {item["分区"] for item in pending} == {"华南"}

        supplement = svc.submit_evidence("EV-ORD-S", operator="数据工程组")
        assert supplement["证据"]["变更后"]["状态"] == "已核验"

        retry = svc.recompute(rid, token=token, selections=[{"分区": "华东"}, {"分区": "华南"}])
        south = next(item for item in retry["回执"] if item["分区"] == "华南")
        assert south["状态"] == "已受理"
        assert south["重复提交"] is False
        assert retry["汇总"]["本次新增受理"] == 1
        assert retry["汇总"]["待核查"] == 0
        receipts = svc.list_receipts(first["筛选轮次"]["筛选指纹"])
        assert receipts["汇总"]["待核查数"] == 0
        assert receipts["汇总"]["已解除核查数"] == 1
        assert receipts["汇总"]["有效受理数"] == 2

    def test_partition_outside_round_rejected(self, svc):
        first = svc.filter_published(HIST_CRITERIA)
        token = first["筛选轮次"]["接续令牌"]
        with pytest.raises(ValueError):
            svc.recompute(first["筛选轮次"]["轮次号"], token=token, selections=[{"分区": "西南"}])

    def test_mismatched_token_rejected(self, svc):
        first = svc.filter_published(HIST_CRITERIA)
        with pytest.raises(ValueError):
            svc.recompute(first["筛选轮次"]["轮次号"], token="CT-WRONG", selections=[{"分区": "华东"}])


class TestEvidenceHistory:
    def test_history_has_before_and_after(self, svc):
        svc.correct_evidence("EV-ORD-S", value=0.6034, note="支付渠道修正")
        history = svc.evidence_history("EV-ORD-S")
        assert history["变更前"]["修订号"] == 1
        assert history["变更前"]["状态"] == "已更正"
        assert history["变更后"]["修订号"] == 2
        assert history["变更后"]["状态"] == "待核"
        assert len(history["修订记录"]) == 2

    def test_published_metric_never_rewritten(self, svc):
        svc.correct_evidence("EV-ORD-S", value=0.6034, note="支付渠道修正")
        history = svc.evidence_history("EV-ORD-S")
        assert history["已发表结果"]["度量"] == 0.5812
        fact = next(row for row in svc.facts if row["事实标识"] == "FACT-ORD-S-0831")
        assert fact["度量"] == 0.5812

    def test_supplement_idempotent_on_valid(self, svc):
        result = svc.submit_evidence("EV-ORD-E")
        assert "无需补交" in result["提示"]

    def test_submit_rejects_non_pending(self, svc):
        svc.correct_evidence("EV-ORD-S", value=0.6034, note="上游更正")
        svc.submit_evidence("EV-ORD-S")
        # 已核验后再次补交应为幂等空操作，不产生新修订
        again = svc.submit_evidence("EV-ORD-S")
        assert "无需补交" in again["提示"]
        assert len(again["证据"]["修订记录"]) == 2


class TestAggregateJobHandoff:
    def test_job_links_round_to_real_sources(self, svc):
        first = svc.filter_published(HIST_CRITERIA)
        token = first["筛选轮次"]["接续令牌"]
        rid = first["筛选轮次"]["轮次号"]
        out = svc.accept_for_aggregate_job(7, rid, {
            "接续令牌": token,
            "选择分区": [{"分区": "华东"}, {"分区": "华南", "佐证": "暂缓"}, {"分区": "华北"}],
        })
        assert out["聚合任务"]["实际重算分区"] == ["华东", "华北"]
        assert out["聚合任务"]["待核查分区"] == ["华南"]
        sources = out["聚合任务"]["关联实际数据源"]
        assert len(sources) == 2
        assert all("v2026.08-r1" in name and "官方数仓" in name for name in sources)
        for item in out["实际重算范围"]:
            assert item["领取行数"] > 0
            assert item["数据源"][0]["存储位置"].startswith("dw.published/v2026.08-r1/")

    def test_job_requires_token(self, svc):
        first = svc.filter_published(HIST_CRITERIA)
        rid = first["筛选轮次"]["轮次号"]
        with pytest.raises(ValueError):
            svc.accept_for_aggregate_job(7, rid, {"选择分区": [{"分区": "华东"}]})


class TestRoundListing:
    def test_lists_rounds_with_recomputable_partitions(self, svc):
        first = svc.filter_published(HIST_CRITERIA)
        svc.correct_evidence("EV-ORD-S", value=0.6034, note="上游更正")
        listing = svc.list_rounds()
        assert listing["总数"] == 1
        row = listing["轮次"][0]
        assert row["轮次号"] == first["筛选轮次"]["轮次号"]
        assert row["可重算分区"] == ["华东", "华北"]
        assert row["待核分区"] == ["华南"]


class TestDomainRouterLayer:
    """不依赖 TestClient 的路由层验证：参数别名、错误码映射和聚合任务入口。"""

    def test_meta_endpoint(self):
        from app.routers import domain
        meta = domain.published_meta()
        assert set(meta["来源范围"]) == {"官方数仓", "补录批次"}
        assert "华东" in meta["分区地区"]

    def test_facts_endpoint_alias_params(self):
        from app.routers import domain
        result = domain.published_facts(
            metric="订单转化率", version_mode="历史", partition_date="2026-08-31",
            source_scope="官方数仓", partition="华东,华北",
        )
        assert [row["事实标识"] for row in result["结果"]] == ["FACT-ORD-E-0831", "FACT-ORD-N-0831"]

    def test_dimension_missing_scope_maps_to_400(self):
        from fastapi import HTTPException
        from app.routers import domain
        with pytest.raises(HTTPException) as exc:
            domain.published_dimensions(
                dimension="渠道", metric="订单转化率", version="",
                partition_date="2026-08-31", source_scope="官方数仓",
            )
        assert exc.value.status_code == 400

    def test_aggregate_job_requires_round_id(self):
        from fastapi import HTTPException
        from app.routers import domain
        with pytest.raises(HTTPException) as exc:
            domain.aggregate_job_accept_recompute(1, {"接续令牌": "CT-01"})
        assert exc.value.status_code == 400
