<template>
  <section class="published-panel" data-testid="published-panel">
    <header class="panel-head">
      <div>
        <h3>已发表结果筛选</h3>
        <p class="page-desc">
          按实际指标版本、分区日期和来源范围定位已发表事实与维度数据；同一轮筛选可凭接续令牌回看，
          同名维度只在本指标口径内取数。
        </p>
      </div>
      <button class="btn ghost" type="button" data-testid="published-refresh" @click="loadMeta">
        刷新口径
      </button>
    </header>

    <form class="filter-grid" @submit.prevent="submitFilter">
      <label class="filter-item">
        <span>指标名称</span>
        <select v-model="criteria.指标名称" data-testid="published-metric" @change="onMetricChange">
          <option v-for="metric in metadata?.指标清单 ?? []" :key="metric.指标名称" :value="metric.指标名称">
            {{ metric.指标名称 }}
          </option>
        </select>
      </label>
      <label class="filter-item">
        <span>版本口径</span>
        <select v-model="criteria.版本口径" data-testid="published-version-mode">
          <option value="实际">实际（当前发布版本）</option>
          <option value="历史">历史版本</option>
          <option value="指定">指定版本号</option>
        </select>
      </label>
      <label class="filter-item">
        <span>实际指标版本</span>
        <input
          v-model="criteria.指标版本"
          data-testid="published-version"
          :disabled="criteria.版本口径 !== '指定'"
          :placeholder="resolvedVersion || '按版本口径自动定位'"
        />
      </label>
      <label class="filter-item">
        <span>分区日期</span>
        <select v-model="criteria.分区日期" data-testid="published-date">
          <option v-for="date in metadata?.分区日期 ?? []" :key="date" :value="date">{{ date }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>来源范围</span>
        <select v-model="criteria.来源范围" data-testid="published-scope">
          <option v-for="scope in metadata?.来源范围 ?? []" :key="scope" :value="scope">{{ scope }}</option>
        </select>
      </label>
      <fieldset class="filter-item partition-picks">
        <span>分区地区</span>
        <label v-for="part in metadata?.分区地区 ?? []" :key="part" class="inline-check">
          <input v-model="criteria.分区地区" type="checkbox" :value="part" :data-testid="`published-part-${part}`" />
          {{ part }}
        </label>
      </fieldset>
      <button class="btn primary" type="submit" data-testid="published-filter" :disabled="busy">
        {{ busyFilter ? '筛选中…' : '查找已发表结果' }}
      </button>
    </form>

    <form class="resume-bar" @submit.prevent="submitResume">
      <label class="filter-item">
        <span>接续令牌</span>
        <input v-model="tokenInput" data-testid="published-token" placeholder="如 CT-01-xxxxxxxx" />
      </label>
      <button class="btn" type="submit" data-testid="published-resume" :disabled="busy">接续筛选</button>
      <span class="hint">{{ resolvedVersion ? `当前口径将定位到 ${resolvedVersion}` : '请选择指标' }}</span>
    </form>

    <p v-if="errorMessage" class="error-text" data-testid="published-error">{{ errorMessage }}</p>

    <article v-if="round" class="round-card" data-testid="published-round">
      <header class="round-head">
        <strong>筛选轮次 #{{ round.筛选轮次.轮次号 }}</strong>
        <span class="tag">{{ round.筛选轮次.实际指标版本 }}</span>
        <span class="tag">{{ round.筛选轮次.分区日期 }}</span>
        <span class="tag">{{ round.筛选轮次.来源范围 }}</span>
        <span class="token">接续令牌 {{ round.筛选轮次.接续令牌 }}</span>
      </header>
      <p class="hint">
        快照事实 {{ round.筛选轮次.快照事实数 }} 条；筛选指纹 {{ round.筛选轮次.筛选指纹 }}；
        {{ round.接续 ? '本次为接续回看，结果取创建时快照' : '已生成新的筛选轮次' }}
      </p>

      <table class="data-table">
        <thead>
          <tr>
            <th>参与复算</th>
            <th>分区</th>
            <th>事实标识</th>
            <th>已发表度量</th>
            <th>证据状态</th>
            <th>可重算</th>
            <th>暂缓佐证</th>
            <th>证据</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="part in round.分区" :key="part.分区" :data-testid="`published-row-${part.分区}`">
            <td>
              <input
                v-model="picks[part.分区]!.selected"
                type="checkbox"
                :disabled="!part.可重算"
                :data-testid="`published-select-${part.分区}`"
              />
            </td>
            <td>{{ part.分区 }}</td>
            <td>{{ part.事实标识.join('、') || '—' }}</td>
            <td>{{ part.已发表度量.join('、') || '—' }}</td>
            <td>
              <span class="tag" :class="`evidence-${part.证据状态}`">{{ part.证据状态 }}</span>
            </td>
            <td>{{ part.可重算 ? '是' : '否' }}</td>
            <td>
              <input
                v-model="picks[part.分区]!.defer"
                type="checkbox"
                :data-testid="`published-defer-${part.分区}`"
              />
            </td>
            <td>
              <button
                class="link"
                type="button"
                :data-testid="`published-evidence-${part.分区}`"
                @click="viewEvidence(part.分区)"
              >
                查看变更前后
              </button>
            </td>
          </tr>
          <tr v-if="!round.分区.length">
            <td colspan="8" class="empty-state">没有匹配的已发表事实，请核对版本、日期与来源范围</td>
          </tr>
        </tbody>
      </table>

      <p class="hint" data-testid="published-isolation">
        同名维度隔离：{{ sharedDimensions }}；已排除 {{ excludedMetrics }} 的其他指标口径。
      </p>

      <div v-if="props.mode === 'aggregate_job'" class="job-handoff">
        <label class="filter-item">
          <span>预聚合任务标识</span>
          <input v-model.number="jobId" type="number" min="1" data-testid="published-job-id" />
        </label>
        <button class="btn" type="button" data-testid="published-job-accept" :disabled="busy" @click="acceptForJob">
          按筛选领取地区数据
        </button>
        <span class="hint">领取实际数据源后返回实际重算与待核查范围</span>
      </div>

      <div class="page-actions">
        <button class="btn primary" type="button" data-testid="published-submit" :disabled="busy" @click="submitRecompute">
          {{ busyRecompute ? '提交中…' : '提交复算' }}
        </button>
        <button class="btn" type="button" data-testid="published-dimensions" :disabled="busy" @click="lookupDimensions">
          查看同名维度
        </button>
        <span class="hint">重复点击不会重复受理；失效分区会列入待核查</span>
      </div>
    </article>

    <article v-if="receipts" class="round-card" data-testid="published-receipt">
      <header class="round-head">
        <strong>实际重算范围</strong>
        <span class="tag">{{ receipts.汇总['已受理'] }} 个已受理</span>
        <span class="tag">{{ receipts.汇总['待核查'] }} 个待核查</span>
        <span class="tag">领取 {{ receipts.汇总['领取行数合计'] }} 行</span>
      </header>
      <table class="data-table">
        <thead>
          <tr>
            <th>分区</th>
            <th>状态</th>
            <th>受理号 / 核查号</th>
            <th>重复提交</th>
            <th>数据源</th>
            <th>领取行数</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in receipts.实际重算范围" :key="item.受理号" :data-testid="`acceptance-${item.分区}`">
            <td>{{ item.分区 }}</td>
            <td>{{ item.重复受理 ? '已受理（原受理有效）' : '已受理' }}</td>
            <td>{{ item.受理号 }}</td>
            <td>{{ item.重复受理 ? '是' : '否' }}</td>
            <td>{{ item.数据源.map((s) => s.存储位置).join('；') }}</td>
            <td>{{ item.领取行数 }}</td>
          </tr>
          <tr v-for="item in receipts.待核查范围" :key="item.核查号" :data-testid="`pending-${item.分区}`">
            <td>{{ item.分区 }}</td>
            <td>待核查</td>
            <td>{{ item.核查号 }}</td>
            <td>—</td>
            <td>{{ item.原因 }}</td>
            <td>待补交</td>
          </tr>
        </tbody>
      </table>
      <p v-for="item in receipts.待核查范围" :key="`follow-${item.核查号}`" class="hint">
        {{ item.分区 }} 后续核对：{{ item.后续核对['动作'] }}（关联证据 {{ item.后续核对['关联证据'] }}）
      </p>
    </article>

    <article v-if="evidence" class="round-card" data-testid="published-evidence-card">
      <header class="round-head">
        <strong>证据 {{ evidence.证据['证据标识'] }}</strong>
        <span class="tag">{{ evidence.证据['指标名称'] }} · {{ evidence.证据['指标版本'] }}</span>
        <button class="link" type="button" @click="evidence = null">关闭</button>
      </header>
      <p class="hint">
        已发表结果 {{ evidence.已发表结果.事实标识 }} = {{ evidence.已发表结果.度量 }}。
        {{ evidence.已发表结果.说明 }}。
      </p>
      <table class="data-table">
        <thead>
          <tr><th>修订号</th><th>状态</th><th>佐证值</th><th>提交人</th><th>提交时间</th><th>核验时间</th><th>变更说明</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in evidence.修订记录" :key="String(item['修订号'])">
            <td>{{ item['修订号'] }}</td>
            <td>
              <span class="tag" :class="`evidence-${item['状态']}`">{{ item['状态'] }}</span>
            </td>
            <td>{{ item['佐证值'] }}</td>
            <td>{{ item['提交人'] }}</td>
            <td>{{ item['提交时间'] }}</td>
            <td>{{ item['核验时间'] || '—' }}</td>
            <td>{{ item['变更说明'] }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="evidence.筛选快照?.变更" class="hint" data-testid="evidence-change">
        筛选快照变更前：{{ describeState(evidence.筛选快照.变更['变更前']) }}；
        变更后：{{ describeState(evidence.筛选快照.变更['变更后']) }}。
      </p>
      <div class="page-actions">
        <button
          class="btn"
          type="button"
          data-testid="published-supplement"
          :disabled="busy"
          @click="supplementCurrent"
        >
          补交佐证
        </button>
        <label class="filter-item">
          <span>上游更正佐证值</span>
          <input v-model="correctionValue" data-testid="published-correction-value" placeholder="如 0.6034" />
        </label>
        <button
          class="btn ghost"
          type="button"
          data-testid="published-correct"
          :disabled="busy"
          @click="registerCorrection"
        >
          登记上游更正
        </button>
        <span class="hint">补交只核验佐证修订，不改写历史已发表度量</span>
      </div>
    </article>

    <article v-if="dimensions" class="round-card" data-testid="published-dimension-card">
      <header class="round-head">
        <strong>同名维度「{{ dimensions.维度名 }}」</strong>
        <span class="tag">{{ dimensions.口径['指标名称'] }} · {{ dimensions.口径['指标版本'] }}</span>
        <span class="tag">共 {{ dimensions.行数 }} 行</span>
      </header>
      <table class="data-table">
        <thead>
          <tr><th>分区</th><th>维度值</th><th>度量值</th><th>指标版本</th><th>来源范围</th></tr>
        </thead>
        <tbody>
          <tr v-for="(row, idx) in dimensions.结果" :key="idx">
            <td>{{ row['分区'] }}</td>
            <td>{{ row['维度值'] }}</td>
            <td>{{ row['度量值'] }}</td>
            <td>{{ row['指标版本'] }}</td>
            <td>{{ row['来源范围'] }}</td>
          </tr>
        </tbody>
      </table>
      <p class="hint">同名维度按指标口径完整匹配，其他指标的同类维度不会混入。</p>
    </article>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import {
  acceptRecomputeForJob,
  correctEvidence,
  createPublishedFilter,
  loadPublishedMeta,
  loadRound,
  loadRoundEvidence,
  queryPublishedDimensions,
  resumePublishedFilter,
  submitRecompute as submitRecomputeApi,
  supplementEvidence,
} from '@/api/backend-service'
import type {
  EvidenceHistory,
  PublishedCriteria,
  PublishedFilterResult,
  PublishedMeta,
  RecomputeResult,
} from '@/api/backend-service'

type DimensionsResult = { 维度名: string; 口径: Record<string, string>; 行数: number; 结果: Record<string, string | number>[] }

const metadata = ref<PublishedMeta | null>(null)
const props = defineProps<{ mode?: string }>()
const criteria = reactive<PublishedCriteria & { 指标版本: string }>({
  指标名称: '订单转化率',
  版本口径: '历史',
  指标版本: '',
  分区日期: '2026-08-31',
  来源范围: '官方数仓',
  分区地区: ['华东', '华南', '华北'],
})
const tokenInput = ref('')
const round = ref<PublishedFilterResult | null>(null)
const receipts = ref<RecomputeResult | null>(null)
const evidence = ref<EvidenceHistory | null>(null)
const evidencePartition = ref('')
const dimensions = ref<DimensionsResult | null>(null)
const errorMessage = ref('')
const busyFilter = ref(false)
const busyRecompute = ref(false)
const busy = computed(() => busyFilter.value || busyRecompute.value)
const picks = ref<Record<string, { selected: boolean; defer: boolean }>>({})
const jobId = ref(1)
const correctionValue = ref('')

const resolvedVersion = computed(() => {
  const metric = metadata.value?.指标清单.find((item) => item.指标名称 === criteria.指标名称)
  if (!metric) return ''
  if (criteria.版本口径 === '指定') return criteria.指标版本
  if (criteria.版本口径 === '历史') return metric.历史版本[0] ?? metric.当前版本
  return metric.当前版本
})

const sharedDimensions = computed(() => {
  const names = round.value?.同名维度隔离?.['同名维度']
  return Array.isArray(names) && names.length ? names.join('、') : '无'
})

const excludedMetrics = computed(() => {
  const names = round.value?.同名维度隔离?.['已排除的其他指标']
  return Array.isArray(names) && names.length ? names.join('、') : '未涉及'
})

function onMetricChange() {
  criteria.指标版本 = ''
}

function ensurePicks(result: PublishedFilterResult) {
  const next: Record<string, { selected: boolean; defer: boolean }> = {}
  for (const part of result.分区) {
    const previous = picks.value[part.分区]
    next[part.分区] = {
      selected: previous ? previous.selected : part.可重算,
      defer: previous ? previous.defer : false,
    }
  }
  picks.value = next
}

function describeState(state: unknown): string {
  if (!state || typeof state !== 'object') return '—'
  const record = state as Record<string, unknown>
  return `修订 ${record['修订号']} · ${record['状态']} · 佐证值 ${record['佐证值']}`
}

async function loadMeta() {
  errorMessage.value = ''
  try {
    metadata.value = await loadPublishedMeta()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '口径元数据读取失败'
  }
}

async function submitFilter() {
  errorMessage.value = ''
  busyFilter.value = true
  try {
    const payload: PublishedCriteria = {
      指标名称: criteria.指标名称,
      分区日期: criteria.分区日期,
      来源范围: criteria.来源范围,
      分区地区: criteria.分区地区,
      ...(criteria.版本口径 === '指定'
        ? { 指标版本: criteria.指标版本 }
        : { 版本口径: criteria.版本口径 }),
    }
    const result = await createPublishedFilter(payload)
    round.value = result
    receipts.value = null
    dimensions.value = null
    tokenInput.value = result.筛选轮次.接续令牌
    ensurePicks(result)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '筛选失败'
  } finally {
    busyFilter.value = false
  }
}

async function submitResume() {
  if (!tokenInput.value.trim()) {
    errorMessage.value = '请先填写接续令牌'
    return
  }
  errorMessage.value = ''
  busyFilter.value = true
  try {
    const resumed = await resumePublishedFilter(tokenInput.value.trim())
    round.value = resumed
    receipts.value = null
    dimensions.value = null
    ensurePicks(resumed)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '接续失败'
  } finally {
    busyFilter.value = false
  }
}

async function submitRecompute() {
  if (!round.value) return
  const selections = currentSelections()
  if (!selections.length) {
    errorMessage.value = '至少勾选一个分区提交复算'
    return
  }
  errorMessage.value = ''
  busyRecompute.value = true
  try {
    receipts.value = await submitRecomputeApi(
      round.value.筛选轮次.轮次号,
      round.value.筛选轮次.接续令牌,
      selections,
    )
    round.value = await loadRound(round.value.筛选轮次.轮次号, round.value.筛选轮次.接续令牌)
    ensurePicks(round.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '提交复算失败'
  } finally {
    busyRecompute.value = false
  }
}

function currentSelections() {
  if (!round.value) return []
  return round.value.分区
    .filter((part) => picks.value[part.分区]?.selected || picks.value[part.分区]?.defer)
    .map((part) => ({
      分区: part.分区,
      ...(picks.value[part.分区]?.defer ? { 佐证: '暂缓' } : {}),
    }))
}

async function acceptForJob() {
  if (!round.value) return
  const selections = currentSelections()
  if (!selections.length) {
    errorMessage.value = '至少勾选一个分区领取数据'
    return
  }
  errorMessage.value = ''
  busyRecompute.value = true
  try {
    const result = await acceptRecomputeForJob(
      Number(jobId.value),
      round.value.筛选轮次.轮次号,
      round.value.筛选轮次.接续令牌,
      selections,
    )
    receipts.value = result
    round.value = await loadRound(round.value.筛选轮次.轮次号, round.value.筛选轮次.接续令牌)
    ensurePicks(round.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '聚合任务领取失败'
  } finally {
    busyRecompute.value = false
  }
}

async function viewEvidence(partition: string) {
  if (!round.value) return
  errorMessage.value = ''
  evidencePartition.value = partition
  try {
    evidence.value = await loadRoundEvidence(
      round.value.筛选轮次.轮次号,
      partition,
      round.value.筛选轮次.接续令牌,
    )
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '证据读取失败'
  }
}

async function registerCorrection() {
  if (!evidence.value) return
  const value = Number(correctionValue.value)
  if (!Number.isFinite(value)) {
    errorMessage.value = '更正佐证值必须是数字'
    return
  }
  errorMessage.value = ''
  busyRecompute.value = true
  try {
    await correctEvidence(String(evidence.value.证据['证据标识']), value, '上游数据源回传口径更正')
    if (round.value) {
      evidence.value = await loadRoundEvidence(
        round.value.筛选轮次.轮次号,
        evidencePartition.value,
        round.value.筛选轮次.接续令牌,
      )
      round.value = await loadRound(round.value.筛选轮次.轮次号, round.value.筛选轮次.接续令牌)
      ensurePicks(round.value)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '更正登记失败'
  } finally {
    busyRecompute.value = false
  }
}

async function supplementCurrent() {
  if (!evidence.value) return
  errorMessage.value = ''
  busyRecompute.value = true
  try {
    const result = await supplementEvidence(String(evidence.value.证据['证据标识']))
    errorMessage.value = ''
    if (round.value) {
      evidence.value = await loadRoundEvidence(
        round.value.筛选轮次.轮次号,
        evidencePartition.value,
        round.value.筛选轮次.接续令牌,
      )
      round.value = await loadRound(round.value.筛选轮次.轮次号, round.value.筛选轮次.接续令牌)
      ensurePicks(round.value)
    }
    if (!result.ok) throw new Error(result.提示)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补交佐证失败'
  } finally {
    busyRecompute.value = false
  }
}

async function lookupDimensions() {
  if (!round.value) return
  errorMessage.value = ''
  busyRecompute.value = true
  try {
    const first = round.value.结果[0]
    dimensions.value = await queryPublishedDimensions({
      维度名: '渠道',
      指标名称: round.value.筛选轮次.指标名称,
      指标版本: round.value.筛选轮次.实际指标版本,
      分区日期: round.value.筛选轮次.分区日期,
      来源范围: round.value.筛选轮次.来源范围,
      分区: first?.分区,
    })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '维度读取失败'
  } finally {
    busyRecompute.value = false
  }
}

onMounted(loadMeta)
</script>

<style scoped>
.published-panel { margin: 16px 0; padding: 14px; background: #fff; border: 1px solid var(--border); border-radius: 10px; }
.panel-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.panel-head h3 { margin: 0; }
.filter-grid { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; margin: 12px 0; }
.partition-picks { border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px 8px; margin: 0; }
.inline-check { margin-right: 8px; font-size: 13px; }
.resume-bar { display: flex; gap: 10px; align-items: flex-end; margin-bottom: 10px; }
.round-card { border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-top: 12px; background: #fafbfc; }
.round-head { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 8px; }
.tag { display: inline-block; background: #eef2ff; color: #1f3a8a; border-radius: 10px; padding: 2px 8px; font-size: 12px; }
.token { font-family: monospace; font-size: 12px; color: var(--muted); }
.hint { color: var(--muted); font-size: 12px; margin: 6px 0; }
.evidence-有效, .evidence-已核验 { background: #dcfce7; color: #166534; }
.evidence-待核 { background: #fef9c3; color: #854d0e; }
.evidence-已更正, .evidence-无已发表事实 { background: #fee2e2; color: #991b1b; }
</style>
