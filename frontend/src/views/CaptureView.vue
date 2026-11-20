<template>
  <section class="page" :data-module="`${moduleKey}-capture`">
    <header class="page-head">
      <div>
        <h2>{{ title }}</h2>
        <p class="page-desc">{{ description }}</p>
      </div>
      <div class="page-actions">
        <input
          v-model="urlFilter"
          class="url-input"
          data-testid="capture-url"
          placeholder="按页面 URL 限定（留空看全部）"
        />
        <button class="btn" data-testid="reload-capture" type="button" @click="reload">刷新</button>
        <button class="btn" data-testid="export-capture" type="button" @click="exportRows">
          导出文本来源数据集
        </button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="capture-error">{{ errorMessage }}</p>

    <template v-if="mode === 'dataset'">
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">有效样本块</span>
          <strong class="stat-value" data-testid="valid-sample-count">{{ dataset?.valid_sample_count ?? 0 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">块总数</span>
          <strong class="stat-value">{{ dataset?.block_count ?? 0 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">时效冲突块</span>
          <strong class="stat-value">{{ dataset?.stale_sample_count ?? 0 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">历史引用块</span>
          <strong class="stat-value">{{ dataset?.legacy_reference_count ?? 0 }}</strong>
        </article>
      </div>

      <p class="status-legend">
        <span v-for="version in dataset?.capture_versions ?? []" :key="version.capture_id" class="legend-item">
          采集版本{{ version.version }}：{{ version.status }}（{{ version.fragment_count }} 片）
        </span>
      </p>

      <table class="data-table">
        <thead>
          <tr>
            <th>块ID</th><th>采集版本</th><th>片段</th><th>块类型</th>
            <th>字符数</th><th>校验结论</th><th>有效样本</th><th>时效</th><th>文本内容</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in dataset?.blocks ?? []"
            :key="row.block_id"
            :data-testid="`source-row-${row.block_id}`"
            :data-valid="String(row.valid_sample)"
          >
            <td>{{ row.block_id }}</td>
            <td>{{ row.capture_version }}</td>
            <td>{{ row.selector }}</td>
            <td>{{ row.block_type }}</td>
            <td>{{ row.char_length }}</td>
            <td>{{ row.verdict }}</td>
            <td>{{ row.valid_sample ? '是' : '否' }}</td>
            <td>{{ row.freshness ?? '—' }}</td>
            <td>{{ row.text }}</td>
          </tr>
          <tr v-if="!(dataset?.blocks ?? []).length">
            <td colspan="9" class="empty-state">该页面没有采集到可用正文，未生成任何文本块</td>
          </tr>
        </tbody>
      </table>

      <h3>历史标注引用（不计入有效样本）</h3>
      <table class="data-table">
        <thead>
          <tr><th>块ID</th><th>来源页面</th><th>块类型</th><th>校验结论</th><th>说明</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in dataset?.legacy_references ?? []" :key="row.block_id">
            <td>{{ row.block_id }}</td>
            <td>{{ row.source_page }}</td>
            <td>{{ row.block_type }}</td>
            <td>{{ row.verdict }}</td>
            <td>{{ row.reason }}</td>
          </tr>
          <tr v-if="!(dataset?.legacy_references ?? []).length">
            <td colspan="5" class="empty-state">没有历史标注引用</td>
          </tr>
        </tbody>
      </table>
    </template>

    <template v-else-if="mode === 'backfill'">
      <p class="status-legend" data-testid="backfill-summary">
        待补采 {{ backfill?.pending ?? 0 }} 页 / 共跟踪 {{ backfill?.total ?? 0 }} 页
      </p>
      <table class="data-table">
        <thead>
          <tr><th>URL</th><th>状态</th><th>最近采集版本</th><th>最近采集状态</th><th>上次可用版本</th><th>原因</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in backfill?.items ?? []" :key="item.URL" :data-testid="`backfill-row-${item.URL}`">
            <td>{{ item.URL }}</td>
            <td>{{ item.状态 }}</td>
            <td>{{ item.最近采集版本 ?? '—' }}</td>
            <td>{{ item.最近采集状态 ?? '—' }}</td>
            <td>{{ item.上次可用采集版本 ?? '—' }}</td>
            <td>{{ item.原因 }}</td>
          </tr>
          <tr v-if="!(backfill?.items ?? []).length">
            <td colspan="6" class="empty-state">待补采清单为空</td>
          </tr>
        </tbody>
      </table>

      <p v-if="consistency" class="status-legend" data-testid="consistency-note">
        有效样本对账：{{ consistency.note }}
        <template v-for="(value, key) in consistency.values" :key="key">
          ｜{{ key }}：{{ value }}
        </template>
      </p>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  downloadSourceDataset,
  loadBackfill,
  loadConsistency,
  loadSourceDataset,
} from '@/api/capture-service'
import type { BackfillList, Consistency, SourceDataset } from '@/api/capture-service'

const props = defineProps<{ moduleKey: string }>()

const mode = computed(() => (props.moduleKey === 'backfill_list' ? 'backfill' : 'dataset'))
const title = computed(() =>
  mode.value === 'backfill' ? '待补采清单' : '文本来源校验数据集')
const description = computed(() =>
  mode.value === 'backfill'
    ? '最近一次采集为空正文或无法获取的页面；情感分析结果回写在这里。'
    : '按实际采集片段切分出的文本块及出处校验结论，情感标注只消费这里的有效块。')

const urlFilter = ref('')
const dataset = ref<SourceDataset | null>(null)
const backfill = ref<BackfillList | null>(null)
const consistency = ref<Consistency | null>(null)
const errorMessage = ref('')

function currentUrl(): string | undefined {
  return urlFilter.value.trim() || undefined
}

async function reload() {
  errorMessage.value = ''
  try {
    if (mode.value === 'dataset') {
      dataset.value = await loadSourceDataset(currentUrl())
    } else {
      backfill.value = await loadBackfill()
      consistency.value = await loadConsistency()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取失败'
  }
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadSourceDataset(currentUrl())
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

onMounted(reload)
</script>
