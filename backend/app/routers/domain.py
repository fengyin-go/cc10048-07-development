"""领域业务操作接口：每个模块的计算/处理/聚合等真实业务操作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from app.services import SERVICES
from app.services.published_result import published_result_service as published

router = APIRouter(prefix="/api/domain", tags=["领域业务"])


def _published(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _plain(value: Any) -> Any:
    """直接调用路由函数时 Query(default=...) 会留下 FieldInfo，这里统一收敛成 None。"""
    return value if isinstance(value, (str, int, float, bool)) or value is None else None


@router.get("/published_result/meta")
def published_meta() -> dict[str, Any]:
    """可筛选口径元数据：指标版本、来源范围、分区日期和地区。"""
    return published.scopes()


@router.get("/published_result/facts")
def published_facts(
    metric: str = Query(alias="指标名称"),
    version: str | None = Query(default=None, alias="指标版本"),
    version_mode: str = Query(default="实际", alias="版本口径"),
    partition_date: str | None = Query(default=None, alias="分区日期"),
    source_scope: str | None = Query(default=None, alias="来源范围"),
    partition: str | None = Query(default=None, alias="分区地区"),
) -> dict[str, Any]:
    """直接查询已发表事实和维度数据，按实际指标版本/分区日期/来源范围精确筛选。"""
    return _published(
        published.query_facts,
        {
            "指标名称": metric,
            "指标版本": _plain(version),
            "版本口径": _plain(version_mode) or "实际",
            "分区日期": _plain(partition_date),
            "来源范围": _plain(source_scope),
            "分区地区": _plain(partition),
        },
    )


@router.get("/published_result/dimensions")
def published_dimensions(
    dimension: str = Query(alias="维度名"),
    metric: str = Query(alias="指标名称"),
    version: str = Query(alias="指标版本"),
    partition_date: str = Query(alias="分区日期"),
    source_scope: str = Query(alias="来源范围"),
    partition: str | None = Query(default=None, alias="分区"),
) -> dict[str, Any]:
    """按完整口径取维度数据，同名维度不会跨指标混入。"""
    return _published(
        published.query_dimensions,
        dimension,
        metric=metric,
        version=version,
        partition_date=partition_date,
        source_scope=source_scope,
        partition=_plain(partition),
    )


@router.post("/published_result/filter")
def published_filter(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """创建或接续一轮可续筛选，返回接续令牌和事实/证据快照。"""
    criteria = payload.get("筛选条件") or payload.get("criteria") or payload
    token = payload.get("接续令牌") or payload.get("token")
    return _published(published.filter_published, criteria, token)


@router.get("/published_result/rounds")
def published_rounds() -> dict[str, Any]:
    """列出已创建的筛选轮次，供预聚合任务挑选可接续结果。"""
    return published.list_rounds()


@router.get("/published_result/rounds/{round_id}")
def published_round(
    round_id: int,
    token: str | None = Query(default=None, alias="接续令牌"),
) -> dict[str, Any]:
    """按轮次号读取筛选结果，可用接续令牌校验并回看历史快照。"""
    return _published(published.get_round, round_id, _plain(token))


@router.get("/published_result/rounds/{round_id}/evidence")
def published_round_evidence(
    round_id: int,
    partition: str = Query(alias="分区"),
    token: str | None = Query(default=None, alias="接续令牌"),
) -> dict[str, Any]:
    """查看某分区在筛选时的证据快照和当前证据修订，变更前后都在。"""
    return _published(published.round_evidence, round_id, partition, _plain(token))


@router.post("/published_result/rounds/{round_id}/recompute")
def published_recompute(
    round_id: int,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """值班员提交复算：同一筛选同一合格分区只生成一次有效受理。"""
    token = str(payload.get("接续令牌") or payload.get("token") or "")
    selections = payload.get("选择分区") or payload.get("selections") or []
    supplied = payload.get("补交佐证") or payload.get("supplied") or {}
    return _published(
        published.recompute,
        round_id,
        token=token,
        selections=selections,
        supplied=supplied,
        operator=str(payload.get("提交人") or "数据工程组"),
    )


@router.get("/published_result/receipts")
def published_receipts(fingerprint: str | None = Query(default=None)) -> dict[str, Any]:
    """查看有效受理、失效受理和待核查清单。"""
    return published.list_receipts(_plain(fingerprint))


@router.get("/published_result/evidence/{evidence_ref}")
def published_evidence(evidence_ref: str) -> dict[str, Any]:
    """证据修订历史：变更前/变更后/已发表原值一并返回。"""
    return _published(published.evidence_history, evidence_ref)


@router.post("/published_result/evidence/{evidence_ref}/submit")
def published_evidence_submit(
    evidence_ref: str,
    payload: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    """补交佐证，把待核修订标记为已核验。"""
    return _published(
        published.submit_evidence,
        evidence_ref,
        operator=str(payload.get("提交人") or "数据工程组"),
        note=str(payload.get("说明") or ""),
    )


@router.post("/published_result/evidence/{evidence_ref}/correct")
def published_evidence_correct(
    evidence_ref: str,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """上游更正佐证：追加待核修订，已发表度量不动。"""
    try:
        value = float(payload.get("佐证值"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="佐证值必须是数字") from exc
    return _published(
        published.correct_evidence,
        evidence_ref,
        value=value,
        note=str(payload.get("变更说明") or "上游数据源更正"),
        operator=str(payload.get("提交人") or "上游数据源"),
    )


@router.get("/metric_def/{metric_id}/compute")
def metric_compute(metric_id: int) -> dict[str, Any]:
    """指标聚合计算。"""
    from app.services.metric_def import MetricDefService
    svc = SERVICES.get("metric_def")
    if not svc or not isinstance(svc, MetricDefService):
        raise HTTPException(404, "指标口径服务不可用")
    return svc.domain_compute(metric_id)


@router.get("/dim_query/{query_id}/execute")
def dim_query_execute(query_id: int) -> dict[str, Any]:
    """多维交叉查询执行。"""
    from app.services.dim_query import DimQueryService
    svc = SERVICES.get("dim_query")
    if not svc or not isinstance(svc, DimQueryService):
        raise HTTPException(404, "多维查询服务不可用")
    return svc.execute_query(query_id)


@router.get("/experiment/{experiment_id}/compute-effect")
def experiment_compute(experiment_id: int) -> dict[str, Any]:
    """实验效果计算。"""
    from app.services.experiment import ExperimentService
    svc = SERVICES.get("experiment")
    if not svc or not isinstance(svc, ExperimentService):
        raise HTTPException(404, "实验管理服务不可用")
    return svc.compute_effect(experiment_id)


@router.get("/significance/{task_id}/run-test")
def significance_run_test(task_id: int) -> dict[str, Any]:
    """显著性检验执行。"""
    from app.services.significance import SignificanceService
    svc = SERVICES.get("significance")
    if not svc or not isinstance(svc, SignificanceService):
        raise HTTPException(404, "显著性检验服务不可用")
    return svc.run_test(task_id)


@router.get("/cohort/{cohort_id}/estimate-scale")
def cohort_estimate(cohort_id: int) -> dict[str, Any]:
    """人群规模预估。"""
    from app.services.cohort import CohortService
    svc = SERVICES.get("cohort")
    if not svc or not isinstance(svc, CohortService):
        raise HTTPException(404, "人群圈选服务不可用")
    return svc.estimate_scale(cohort_id)


@router.get("/data_service/{service_id}/invoke")
def data_service_invoke(service_id: int) -> dict[str, Any]:
    """数据服务接口调用。"""
    from app.services.data_service import DataServiceService
    svc = SERVICES.get("data_service")
    if not svc or not isinstance(svc, DataServiceService):
        raise HTTPException(404, "数据服务不可用")
    return svc.invoke_service(service_id)


@router.get("/anomaly_detect/{rule_id}/detect")
def anomaly_detect_run(rule_id: int) -> dict[str, Any]:
    """异常检测执行。"""
    from app.services.anomaly_detect import AnomalyDetectService
    svc = SERVICES.get("anomaly_detect")
    if not svc or not isinstance(svc, AnomalyDetectService):
        raise HTTPException(404, "异常检测服务不可用")
    return svc.detect_anomalies(rule_id)


@router.get("/analysis_chart/{chart_id}/render")
def chart_render(chart_id: int) -> dict[str, Any]:
    """图表数据渲染。"""
    from app.services.analysis_chart import AnalysisChartService
    svc = SERVICES.get("analysis_chart")
    if not svc or not isinstance(svc, AnalysisChartService):
        raise HTTPException(404, "分析图表服务不可用")
    return svc.render_chart_data(chart_id)


@router.get("/aggregate_job/{job_id}/execute")
def aggregate_job_execute(job_id: int) -> dict[str, Any]:
    """预聚合任务执行。"""
    from app.services.aggregate_job import AggregateJobService
    svc = SERVICES.get("aggregate_job")
    if not svc or not isinstance(svc, AggregateJobService):
        raise HTTPException(404, "预聚合任务服务不可用")
    return svc.execute_aggregate(job_id)


@router.post("/aggregate_job/{job_id}/accept-recompute")
def aggregate_job_accept_recompute(
    job_id: int,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """预聚合任务按可接续筛选结果关联实际数据源、领取地区数据并返回重算/待核查范围。"""
    from app.services.aggregate_job import AggregateJobService
    svc = SERVICES.get("aggregate_job")
    if not svc or not isinstance(svc, AggregateJobService):
        raise HTTPException(404, "预聚合任务服务不可用")
    round_id = payload.get("筛选轮次")
    if not isinstance(round_id, int):
        raise HTTPException(400, "缺少筛选轮次")
    return _published(published.accept_for_aggregate_job, job_id, round_id, payload)


@router.get("/drill_down/{path_id}/execute")
def drill_down_execute(path_id: int) -> dict[str, Any]:
    """下钻分析执行。"""
    from app.services.drill_down import DrillDownService
    svc = SERVICES.get("drill_down")
    if not svc or not isinstance(svc, DrillDownService):
        raise HTTPException(404, "下钻分析服务不可用")
    return svc.execute_drill_down(path_id)


@router.get("/data_export/{task_id}/execute")
def data_export_execute(task_id: int) -> dict[str, Any]:
    """数据导出执行。"""
    from app.services.data_export import DataExportService
    svc = SERVICES.get("data_export")
    if not svc or not isinstance(svc, DataExportService):
        raise HTTPException(404, "数据导出服务不可用")
    return svc.execute_export(task_id)


@router.get("/data_freshness/{check_id}/check")
def data_freshness_check(check_id: int) -> dict[str, Any]:
    """数据时效检查执行。"""
    from app.services.data_freshness import DataFreshnessService
    svc = SERVICES.get("data_freshness")
    if not svc or not isinstance(svc, DataFreshnessService):
        raise HTTPException(404, "数据时效服务不可用")
    return svc.run_freshness_check(check_id)


@router.get("/query_cache/{strategy_id}/check-status")
def query_cache_check(strategy_id: int) -> dict[str, Any]:
    """查询缓存状态检查。"""
    from app.services.query_cache import QueryCacheService
    svc = SERVICES.get("query_cache")
    if not svc or not isinstance(svc, QueryCacheService):
        raise HTTPException(404, "查询缓存服务不可用")
    return svc.check_cache_status(strategy_id)


@router.get("/segment_compare/{plan_id}/execute")
def segment_compare_execute(plan_id: int) -> dict[str, Any]:
    """分群对比执行。"""
    from app.services.segment_compare import SegmentCompareService
    svc = SERVICES.get("segment_compare")
    if not svc or not isinstance(svc, SegmentCompareService):
        raise HTTPException(404, "分群对比服务不可用")
    return svc.execute_compare(plan_id)


@router.get("/metric_calibrate/{record_id}/compute")
def metric_calibrate_compute(record_id: int) -> dict[str, Any]:
    """指标校准偏差计算。"""
    from app.services.metric_calibrate import MetricCalibrateService
    svc = SERVICES.get("metric_calibrate")
    if not svc or not isinstance(svc, MetricCalibrateService):
        raise HTTPException(404, "指标校准服务不可用")
    return svc.compute_calibration(record_id)


@router.post("/query_log/log")
def query_log_record() -> dict[str, Any]:
    """查询操作记录。"""
    from app.services.query_log import QueryLogService
    svc = SERVICES.get("query_log")
    if not svc or not isinstance(svc, QueryLogService):
        raise HTTPException(404, "查询日志服务不可用")
    return svc.log_query()


@router.get("/data_sample/{config_id}/execute")
def data_sample_execute(config_id: int) -> dict[str, Any]:
    """数据采样执行。"""
    from app.services.data_sample import DataSampleService
    svc = SERVICES.get("data_sample")
    if not svc or not isinstance(svc, DataSampleService):
        raise HTTPException(404, "数据采样服务不可用")
    return svc.execute_sampling(config_id)


@router.get("/report_schedule/{task_id}/generate")
def report_schedule_generate(task_id: int) -> dict[str, Any]:
    """报告生成。"""
    from app.services.report_schedule import ReportScheduleService
    svc = SERVICES.get("report_schedule")
    if not svc or not isinstance(svc, ReportScheduleService):
        raise HTTPException(404, "报告调度服务不可用")
    return svc.generate_report(task_id)