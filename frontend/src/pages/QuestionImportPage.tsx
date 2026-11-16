import { Alert, Button, Card, Empty, Input, InputNumber, Modal, Progress, Select, Space, Table, Tag, Typography, message } from 'antd'
import { useEffect, useState } from 'react'
import { apiPost, apiPostForm, apiGet, apiUrl } from '../api/client'
import { RichQuestionText } from '../components/RichQuestionText'

type Batch = {
  id: string
  source_mode: string
  status: string
  stage: string
  files: Array<{ file_id: string; original_name: string; size_bytes: number; page_count: number | null; role: string }>
  checkpoint: {
    s2?: { calls?: number; page_count?: number; prompt_version?: string; auto_global_dedupe?: boolean; units?: Array<{ index: number; first_page: number; last_page: number; core_first_page: number; core_last_page: number; status: string; candidate_count?: number; source_answers?: Array<{ original_number: string | null; text: string; page_start: number; candidate_item_ids?: string[] }>; source_answer_signals?: Array<{ page: number; evidence: string }>; overlap_suspects?: Array<{ candidate_item_id: string; other_item_id: string }>; unresolved_fragments?: Array<{ page: number; text: string; reason: string }> }> }
    s3?: { global_dedupe?: { status: string; reviewed_candidate_count?: number; removed_duplicate_count?: number; error_message?: string; estimated_cny?: number }; separate_answers?: { status: string; calls?: number; error_message?: string; units?: Array<{ index: number; first_page: number; core_last_page: number; status: string; source_answers?: Array<{ original_number: string | null; text: string; page_start: number; candidate_item_ids?: string[] }>; unresolved_fragments?: Array<{ page: number; text: string; reason: string }> }> }; source_answer_reconciliation?: { schema_version: string; source_answer_count: number; linked_answer_count: number; linked_question_count: number; overlap_duplicate_answers?: Array<{ text: string; page_start: number; matched_original_number: string; matched_page: number }>; unassigned_answers: Array<{ original_number: string | null; text: string; page_start: number; reason: string; candidate_item_ids?: string[] }> } }
  }
  error_code: string | null
  error_message: string | null
  attempt_count: number
  created_at: string
  expires_at: string
  files_purged_at: string | null
}

type ReviewIssue = {
  key: string
  kind: string
  summary: string
  fragment_text?: string | null
  fragment_reason?: string | null
  answer_text?: string | null
  answer_number?: string | null
  page_numbers: number[]
  candidate_item_ids: string[]
  candidate_total: number
  recall_method: string | null
  review: null | { status: string; stale?: boolean; page_numbers: number[]; estimated_cny?: number; error_message?: string; advice?: { verdict: string; candidate_item_id: string | null; source_page: number; evidence_quote: string; reason: string } }
}

type ReviewUnit = {
  unit_index: number
  issue_keys: string[]
  page_numbers: number[]
  candidate_count: number
  skipped_count: number
  available: boolean
}

type Candidate = {
  id: string
  ordinal: number
  revision: number
  is_fake: boolean
  payload: {
    source: { original_number: string | null; page_start: number | null; page_end: number | null }
    content: { question_text: string; options: Array<{ key: string; text: string }>; asset_ids?: string[] }
    extraction?: { math_fragments?: string[]; diagram_clues?: string[]; figure_status?: 'no_figure' | 'complete' | 'uncertain'; uncertain_fragments?: string[]; source_classification?: string; classification_evidence?: string }
    answers?: Array<{ answer_id: string; answer_source: string; text: string | null; teacher_confirmed: boolean }>
    answer_match_status?: string
    duplicate_status?: string
    warnings?: string[]
  }
}

type S2Config = { ready: boolean; model: string; max_budget_cny: number; max_output_tokens: number; pages_per_unit: number; overlap_pages: number }

type ImageAsset = { asset_id: string; kind: string; page: number | null; bbox: number[] | null; width: number; height: number; sha256: string; candidate_item_ids: string[]; removed_candidate_for_item_ids?: string[]; duplicate_of_kept_image?: boolean }
type ImageInventory = { version: string | null; page_count: number | null; embedded_scanned: boolean; files_available: boolean; assets: ImageAsset[] }

const modeLabels: Record<string, string> = {
  questions_only: '只有题目', combined: '题目与答案同文件', separate: '题目与答案分文件',
}

const reviewVerdictLabels: Record<string, string> = {
  same_question: '可能是同一题', different_questions: '可能是不同题',
  match_candidate: '可能归属所列候选', no_match: '未找到可信归属', false_positive: '疑似提取误读，原页无此片段', uncertain: '仍无法确定，需人工核对',
}

function stageLabel(batch: Batch): string {
  if (batch.stage === 'awaiting_s2') return '文件已校验，等待 S2 解析'
  if (batch.stage === 's2_queued') return '等待识别题目文件'
  if (batch.stage === 's2_processing') return '正在识别题目文件页段'
  if (batch.stage === 's2_completed') {
    const dedupe = batch.checkpoint?.s3?.global_dedupe?.status
    if (dedupe === 'auto_claimed' || dedupe === 'running') return batch.source_mode === 'combined' ? '正在整批去重和关联答案' : '正在整批去重题目'
    if (dedupe === 'pending_budget') return '整批去重待补预算'
    if (dedupe === 'failed') return '整批去重失败，可重试'
    if (!dedupe && batch.checkpoint?.s2?.auto_global_dedupe) return '等待整批去重'
    if (batch.source_mode === 'separate' && dedupe === 'completed') {
      const answers = batch.checkpoint?.s3?.separate_answers?.status
      if (answers === 'queued') return '答案文件等待识别'
      if (answers === 'processing') return '正在识别答案文件'
      if (answers === 'paused') return '答案文件识别已暂停'
      if (answers === 'failed') return '答案文件识别失败，可重试'
      if (answers === 'completed') return '答案文件识别完成，待核对'
      return '题目去重完成，待启动答案文件识别'
    }
    return 'S2 已完成，待人工核对'
  }
  if (batch.stage === 's2_budget_paused') return 'S2 已暂停，需核对预算'
  if (batch.stage === 's2_failed') return 'S2 处理失败'
  if (batch.stage === 'files_saved') return '文件已保存，等待校验'
  if (batch.stage === 'validating_files') return '正在校验文件'
  if (batch.stage === 'expired') return '临时文件已过期'
  if (batch.status === 'cancelled') return '已取消'
  if (batch.status === 'failed') return '处理失败'
  return batch.stage
}

export function QuestionImportPage() {
  const [mode, setMode] = useState('combined')
  const [questionFile, setQuestionFile] = useState<File | null>(null)
  const [answerFile, setAnswerFile] = useState<File | null>(null)
  const [batches, setBatches] = useState<Batch[]>([])
  const [batchPage, setBatchPage] = useState(1)
  const [focusedBatchId, setFocusedBatchId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [s2Config, setS2Config] = useState<S2Config | null>(null)
  const [budget, setBudget] = useState<number | null>(2)
  const [operatorToken, setOperatorToken] = useState('')
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null)
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [showDuplicateCandidates, setShowDuplicateCandidates] = useState(false)
  const [reviewIssues, setReviewIssues] = useState<ReviewIssue[]>([])
  const [reviewUnits, setReviewUnits] = useState<ReviewUnit[]>([])
  const [reviewingKey, setReviewingKey] = useState<string | null>(null)
  const [images, setImages] = useState<ImageInventory | null>(null)
  const [imageBusy, setImageBusy] = useState(false)

  async function reload(focusBatchId?: string) {
    try {
      const rows = await apiGet<Batch[]>('/question-imports')
      setBatches(rows)
      if (focusBatchId) {
        const index = rows.findIndex((batch) => batch.id === focusBatchId)
        if (index >= 0) setBatchPage(Math.floor(index / 10) + 1)
      }
    }
    catch (error) { message.error(error instanceof Error ? error.message : '批次列表加载失败') }
  }

  useEffect(() => {
    void reload()
    void apiGet<S2Config>('/question-imports/s2-config').then(setS2Config).catch(() => undefined)
    const timer = window.setInterval(() => { void reload() }, 10000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!selectedBatchId) return
    const timer = window.setInterval(() => {
      void apiGet<Candidate[]>(`/question-imports/${selectedBatchId}/items`)
        .then(setCandidates).catch(() => undefined)
    }, 10000)
    return () => window.clearInterval(timer)
  }, [selectedBatchId])

  function upload() {
    if (!questionFile || (mode === 'separate' && !answerFile)) {
      message.warning('请选择题目文件；分文件场景还需答案文件')
      return
    }
    const files = [questionFile, ...(mode === 'separate' && answerFile ? [answerFile] : [])]
    if (files.some((file) => file.size > 30 * 1024 * 1024) || files.reduce((sum, file) => sum + file.size, 0) > 60 * 1024 * 1024) {
      message.error('单文件上限 30 MB，单批上限 60 MB')
      return
    }
    const body = new FormData()
    body.set('source_mode', mode)
    body.set('question_file', questionFile)
    if (mode === 'separate' && answerFile) body.set('answer_file', answerFile)
    setUploading(true)
    setProgress(0)
    const request = new XMLHttpRequest()
    request.open('POST', apiUrl('/question-imports'))
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) setProgress(Math.round(event.loaded / event.total * 100))
    }
    request.onload = () => {
      setUploading(false)
      if (request.status >= 200 && request.status < 300) {
        const batch = JSON.parse(request.responseText) as Batch
        setFocusedBatchId(batch.id)
        if (request.status === 200) message.info('文件内容与已有批次相同，已定位原批次；未重复创建')
        else message.success('上传完成，已建立暂存批次')
        setQuestionFile(null)
        setAnswerFile(null)
        void reload(batch.id)
      } else {
        try { message.error(JSON.parse(request.responseText).message || '上传失败') }
        catch { message.error('上传失败') }
      }
    }
    request.onerror = () => { setUploading(false); message.error('上传网络错误') }
    request.send(body)
  }

  async function act(id: string, action: 'retry' | 'cancel') {
    setLoading(true)
    try {
      const batch = batches.find((item) => item.id === id)
      if (action === 'retry' && batch?.stage === 's2_failed' && !operatorToken) {
        message.warning('请输入 S2 操作口令')
        return
      }
      await apiPost(`/question-imports/${id}/${action}`, {}, undefined,
        action === 'retry' && batch?.stage === 's2_failed' ? { 'X-Import-S2-Token': operatorToken } : undefined)
      await reload()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '操作失败')
    } finally { setLoading(false) }
  }

  function confirmRetry(batch: Batch) {
    if (batch.error_code !== 'INCOMPLETE_MODEL_OUTPUT') {
      void act(batch.id, 'retry')
      return
    }
    Modal.confirm({
      title: '自动调整失败页段并重试？',
      content: '系统会缩小失败页段或增加后续页作为上下文，已完成的页段不会重跑。新的模型调用可能产生费用，仍受本批预算阈值约束。',
      okText: '确认重试', cancelText: '取消',
      onOk: () => act(batch.id, 'retry'),
    })
  }

  function confirmStart(batch: Batch) {
    if (!budget) { message.warning('请设置本批调用预算阈值'); return }
    if (!operatorToken) { message.warning('请输入 S2 操作口令'); return }
    Modal.confirm({
      title: '确认发送题目文件到百炼？',
      content: `将题目文件按页段发送给百炼 ${s2Config?.model || ''}，页段识别完成后自动把整批候选题发送给模型去重${batch.source_mode === 'combined' ? '并关联同文件原文答案' : ''}。请确认文件不含学生个人信息。本批调用预算阈值为 ¥${budget}；这是基于已返回用量的停机阈值，单次调用可能使实际费用略高。`,
      okText: '开始识别', cancelText: '取消',
      onOk: async () => {
        try {
          await apiPost(`/question-imports/${batch.id}/start-s2`, { budget_cny: budget }, undefined,
            { 'X-Import-S2-Token': operatorToken })
          message.success('S2 任务已排队')
          await reload()
        } catch (error) { message.error(error instanceof Error ? error.message : '启动失败') }
      },
    })
  }

  async function viewCandidates(id: string) {
    try {
      const [items, issues, units, initialImages] = await Promise.all([
        apiGet<Candidate[]>(`/question-imports/${id}/items`),
        apiGet<ReviewIssue[]>(`/question-imports/${id}/s3/review-issues`),
        apiGet<ReviewUnit[]>(`/question-imports/${id}/s3/review-units`),
        apiGet<ImageInventory>(`/question-imports/${id}/s3/images`),
      ])
      let imageRows = initialImages
      if (!imageRows.embedded_scanned && imageRows.files_available && batches.find((batch) => batch.id === id)?.stage === 's2_completed') {
        try { imageRows = await apiPost<ImageInventory>(`/question-imports/${id}/s3/images`, {}) }
        catch (error) { message.warning(error instanceof Error ? error.message : 'PDF 图片自动提取失败') }
      }
      setCandidates(items)
      setImages(imageRows)
      setShowDuplicateCandidates(false)
      setReviewIssues(issues)
      setReviewUnits(units)
      setSelectedBatchId(id)
    } catch (error) { message.error(error instanceof Error ? error.message : '候选题加载失败') }
  }

  async function refreshImages(id: string) {
    setImages(await apiGet<ImageInventory>(`/question-imports/${id}/s3/images`))
  }

  async function linkQuestionImage(batch: Batch, asset: ImageAsset, candidate: Candidate, linked: boolean) {
    setImageBusy(true)
    try {
      const updated = await apiPost<Candidate>(`/question-imports/${batch.id}/s3/images/${asset.asset_id}/items/${candidate.id}`,
        { expected_revision: candidate.revision, linked })
      setCandidates((rows) => rows.map((row) => row.id === updated.id ? updated : row))
      await refreshImages(batch.id)
      message.success(linked ? '题图已关联' : '题图已删除')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '题图关联失败')
      setCandidates(await apiGet<Candidate[]>(`/question-imports/${batch.id}/items`).catch(() => candidates))
    } finally { setImageBusy(false) }
  }

  async function uploadCandidateImage(batch: Batch, candidate: Candidate, file: File) {
    if (file.size > 8 * 1024 * 1024) { message.error('单张图片不能超过 8 MB'); return }
    const body = new FormData()
    body.set('expected_revision', String(candidate.revision))
    body.set('file', file)
    setImageBusy(true)
    try {
      const updated = await apiPostForm<Candidate>(`/question-imports/${batch.id}/s3/items/${candidate.id}/images`, body)
      setCandidates((rows) => rows.map((row) => row.id === updated.id ? updated : row))
      await refreshImages(batch.id)
      message.success('图片已添加到该题')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '题图上传失败')
      setCandidates(await apiGet<Candidate[]>(`/question-imports/${batch.id}/items`).catch(() => candidates))
    } finally { setImageBusy(false) }
  }

  function confirmIssueReview(batch: Batch, issue: ReviewIssue) {
    if (!budget || !operatorToken) { message.warning('请先填写预算阈值和 S2 操作口令'); return }
    Modal.confirm({
      title: '确认发送问题原页给百炼复核？',
      content: `问题：${issue.summary}。将${batch.source_mode === 'separate' ? '题目文件' : '原文件'}第 ${issue.page_numbers.join('、')} 页及相关候选题文字发送给百炼 ${s2Config?.model || ''}${issue.kind === 'fragment' ? '，先核对片段是否真实存在，再判断归属' : ''}。本批 S3 复核预算阈值为 ¥${budget}，单次调用仍可能略超阈值；未确定片段只记录模型建议。`,
      okText: '发送并复核', cancelText: '取消',
      onOk: async () => {
        setReviewingKey(issue.key)
        try {
          const result = await apiPost<ReviewIssue['review']>(`/question-imports/${batch.id}/s3/review-issues/${issue.key}`,
            { budget_cny: budget }, undefined, { 'X-Import-S2-Token': operatorToken })
          if (result?.status === 'completed') {
            message[result.advice?.verdict === 'uncertain' ? 'warning' : 'success'](
              result.advice?.verdict === 'uncertain'
                ? '模型仍无法确定，复核建议已保存，需人工核对'
                : result.advice?.verdict === 'false_positive'
                  ? '模型认为原片段是提取误读，建议对照原页确认'
                : issue.kind === 'fragment'
                  ? '片段复核建议已保存，仍需人工核对'
                  : '模型复核已保存，确定的重复题或答案归属已处理')
          } else {
            message.warning(result?.error_message || '复核尚未完成')
          }
          await reload(batch.id)
          await viewCandidates(batch.id)
        } catch (error) { message.error(error instanceof Error ? error.message : '复核失败') }
        finally { setReviewingKey(null) }
      },
    })
  }

  function confirmUnitReview(batch: Batch, unit: ReviewUnit) {
    if (!budget || !operatorToken) { message.warning('请先填写预算阈值和 S2 操作口令'); return }
    Modal.confirm({
      title: `确认一次复核第 ${unit.unit_index + 1} 页段的多个问题？`,
      content: `将${batch.source_mode === 'separate' ? '题目文件' : '原文件'}第 ${unit.page_numbers.join('、')} 页和相关候选文字一次发送给百炼 ${s2Config?.model || ''}，同时复核 ${unit.issue_keys.length} 个问题${unit.skipped_count ? `；另有 ${unit.skipped_count} 个问题需单独处理` : ''}。本批 S3 预算阈值为 ¥${budget}；单次调用可能略超阈值。模型确认同题或答案归属后会自动处理。`,
      okText: '发送页段复核', cancelText: '取消',
      onOk: async () => {
        const key = `unit:${unit.unit_index}`
        setReviewingKey(key)
        try {
          const result = await apiPost<{ status: string; reviewed_count: number; error_message?: string }>(
            `/question-imports/${batch.id}/s3/review-units/${unit.unit_index}`,
            { budget_cny: budget }, undefined, { 'X-Import-S2-Token': operatorToken })
          message[result.status === 'completed' ? 'success' : 'warning'](result.status === 'completed' ? `已保存 ${result.reviewed_count} 条模型建议` : result.error_message || '没有可复核的问题')
          await reload(batch.id)
          await viewCandidates(batch.id)
        } catch (error) { message.error(error instanceof Error ? error.message : '页段复核失败') }
        finally { setReviewingKey(null) }
      },
    })
  }

  function confirmBatchDedup(batch: Batch) {
    if (!budget || !operatorToken) { message.warning('请先填写预算阈值和 S2 操作口令'); return }
    Modal.confirm({
      title: '让模型整理整批题目和答案？',
      content: `将整批候选题文字、题号和来源页码发送给百炼 ${s2Config?.model || ''}，跨页段判断重复题，移除重复候选${batch.source_mode === 'combined' ? '后自动关联同文件资料答案' : ''}。预算阈值 ¥${budget}；模型可能产生费用。`,
      okText: '整理整批', cancelText: '取消',
      onOk: async () => {
        setReviewingKey(`batch:${batch.id}`)
        try {
          const result = await apiPost<{ status: string; removed_duplicate_count?: number; error_message?: string }>(
            `/question-imports/${batch.id}/s3/dedupe-batch`, { budget_cny: budget }, undefined,
            { 'X-Import-S2-Token': operatorToken })
          message[result.status === 'completed' ? 'success' : 'warning'](result.status === 'completed'
            ? `整批判重完成，移除 ${result.removed_duplicate_count || 0} 道重复候选${batch.source_mode === 'combined' ? '，已重新关联资料答案' : ''}`
            : result.error_message || '整批判重未完成')
          await reload(batch.id)
          if (selectedBatchId === batch.id) await viewCandidates(batch.id)
        } catch (error) { message.error(error instanceof Error ? error.message : '整批判重失败') }
        finally { setReviewingKey(null) }
      },
    })
  }

  function confirmSeparateAnswers(batch: Batch) {
    if (!budget || !operatorToken) { message.warning('请先填写预算阈值和 S2 操作口令'); return }
    Modal.confirm({
      title: '确认发送答案文件到百炼？',
      content: `将独立答案文件按页段发送给百炼 ${s2Config?.model || ''}，只提取原文标准答案，完成后保守匹配题目。答案不会被标记为教师已确认。请确认文件不含学生个人信息。预算阈值 ¥${budget}；单次调用可能使实际费用超过阈值。`,
      okText: '开始识别答案', cancelText: '取消',
      onOk: async () => {
        try {
          await apiPost(`/question-imports/${batch.id}/s3/start-separate-answers`,
            { budget_cny: budget }, undefined, { 'X-Import-S2-Token': operatorToken })
          message.success('答案文件识别已排队')
          await reload(batch.id)
        } catch (error) { message.error(error instanceof Error ? error.message : '启动答案识别失败') }
      },
    })
  }

  async function reconcileAnswers(batch: Batch) {
    try {
      const result = await apiPost<{ source_answer_count: number; linked_answer_count: number; overlap_duplicate_answers: unknown[]; unassigned_answers: unknown[] }>(
        `/question-imports/${batch.id}/s3/reconcile-source-answers`, {})
      message[result.linked_answer_count ? 'success' : 'warning'](`原文件提取答案 ${result.source_answer_count} 条，已关联 ${result.linked_answer_count} 条；跨页重复 ${result.overlap_duplicate_answers.length} 条；待认领 ${result.unassigned_answers.length} 条`)
      await reload(batch.id)
      if (selectedBatchId === batch.id) await viewCandidates(batch.id)
    } catch (error) { message.error(error instanceof Error ? error.message : '资料答案关联失败') }
  }

  const selectedBatch = batches.find((item) => item.id === selectedBatchId)
  const s2 = selectedBatch?.checkpoint?.s2
  const reconciliation = selectedBatch?.checkpoint?.s3?.source_answer_reconciliation
  const suspectedIds = new Set(s2?.units?.flatMap((unit) => unit.overlap_suspects?.map((pair) => pair.candidate_item_id) || []) || [])
  const duplicateCandidates = selectedBatch?.checkpoint?.s3?.global_dedupe?.status === 'completed' ? []
    : candidates.filter((item) => item.payload.duplicate_status === 'suspected' || suspectedIds.has(item.id))
  const visibleCandidates = showDuplicateCandidates ? candidates : candidates.filter((item) => !duplicateCandidates.includes(item))
  const separateAnswers = selectedBatch?.checkpoint?.s3?.separate_answers
  const sourceAnswers = (selectedBatch?.source_mode === 'separate'
    ? separateAnswers?.units?.flatMap((unit) => unit.source_answers || [])
    : s2?.units?.flatMap((unit) => unit.source_answers || [])) || []
  const pendingAnswers = reconciliation?.unassigned_answers || sourceAnswers
  const dedupeStatus = selectedBatch?.checkpoint?.s3?.global_dedupe?.status
  const answerMatchingComplete = !!reconciliation && selectedBatch?.stage === 's2_completed'
    && (!dedupeStatus || dedupeStatus === 'completed')
    && (selectedBatch?.source_mode !== 'separate' || separateAnswers?.status === 'completed')
  const matchedImageIds = new Set(candidates.flatMap((candidate) => candidate.payload.content.asset_ids || []))
  const unmatchedImages = (images?.assets || []).filter((asset) => !matchedImageIds.has(asset.asset_id) && !asset.duplicate_of_kept_image)

  function renderCandidateContent(item: Candidate) {
    const assetIds = item.payload.content.asset_ids || []
    const clues = item.payload.extraction?.diagram_clues || []
    const figureMissing = !assetIds.length && (clues.length > 0 || ['complete', 'uncertain'].includes(item.payload.extraction?.figure_status || ''))
    const removedCandidateImages = unmatchedImages.filter((asset) => asset.removed_candidate_for_item_ids?.includes(item.id))
    return <Space className="question-import-candidate" direction="vertical" style={{ width: '100%' }}>
      <RichQuestionText text={item.payload.content.question_text} assets={[]} mathFragments={item.payload.extraction?.math_fragments ?? []} />
      {item.payload.content.options.map((option, index) => <RichQuestionText key={`option-${index}-${option.key}`} text={`${option.key}. ${option.text}`} assets={[]} mathFragments={item.payload.extraction?.math_fragments ?? []} />)}
      {images?.files_available && assetIds.map((id) => { const asset = images.assets.find((value) => value.asset_id === id); return asset && selectedBatch && <div key={id}>
        <img src={apiUrl(`/question-imports/${selectedBatch.id}/s3/images/${id}/content`)} alt={`候选 ${item.ordinal} 题的题图`} style={{ display: 'block', maxWidth: '100%', width: 320, maxHeight: 280, objectFit: 'contain', marginBottom: 6 }} />
        <Button size="small" danger disabled={imageBusy || !images?.files_available} onClick={() => void linkQuestionImage(selectedBatch, asset, item, false)}>删除图片</Button>
      </div> })}
      {figureMissing && clues.map((clue, index) => <Typography.Text type="warning" key={`clue-${index}`}>题图线索：{clue}</Typography.Text>)}
      {figureMissing && clues.length === 0 && <Typography.Text type="warning">题图线索：模型未提供具体描述</Typography.Text>}
      {figureMissing && <Typography.Text type="warning">题图尚未裁剪；请对照原页复核</Typography.Text>}
      {removedCandidateImages.length > 0 && <Typography.Text type="warning">去重题目有 {removedCandidateImages.length} 张未自动转移的图片，请在上方“无法匹配到题目的图片”中核对是否缺图。</Typography.Text>}
      {selectedBatch?.stage === 's2_completed' && <label>添加图片：<input type="file" accept="image/jpeg,image/png,image/webp" disabled={imageBusy || !images?.files_available} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file && selectedBatch) void uploadCandidateImage(selectedBatch, item, file) }} /></label>}
      {item.payload.extraction?.uncertain_fragments?.map((fragment, index) => <Typography.Text type="warning" key={`uncertain-${index}`}>模型不确定：{fragment}</Typography.Text>)}
      {item.payload.answers?.map((answer) => <div key={answer.answer_id} className="question-import-answer"><Typography.Text>资料答案（待确认）：</Typography.Text>{answer.text ? <RichQuestionText text={answer.text} assets={[]} /> : <Typography.Text type="secondary">未提供</Typography.Text>}</div>)}
      {answerMatchingComplete && !item.payload.answers?.some((answer) => !!answer.text?.trim()) && <Typography.Text type="warning">无答案</Typography.Text>}
      {pendingAnswers.filter((answer) => answer.candidate_item_ids?.length === 1 && answer.candidate_item_ids[0] === item.id).map((answer, index) => <div key={`pending-${index}`} className="question-import-answer"><Typography.Text type="warning">第 {answer.page_start} 页资料答案（归属待确认）：</Typography.Text><RichQuestionText text={answer.text} assets={[]} /></div>)}
      {item.payload.warnings?.filter((warning) => !['资料答案已关联，须对照原页由教师确认', '题图尚未裁剪；请对照原页复核', '题图已自动关联，须核对原页和图片完整性', '题图已关联，须对照原页核对图片内容和完整性'].includes(warning) && (warning !== '模型报告不确定片段；请人工复核' || !item.payload.extraction?.uncertain_fragments?.length)).map((warning, index) => <Typography.Text type="warning" key={`warning-${index}`}>{warning}</Typography.Text>)}
    </Space>
  }

  return <Space direction="vertical" size="large" style={{ width: '100%' }}>
    <Card title="批量题目导入 · 内部识别与候选核对">
      <Typography.Paragraph>上传后先校验文件；S2 按页段提取题目并自动整批去重。同文件场景同时提取答案；分文件场景先读取题目文件，整批去重完成后，在批次操作栏点击“识别答案文件”才会读取答案文件。请先确认文件场景。</Typography.Paragraph>
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <Select aria-label="文件场景" value={mode} onChange={(value) => { setMode(value); setAnswerFile(null) }}
          options={Object.entries(modeLabels).map(([value, label]) => ({ value, label }))} style={{ width: 260 }} />
        <label>题目文件（PDF/DOCX） <input type="file" accept=".pdf,.docx" onChange={(event) => setQuestionFile(event.target.files?.[0] || null)} /></label>
        {mode === 'separate' && <label>答案文件（PDF/DOCX） <input type="file" accept=".pdf,.docx" onChange={(event) => setAnswerFile(event.target.files?.[0] || null)} /></label>}
        <Typography.Text type="secondary">单文件不超过 30 MB；PDF 不超过 50 页；临时文件最多保留 72 小时、最近 30 批。</Typography.Text>
        <Space><Button type="primary" onClick={upload} loading={uploading}>上传并建立批次</Button><Button onClick={() => void reload()}>刷新</Button></Space>
        <Space wrap><Typography.Text>S2 操作口令</Typography.Text><Input.Password aria-label="S2 操作口令" value={operatorToken} onChange={(event) => setOperatorToken(event.target.value)} autoComplete="off" style={{ width: 220 }} /><Typography.Text>单批调用预算阈值（元）</Typography.Text><InputNumber min={0.01} max={s2Config?.max_budget_cny || 20} step={0.5} value={budget} onChange={setBudget} />
          <Typography.Text type="secondary">{s2Config?.ready ? `已配置 ${s2Config.model}；每段主体 ${s2Config.pages_per_unit} 页，向后重叠 ${s2Config.overlap_pages} 页；单次回答上限 ${s2Config.max_output_tokens} Token` : '百炼尚未在服务端启用；仍可上传与校验'}</Typography.Text></Space>
        {uploading && <Progress percent={progress} />}
      </Space>
    </Card>
    <Card title="导入批次">
      <Table rowKey="id" dataSource={batches} locale={{ emptyText: <Empty description="暂无导入批次" /> }}
        pagination={{ current: batchPage, pageSize: 10, onChange: setBatchPage }}
        columns={[
          { title: '上传时间', dataIndex: 'created_at', render: (value: string) => new Date(value).toLocaleString('zh-CN') },
          { title: '场景', dataIndex: 'source_mode', render: (value: string) => modeLabels[value] || value },
          { title: '文件', dataIndex: 'files', render: (files: Batch['files'], batch: Batch) => <Space wrap>{batch.id === focusedBatchId && <Tag color="blue">当前批次</Tag>}{files.map((file) => file.original_name).join('、')}</Space> },
          { title: '状态', render: (_, batch: Batch) => <Space direction="vertical" size={0}><Tag color={batch.status === 'failed' ? 'red' : batch.status === 'cancelled' ? 'default' : batch.status === 'review_pending' ? 'green' : 'blue'}>{stageLabel(batch)}</Tag>{batch.checkpoint?.s2 && <Typography.Text type="secondary">题目识别调用 {batch.checkpoint.s2.calls || 0} 次</Typography.Text>}{batch.checkpoint?.s3?.separate_answers && <Typography.Text type={['failed', 'paused'].includes(batch.checkpoint.s3.separate_answers.status) ? 'warning' : 'secondary'}>答案识别：{batch.checkpoint.s3.separate_answers.status}，调用 {batch.checkpoint.s3.separate_answers.calls || 0} 次{batch.checkpoint.s3.separate_answers.error_message ? `；${batch.checkpoint.s3.separate_answers.error_message}` : ''}</Typography.Text>}{batch.error_message && <Typography.Text type="danger">{batch.error_message}</Typography.Text>}{batch.error_code === 'S2_REVIEW_REQUIRED' && <Typography.Text type="warning">请检查原文件对应页面并重新上传清晰版，或将批次号发给管理员；已完成的候选题仍可查看。批次号：<Typography.Text copyable={{ text: batch.id }}>{batch.id}</Typography.Text></Typography.Text>}{batch.error_code === 'CALL_LIMIT_REACHED' && <Typography.Text type="warning">已达到单批调用次数上限，请将批次号发给管理员：<Typography.Text copyable={{ text: batch.id }}>{batch.id}</Typography.Text></Typography.Text>}</Space> },
           { title: '操作', render: (_, batch: Batch) => <Space wrap>
             {['awaiting_s2', 's2_budget_paused'].includes(batch.stage) && batch.error_code !== 'CALL_LIMIT_REACHED' && <Button disabled={!s2Config?.ready} onClick={() => confirmStart(batch)}>{batch.stage === 's2_budget_paused' ? '调整预算并继续识别' : '开始 S2 识别'}</Button>}
             {batch.checkpoint?.s2 && <Button onClick={() => void viewCandidates(batch.id)}>查看候选与页段</Button>}
             {batch.stage === 's2_completed' && batch.checkpoint?.s3?.global_dedupe?.status !== 'completed' && !['auto_claimed', 'running'].includes(batch.checkpoint?.s3?.global_dedupe?.status || '') && !(batch.checkpoint?.s2?.auto_global_dedupe && !batch.checkpoint?.s3?.global_dedupe?.status) && <Button type="primary" loading={reviewingKey === `batch:${batch.id}`} disabled={!s2Config?.ready || !!batch.files_purged_at} onClick={() => confirmBatchDedup(batch)}>{batch.checkpoint?.s3?.global_dedupe?.status === 'pending_budget' ? '增加预算并整理整批' : batch.source_mode === 'separate' ? '模型整理整批题目' : '模型整理整批题答'}</Button>}
             {batch.stage === 's2_completed' && batch.source_mode === 'separate' && batch.checkpoint?.s3?.global_dedupe?.status === 'completed' && !['queued', 'processing', 'completed'].includes(batch.checkpoint?.s3?.separate_answers?.status || '') && <Button type="primary" disabled={!s2Config?.ready || !!batch.files_purged_at} onClick={() => confirmSeparateAnswers(batch)}>{batch.checkpoint?.s3?.separate_answers?.status === 'paused' ? '增加预算并继续识别答案' : batch.checkpoint?.s3?.separate_answers?.status === 'failed' ? '重试答案识别' : '识别答案文件'}</Button>}
             {batch.stage === 's2_completed' && (batch.source_mode === 'combined' || batch.checkpoint?.s3?.separate_answers?.status === 'completed') && <Button onClick={() => void reconcileAnswers(batch)}>重新匹配答案</Button>}
             {batch.status === 'failed' && batch.error_code !== 'S2_REVIEW_REQUIRED' && !batch.files_purged_at && <Button disabled={loading} onClick={() => confirmRetry(batch)}>{batch.error_code === 'INCOMPLETE_MODEL_OUTPUT' ? '自动调整页段并重试' : '重试'}</Button>}
             {!['cancelled', 'completed', 'completed_with_unassigned'].includes(batch.status) && <Button disabled={loading} onClick={() => void act(batch.id, 'cancel')}>取消</Button>}
           </Space> },
        ]} />
    </Card>
    <Modal title="候选题、来源答案与页段记录" open={!!selectedBatchId} width={900} footer={<Button onClick={() => setSelectedBatchId(null)}>关闭</Button>} onCancel={() => setSelectedBatchId(null)}>
      <Typography.Paragraph>按 PDF 页段自动提取位图，并在同次模型调用中识别题目、原文答案和所需题图；缺少独立题图时按模型坐标自动裁剪。已匹配图片直接显示在对应题目处。</Typography.Paragraph>
      {selectedBatch?.stage === 's2_completed' && <Card size="small" style={{ marginBottom: 16 }}><details>
        <summary>无法匹配到题目的图片（{unmatchedImages.length}）</summary>
        {!images?.files_available && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message="临时题图已过期，无法显示或修改；请重新上传文件" />}
        {unmatchedImages.length === 0 ? <Typography.Text type="secondary">暂无未匹配图片</Typography.Text> : images?.files_available ? <>
          <Typography.Paragraph type="warning">无法匹配到题目，请人工复核。</Typography.Paragraph>
          <Space wrap>{unmatchedImages.map((asset) => <div key={asset.asset_id} style={{ width: 180 }}>
            <img src={apiUrl(`/question-imports/${selectedBatch.id}/s3/images/${asset.asset_id}/content`)} alt="未匹配的题图" style={{ maxWidth: 180, maxHeight: 160, objectFit: 'contain' }} />
            <div><Typography.Text type="secondary">{asset.page ? `第 ${asset.page} 页` : '上传图片'} · {asset.kind === 'embedded' ? 'PDF 提取' : asset.kind === 'model_crop' ? '自动裁剪' : asset.kind === 'upload' ? '上传图片' : '题图'}</Typography.Text></div>
            {!!asset.removed_candidate_for_item_ids?.length && <div><Typography.Text type="warning">来自已去重题目，请核对保留题是否缺图</Typography.Text></div>}
          </div>)}</Space>
        </> : null}
      </details></Card>}
      {['combined', 'separate'].includes(selectedBatch?.source_mode || '') && <Typography.Paragraph>
        原文答案已提取 {sourceAnswers.length} 条；{reconciliation
          ? `S3 已为 ${reconciliation.linked_question_count}/${candidates.length} 道题关联答案，答案显示在下方对应题目内。`
          : selectedBatch?.source_mode === 'separate'
            ? `答案文件识别状态：${separateAnswers?.status || '未开始'}。`
          : selectedBatch?.stage === 's2_completed' && selectedBatch?.checkpoint?.s2?.auto_global_dedupe
            ? '后台正在整批去重并关联题答，题目内的答案会在完成后自动刷新。'
            : '尚未完成题答关联。'}
      </Typography.Paragraph>}
      {selectedBatch?.checkpoint?.s3?.global_dedupe?.status === 'completed' && <Typography.Paragraph>整批模型判重：比较 {selectedBatch.checkpoint.s3.global_dedupe.reviewed_candidate_count || 0} 道候选，移除 {selectedBatch.checkpoint.s3.global_dedupe.removed_duplicate_count || 0} 道重复题。</Typography.Paragraph>}
      {['auto_claimed', 'running'].includes(selectedBatch?.checkpoint?.s3?.global_dedupe?.status || '') && <Typography.Paragraph>正在自动整批去重；请稍后刷新。</Typography.Paragraph>}
      {selectedBatch?.checkpoint?.s3?.global_dedupe?.status === 'pending_budget' && <Typography.Paragraph type="warning">S2 预算不足以进行整批判重；请在上方提高预算阈值后点击“增加预算并整理整批”。</Typography.Paragraph>}
      {selectedBatch?.checkpoint?.s3?.global_dedupe?.status === 'failed' && <Typography.Paragraph type="warning">整批模型判重失败：{selectedBatch.checkpoint.s3.global_dedupe.error_message}</Typography.Paragraph>}
      {!!s2?.units?.length && <details key={`${selectedBatch?.id}:s2-units`}>
        <summary>题目识别页段记录（{s2.units.length} 段）</summary>
        <Typography.Paragraph type="secondary">每页只归一个主体页段；后续页作为跨页上下文重复传入模型。模型报告不完整后，重试会拆小主体页并向后多带上下文，所以传送范围会重叠。</Typography.Paragraph>
        {s2.units.map((unit) => <div key={unit.index}>
          <Typography.Paragraph>页段 {unit.index + 1}：主体为原文件第 {unit.core_first_page}–{unit.core_last_page} 页{unit.last_page > unit.core_last_page ? `，第 ${unit.core_last_page + 1}–${unit.last_page} 页仅作跨页上下文` : ''}；{unit.status === 'completed' ? `S2 当时提取 ${unit.candidate_count || 0} 题（下方展示当前候选）` : '待处理'}</Typography.Paragraph>
          {selectedBatch?.source_mode !== 'combined' && unit.source_answer_signals?.map((signal, index) => <Typography.Paragraph key={`answer-signal-${index}`} type="warning">第 {signal.page} 页可能含资料答案，请核对上传场景：{signal.evidence}</Typography.Paragraph>)}
          {!!unit.overlap_suspects?.length && <details><summary>S2 当时检出的疑似跨页重复 {unit.overlap_suspects.length} 对</summary>{unit.overlap_suspects.map((pair, index) => <Typography.Paragraph key={index}>候选 {pair.candidate_item_id} 与 {pair.other_item_id}；当前结果以整批模型判重为准。</Typography.Paragraph>)}</details>}
        </div>)}
      </details>}
      {['combined', 'separate'].includes(selectedBatch?.source_mode || '') && <details key={selectedBatch?.id}><summary>{selectedBatch?.source_mode === 'separate' ? '答案文件' : '原文件'}提取答案 {sourceAnswers.length} 条（点击展开）</summary>{sourceAnswers.length === 0 && <Typography.Paragraph type="warning">尚未提取到资料答案，请核对答案页和页段记录。</Typography.Paragraph>}{sourceAnswers.map((answer, index) => <div key={index} className="question-import-answer"><Typography.Text type="secondary">{selectedBatch?.source_mode === 'separate' ? '答案文件' : '原文件'}第 {answer.page_start} 页，题号 {answer.original_number || '缺失'}：</Typography.Text><RichQuestionText text={answer.text} assets={[]} /></div>)}</details>}
      {separateAnswers?.units?.flatMap((unit) => unit.unresolved_fragments || []).map((fragment, index) => <Typography.Paragraph key={`answer-unresolved-${index}`} type="warning">答案文件第 {fragment.page} 页待核对：{fragment.text}（{fragment.reason}）</Typography.Paragraph>)}
      {reconciliation && <div>
         <Typography.Paragraph>资料答案 {reconciliation.source_answer_count} 条，已关联 {reconciliation.linked_answer_count} 条，跨页重复 {reconciliation.overlap_duplicate_answers?.length || 0} 条，待认领 {reconciliation.unassigned_answers.length} 条。{reconciliation.linked_answer_count > 0 && '资料答案已关联，须对照原页由教师确认。'}</Typography.Paragraph>
         {!!((reconciliation.overlap_duplicate_answers?.length || 0) + reconciliation.unassigned_answers.length) &&
           <details key={`${selectedBatch?.id}:reconciliation`}>
             <summary>查看跨页重复与待认领答案（{(reconciliation.overlap_duplicate_answers?.length || 0) + reconciliation.unassigned_answers.length} 条）</summary>
             {reconciliation.overlap_duplicate_answers?.map((answer, index) => <div key={`overlap-${index}`} className="question-import-answer"><Typography.Text type="secondary">第 {answer.page_start} 页重复了第 {answer.matched_page} 页第 {answer.matched_original_number} 题的答案：</Typography.Text><RichQuestionText text={answer.text} assets={[]} /></div>)}
             {reconciliation.unassigned_answers.map((answer, index) => <div key={index} className="question-import-answer">
               <Typography.Text type="warning">第 {answer.page_start} 页，题号 {answer.original_number || '缺失'}（{answer.reason}）：</Typography.Text>
               <RichQuestionText text={answer.text} assets={[]} />
             </div>)}
           </details>}
      </div>}
      {!!reviewIssues.length && <details key={`${selectedBatch?.id}:review-issues`}>
        <summary>按问题复核原页（{reviewIssues.length} 个问题）</summary>
        <Typography.Paragraph type="secondary">同一页段有多个问题时，可一次发送相关原页。重复题和答案归属可能由系统自动处理；未确定片段只保存模型建议，题目不会自动修改。</Typography.Paragraph>
        {reviewUnits.filter((unit) => unit.issue_keys.length > 1).map((unit) => <Typography.Paragraph key={`unit-${unit.unit_index}`}>
          第 {unit.unit_index + 1} 页段：待复核 {unit.issue_keys.length} 个问题，原页 {unit.page_numbers.join('、')}，候选 {unit.candidate_count} 条。
          {selectedBatch && <Button size="small" type="primary" loading={reviewingKey === `unit:${unit.unit_index}`} disabled={!unit.available || !s2Config?.ready || selectedBatch.status !== 'review_pending' || !!selectedBatch.files_purged_at} onClick={() => confirmUnitReview(selectedBatch, unit)}>一次复核本页段</Button>}
          {!unit.available && <Typography.Text type="warning"> 本页段超过单次范围，请逐条复核。</Typography.Text>}
        </Typography.Paragraph>)}
        {reviewIssues.map((issue) => {
          const advice = issue.review?.status === 'completed' ? issue.review.advice : undefined
          const matchedCandidate = candidates.find((item) => item.id === advice?.candidate_item_id)
          const candidateSummaries = issue.kind === 'overlap' ? issue.candidate_item_ids.map((id) => {
            const item = candidates.find((candidate) => candidate.id === id)
            return item ? `候选第 ${item.ordinal} 题（原题号 ${item.payload.source.original_number || '未识别'}，原页 ${item.payload.source.page_start ?? '未知'}）` : `候选 ID ${id}`
          }) : []
          const candidateLabel = matchedCandidate
            ? `候选第 ${matchedCandidate.ordinal} 题（原题号 ${matchedCandidate.payload.source.original_number || '未识别'}，原页 ${matchedCandidate.payload.source.page_start ?? '未知'}）`
            : advice?.candidate_item_id ? `候选 ID ${advice.candidate_item_id}` : null
          const verdictLabel = issue.kind === 'fragment' && advice?.verdict === 'match_candidate'
            ? '片段存在，可能归属所列候选题'
            : issue.kind === 'fragment' && advice?.verdict === 'no_match'
              ? '片段存在，但未找到可信归属'
              : advice ? reviewVerdictLabels[advice.verdict] || advice.verdict : ''
          const reviewStatus = !issue.review ? '待复核'
            : issue.review.status === 'failed' ? '复核失败'
              : issue.review.status === 'running' ? '复核中'
                : issue.review.stale ? '建议已过时'
                  : advice?.verdict === 'uncertain' ? '需人工核对'
                    : issue.kind === 'fragment' ? '待人工对照原页'
                      : issue.kind === 'answer' ? advice?.verdict === 'match_candidate' ? '待教师确认' : '待人工认领'
                        : '模型结果已保存'
          return <Card key={issue.key} size="small" style={{ marginTop: 12 }}
            title={`${selectedBatch?.source_mode === 'separate' ? '题目文件' : '原文件'}${issue.summary}`}
            extra={<Tag color={issue.review?.status === 'failed' ? 'red' : issue.review?.status === 'running' ? 'blue' : reviewStatus === '模型结果已保存' ? 'green' : 'orange'}>
              {reviewStatus}
            </Tag>}>
            {issue.kind === 'fragment' && <>
              <Typography.Paragraph><Typography.Text strong>待核对片段：</Typography.Text></Typography.Paragraph>
              <RichQuestionText text={issue.fragment_text || '内容未记录'} assets={[]} />
              <Typography.Paragraph type="secondary">首次提取报告：{issue.fragment_reason || '原因未记录'}</Typography.Paragraph>
            </>}
            {issue.kind === 'answer' && <>
              <Typography.Paragraph><Typography.Text strong>待认领答案：</Typography.Text>原题号 {issue.answer_number || '缺失'}</Typography.Paragraph>
              <RichQuestionText text={issue.answer_text || '内容未记录'} assets={[]} />
            </>}
            {!!candidateSummaries.length && <Typography.Paragraph>待比较题目：{candidateSummaries.join('；')}</Typography.Paragraph>}
            <Typography.Paragraph type="secondary">
              复核涉及原页：{issue.page_numbers.join('、') || '不可复核'}{issue.kind === 'fragment' && '（含邻近题目上下文）'}。{issue.kind === 'fragment'
                ? `邻近候选题 ${issue.candidate_item_ids.length} 道，仅供判断归属，不表示这些题有错误。`
                : `候选 ${issue.candidate_item_ids.length} 条。`}
              {issue.candidate_total > issue.candidate_item_ids.length && `共 ${issue.candidate_total} 条候选，仅列部分供模型参考。`}
            </Typography.Paragraph>
            {advice && <Alert showIcon type={advice.verdict === 'false_positive' ? 'info' : issue.kind === 'fragment' || advice.verdict === 'uncertain' ? 'warning' : 'success'}
              message={`模型复核结果：${verdictLabel}`}
              description={<Space direction="vertical" size={4}>
                {candidateLabel && <Typography.Text>模型指向：{candidateLabel}</Typography.Text>}
                <Typography.Text>原页依据：第 {advice.source_page} 页“{advice.evidence_quote}”</Typography.Text>
                <Typography.Text>判断理由：{advice.reason}</Typography.Text>
                {issue.kind === 'fragment' && <Typography.Text strong>
                  {advice.verdict === 'false_positive'
                    ? '模型认为该片段是提取误读，请对照原页确认；题目未自动修改。'
                    : '请根据模型复核结果，人工对照原页处理；题目不会自动修改。'}
                </Typography.Text>}
              </Space>} />}
            {issue.review?.stale && <Typography.Paragraph type="warning">候选内容已变化，此建议已过时，请以当前原页和候选为准。</Typography.Paragraph>}
            {issue.review?.status === 'failed' && <Alert type="warning" showIcon message={`复核失败：${issue.review.error_message}；可能已产生费用。`} />}
            {issue.review?.status === 'running' && <Typography.Paragraph type="secondary">复核进行中，请稍后刷新。</Typography.Paragraph>}
            {!issue.review && selectedBatch && <Button size="small" loading={reviewingKey === issue.key} disabled={!issue.page_numbers.length || !s2Config?.ready || selectedBatch.status !== 'review_pending' || !!selectedBatch.files_purged_at || (issue.kind === 'answer' && (!issue.candidate_item_ids.length || (issue.recall_method === 'number' && issue.candidate_total > issue.candidate_item_ids.length)))} onClick={() => confirmIssueReview(selectedBatch, issue)}>发送原页复核</Button>}
          </Card>
        })}
      </details>}
      {!!duplicateCandidates.length && <Typography.Paragraph><Button size="small" onClick={() => setShowDuplicateCandidates((value) => !value)}>{showDuplicateCandidates ? '收起' : '显示'}疑似重复候选 {duplicateCandidates.length} 条</Button><Typography.Text type="secondary"> 原始记录仍保留；确认不是重复题时可展开核对。</Typography.Text></Typography.Paragraph>}
      <Table rowKey="id" dataSource={visibleCandidates} size="small" pagination={{ pageSize: 10 }} columns={[
        { title: '序号', dataIndex: 'ordinal', width: 70 },
        { title: '原题号', render: (_, item: Candidate) => item.payload.source.original_number || '未识别', width: 90 },
        { title: '原页', render: (_, item: Candidate) => `${item.payload.source.page_start ?? '?'}–${item.payload.source.page_end ?? '?'}`, width: 90 },
        ...(candidates.some((item) => item.payload.extraction?.classification_evidence)
          ? [{ title: '模型分类依据', render: (_: unknown, item: Candidate) => item.payload.extraction?.classification_evidence || '未记录', width: 180 }]
          : []),
        { title: '题目、题图与答案', render: (_, item: Candidate) => renderCandidateContent(item) },
      ]} />
    </Modal>
  </Space>
}

export default QuestionImportPage
