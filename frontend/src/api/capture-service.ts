/** 文本来源校验数据集与待补采清单的接口封装。 */
import { request } from './client'

export type SourceBlockRow = {
  block_id: string
  source_page: string | null
  block_type: string
  char_length: string
  capture_id: string | null
  capture_version: string | null
  fragment_index: string | null
  selector: string | null
  text: string
  text_sha256: string | null
  verdict: string
  valid_sample: boolean
  freshness: string | null
  stale_conflict: boolean
  reason: string
}

export type SourceCaptureVersion = {
  capture_id: number
  version: number
  status: string
  fetched_at: string
  fragment_count: number
  char_length: number
}

export type SourceDataset = {
  ok: boolean
  url: string | null
  capture_versions: SourceCaptureVersion[]
  block_count: number
  valid_sample_count: number
  stale_sample_count: number
  blocks: SourceBlockRow[]
  legacy_reference_count: number
  legacy_references: SourceBlockRow[]
}

export type BackfillItem = {
  URL: string
  状态: string
  原因: string
  最近采集ID?: number
  最近采集版本?: number
  最近采集状态?: string
  上次可用采集版本?: number
  更新时间?: string
}

export type BackfillList = {
  ok: boolean
  total: number
  pending: number
  items: BackfillItem[]
}

export type Consistency = {
  ok: boolean
  values: Record<string, number>
  unanalyzed_valid_blocks: string[]
  excluded_records: number
  mismatches: string[]
  note: string
}

export async function loadSourceDataset(url?: string): Promise<SourceDataset> {
  const query = url ? `?url=${encodeURIComponent(url)}` : ''
  const response = await request(`/api/capture/text-source${query}`)
  if (!response.ok) throw new Error('文本来源校验数据集读取失败')
  return (await response.json()) as SourceDataset
}

export async function loadBackfill(): Promise<BackfillList> {
  const response = await request('/api/capture/backfill')
  if (!response.ok) throw new Error('待补采清单读取失败')
  return (await response.json()) as BackfillList
}

export async function loadConsistency(): Promise<Consistency> {
  const response = await request('/api/capture/consistency')
  if (!response.ok) throw new Error('有效样本对账读取失败')
  return (await response.json()) as Consistency
}

export async function analyzePage(url: string): Promise<Record<string, unknown>> {
  const response = await request('/api/capture/sentiment/analyze', {
    method: 'POST',
    body: JSON.stringify({ url }),
  })
  if (!response.ok) throw new Error('情感分析失败')
  return (await response.json()) as Record<string, unknown>
}

export async function downloadSourceDataset(url?: string): Promise<void> {
  const query = url ? `?url=${encodeURIComponent(url)}` : ''
  const response = await request(`/api/capture/text-source/export${query}`)
  if (!response.ok) throw new Error('文本来源数据集导出失败')
  const blobUrl = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = blobUrl
  anchor.download = 'text_source_dataset.csv'
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(blobUrl)
}
