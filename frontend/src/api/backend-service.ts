import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ---- 已发表结果筛选与复算受理 ----
export type PublishedMeta = {
  指标清单: { 指标名称: string; 当前版本: string; 历史版本: string[] }[]
  来源范围: string[]
  分区日期: string[]
  分区地区: string[]
}

export type PublishedEvidenceState = {
  证据标识: string
  修订号: number
  状态: string
  佐证值: number
  提交人: string
  提交时间: string
  核验时间: string
  变更说明: string
}

export type PublishedFact = {
  id: number
  事实标识: string
  指标名称: string
  指标版本: string
  分区日期: string
  来源范围: string
  分区: string
  度量: number
  发布状态: string
  发布批次: string
  发布时间: string
  证据标识: string
  维度: Record<string, string | number>[]
  维度行数: number
  证据: PublishedEvidenceState
  可重算: boolean
  已发表值是否被更正覆盖: boolean
  证据变更?: { 变更: boolean; 变更前?: PublishedEvidenceState; 变更后?: PublishedEvidenceState; 说明?: string }
}

export type PublishedPartition = {
  分区: string
  事实数: number
  事实标识: string[]
  已发表度量: number[]
  证据标识: string[]
  证据状态: string
  可重算: boolean
}

export type PublishedCriteria = {
  指标名称: string
  指标版本?: string
  版本口径?: string
  分区日期?: string
  来源范围?: string
  分区地区?: string[]
}

export type PublishedRound = {
  轮次号: number
  接续令牌: string
  筛选指纹: string
  指标名称: string
  实际指标版本: string
  版本口径: string
  分区日期: string
  来源范围: string
  分区地区: string[]
  创建时间: string
  快照事实数: number
}

export type PublishedFilterResult = {
  ok: boolean
  接续: boolean
  筛选轮次: PublishedRound
  结果: PublishedFact[]
  分区: PublishedPartition[]
  统计: Record<string, unknown>
  同名维度隔离: Record<string, unknown>
  提示: string
}

export type RecomputeReceipt = {
  分区: string
  受理号?: string
  核查号?: string
  状态: string
  重复提交: boolean
  原因?: string
  领取行数?: number
  后续核对?: Record<string, unknown>
}

export type Acceptance = {
  受理号: string
  分区: string
  状态: string
  指标版本: string
  分区日期: string
  来源范围: string
  事实标识: string[]
  佐证: { 证据标识: string; 修订号: number; 佐证值: number }
  数据源: { 数据源名称: string; 存储位置: string; 行数: number }[]
  领取行数: number
  领取批次: string
  受理时间: string
  重复受理: boolean
}

export type RecomputeResult = {
  ok: boolean
  筛选轮次: PublishedRound
  实际重算范围: Acceptance[]
  待核查范围: {
    核查号: string
    分区: string
    原因: string
    事实标识: string
    已发表度量: number
    佐证: PublishedEvidenceState
    后续核对: Record<string, string>
  }[]
  回执: RecomputeReceipt[]
  汇总: Record<string, number>
}

export type EvidenceHistory = {
  ok: boolean
  证据: Record<string, string>
  变更前: Record<string, unknown>
  变更后: Record<string, unknown> | null
  修订记录: Record<string, unknown>[]
  已发表结果: { 事实标识: string; 度量: number; 说明: string }
  筛选快照?: { 变更: Record<string, unknown> }
  本次筛选结果?: Record<string, unknown>
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  const data = await response.json()
  if (!response.ok) throw new Error(data?.detail ?? '业务提交失败')
  return data as T
}

async function getJson<T>(path: string, params: Record<string, string | undefined> = {}): Promise<T> {
  const query = new URLSearchParams(
    Object.fromEntries(
      Object.entries(params).filter(([, v]) => v != null && v !== ''),
    ) as Record<string, string>,
  ).toString()
  const response = await request(`${path}${query ? `?${query}` : ''}`)
  const data = await response.json()
  if (!response.ok) throw new Error(data?.detail ?? '业务数据读取失败')
  return data as T
}

export function loadPublishedMeta(): Promise<PublishedMeta> {
  return getJson<PublishedMeta>('/api/domain/published_result/meta')
}

export function queryPublishedFacts(criteria: PublishedCriteria): Promise<{
  结果: PublishedFact[]
  统计: Record<string, unknown>
  筛选条件: Record<string, unknown>
  提示: string
}> {
  return getJson('/api/domain/published_result/facts', {
    指标名称: criteria.指标名称,
    指标版本: criteria.指标版本,
    版本口径: criteria.版本口径,
    分区日期: criteria.分区日期,
    来源范围: criteria.来源范围,
    分区地区: criteria.分区地区?.join(','),
  })
}

export function queryPublishedDimensions(params: {
  维度名: string
  指标名称: string
  指标版本: string
  分区日期: string
  来源范围: string
  分区?: string
}): Promise<{ 维度名: string; 口径: Record<string, string>; 行数: number; 结果: Record<string, string | number>[] }> {
  return getJson('/api/domain/published_result/dimensions', params)
}

export function createPublishedFilter(criteria: PublishedCriteria): Promise<PublishedFilterResult> {
  return postJson<PublishedFilterResult>('/api/domain/published_result/filter', { 筛选条件: criteria })
}

export function resumePublishedFilter(token: string): Promise<PublishedFilterResult> {
  return postJson<PublishedFilterResult>('/api/domain/published_result/filter', { 接续令牌: token })
}

export function listPublishedRounds(): Promise<{
  轮次: PublishedRound[]
  总数: number
}> {
  return getJson('/api/domain/published_result/rounds')
}

export function loadRound(roundId: number, token: string): Promise<PublishedFilterResult> {
  return getJson(`/api/domain/published_result/rounds/${roundId}`, { 接续令牌: token })
}

export function loadRoundEvidence(roundId: number, partition: string, token: string): Promise<EvidenceHistory> {
  return getJson(`/api/domain/published_result/rounds/${roundId}/evidence`, { 分区: partition, 接续令牌: token })
}

export function submitRecompute(
  roundId: number,
  token: string,
  selections: { 分区: string; 佐证?: string }[],
): Promise<RecomputeResult> {
  return postJson<RecomputeResult>(`/api/domain/published_result/rounds/${roundId}/recompute`, {
    接续令牌: token,
    选择分区: selections,
  })
}

export function acceptRecomputeForJob(
  jobId: number,
  roundId: number,
  token: string,
  selections: { 分区: string; 佐证?: string }[],
): Promise<RecomputeResult & { 聚合任务: Record<string, unknown> }> {
  return postJson(`/api/domain/aggregate_job/${jobId}/accept-recompute`, {
    筛选轮次: roundId,
    接续令牌: token,
    选择分区: selections,
  })
}

export function correctEvidence(
  evidenceRef: string,
  value: number,
  note = '',
): Promise<{ ok: boolean; 提示: string; 失效受理: string[]; 证据: EvidenceHistory }> {
  return postJson(`/api/domain/published_result/evidence/${evidenceRef}/correct`, {
    佐证值: value,
    变更说明: note,
  })
}

export function supplementEvidence(evidenceRef: string, note = ''): Promise<{ ok: boolean; 提示: string; 证据: EvidenceHistory }> {
  return postJson(`/api/domain/published_result/evidence/${evidenceRef}/submit`, { 说明: note })
}
