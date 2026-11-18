import { Alert, Button, Card, Empty, Input, InputNumber, Modal, Progress, Select, Space, Table, Tag, Typography, message } from 'antd'
import { useEffect, useState } from 'react'
import { apiDelete, apiPost, apiPostForm, apiGet, apiPatch, apiUrl } from '../api/client'
import { RichQuestionText } from '../components/RichQuestionText'

type Batch = {
  id: string
  source_mode: string
  status: string
  stage: string
  files: Array<{ file_id: string; original_name: string; size_bytes: number; page_count: number | null; role: string }>
  checkpoint: {
    s2?: { calls?: number; page_count?: number; prompt_version?: string; auto_global_dedupe?: boolean; failed_unit?: { index: number; code: string }; units?: Array<{ index: number; first_page: number; last_page: number; core_first_page: number; core_last_page: number; status: string; candidate_count?: number; skipped_by_teacher?: boolean; skip_error_message?: string | null; source_answers?: Array<{ source_file_id?: string; original_number: string | null; subquestion_number?: string | null; text: string; page_start: number; candidate_item_ids?: string[] }>; source_answer_signals?: Array<{ page: number; evidence: string }>; overlap_suspects?: Array<{ candidate_item_id: string; other_item_id: string }>; unresolved_fragments?: Array<{ page: number; text: string; reason: string }> }> }
    s3?: { global_dedupe?: { status: string; reviewed_candidate_count?: number; removed_duplicate_count?: number; error_message?: string; estimated_cny?: number }; separate_answers?: { status: string; calls?: number; error_message?: string; units?: Array<{ index: number; first_page: number; core_last_page: number; status: string; source_answers?: Array<{ source_file_id?: string; original_number: string | null; subquestion_number?: string | null; text: string; page_start: number; candidate_item_ids?: string[] }>; unresolved_fragments?: Array<{ page: number; text: string; reason: string }> }> }; labels?: { status: string; calls?: number; estimated_cny?: number; cost_unknown?: boolean; error_code?: string; error_message?: string; skipped_item_ids?: string[]; groups?: Array<{ index: number; status: string; applied_count?: number; skipped_item_ids?: string[] }> }; source_answer_reconciliation?: { schema_version: string; source_answer_count: number; linked_answer_count: number; linked_question_count: number; manual_candidate_changes_pending?: boolean; overlap_duplicate_answers?: Array<{ text: string; page_start: number; matched_original_number: string; matched_page: number }>; unassigned_answers: Array<{ source_file_id?: string; original_number: string | null; text: string; page_start: number; subquestion_number?: string | null; reason: string; candidate_item_ids?: string[] }> } }
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
  source_answer?: { source_file_id?: string; original_number: string | null; text: string; page_start: number; page_end?: number; reason: string; candidate_item_ids?: string[] } | null
  page_numbers: number[]
  candidate_item_ids: string[]
  candidate_total: number
  recall_method: string | null
}

type Candidate = {
  id: string
  ordinal: number
  revision: number
  is_fake: boolean
  payload: {
    source: { original_number: string | null; page_start: number | null; page_end: number | null }
    content: { question_text: string; options: Array<{ key: string; text: string }>; asset_ids?: string[] }
    extraction?: { math_fragments?: string[]; diagram_clues?: string[]; figure_status?: 'no_figure' | 'complete' | 'uncertain'; uncertain_fragments?: string[] }
    answers?: Array<{ answer_id: string; answer_source: string; text: string | null; teacher_confirmed: boolean; evidence?: Array<{ file_id: string; page: number; quote: string }> }>
    selected_answer_id?: string | null
    answer_match_status?: string
    duplicate_status?: string
    label_status?: string
    tags?: { question_type?: string; difficulty?: number | null; main_kp_code?: string | null; grade?: string | null; chapter_code?: string | null; section?: string | null; core_literacies?: string[]; is_required?: boolean }
    label_suggestion?: { question_type: string; difficulty: number | null; main_kp_code: string | null; alternative_kp_codes: string[]; uncertainty: string | null; revision: number; model: string; stale: boolean }
    warnings?: string[]
  }
}

type S2Config = { ready: boolean; model: string; max_budget_cny: number; max_output_tokens: number; pages_per_unit: number; overlap_pages: number }
type LabelCatalogPoint = { code: string; name: string; grade: string; chapter_name: string; section: string; core_literacies: string[] }
type LabelEdit = { question_type?: string; difficulty?: number | null; main_kp_code?: string | null; is_required?: boolean }
type CandidateEditor = {
  itemId: string
  revision: number
  questionText: string
  options: Array<{ key: string; text: string }>
  answerText: string
  originalQuestionText: string
  originalOptions: Array<{ key: string; text: string }>
  originalAnswerText: string
}
type NewCandidateDraft = {
  questionText: string
  options: Array<{ key: string; text: string }>
  answerText: string
  originalNumber: string
  sourcePage: number | null
}
const emptyCandidateDraft = (): NewCandidateDraft => ({
  questionText: '', options: [], answerText: '', originalNumber: '', sourcePage: null,
})

function hasImportLabels(item: Candidate): boolean {
  const tags = item.payload.tags
  return !!tags && ['choice', 'fill_blank', 'calculation', 'solution', 'true_false'].includes(tags.question_type || '')
    && Number.isInteger(tags.difficulty) && (tags.difficulty ?? 0) >= 1 && (tags.difficulty ?? 0) <= 5
    && !!tags.main_kp_code && !!tags.grade && !!tags.chapter_code && !!tags.section
    && !!tags.core_literacies?.length
}
const questionTypeLabels: Record<string, string> = { choice: '选择题', fill_blank: '填空题', calculation: '计算题', solution: '解答题', true_false: '判断题', unknown: '待确认' }

type ImageAsset = { asset_id: string; kind: string; page: number | null; bbox: number[] | null; width: number; height: number; sha256: string; candidate_item_ids: string[]; removed_candidate_for_item_ids?: string[]; duplicate_of_kept_image?: boolean }
type ImageInventory = { version: string | null; page_count: number | null; embedded_scanned: boolean; files_available: boolean; assets: ImageAsset[] }

const modeLabels: Record<string, string> = {
  questions_only: '只有题目', combined: '题目与答案同文件', separate: '题目与答案分文件',
}

function stageLabel(batch: Batch): string {
  if (batch.stage === 'awaiting_s2') return '文件已校验，等待 S2 解析'
  if (batch.stage === 's2_queued') return '等待识别题目文件'
  if (batch.stage === 's2_processing') return '正在识别题目文件页段'
  if (batch.stage === 's2_completed') {
    const labels = batch.checkpoint?.s3?.labels
    if (labels?.status === 'queued') return '标签建议等待处理'
    if (labels?.status === 'processing') return '正在生成标签建议'
    if (labels?.status === 'paused') return '标签建议已暂停'
    if (labels?.status === 'failed') return '标签建议失败'
    if (labels?.status === 'completed') return '标签建议完成，待教师核对'
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
      if (answers === 'completed') return '答案关联完成，待生成标签建议'
      return '题目去重完成，待启动答案文件识别'
    }
    return dedupe === 'completed' ? '整批整理完成，待生成标签建议' : 'S2 已完成，待人工核对'
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

function skippableS2Page(batch: Batch): number | null {
  const s2 = batch.checkpoint?.s2
  const failed = s2?.failed_unit
  if (batch.status !== 'failed' || batch.stage !== 's2_failed' || batch.files_purged_at
      || new Date(batch.expires_at).getTime() <= Date.now()
      || !['INCOMPLETE_MODEL_OUTPUT', 'S2_REVIEW_REQUIRED'].includes(batch.error_code || '')
      || !failed || failed.code !== 'INCOMPLETE_MODEL_OUTPUT') return null
  const unit = s2?.units?.[failed.index]
  return unit && unit.index === failed.index && unit.status === 'pending'
    && unit.core_first_page === unit.core_last_page ? unit.core_first_page : null
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
  const [reviewingKey, setReviewingKey] = useState<string | null>(null)
  const [images, setImages] = useState<ImageInventory | null>(null)
  const [imageBusy, setImageBusy] = useState(false)
  const [unmatchedImageTargets, setUnmatchedImageTargets] = useState<Record<string, string>>({})
  const [labelCatalog, setLabelCatalog] = useState<LabelCatalogPoint[]>([])
  const [labelEdits, setLabelEdits] = useState<Record<string, LabelEdit>>({})
  const [labelSaving, setLabelSaving] = useState<string | null>(null)
  const [candidateEditor, setCandidateEditor] = useState<CandidateEditor | null>(null)
  const [candidateSaving, setCandidateSaving] = useState(false)
  const [newCandidateOpen, setNewCandidateOpen] = useState(false)
  const [newCandidateDraft, setNewCandidateDraft] = useState<NewCandidateDraft>(emptyCandidateDraft)
  const [creatingCandidate, setCreatingCandidate] = useState(false)
  const [deletingCandidateId, setDeletingCandidateId] = useState<string | null>(null)

  function closeCandidateModal() {
    setCandidateEditor(null)
    setNewCandidateOpen(false)
    setSelectedBatchId(null)
  }

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
    void apiGet<LabelCatalogPoint[]>('/question-imports/label-catalog').then(setLabelCatalog).catch(() => undefined)
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

  function confirmCancel(batch: Batch) {
    Modal.confirm({
      title: '确认取消导入任务？',
      content: '取消后任务将停止处理，当前没有恢复操作；如需继续导入，须重新上传文件。已暂存的题目、答案和文件不会立即删除。',
      okText: '确认取消任务', okType: 'danger', cancelText: '继续保留任务',
      onOk: () => act(batch.id, 'cancel'),
    })
  }

  function confirmDeleteBatch(batch: Batch) {
    Modal.confirm({
      title: '永久删除已取消的导入任务？',
      content: '将删除该批次的源文件、模型响应与题图文件，以及暂存的题目、答案、标签和处理记录。删除后无法恢复。',
      okText: '永久删除', okType: 'danger', cancelText: '保留任务',
      onOk: async () => {
        setLoading(true)
        try {
          await apiDelete(`/question-imports/${batch.id}`)
          if (selectedBatchId === batch.id) closeCandidateModal()
          await reload()
          message.success('已删除导入任务及关联数据')
        } catch (error) {
          message.error(error instanceof Error ? error.message : '删除任务失败')
          throw error
        } finally { setLoading(false) }
      },
    })
  }

  function confirmRetry(batch: Batch) {
    if (batch.error_code !== 'INCOMPLETE_MODEL_OUTPUT') {
      void act(batch.id, 'retry')
      return
    }
    Modal.confirm({
      title: '自动调整失败页段并重试？',
      content: '系统会缩小失败页段的主体页，每段最多带 1 页后续上下文；主体页已缩至 1 页时将提示人工核对，不再增加上下文页。已完成的页段不会重跑。新的模型调用可能产生费用，仍受本批预算阈值约束。',
      okText: '确认重试', cancelText: '取消',
      onOk: () => act(batch.id, 'retry'),
    })
  }

  function confirmSkipS2Page(batch: Batch, page: number) {
    if (!operatorToken) { message.warning('请输入 S2 操作口令'); return }
    Modal.confirm({
      title: `跳过第 ${page} 页并继续识别？`,
      content: `请先核对原文件第 ${page} 页和前一页段的候选题。确认跳过后，该页不会再自动提取题目或答案，后续页段会继续识别并可能产生模型费用。系统会保留跳过记录和待教师核对的问题；若该页有尚未提取的新题，请在 S2 完成后人工新增。`,
      okText: '确认跳过本页', okType: 'danger', cancelText: '继续核对',
      onOk: async () => {
        setLoading(true)
        try {
          await apiPost(`/question-imports/${batch.id}/s2/skip-page`, { page }, undefined,
            { 'X-Import-S2-Token': operatorToken })
          await reload(batch.id)
          message.success(`第 ${page} 页已标记为人工跳过，后续页段将继续识别`)
        } catch (error) {
          message.error(error instanceof Error ? error.message : '跳过失败')
        } finally { setLoading(false) }
      },
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
      const [items, issues, initialImages, currentCatalog] = await Promise.all([
        apiGet<Candidate[]>(`/question-imports/${id}/items`),
        apiGet<ReviewIssue[]>(`/question-imports/${id}/s3/review-issues`),
        apiGet<ImageInventory>(`/question-imports/${id}/s3/images`),
        apiGet<LabelCatalogPoint[]>('/question-imports/label-catalog').catch(() => labelCatalog),
      ])
      let imageRows = initialImages
      if (!imageRows.embedded_scanned && imageRows.files_available && batches.find((batch) => batch.id === id)?.stage === 's2_completed') {
        try { imageRows = await apiPost<ImageInventory>(`/question-imports/${id}/s3/images`, {}) }
        catch (error) { message.warning(error instanceof Error ? error.message : 'PDF 图片自动提取失败') }
      }
      setCandidates(items)
      setLabelCatalog(currentCatalog)
      setImages(imageRows)
      setUnmatchedImageTargets({})
      setShowDuplicateCandidates(false)
      setReviewIssues(issues)
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

  function confirmBatchDedup(batch: Batch) {
    if (!budget || !operatorToken) { message.warning('请先填写预算阈值和 S2 操作口令'); return }
    Modal.confirm({
      title: '让模型整批去重题目？',
      content: `将整批候选题文字、题号和来源页码${batch.source_mode === 'combined' ? '，以及来源答案摘录作为判重上下文' : ''}发送给百炼 ${s2Config?.model || ''}，跨页段判断重复题。${batch.source_mode === 'combined' ? '去重后由程序按规则关联同文件答案；未能关联的答案留给教师核对。' : ''}预算阈值 ¥${budget}；模型可能产生费用。`,
      okText: '整理整批', cancelText: '取消',
      onOk: async () => {
        setReviewingKey(`batch:${batch.id}`)
        try {
          const result = await apiPost<{ status: string; reviewed_candidate_count?: number; removed_duplicate_count?: number; error_message?: string }>(
            `/question-imports/${batch.id}/s3/dedupe-batch`, { budget_cny: budget }, undefined,
            { 'X-Import-S2-Token': operatorToken })
          message[result.status === 'completed' ? 'success' : 'warning'](result.status === 'completed'
            ? `去重后保留 ${(result.reviewed_candidate_count || 0) - (result.removed_duplicate_count || 0)} 道题`
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

  function confirmLabels(batch: Batch, rerun = false) {
    if (!budget || !operatorToken) { message.warning('请先填写预算阈值和 S2 操作口令'); return }
    const replan = batch.checkpoint?.s3?.labels?.error_code === 'S3_LABEL_REPLAN_REQUIRED'
    Modal.confirm({
      title: rerun ? '全部重新打标签？' : replan ? '按新规则重新处理整批标签？' : '生成本批候选题的标签建议？',
      content: `${rerun ? '将整批候选题按每组最多 12 题重新发送给模型；新结果会覆盖此前手动修改的题型、难度和主属知识点，必做标记保留。原调用费用仍计入累计预算，本次会产生新费用。' : replan ? '旧分组已停用。本次会按每组最多 12 题重新处理整批候选题；此前已成功的题目也会再次发送并产生费用，历史费用仍计入累计预算。' : '仅处理已去重且已完成资料答案关联的候选题。'}将题干、选项和已关联题图分组发送给百炼 ${s2Config?.model || ''}；模型建议题型、难度和主属 KP，教师仍须核对。预算阈值 ¥${budget}；单次调用可能超过阈值。`,
      okText: rerun ? '全部重新打标签' : '开始生成标签', cancelText: '取消',
      onOk: async () => {
        try {
          await apiPost(`/question-imports/${batch.id}/s3/${rerun ? 'rerun-labels' : 'start-labels'}`,
            { budget_cny: budget }, undefined, { 'X-Import-S2-Token': operatorToken })
          if (rerun) setLabelEdits({})
          message.success('标签建议已排队')
          await reload(batch.id)
        } catch (error) { message.error(error instanceof Error ? error.message : '启动标签建议失败') }
      },
    })
  }

  async function saveLabels(item: Candidate) {
    if (!selectedBatch) return
    const changes = labelEdits[item.id]
    if (!changes || !Object.keys(changes).length) return
    setLabelSaving(item.id)
    try {
      await apiPatch(`/question-imports/${selectedBatch.id}/items/${item.id}`,
        { expected_revision: item.revision, changes: { tags: changes } })
      setLabelEdits((current) => { const copy = { ...current }; delete copy[item.id]; return copy })
      await viewCandidates(selectedBatch.id)
      message.success('标签调整已保存，仍需教师审核后才能入库')
    } catch (error) { message.error(error instanceof Error ? error.message : '保存标签失败') }
    finally { setLabelSaving(null) }
  }

  function startEditingCandidate(item: Candidate) {
    const answers = item.payload.answers || []
    const currentAnswer = answers.find((answer) => answer.answer_id === item.payload.selected_answer_id)
      || [...answers].reverse().find((answer) => answer.answer_source === 'teacher_entered')
      || answers[0]
    const questionText = item.payload.content.question_text
    const options = item.payload.content.options.map((option) => ({ ...option }))
    const answerText = currentAnswer?.text || ''
    setCandidateEditor({ itemId: item.id, revision: item.revision, questionText, options, answerText,
      originalQuestionText: questionText, originalOptions: options.map((option) => ({ ...option })), originalAnswerText: answerText })
  }

  async function saveCandidate(item: Candidate) {
    if (!selectedBatch || !candidateEditor || candidateEditor.itemId !== item.id) return
    const questionText = candidateEditor.questionText.trim()
    const options = candidateEditor.options.map((option) => ({ key: option.key.trim(), text: option.text.trim() }))
    const answerText = candidateEditor.answerText.trim()
    if (!questionText || options.some((option) => !option.key || !option.text)
      || new Set(options.map((option) => option.key)).size !== options.length) {
      message.warning('请填写题干，并检查选项内容及编号是否为空或重复')
      return
    }
    const contentChanged = questionText !== candidateEditor.originalQuestionText
      || JSON.stringify(options) !== JSON.stringify(candidateEditor.originalOptions)
    const answerChanged = answerText !== candidateEditor.originalAnswerText.trim()
    if (!contentChanged && !answerChanged) { setCandidateEditor(null); return }
    if (answerChanged && !answerText) { message.warning('修改后的答案不能为空'); return }
    const changes: Record<string, unknown> = {}
    if (contentChanged) changes.content = { ...item.payload.content, question_text: questionText, options }
    if (answerChanged) changes.teacher_answer_text = answerText
    setCandidateSaving(true)
    try {
      await apiPatch(`/question-imports/${selectedBatch.id}/items/${item.id}`,
        { expected_revision: candidateEditor.revision, changes })
      setCandidateEditor(null)
      await viewCandidates(selectedBatch.id)
      message.success('题目与答案修改已保存，请核对答案和入库标签')
    } catch (error) { message.error(error instanceof Error ? error.message : '保存题目与答案失败') }
    finally { setCandidateSaving(false) }
  }

  async function createCandidate() {
    if (!selectedBatch || !canEditCandidates) return
    const questionText = newCandidateDraft.questionText.trim()
    const options = newCandidateDraft.options.map((option) => ({ key: option.key.trim(), text: option.text.trim() }))
    if (!questionText || options.some((option) => !option.key || !option.text)
      || new Set(options.map((option) => option.key)).size !== options.length) {
      message.warning('请填写题干，并检查选项内容及编号是否为空或重复')
      return
    }
    setCreatingCandidate(true)
    try {
      await apiPost(`/question-imports/${selectedBatch.id}/items`, {
        question_text: questionText, options,
        answer_text: newCandidateDraft.answerText.trim() || null,
        original_number: newCandidateDraft.originalNumber.trim() || null,
        source_page: newCandidateDraft.sourcePage,
      })
      setNewCandidateOpen(false)
      setNewCandidateDraft(emptyCandidateDraft())
      await viewCandidates(selectedBatch.id)
      await reload(selectedBatch.id)
      message.success('题目已新增，请核对重复情况与入库标签')
    } catch (error) { message.error(error instanceof Error ? error.message : '新增题目失败') }
    finally { setCreatingCandidate(false) }
  }

  function confirmDeleteCandidate(item: Candidate) {
    if (!selectedBatch || !canEditCandidates) return
    const batchId = selectedBatch.id
    Modal.confirm({
      title: `删除第 ${item.ordinal} 题？`,
      content: '删除后此题不再参与答案关联或打标签；原候选记录会保留以供追溯。',
      okText: '删除题目', okButtonProps: { danger: true }, cancelText: '取消',
      onOk: async () => {
        setDeletingCandidateId(item.id)
        try {
          await apiDelete(`/question-imports/${batchId}/items/${item.id}`, { expected_revision: item.revision })
          setCandidateEditor((current) => current?.itemId === item.id ? null : current)
          await viewCandidates(batchId)
          await reload(batchId)
          message.success('题目已删除')
        } catch (error) { message.error(error instanceof Error ? error.message : '删除题目失败') }
        finally { setDeletingCandidateId(null) }
      },
    })
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
  const pendingAnswers = reconciliation
    ? reviewIssues.filter((issue) => issue.kind === 'answer' && issue.source_answer).map((issue) => issue.source_answer!)
    : sourceAnswers
  const linkedSourceQuestionCount = candidates.filter((item) => item.payload.answers?.some((answer) => answer.answer_source === 'source_extracted')).length
  const reviewCategories = [
    { kind: 'answer', title: '答案归属待核对' },
    { kind: 'skipped_page', title: '人工跳过页' },
    { kind: 'fragment', title: '提取时未确定的片段' },
    ...(selectedBatch?.source_mode === 'separate' ? [{ kind: 'answer_fragment', title: '答案文件未确定片段' }] : []),
    { kind: 'overlap', title: '疑似重复题' },
  ]
  const dedupeStatus = selectedBatch?.checkpoint?.s3?.global_dedupe?.status
  const labelTaskStatus = selectedBatch?.checkpoint?.s3?.labels?.status
  const canEditCandidates = selectedBatch?.status === 'review_pending' && selectedBatch.stage === 's2_completed'
    && dedupeStatus === 'completed' && (selectedBatch.source_mode !== 'separate' || separateAnswers?.status === 'completed')
    && !['queued', 'processing'].includes(labelTaskStatus || '')
  const answerMatchingComplete = !!reconciliation && selectedBatch?.stage === 's2_completed'
    && (!dedupeStatus || dedupeStatus === 'completed')
    && (selectedBatch?.source_mode !== 'separate' || separateAnswers?.status === 'completed')
  const matchedImageIds = new Set(candidates.flatMap((candidate) => candidate.payload.content.asset_ids || []))
  const unmatchedImages = (images?.assets || []).filter((asset) => !matchedImageIds.has(asset.asset_id) && !asset.duplicate_of_kept_image)
  const imageTargetOptions = candidates.filter((candidate) => !candidate.is_fake).map((candidate) => ({
    value: candidate.id,
    label: `第 ${candidate.ordinal} 题${candidate.payload.source.original_number ? `（原题号 ${candidate.payload.source.original_number}）` : ''}${candidate.payload.source.page_start ? ` · 第 ${candidate.payload.source.page_start} 页` : ''} · ${candidate.payload.content.question_text.replace(/\s+/g, ' ').slice(0, 48)}`,
  }))

  function renderCandidateContent(item: Candidate) {
    const assetIds = item.payload.content.asset_ids || []
    const tags = item.payload.tags || {}
    const suggestion = item.payload.label_suggestion
    const edit = labelEdits[item.id] || {}
    const selectedKp = labelCatalog.find((point) => point.code === tags.main_kp_code)
    const editedKpCode = edit.main_kp_code === undefined ? tags.main_kp_code : edit.main_kp_code
    const visibleKpCode = labelCatalog.some((point) => point.code === editedKpCode) ? editedKpCode : undefined
    const labelTask = selectedBatch?.checkpoint?.s3?.labels
    const skippedByTask = labelTask?.skipped_item_ids?.includes(item.id) || labelTask?.groups?.some((group) => group.skipped_item_ids?.includes(item.id))
    const clues = item.payload.extraction?.diagram_clues || []
    const figureMissing = !assetIds.length && (clues.length > 0 || ['complete', 'uncertain'].includes(item.payload.extraction?.figure_status || ''))
    const removedCandidateImages = unmatchedImages.filter((asset) => asset.removed_candidate_for_item_ids?.includes(item.id))
    return <Space className="question-import-candidate" direction="vertical" style={{ width: '100%' }}>
      <RichQuestionText text={item.payload.content.question_text} assets={[]} mathFragments={item.payload.extraction?.math_fragments ?? []} />
      {item.payload.content.options.map((option, index) => <RichQuestionText key={`option-${index}-${option.key}`} text={`${option.key}. ${option.text}`} assets={[]} mathFragments={item.payload.extraction?.math_fragments ?? []} />)}
      {images?.files_available && assetIds.map((id) => { const asset = images.assets.find((value) => value.asset_id === id); return asset && selectedBatch && <div key={id}>
        <img src={apiUrl(`/question-imports/${selectedBatch.id}/s3/images/${id}/content`)} alt={`候选 ${item.ordinal} 题的题图`} style={{ display: 'block', maxWidth: '100%', width: 320, maxHeight: 280, objectFit: 'contain', marginBottom: 6 }} />
        <Button size="small" danger disabled={!canEditCandidates || imageBusy || !images?.files_available} onClick={() => void linkQuestionImage(selectedBatch, asset, item, false)}>删除图片</Button>
      </div> })}
      {figureMissing && clues.map((clue, index) => <Typography.Text type="warning" key={`clue-${index}`}>题图线索：{clue}</Typography.Text>)}
      {figureMissing && clues.length === 0 && <Typography.Text type="warning">题图线索：模型未提供具体描述</Typography.Text>}
      {figureMissing && <Typography.Text type="warning">题图尚未裁剪；请对照原页复核</Typography.Text>}
      {removedCandidateImages.length > 0 && <Typography.Text type="warning">去重题目有 {removedCandidateImages.length} 张未自动转移的图片，请在上方“无法匹配到题目的图片”中核对是否缺图。</Typography.Text>}
      {canEditCandidates && <label>添加图片：<input type="file" accept="image/jpeg,image/png,image/webp" disabled={imageBusy || !images?.files_available} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file && selectedBatch) void uploadCandidateImage(selectedBatch, item, file) }} /></label>}
      {item.payload.extraction?.uncertain_fragments?.map((fragment, index) => <Typography.Text type="warning" key={`uncertain-${index}`}>模型不确定：{fragment}</Typography.Text>)}
      {item.payload.answers?.map((answer) => <div key={answer.answer_id} className="question-import-answer">
        <Typography.Text>{answer.answer_source === 'teacher_entered' ? '教师答案' : answer.answer_source === 'source_extracted' ? '资料答案' : '答案'}{answer.answer_id === item.payload.selected_answer_id ? '（当前采用）' : ''}：</Typography.Text>
        {answer.text ? <RichQuestionText text={answer.text} assets={[]} /> : <Typography.Text type="secondary">未提供</Typography.Text>}
      </div>)}
      {item.payload.warnings?.includes('题目已修改，请重新核对答案是否仍适用') && <Typography.Text type="warning">题目已修改，请重新核对答案是否仍适用。</Typography.Text>}
      {canEditCandidates && candidateEditor?.itemId !== item.id && <Space>
        <Button size="small" onClick={() => startEditingCandidate(item)}>编辑题目和答案</Button>
        <Button size="small" danger loading={deletingCandidateId === item.id} onClick={() => confirmDeleteCandidate(item)}>删除题目</Button>
      </Space>}
      {candidateEditor?.itemId === item.id && <Card size="small" title="编辑题目和答案" style={{ width: '100%' }}>
        {candidateEditor.revision !== item.revision && <Alert type="warning" showIcon message="此题在编辑期间已更新。请取消编辑后重新打开，避免覆盖新修改。" style={{ marginBottom: 12 }} />}
        <Typography.Paragraph>题干</Typography.Paragraph>
        <Input.TextArea value={candidateEditor.questionText} autoSize={{ minRows: 3, maxRows: 12 }} maxLength={30000}
          onChange={(event) => setCandidateEditor((current) => current ? { ...current, questionText: event.target.value } : current)} />
        <Typography.Paragraph style={{ marginTop: 12 }}>选项</Typography.Paragraph>
        {candidateEditor.options.map((option, index) => <Space key={index} style={{ display: 'flex', marginBottom: 8 }} align="start">
          <Input aria-label={`第 ${index + 1} 个选项编号`} style={{ width: 70 }} value={option.key} maxLength={20}
            onChange={(event) => setCandidateEditor((current) => current ? { ...current, options: current.options.map((value, i) => i === index ? { ...value, key: event.target.value } : value) } : current)} />
          <Input.TextArea aria-label={`第 ${index + 1} 个选项内容`} style={{ width: 550, maxWidth: '55vw' }} value={option.text} autoSize={{ minRows: 1, maxRows: 5 }} maxLength={10000}
            onChange={(event) => setCandidateEditor((current) => current ? { ...current, options: current.options.map((value, i) => i === index ? { ...value, text: event.target.value } : value) } : current)} />
          <Button size="small" danger onClick={() => setCandidateEditor((current) => current ? { ...current, options: current.options.filter((_, i) => i !== index) } : current)}>删除</Button>
        </Space>)}
        {candidateEditor.options.length < 20 && <Button size="small" onClick={() => setCandidateEditor((current) => current ? { ...current, options: [...current.options, { key: String.fromCharCode(65 + current.options.length), text: '' }] } : current)}>添加选项</Button>}
        <Typography.Paragraph style={{ marginTop: 16 }}>答案</Typography.Paragraph>
        <Input.TextArea value={candidateEditor.answerText} autoSize={{ minRows: 3, maxRows: 12 }} maxLength={30000}
          placeholder="可填写教师修订后的答案；留空表示暂不提供" onChange={(event) => setCandidateEditor((current) => current ? { ...current, answerText: event.target.value } : current)} />
        <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>修改答案会新增教师答案版本，原资料答案保留。修改题目后请重新核对答案与标签。</Typography.Paragraph>
        <Space>
          <Button type="primary" loading={candidateSaving} disabled={!canEditCandidates || candidateEditor.revision !== item.revision} onClick={() => void saveCandidate(item)}>保存修改</Button>
          <Button disabled={candidateSaving} onClick={() => setCandidateEditor(null)}>取消</Button>
        </Space>
      </Card>}
      {selectedBatch?.stage === 's2_completed' && <Card size="small" title="入库标签 · 待教师核对" style={{ width: '100%' }}>
        {!hasImportLabels(item) && <Typography.Paragraph type="warning">无标签，请人工打标签</Typography.Paragraph>}
        {suggestion?.stale && <Typography.Paragraph type="warning">题干或题图已变化，原标签建议已过时。请全部重新打标签或手动核对调整。</Typography.Paragraph>}
        {suggestion && (suggestion.uncertainty || suggestion.alternative_kp_codes.length > 0) && <Typography.Paragraph type="secondary">{suggestion.uncertainty ? `不确定：${suggestion.uncertainty}` : ''}{suggestion.alternative_kp_codes.length ? `${suggestion.uncertainty ? '；' : ''}备选知识点：${suggestion.alternative_kp_codes.map((code) => labelCatalog.find((point) => point.code === code)?.name || '知识点名称待加载').join('、')}` : ''}</Typography.Paragraph>}
        {!suggestion && <Typography.Paragraph type="warning">{skippedByTask ? '本题在排队后发生变化，未发送给模型；请人工填写，或在本批完成后全部重新打标签。' : ['queued', 'processing'].includes(labelTask?.status || '') ? '标签任务尚未处理本题；也可先人工填写。' : '本题没有可用的模型建议，请人工填写或全部重新打标签。'}</Typography.Paragraph>}
        <Space wrap>
          <label>题型：<Select aria-label={`第 ${item.ordinal} 题题型`} style={{ width: 120 }} value={edit.question_type ?? tags.question_type ?? 'unknown'}
            options={Object.entries(questionTypeLabels).map(([value, label]) => ({ value, label }))}
            onChange={(value: string) => setLabelEdits((current) => ({ ...current, [item.id]: { ...current[item.id], question_type: value } }))} /></label>
          <label>难度：<Select aria-label={`第 ${item.ordinal} 题难度`} allowClear placeholder="待确认" style={{ width: 105 }} value={edit.difficulty === undefined ? tags.difficulty : edit.difficulty}
            options={[1, 2, 3, 4, 5].map((value) => ({ value, label: `${value} 星` }))}
            onChange={(value: number | undefined) => setLabelEdits((current) => ({ ...current, [item.id]: { ...current[item.id], difficulty: value ?? null } }))} /></label>
          <label>主属知识点：<Select aria-label={`第 ${item.ordinal} 题主属知识点`} allowClear showSearch optionFilterProp="label" placeholder={editedKpCode ? '知识点名称待加载' : '待确认'} style={{ width: 300 }} value={visibleKpCode}
            options={labelCatalog.map((point) => ({ value: point.code, label: point.name }))}
            onChange={(value: string | undefined) => setLabelEdits((current) => ({ ...current, [item.id]: { ...current[item.id], main_kp_code: value ?? null } }))} /></label>
          <label>必做：<Select aria-label={`第 ${item.ordinal} 题必做`} style={{ width: 90 }} value={edit.is_required ?? tags.is_required ?? false}
            options={[{ value: false, label: '否' }, { value: true, label: '是' }]}
            onChange={(value: boolean) => setLabelEdits((current) => ({ ...current, [item.id]: { ...current[item.id], is_required: value } }))} /></label>
          <Button size="small" disabled={!Object.keys(edit).length} loading={labelSaving === item.id} onClick={() => void saveLabels(item)}>保存调整</Button>
        </Space>
        <div><Typography.Text type="secondary">当前派生标签：年级：{selectedKp?.grade || tags.grade || '待定'}；章：{selectedKp?.chapter_name || (tags.main_kp_code ? '名称待加载' : '待定')}；节：{selectedKp?.section || tags.section || '待定'}；核心素养：{(selectedKp?.core_literacies || tags.core_literacies || []).join('、') || '待定'}；必做：{tags.is_required ? '是' : '否（默认）'}；状态：{item.payload.label_status === 'out_of_scope' ? '超出当前知识点范围或无法判断' : '待核对'}</Typography.Text></div>
      </Card>}
      {answerMatchingComplete && !item.payload.answers?.some((answer) => !!answer.text?.trim()) && <Typography.Text type="warning">无答案</Typography.Text>}
      {pendingAnswers.filter((answer) => answer.candidate_item_ids?.length === 1 && answer.candidate_item_ids[0] === item.id).map((answer, index) => <div key={`pending-${index}`} className="question-import-answer"><Typography.Text type="warning">{selectedBatch?.source_mode === 'separate' ? '答案文件' : '原文件'}第 {answer.page_start} 页资料答案（归属待确认）：</Typography.Text><RichQuestionText text={answer.text} assets={[]} /></div>)}
      {item.payload.warnings?.filter((warning) => !['资料答案已关联，须对照原页由教师确认', '题图尚未裁剪；请对照原页复核', '题图已自动关联，须核对原页和图片完整性', '题图已关联，须对照原页核对图片内容和完整性'].includes(warning) && (warning !== '模型报告不确定片段；请人工复核' || !item.payload.extraction?.uncertain_fragments?.length)).map((warning, index) => <Typography.Text type="warning" key={`warning-${index}`}>{warning}</Typography.Text>)}
    </Space>
  }

  return <Space direction="vertical" size="large" style={{ width: '100%' }}>
    <Card title="批量题目导入 · 内部识别与候选核对">
      <Typography.Paragraph>上传后先校验文件；S2 按页段提取题目并自动整批去重。同文件场景同时提取答案；分文件场景先读取题目文件，整批去重完成后识别并关联答案文件。然后可单独启动标签建议，供教师核对。</Typography.Paragraph>
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
          { title: '状态', render: (_, batch: Batch) => <Space direction="vertical" size={0}><Tag color={batch.status === 'failed' ? 'red' : batch.status === 'cancelled' ? 'default' : batch.status === 'review_pending' ? 'green' : 'blue'}>{stageLabel(batch)}</Tag>{batch.checkpoint?.s2 && <Typography.Text type="secondary">题目识别调用 {batch.checkpoint.s2.calls || 0} 次</Typography.Text>}{batch.checkpoint?.s3?.separate_answers && <Typography.Text type={['failed', 'paused'].includes(batch.checkpoint.s3.separate_answers.status) ? 'warning' : 'secondary'}>答案识别：{batch.checkpoint.s3.separate_answers.status}，调用 {batch.checkpoint.s3.separate_answers.calls || 0} 次{batch.checkpoint.s3.separate_answers.error_message ? `；${batch.checkpoint.s3.separate_answers.error_message}` : ''}</Typography.Text>}{batch.checkpoint?.s3?.labels && <Typography.Text type={['failed', 'paused'].includes(batch.checkpoint.s3.labels.status) ? 'warning' : 'secondary'}>标签建议：{batch.checkpoint.s3.labels.status}，调用 {batch.checkpoint.s3.labels.calls || 0} 次{batch.checkpoint.s3.labels.error_message ? `；${batch.checkpoint.s3.labels.error_message}` : ''}</Typography.Text>}{batch.error_message && <Typography.Text type="danger">{batch.error_message}</Typography.Text>}{batch.error_code === 'S2_REVIEW_REQUIRED' && <Typography.Text type="warning">{skippableS2Page(batch) !== null ? '请核对失败页和前一页段的候选题；确认没有新题遗漏后，可人工跳过本页并继续。' : '请检查原文件对应页面并重新上传清晰版，或将批次号发给管理员；已完成的候选题仍可查看。'}批次号：<Typography.Text copyable={{ text: batch.id }}>{batch.id}</Typography.Text></Typography.Text>}{batch.error_code === 'CALL_LIMIT_REACHED' && <Typography.Text type="warning">已达到单批调用次数上限，请将批次号发给管理员：<Typography.Text copyable={{ text: batch.id }}>{batch.id}</Typography.Text></Typography.Text>}</Space> },
           { title: '操作', render: (_, batch: Batch) => <Space wrap>
             {['awaiting_s2', 's2_budget_paused'].includes(batch.stage) && batch.error_code !== 'CALL_LIMIT_REACHED' && <Button disabled={!s2Config?.ready} onClick={() => confirmStart(batch)}>{batch.stage === 's2_budget_paused' ? '调整预算并继续识别' : '开始 S2 识别'}</Button>}
             {batch.checkpoint?.s2 && <Button onClick={() => void viewCandidates(batch.id)}>查看候选与页段</Button>}
             {batch.stage === 's2_completed' && batch.checkpoint?.s3?.global_dedupe?.status !== 'completed' && !['auto_claimed', 'running'].includes(batch.checkpoint?.s3?.global_dedupe?.status || '') && !(batch.checkpoint?.s2?.auto_global_dedupe && !batch.checkpoint?.s3?.global_dedupe?.status) && <Button type="primary" loading={reviewingKey === `batch:${batch.id}`} disabled={!s2Config?.ready || !!batch.files_purged_at} onClick={() => confirmBatchDedup(batch)}>{batch.checkpoint?.s3?.global_dedupe?.status === 'pending_budget' ? '增加预算并整理整批' : batch.source_mode === 'separate' ? '模型整理整批题目' : '模型整理整批题答'}</Button>}
             {batch.stage === 's2_completed' && batch.source_mode === 'separate' && batch.checkpoint?.s3?.global_dedupe?.status === 'completed' && !['queued', 'processing', 'completed'].includes(batch.checkpoint?.s3?.separate_answers?.status || '') && <Button type="primary" disabled={!s2Config?.ready || !!batch.files_purged_at} onClick={() => confirmSeparateAnswers(batch)}>{batch.checkpoint?.s3?.separate_answers?.status === 'paused' ? '增加预算并继续识别答案' : batch.checkpoint?.s3?.separate_answers?.status === 'failed' ? '重试答案识别' : '识别答案文件'}</Button>}
             {batch.stage === 's2_completed' && batch.checkpoint?.s3?.global_dedupe?.status === 'completed' && (batch.source_mode === 'questions_only' || (batch.source_mode === 'combined' && !!batch.checkpoint?.s3?.source_answer_reconciliation) || (batch.source_mode === 'separate' && batch.checkpoint?.s3?.separate_answers?.status === 'completed' && !!batch.checkpoint?.s3?.source_answer_reconciliation)) && !['queued', 'processing', 'completed'].includes(batch.checkpoint?.s3?.labels?.status || '') && !batch.checkpoint?.s3?.labels?.cost_unknown && <Button type="primary" disabled={!s2Config?.ready || !!batch.files_purged_at} onClick={() => confirmLabels(batch)}>{batch.checkpoint?.s3?.labels?.status === 'paused' ? '增加预算并继续打标签' : batch.checkpoint?.s3?.labels?.status === 'failed' ? '重试标签建议' : '生成入库标签建议'}</Button>}
             {batch.stage === 's2_completed' && batch.checkpoint?.s3?.labels?.status === 'completed' && <Button disabled={!s2Config?.ready || !!batch.files_purged_at} onClick={() => confirmLabels(batch, true)}>全部重新打标签</Button>}
             {batch.checkpoint?.s3?.labels?.cost_unknown && <Typography.Text type="warning">标签调用费用未知，请先核对账单后处理</Typography.Text>}
             {batch.status === 'failed' && batch.error_code !== 'S2_REVIEW_REQUIRED' && skippableS2Page(batch) === null && !batch.files_purged_at && <Button disabled={loading} onClick={() => confirmRetry(batch)}>{batch.error_code === 'INCOMPLETE_MODEL_OUTPUT' ? '自动调整页段并重试' : '重试'}</Button>}
             {skippableS2Page(batch) !== null && <Button danger disabled={loading} onClick={() => confirmSkipS2Page(batch, skippableS2Page(batch)!)}>跳过第 {skippableS2Page(batch)} 页并继续</Button>}
             {!['cancelled', 'completed', 'completed_with_unassigned'].includes(batch.status) && <Button disabled={loading} onClick={() => confirmCancel(batch)}>取消</Button>}
             {batch.status === 'cancelled' && <Button danger disabled={loading} onClick={() => confirmDeleteBatch(batch)}>永久删除</Button>}
           </Space> },
        ]} />
    </Card>
    <Modal title="候选题、来源答案与页段记录" open={!!selectedBatchId} width={900} footer={<Button onClick={closeCandidateModal}>关闭</Button>} onCancel={closeCandidateModal}>
      <Typography.Paragraph>按 PDF 页段自动提取位图，并由模型识别题目和所需题图；题答同文件时还会在同次调用中提取原文答案。缺少独立题图时按模型坐标自动裁剪。已匹配图片直接显示在对应题目处。</Typography.Paragraph>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message={selectedBatch?.source_mode === 'separate'
          ? '先提取题目、整批去重，再识别答案文件；答案识别完成后才能增删改题目和答案。'
          : '先提取题目、整批去重；去重完成后才能增删改题目和答案。'}
        description="可在打标签前后增删改。首次打标签和“全部重新打标签”使用启动时的当前题目；修改已打标签的题目或答案不会自动重算标签，修改题目后请复核原标签。全部重新打标签会覆盖已有的题型、难度和主属知识点。" />
      {canEditCandidates && <Space style={{ marginBottom: 12 }}>
        <Button type="primary" onClick={() => { setNewCandidateDraft(emptyCandidateDraft()); setNewCandidateOpen(true) }}>新增题目</Button>
        <Typography.Text type="secondary">新增题目请人工核对重复情况；可在打标签前或后修改。</Typography.Text>
      </Space>}
      {selectedBatch?.stage === 's2_completed' && candidates.some((item) => !hasImportLabels(item)) && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={`还有 ${candidates.filter((item) => !hasImportLabels(item)).length} 道题缺少完整入库标签，请人工打标签；所有题都有标签后才能入库。`} />}
      {selectedBatch?.stage === 's2_completed' && <Card size="small" style={{ marginBottom: 16 }}><details>
        <summary>无法匹配到题目的图片（{unmatchedImages.length}）</summary>
        {!images?.files_available && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message="临时题图已过期，无法显示或修改；请重新上传文件" />}
        {unmatchedImages.length === 0 ? <Typography.Text type="secondary">暂无未匹配图片</Typography.Text> : images?.files_available ? <>
          <Typography.Paragraph type="warning">无法匹配到题目，请人工复核。</Typography.Paragraph>
          <Space wrap>{unmatchedImages.map((asset) => <div key={asset.asset_id} style={{ width: 180 }}>
            <img src={apiUrl(`/question-imports/${selectedBatch.id}/s3/images/${asset.asset_id}/content`)} alt="未匹配的题图" style={{ maxWidth: 180, maxHeight: 160, objectFit: 'contain' }} />
            <div><Typography.Text type="secondary">{asset.page ? `第 ${asset.page} 页` : '上传图片'} · {asset.kind === 'embedded' ? 'PDF 提取' : asset.kind === 'model_crop' ? '自动裁剪' : asset.kind === 'upload' ? '上传图片' : '题图'}</Typography.Text></div>
            {!!asset.removed_candidate_for_item_ids?.length && <div><Typography.Text type="warning">来自已去重题目，请核对保留题是否缺图</Typography.Text></div>}
            <Space direction="vertical" size="small" style={{ width: '100%', marginTop: 8 }}>
              <Select aria-label="选择要关联的题目" placeholder="选择对应题目" showSearch optionFilterProp="label" style={{ width: '100%' }}
                value={unmatchedImageTargets[asset.asset_id]}
                options={imageTargetOptions}
                onChange={(itemId: string) => setUnmatchedImageTargets((current) => ({ ...current, [asset.asset_id]: itemId }))} />
              <Button size="small" type="primary" disabled={!canEditCandidates || imageBusy || !images?.files_available || !imageTargetOptions.some((option) => option.value === unmatchedImageTargets[asset.asset_id])}
                onClick={() => { const target = candidates.find((candidate) => candidate.id === unmatchedImageTargets[asset.asset_id]); if (target && selectedBatch) void linkQuestionImage(selectedBatch, asset, target, true) }}>关联到题目</Button>
            </Space>
          </div>)}</Space>
        </> : null}
      </details></Card>}
      {['combined', 'separate'].includes(selectedBatch?.source_mode || '') && <Typography.Paragraph>
        {reconciliation
          ? reconciliation.manual_candidate_changes_pending
            ? `题目增删后，当前 ${linkedSourceQuestionCount}/${candidates.length} 道题保留资料答案关联；请人工核对待认领答案。`
            : `当前 ${linkedSourceQuestionCount}/${candidates.length} 道题带资料答案；请逐题核对。`
          : selectedBatch?.source_mode === 'separate'
            ? `答案文件识别状态：${separateAnswers?.status || '未开始'}。`
          : selectedBatch?.stage === 's2_completed' && selectedBatch?.checkpoint?.s2?.auto_global_dedupe
            ? '后台正在整批去重并关联题答，题目内的答案会在完成后自动刷新。'
            : '尚未完成题答关联。'}
      </Typography.Paragraph>}
      {selectedBatch?.checkpoint?.s3?.global_dedupe?.status === 'completed' && <Typography.Paragraph>去重后保留 {candidates.length} 道题。</Typography.Paragraph>}
      {['auto_claimed', 'running'].includes(selectedBatch?.checkpoint?.s3?.global_dedupe?.status || '') && <Typography.Paragraph>正在自动整批去重；请稍后刷新。</Typography.Paragraph>}
      {selectedBatch?.checkpoint?.s3?.global_dedupe?.status === 'pending_budget' && <Typography.Paragraph type="warning">S2 预算不足以进行整批判重；请在上方提高预算阈值后点击“增加预算并整理整批”。</Typography.Paragraph>}
      {selectedBatch?.checkpoint?.s3?.global_dedupe?.status === 'failed' && <Typography.Paragraph type="warning">整批模型判重失败：{selectedBatch.checkpoint.s3.global_dedupe.error_message}</Typography.Paragraph>}
      {!!s2?.units?.length && <details key={`${selectedBatch?.id}:s2-units`}>
        <summary>题目识别页段记录（{s2.units.length} 段）</summary>
        <Typography.Paragraph type="secondary">每页只归一个主体页段；后续页作为跨页上下文重复传入模型。模型报告不完整后，新拆分的页段最多带一页上下文；单页主体仍失败时可核对原页后人工跳过。</Typography.Paragraph>
        {s2.units.map((unit) => <div key={unit.index}>
          <Typography.Paragraph>页段 {unit.index + 1}：主体为原文件第 {unit.core_first_page}–{unit.core_last_page} 页{unit.last_page > unit.core_last_page ? `，第 ${unit.core_last_page + 1}–${unit.last_page} 页仅作跨页上下文` : ''}；{unit.skipped_by_teacher ? '已人工跳过，需核对本页是否漏题' : unit.status === 'completed' ? `S2 当时提取 ${unit.candidate_count || 0} 题（下方展示当前候选）` : '待处理'}</Typography.Paragraph>
          {unit.skipped_by_teacher && unit.skip_error_message && <Typography.Paragraph type="warning">跳过前的模型报错：{unit.skip_error_message}</Typography.Paragraph>}
          {selectedBatch?.source_mode !== 'combined' && unit.source_answer_signals?.map((signal, index) => <Typography.Paragraph key={`answer-signal-${index}`} type="warning">第 {signal.page} 页可能含资料答案，请核对上传场景：{signal.evidence}</Typography.Paragraph>)}
          {!!unit.overlap_suspects?.length && <details><summary>S2 当时检出的疑似跨页重复 {unit.overlap_suspects.length} 对</summary>{unit.overlap_suspects.map((pair, index) => <Typography.Paragraph key={index}>候选 {pair.candidate_item_id} 与 {pair.other_item_id}；当前结果以整批模型判重为准。</Typography.Paragraph>)}</details>}
        </div>)}
      </details>}
      {!!reviewIssues.length && <details key={`${selectedBatch?.id}:review-issues`} className="question-import-issues">
        <summary>待教师核对的问题（{reviewIssues.length} 条）</summary>
        <div className="question-import-issues-content">
          <Typography.Paragraph type="secondary">请根据来源页码、原文件和候选题人工核对。</Typography.Paragraph>
          <div className="question-import-issue-types">
            {reviewCategories.map((category) => {
              const categoryIssues = reviewIssues.filter((issue) => issue.kind === category.kind)
              return <details key={category.kind} className="question-import-issue-group">
                <summary>{category.title}（{categoryIssues.length} 条）</summary>
                <div className="question-import-issue-list">
                  {categoryIssues.length === 0 && <Typography.Text type="secondary">暂无此类问题</Typography.Text>}
                  {categoryIssues.map((issue) => {
                    const candidateSummaries = issue.candidate_item_ids.map((id) => {
                      const item = candidates.find((candidate) => candidate.id === id)
                      return item ? `候选第 ${item.ordinal} 题（原题号 ${item.payload.source.original_number || '未识别'}，${selectedBatch?.source_mode === 'separate' ? '题目文件' : '原文件'}第 ${item.payload.source.page_start ?? '未知'} 页）` : `候选 ID ${id}`
                    })
                    return <Card key={issue.key} size="small" className="question-import-issue-card"
                      title={`${selectedBatch?.source_mode === 'separate' ? ['answer', 'answer_fragment'].includes(issue.kind) ? '答案文件' : '题目文件' : '原文件'}${issue.summary}`}
                      extra={<Tag color="orange">待教师核对</Tag>}>
                      {['fragment', 'answer_fragment', 'skipped_page'].includes(issue.kind) && <>
                        <Typography.Paragraph><Typography.Text strong>{issue.kind === 'skipped_page' ? '跳过记录：' : '待核对片段：'}</Typography.Text></Typography.Paragraph>
                        <RichQuestionText text={issue.fragment_text || '内容未记录'} assets={[]} />
                        <Typography.Paragraph type="secondary">问题说明：{issue.fragment_reason || '原因未记录'}</Typography.Paragraph>
                      </>}
                      {issue.kind === 'answer' && <>
                        <Typography.Paragraph><Typography.Text strong>待认领答案：</Typography.Text>原题号 {issue.answer_number || '缺失'}</Typography.Paragraph>
                        <RichQuestionText text={issue.answer_text || '内容未记录'} assets={[]} />
                      </>}
                      {!!candidateSummaries.length && <Typography.Paragraph>相关候选题：{candidateSummaries.join('；')}</Typography.Paragraph>}
                      <Typography.Paragraph type="secondary">
                        {selectedBatch?.source_mode === 'separate' && issue.kind === 'answer'
                          ? `答案文件来源页：${issue.source_answer?.page_start ?? '未知'}${issue.source_answer?.page_end && issue.source_answer.page_end !== issue.source_answer.page_start ? `–${issue.source_answer.page_end}` : ''}；相关候选题的题目文件页码见上方。`
                          : `相关来源页：${issue.page_numbers.join('、') || '未知'}${['fragment', 'skipped_page'].includes(issue.kind) ? '（含邻近题目上下文）' : ''}。`}{['fragment', 'skipped_page'].includes(issue.kind)
                          ? `邻近候选题 ${issue.candidate_item_ids.length} 道，仅供判断归属，不表示这些题有错误。`
                          : issue.kind === 'answer_fragment'
                          ? '答案文件与题目文件页码彼此独立，请对照答案原页和题目内容核对。'
                          : `候选 ${issue.candidate_item_ids.length} 条。`}
                        {issue.candidate_total > issue.candidate_item_ids.length && `共 ${issue.candidate_total} 条候选，此处仅列部分。`}
                      </Typography.Paragraph>
                    </Card>
                  })}
                </div>
              </details>
            })}
          </div>
        </div>
      </details>}
      {!!duplicateCandidates.length && <Typography.Paragraph><Button size="small" onClick={() => setShowDuplicateCandidates((value) => !value)}>{showDuplicateCandidates ? '收起' : '显示'}疑似重复候选 {duplicateCandidates.length} 条</Button><Typography.Text type="secondary"> 原始记录仍保留；确认不是重复题时可展开核对。</Typography.Text></Typography.Paragraph>}
      <Table rowKey="id" dataSource={visibleCandidates} size="small" pagination={{ pageSize: 10 }} columns={[
        { title: '序号', dataIndex: 'ordinal', width: 70 },
        { title: '原题号', render: (_, item: Candidate) => item.payload.source.original_number || '未识别', width: 90 },
        { title: '原页', render: (_, item: Candidate) => `${item.payload.source.page_start ?? '?'}–${item.payload.source.page_end ?? '?'}`, width: 90 },
        { title: '题目、题图与答案', render: (_, item: Candidate) => renderCandidateContent(item) },
      ]} />
    </Modal>
    <Modal title="新增候选题" open={newCandidateOpen} width={760}
      onCancel={() => setNewCandidateOpen(false)}
      footer={<Space><Button disabled={creatingCandidate} onClick={() => setNewCandidateOpen(false)}>取消</Button><Button type="primary" loading={creatingCandidate} onClick={() => void createCandidate()}>保存新题</Button></Space>}>
      <Typography.Paragraph type="secondary">新增题目保留在当前导入批次中；来源题号和页码可选，答案可稍后编辑。新题需要人工核对重复与标签。</Typography.Paragraph>
      <Space wrap style={{ marginBottom: 12 }}>
        <Input aria-label="原题号" placeholder="原题号（可选）" value={newCandidateDraft.originalNumber} maxLength={80}
          onChange={(event) => setNewCandidateDraft((current) => ({ ...current, originalNumber: event.target.value }))} />
        <InputNumber aria-label="题目来源页码" placeholder="来源页码（可选）" min={1} max={s2?.page_count} value={newCandidateDraft.sourcePage}
          onChange={(value) => setNewCandidateDraft((current) => ({ ...current, sourcePage: value }))} />
      </Space>
      <Typography.Paragraph>题干</Typography.Paragraph>
      <Input.TextArea value={newCandidateDraft.questionText} autoSize={{ minRows: 3, maxRows: 12 }} maxLength={30000}
        onChange={(event) => setNewCandidateDraft((current) => ({ ...current, questionText: event.target.value }))} />
      <Typography.Paragraph style={{ marginTop: 12 }}>选项（非选择题可不填）</Typography.Paragraph>
      {newCandidateDraft.options.map((option, index) => <Space key={index} style={{ display: 'flex', marginBottom: 8 }} align="start">
        <Input aria-label={`新题第 ${index + 1} 个选项编号`} style={{ width: 70 }} value={option.key} maxLength={20}
          onChange={(event) => setNewCandidateDraft((current) => ({ ...current, options: current.options.map((value, i) => i === index ? { ...value, key: event.target.value } : value) }))} />
        <Input.TextArea aria-label={`新题第 ${index + 1} 个选项内容`} style={{ width: 520, maxWidth: '55vw' }} value={option.text} autoSize={{ minRows: 1, maxRows: 5 }} maxLength={10000}
          onChange={(event) => setNewCandidateDraft((current) => ({ ...current, options: current.options.map((value, i) => i === index ? { ...value, text: event.target.value } : value) }))} />
        <Button size="small" danger onClick={() => setNewCandidateDraft((current) => ({ ...current, options: current.options.filter((_, i) => i !== index) }))}>删除</Button>
      </Space>)}
      {newCandidateDraft.options.length < 20 && <Button size="small" onClick={() => setNewCandidateDraft((current) => ({ ...current, options: [...current.options, { key: String.fromCharCode(65 + current.options.length), text: '' }] }))}>添加选项</Button>}
      <Typography.Paragraph style={{ marginTop: 16 }}>答案（可选）</Typography.Paragraph>
      <Input.TextArea value={newCandidateDraft.answerText} autoSize={{ minRows: 3, maxRows: 12 }} maxLength={30000}
        onChange={(event) => setNewCandidateDraft((current) => ({ ...current, answerText: event.target.value }))} />
    </Modal>
  </Space>
}

export default QuestionImportPage
