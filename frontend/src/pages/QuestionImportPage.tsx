import { Alert, Button, Card, Checkbox, Empty, Input, InputNumber, Modal, Progress, Select, Space, Table, Tag, Typography, message } from 'antd'
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
    s2?: { calls?: number; page_count?: number; prompt_version?: string; auto_global_dedupe?: boolean; auto_answer_matching?: boolean; failed_unit?: { index: number; code: string }; units?: Array<{ index: number; first_page: number; last_page: number; core_first_page: number; core_last_page: number; status: string; candidate_count?: number; skipped_by_teacher?: boolean; skip_error_message?: string | null; source_answers?: Array<{ source_file_id?: string; original_number: string | null; subquestion_number?: string | null; text: string; page_start: number; candidate_item_ids?: string[] }>; source_answer_signals?: Array<{ page: number; evidence: string }>; overlap_suspects?: Array<{ candidate_item_id: string; other_item_id: string }>; unresolved_fragments?: Array<{ page: number; text: string; reason: string }> }> }
    s3?: { global_dedupe?: { status: string; reviewed_candidate_count?: number; removed_duplicate_count?: number; error_message?: string; estimated_cny?: number }; separate_answers?: { status: string; auto_answer_matching?: boolean; calls?: number; error_message?: string; units?: Array<{ index: number; first_page: number; core_last_page: number; status: string; source_answers?: Array<{ source_file_id?: string; original_number: string | null; subquestion_number?: string | null; text: string; page_start: number; candidate_item_ids?: string[] }>; unresolved_fragments?: Array<{ page: number; text: string; reason: string }> }> }; answer_model_matching?: { status: string; calls?: number; estimated_cny?: number; error_message?: string; cost_unknown?: boolean; skipped?: Array<{ answer_id: string; reason: string }>; groups?: Array<{ status: string }> }; answer_generation?: { status: string; calls?: number; estimated_cny?: number; cost_unknown?: boolean; error_message?: string; groups?: Array<{ status: string; applied_count?: number; skipped_item_ids?: string[] }> }; labels?: { status: string; calls?: number; estimated_cny?: number; cost_unknown?: boolean; error_code?: string; error_message?: string; skipped_item_ids?: string[]; groups?: Array<{ index: number; status: string; applied_count?: number; skipped_item_ids?: string[] }> }; source_answer_reconciliation?: { schema_version: string; source_answer_count: number; linked_answer_count: number; linked_question_count: number; manual_candidate_changes_pending?: boolean; overlap_duplicate_answers?: Array<{ text: string; page_start: number; matched_original_number: string; matched_page: number }>; unassigned_answers: Array<{ source_file_id?: string; original_number: string | null; text: string; page_start: number; subquestion_number?: string | null; reason: string; candidate_item_ids?: string[] }> } }
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
  model_reason?: string | null
  model_suggestion?: string | null
  current_item_id?: string | null
  evidence_quote?: string | null
  signature: string
  handling?: { decision: string; reason: string | null; valid: boolean; created_at: string } | null
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
    figure_text_confirmation?: { signature: string; confirmed_at: string }
    answers?: Array<{ answer_id: string; answer_source: string; text: string | null; teacher_confirmed: boolean; evidence?: Array<{ file_id: string; page: number; quote: string }> }>
    selected_answer_id?: string | null
    answer_match_status?: string
    teacher_decision?: string
    commit_intent?: 'library_only' | 'direct_pool' | null
    duplicate_status?: string
    label_status?: string
    tags?: { question_type?: string; difficulty?: number | null; main_kp_code?: string | null; grade?: string | null; chapter_code?: string | null; section?: string | null; core_literacies?: string[]; is_required?: boolean }
    label_suggestion?: { question_type: string; difficulty: number | null; main_kp_code: string | null; alternative_kp_codes: string[]; uncertainty: string | null; revision: number; model: string; stale: boolean }
    warnings?: string[]
  }
}

type S2Config = { ready: boolean; model: string; max_budget_cny: number; max_output_tokens: number; pages_per_unit: number; overlap_pages: number }
type LabelCatalogPoint = { code: string; name: string; grade: string; chapter_code: string; chapter_name: string; section: string; core_literacies: string[] }
type LabelEdit = { question_type?: string; difficulty?: number | null; main_kp_code?: string | null; is_required?: boolean }
type LabelScope = { grade: string | null; chapter_code: string | null }
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
type CatalogReviewMatch = {
  id: string; question_id: string | null; original_qid: string; kind: 'exact' | 'nearby'
  similarity: number | null; evidence: { differences?: string[]; image_relation?: string | null;
    answer_text_differs?: boolean | null }
  decision: 'pending' | 'skip_existing' | 'keep_variant' | 'unrelated'; reason: string | null
  catalog_stale: boolean; catalog_unverifiable: boolean; question_text: string | null
  options: unknown; reference_answer_raw: string | null
}
type CatalogReviewResult = { batch_id: string; items: Array<{
  item_id: string; check_id: string; check_no: number; candidate_revision: number;
  current_revision: number; candidate_stale: boolean; catalog_stale: boolean;
  near_review_min_similarity?: number | null;
  checked_at: string | null; matches: CatalogReviewMatch[]
}> }

type S5Problem = { code: string; message: string }
type S5Summary = { counts: {
  active_real: number; active_fake: number; pending: number; approved: number; rejected: number;
  skipped_duplicate: number; invalid_review_records: number;
  ready_for_library_confirmation: number; ready_for_pool_confirmation: number;
  needs_answer_review_for_pool: number; missing_catalog_check: number; stale_catalog_check: number;
  exact_match_items: number; near_match_items: number; unassigned_answer_issues: number;
  unassigned_images: number; review_issues: number; resolved_review_issues: number;
} }
type S5ReviewRow = { item_id: string; decision: string; recorded_decision?: string; valid: boolean;
  reason: string | null; route: 'library_only' | 'direct_pool' | null;
  question_event_id: string | null; answer_confirmed: boolean; candidate_revision?: number; created_at?: string }
type S5Reviews = { batch_id: string; items: S5ReviewRow[] }
type S5PreflightRow = { item_id: string; current_revision: number | null; route: 'library_only' | 'direct_pool';
  batch_eligible: boolean; blockers: S5Problem[]; warnings: S5Problem[]; confirmation_pending: boolean }
type S5Preflight = { items: S5PreflightRow[]; summary: { selected: number; eligible: number; blocked: number } }
type AnswerUnitDraft = { item_id: string; subquestion_no: number | null; blank_no: number | null;
  answer: string; accepted_answers: string; answer_match_type: 'choice' | 'numeric' | 'exact' | 'latex_exact' }
type S5ReviewDraft = { itemId: string; revision: number; route: 'library_only' | 'direct_pool';
  reason: string; units: AnswerUnitDraft[]; preflight: S5PreflightRow | null }
type S5BulkDraft = { selectedIds: string[]; route: 'library_only' | 'direct_pool'; reason: string;
  units: Record<string, AnswerUnitDraft[]>; preflight: S5Preflight | null; screening: S5Preflight | null }
type S5IssueDraft = { issue: ReviewIssue; decision: 'acknowledged' | 'deferred' | 'ignored' | 'pending'; reason: string }

function defaultAnswerUnit(item: Candidate): AnswerUnitDraft {
  const questionType = item.payload.tags?.question_type
  const answers = item.payload.answers || []
  const selected = answers.find((answer) => answer.answer_id === item.payload.selected_answer_id)
    || (answers.length === 1 ? answers[0] : undefined)
  return { item_id: 'answer-1', subquestion_no: null, blank_no: null,
    answer: selected?.text || '', accepted_answers: '',
    answer_match_type: questionType === 'choice' ? 'choice' : questionType === 'true_false' ? 'exact' : 'exact' }
}

function answerMatchOptions(questionType?: string): Array<{ value: AnswerUnitDraft['answer_match_type']; label: string }> {
  if (questionType === 'choice') return [{ value: 'choice', label: '选项字母' }]
  if (questionType === 'true_false') return [{ value: 'exact', label: '正确／错误' }]
  return [{ value: 'exact', label: '精确文字' }, { value: 'numeric', label: '整数或小数' }, { value: 'latex_exact', label: '公式' }]
}

function choiceOptionKeys(item: Candidate): string {
  return item.payload.content.options.map((option) => option.key).join('、')
}

function answerUnitsPayload(units: AnswerUnitDraft[]) {
  return units.map((unit) => ({ item_id: unit.item_id, subquestion_no: unit.subquestion_no,
    blank_no: unit.blank_no, answer: unit.answer.trim(),
    accepted_answers: unit.accepted_answers.split(/[\n；;]/).map((value) => value.trim()).filter(Boolean),
    answer_match_type: unit.answer_match_type }))
}

function catalogOptionRows(options: unknown): Array<{ key: string; text: string }> {
  if (Array.isArray(options)) {
    return options.flatMap((option, index) => {
      if (typeof option === 'string') return [{ key: String.fromCharCode(65 + index), text: option }]
      if (option && typeof option === 'object' && typeof option.text === 'string') {
        return [{ key: typeof option.key === 'string' ? option.key : String.fromCharCode(65 + index), text: option.text }]
      }
      return []
    })
  }
  if (options && typeof options === 'object') {
    return Object.entries(options).flatMap(([key, value]) => {
      if (typeof value === 'string') return [{ key, text: value }]
      if (value && typeof value === 'object' && 'text' in value && typeof value.text === 'string') {
        return [{ key, text: value.text }]
      }
      return []
    })
  }
  return []
}

const modeLabels: Record<string, string> = {
  questions_only: '只有题目', combined: '题目与答案同文件', separate: '题目与答案分文件',
}

function stageLabel(batch: Batch): string {
  if (batch.stage === 'awaiting_s2') return '文件已校验，等待 S2 解析'
  if (batch.stage === 's2_queued') return '等待识别题目文件'
  if (batch.stage === 's2_processing') return '正在识别题目文件页段'
  if (batch.stage === 's2_completed') {
    const generation = batch.checkpoint?.s3?.answer_generation
    if (generation?.status === 'queued') return '答案生成等待处理'
    if (generation?.status === 'processing') return '正在生成答案'
    if (generation?.status === 'paused') return '答案生成已暂停'
    if (generation?.status === 'failed') return '答案生成失败'
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
    const matching = batch.checkpoint?.s3?.answer_model_matching?.status
    const matchingLabel = matching === 'queued' ? '答案模型配对等待处理'
      : matching === 'processing' ? '正在用模型辅助配对答案'
      : matching === 'pending_budget' ? '答案模型配对待补预算'
      : matching === 'failed' ? '答案模型配对失败'
      : null
    if (batch.source_mode === 'separate' && dedupe === 'completed') {
      const answers = batch.checkpoint?.s3?.separate_answers?.status
      if (answers === 'queued') return '答案文件等待识别'
      if (answers === 'processing') return '正在识别答案文件'
      if (answers === 'paused') return '答案文件识别已暂停'
      if (answers === 'failed') return '答案文件识别失败，可重试'
      if (answers === 'completed') return matchingLabel || (batch.checkpoint?.s3?.separate_answers?.auto_answer_matching && !matching
        ? '等待模型辅助配对答案' : '答案关联完成，待生成标签建议')
      return '题目去重完成，待启动答案文件识别'
    }
    if (batch.source_mode === 'combined' && dedupe === 'completed') {
      return matchingLabel || (batch.checkpoint?.s2?.auto_answer_matching && !matching
        ? '等待模型辅助配对答案' : '整批整理完成，待生成标签建议')
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
  const [labelScopes, setLabelScopes] = useState<Record<string, LabelScope>>({})
  const [labelSaving, setLabelSaving] = useState<string | null>(null)
  const [candidateEditor, setCandidateEditor] = useState<CandidateEditor | null>(null)
  const [candidateSaving, setCandidateSaving] = useState(false)
  const [newCandidateOpen, setNewCandidateOpen] = useState(false)
  const [newCandidateDraft, setNewCandidateDraft] = useState<NewCandidateDraft>(emptyCandidateDraft)
  const [creatingCandidate, setCreatingCandidate] = useState(false)
  const [deletingCandidateId, setDeletingCandidateId] = useState<string | null>(null)
  const [catalogReviewResult, setCatalogReviewResult] = useState<CatalogReviewResult | null>(null)
  const [exactCatalogBusy, setExactCatalogBusy] = useState(false)
  const [catalogDecisionDraft, setCatalogDecisionDraft] = useState<{ item: Candidate; match: CatalogReviewMatch; decision: CatalogReviewMatch['decision'] } | null>(null)
  const [catalogDecisionReason, setCatalogDecisionReason] = useState('')
  const [catalogDecisionBusy, setCatalogDecisionBusy] = useState(false)
  const [s5Summary, setS5Summary] = useState<S5Summary | null>(null)
  const [s5Reviews, setS5Reviews] = useState<S5Reviews | null>(null)
  const [candidateFilter, setCandidateFilter] = useState('all')
  const [candidateQuery, setCandidateQuery] = useState('')
  const [reviewDraft, setReviewDraft] = useState<S5ReviewDraft | null>(null)
  const [reviewBusy, setReviewBusy] = useState(false)
  const [bulkDraft, setBulkDraft] = useState<S5BulkDraft | null>(null)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [figureTextBusy, setFigureTextBusy] = useState<string | null>(null)
  const [issueDraft, setIssueDraft] = useState<S5IssueDraft | null>(null)
  const [issueBusy, setIssueBusy] = useState(false)
  const [answerClaimTargets, setAnswerClaimTargets] = useState<Record<string, string>>({})
  const [answerSourceBusy, setAnswerSourceBusy] = useState<string | null>(null)
  const [answerSourceDraft, setAnswerSourceDraft] = useState<{ itemId: string; revision: number; answerId: string; reason: string } | null>(null)

  function closeCandidateModal() {
    setCandidateEditor(null)
    setNewCandidateOpen(false)
    setLabelEdits({})
    setLabelScopes({})
    setSelectedBatchId(null)
    setCatalogReviewResult(null)
    setS5Summary(null)
    setS5Reviews(null)
    setReviewDraft(null)
    setIssueDraft(null)
    setAnswerSourceDraft(null)
  }

  async function refreshS5(id: string) {
    const [summary, reviews, issues] = await Promise.all([
      apiGet<S5Summary>(`/question-imports/${id}/s5/summary`),
      apiGet<S5Reviews>(`/question-imports/${id}/s5/reviews`),
      apiGet<ReviewIssue[]>(`/question-imports/${id}/s3/review-issues`),
    ])
    setS5Summary(summary)
    setS5Reviews(reviews)
    setReviewIssues(issues)
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
      content: `将题目文件按页段发送给百炼 ${s2Config?.model || ''}，页段识别完成后自动把整批候选题发送给模型去重${batch.source_mode === 'combined' ? '，再自动关联并用已提取文字辅助核对同文件答案' : ''}。请确认文件不含学生个人信息。本批调用预算阈值为 ¥${budget}；这是基于已返回用量的停机阈值，单次调用可能使实际费用略高。`,
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
      const [items, issues, initialImages, currentCatalog, summary, reviews] = await Promise.all([
        apiGet<Candidate[]>(`/question-imports/${id}/items`),
        apiGet<ReviewIssue[]>(`/question-imports/${id}/s3/review-issues`),
        apiGet<ImageInventory>(`/question-imports/${id}/s3/images`),
        apiGet<LabelCatalogPoint[]>('/question-imports/label-catalog').catch(() => labelCatalog),
        apiGet<S5Summary>(`/question-imports/${id}/s5/summary`),
        apiGet<S5Reviews>(`/question-imports/${id}/s5/reviews`),
      ])
      let imageRows = initialImages
      if (!imageRows.embedded_scanned && imageRows.files_available && batches.find((batch) => batch.id === id)?.stage === 's2_completed') {
        try { imageRows = await apiPost<ImageInventory>(`/question-imports/${id}/s3/images`, {}) }
        catch (error) { message.warning(error instanceof Error ? error.message : 'PDF 图片自动提取失败') }
      }
      const savedReview = await apiGet<CatalogReviewResult>(`/question-imports/${id}/s4/review`)
      setCandidates(items)
      setLabelCatalog(currentCatalog)
      setImages(imageRows)
      setUnmatchedImageTargets({})
      setShowDuplicateCandidates(false)
      setReviewIssues(issues)
      setSelectedBatchId(id)
      setCatalogReviewResult(savedReview)
      setS5Summary(summary)
      setS5Reviews(reviews)
      if (imageRows !== initialImages) await refreshS5(id)
    } catch (error) { message.error(error instanceof Error ? error.message : '候选题加载失败') }
  }

  async function checkExactCatalog(id: string) {
    setExactCatalogBusy(true)
    try {
      setCatalogReviewResult(await apiPost<CatalogReviewResult>(`/question-imports/${id}/s4/review`, {}))
      setCandidates(await apiGet<Candidate[]>(`/question-imports/${id}/items`))
      await refreshS5(id)
    } catch (error) { message.error(error instanceof Error ? error.message : '题库查重失败') }
    finally { setExactCatalogBusy(false) }
  }

  async function saveCatalogDecision() {
    const draft = catalogDecisionDraft
    if (!draft || !selectedBatchId) return
    setCatalogDecisionBusy(true)
    try {
      const result = await apiPost<CatalogReviewResult>(
        `/question-imports/${selectedBatchId}/s4/items/${draft.item.id}/matches/${draft.match.id}/decision`,
        { expected_revision: draft.item.revision, decision: draft.decision, reason: catalogDecisionReason.trim() || null },
      )
      setCatalogReviewResult(result)
      setCandidates(await apiGet<Candidate[]>(`/question-imports/${selectedBatchId}/items`))
      await refreshS5(selectedBatchId)
      setCatalogDecisionDraft(null)
      setCatalogDecisionReason('')
      message.success('查重决定已保存')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '查重决定保存失败')
      setCatalogReviewResult(await apiGet<CatalogReviewResult>(`/question-imports/${selectedBatchId}/s4/review`).catch(() => catalogReviewResult))
      setCandidates(await apiGet<Candidate[]>(`/question-imports/${selectedBatchId}/items`).catch(() => candidates))
      await refreshS5(selectedBatchId).catch(() => undefined)
    } finally { setCatalogDecisionBusy(false) }
  }

  function openCatalogDecision(item: Candidate, match: CatalogReviewMatch, decision: CatalogReviewMatch['decision']) {
    setCatalogDecisionDraft({ item, match, decision })
    setCatalogDecisionReason(decision === 'pending' ? '' : match.reason || '')
  }

  function confirmFigureText(item: Candidate, confirmed: boolean) {
    if (!selectedBatchId) return
    Modal.confirm({
      title: confirmed ? '确认文字已完整表达，无需题图？' : '撤销无需题图的确认？',
      width: 680,
      content: confirmed ? <Space direction="vertical" style={{ maxHeight: '55vh', overflowY: 'auto' }}>
        <Typography.Text>请对照原文件第 {item.payload.source.page_start ?? '?'} 页，确认表格或图示中的所有解题信息均已写入文字。</Typography.Text>
        <RichQuestionText text={item.payload.content.question_text} assets={[]} />
        {item.payload.content.options.map((option) => <RichQuestionText key={option.key} text={`${option.key}. ${option.text}`} assets={[]} />)}
        {(item.payload.extraction?.diagram_clues || []).map((clue, index) => <Typography.Text type="warning" key={index}>题图线索：{clue}</Typography.Text>)}
        <Typography.Text type="secondary">保存后系统会自动重新查重和预检。</Typography.Text>
      </Space> : '撤销后，该题会重新要求关联关键题图，系统会自动重新查重和预检。',
      okText: confirmed ? '确认无需题图' : '撤销确认',
      onOk: async () => {
        const batchId = selectedBatchId
        let saved = false
        setFigureTextBusy(item.id)
        try {
          await apiPost<Candidate>(`/question-imports/${batchId}/s5/items/${item.id}/figure-text-confirmation`,
            { expected_revision: item.revision, confirmed })
          saved = true
          setBulkDraft((current) => current ? { ...current, preflight: null, screening: null } : current)
          const rows = await apiGet<Candidate[]>(`/question-imports/${batchId}/items`)
          setCandidates(rows)
          setCatalogReviewResult(await apiGet<CatalogReviewResult>(`/question-imports/${batchId}/s4/review`))
          await refreshS5(batchId)
          setReviewDraft(null)
          if (bulkDraft) {
            const selections = bulkDraft.selectedIds.length ? bulkSelections(bulkDraft, bulkDraft.selectedIds, rows) : []
            const screeningSelections = bulkDraft.screening
              ? bulkSelections(bulkDraft, bulkDraft.screening.items.map((row) => row.item_id), rows) : []
            const [preflight, screening] = await Promise.all([
              selections.length ? apiPost<S5Preflight>(`/question-imports/${batchId}/s5/preflight`, { items: selections }) : Promise.resolve(null),
              screeningSelections.length ? apiPost<S5Preflight>(`/question-imports/${batchId}/s5/preflight`, { items: screeningSelections }) : Promise.resolve(null),
            ])
            setBulkDraft((current) => current ? { ...current, preflight, screening } : current)
          }
          message.success(confirmed ? '已确认无需题图，查重与预检已自动更新' : '已撤销确认，查重与预检已自动更新')
        } catch (error) {
          message.error(saved ? '确认已保存，但预检状态刷新失败；请重新打开审核页面' : error instanceof Error ? error.message : '题图确认保存失败')
          setCandidates(await apiGet<Candidate[]>(`/question-imports/${batchId}/items`).catch(() => candidates))
          if (saved) setCatalogReviewResult(await apiGet<CatalogReviewResult>(`/question-imports/${batchId}/s4/review`).catch(() => catalogReviewResult))
        } finally {
          setFigureTextBusy(null)
        }
      },
    })
  }

  function renderFigureTextAction(item: Candidate) {
    const extraction = item.payload.extraction
    if ((item.payload.content.asset_ids || []).length || !(extraction?.diagram_clues?.length
      || ['complete', 'uncertain'].includes(extraction?.figure_status || ''))) return null
    const confirmed = !!item.payload.figure_text_confirmation
    return <Space wrap style={{ marginTop: 6 }}>
      <Tag color={confirmed ? 'green' : 'orange'}>{confirmed ? '教师已确认文字完整，无需题图' : '题图线索待核对'}</Tag>
      {canEditCandidates && dedupeStatus === 'completed' && <Button size="small" loading={figureTextBusy === item.id}
        disabled={!!figureTextBusy || bulkBusy} onClick={() => confirmFigureText(item, !confirmed)}>
        {confirmed ? '撤销无需题图的确认' : '确认文字已完整表达，无需题图'}
      </Button>}
    </Space>
  }

  function openS5Review(item: Candidate) {
    const current = s5Reviews?.items.find((row) => row.item_id === item.id)
    setReviewDraft({ itemId: item.id, revision: item.revision,
      route: current?.valid && current.route ? current.route : 'library_only',
      reason: '', units: [defaultAnswerUnit(item)], preflight: null })
  }

  async function selectS5Answer(item: Candidate, answerId: string) {
    if (!selectedBatchId || !canEditCandidates) return
    setReviewBusy(true)
    try {
      const updated = await apiPatch<Candidate>(`/question-imports/${selectedBatchId}/items/${item.id}`, {
        expected_revision: item.revision, changes: { selected_answer_id: answerId },
      })
      await viewCandidates(selectedBatchId)
      setReviewDraft((current) => current?.itemId === item.id ? { ...current,
        revision: updated.revision, units: [defaultAnswerUnit(updated)], preflight: null } : current)
      setBulkDraft((current) => current ? { ...current, preflight: null, screening: null } : current)
      message.success('已选定此答案；答案内容仍需教师审核')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '选定答案失败')
      await viewCandidates(selectedBatchId)
      setReviewDraft(null)
    } finally { setReviewBusy(false) }
  }

  async function previewS5Review() {
    if (!selectedBatchId || !reviewDraft) return
    const item = candidates.find((row) => row.id === reviewDraft.itemId)
    if (!item || item.revision !== reviewDraft.revision) { message.warning('候选题已变化，请刷新后重新审核'); return }
    setReviewBusy(true)
    try {
      const result = await apiPost<S5Preflight>(`/question-imports/${selectedBatchId}/s5/preflight`, {
        items: [{ item_id: item.id, expected_revision: reviewDraft.revision, route: reviewDraft.route,
          ...(reviewDraft.route === 'direct_pool' ? { answer_review_items: answerUnitsPayload(reviewDraft.units) } : {}) }],
      })
      setReviewDraft((current) => current ? { ...current, preflight: result.items[0] } : current)
    } catch (error) { message.error(error instanceof Error ? error.message : '预检失败') }
    finally { setReviewBusy(false) }
  }

  async function saveS5Review(decision: 'approved' | 'rejected' | 'pending') {
    if (!selectedBatchId || !reviewDraft) return
    if (decision === 'approved' && !reviewDraft.preflight?.batch_eligible) {
      message.warning('请先通过当前路径的预检'); return
    }
    if (decision === 'rejected' && !reviewDraft.reason.trim()) { message.warning('请填写拒绝理由'); return }
    setReviewBusy(true)
    try {
      await apiPost(`/question-imports/${selectedBatchId}/s5/items/${reviewDraft.itemId}/decision`, {
        expected_revision: reviewDraft.revision, decision,
        ...(decision === 'approved' ? { route: reviewDraft.route,
          ...(reviewDraft.route === 'direct_pool' ? { answer_review_items: answerUnitsPayload(reviewDraft.units) } : {}) } : {}),
        reason: reviewDraft.reason.trim() || null,
      })
      setReviewDraft(null)
      await viewCandidates(selectedBatchId)
      message.success(decision === 'approved' ? '审核决定已保存' : decision === 'rejected' ? '已拒绝并记录理由' : '已撤回审核决定')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '审核决定保存失败')
      await viewCandidates(selectedBatchId)
      setReviewDraft(null)
    } finally { setReviewBusy(false) }
  }

  function bulkPendingItems() {
    return candidates.filter((item) => !item.is_fake
      && !['approved', 'rejected', 'skipped_duplicate'].includes(item.payload.teacher_decision || '')
      && !s5Reviews?.items.some((review) => review.item_id === item.id && review.valid && review.decision !== 'pending'))
  }

  function openBulkReview() {
    setBulkDraft({ selectedIds: [], route: 'library_only', reason: '', units: {}, preflight: null, screening: null })
  }

  function bulkSelections(draft: S5BulkDraft, ids: string[], rows: Candidate[] = candidates) {
    return ids.map((id) => {
      const item = rows.find((row) => row.id === id)!
      return { item_id: id, expected_revision: item.revision, route: draft.route,
        ...(draft.route === 'direct_pool' ? {
          answer_review_items: answerUnitsPayload(draft.units[id] || [defaultAnswerUnit(item)]),
        } : {}) }
    })
  }

  async function previewBulkReview(selectEligible = false) {
    if (!selectedBatchId || !bulkDraft) return
    const ids = selectEligible ? bulkPendingItems().slice(0, 200).map((item) => item.id) : bulkDraft.selectedIds
    if (!ids.length) { message.warning(selectEligible ? '当前没有待审题' : '请先选择题目'); return }
    setBulkBusy(true)
    try {
      const result = await apiPost<S5Preflight>(`/question-imports/${selectedBatchId}/s5/preflight`, {
        items: bulkSelections(bulkDraft, ids),
      })
      const eligibleRows = result.items.filter((row) => row.batch_eligible)
      const selectedPreflight: S5Preflight = { items: eligibleRows,
        summary: { selected: eligibleRows.length, eligible: eligibleRows.length, blocked: 0 } }
      setBulkDraft((current) => current === bulkDraft ? { ...current,
        selectedIds: selectEligible ? eligibleRows.map((row) => row.item_id) : ids,
        preflight: selectEligible ? selectedPreflight : result,
        screening: selectEligible ? result : current.screening } : current)
      if (selectEligible) {
        if (eligibleRows.length) message.info(`预检通过 ${eligibleRows.length} 题，已选中；核对后可直接确认`)
        else message.warning('当前没有通过预检的题，请查看各题阻断原因')
      }
    } catch (error) { message.error(error instanceof Error ? error.message : '批量预检失败') }
    finally { setBulkBusy(false) }
  }

  async function saveBulkReview(decision: 'approved' | 'rejected') {
    if (!selectedBatchId || !bulkDraft || !bulkDraft.selectedIds.length) return
    if (decision === 'approved' && (bulkDraft.preflight?.summary.selected !== bulkDraft.selectedIds.length
        || bulkDraft.preflight.summary.blocked > 0)) { message.warning('请先通过所选题目的整批预检'); return }
    if (decision === 'rejected' && !bulkDraft.reason.trim()) { message.warning('请填写批量拒绝理由'); return }
    setBulkBusy(true)
    try {
      await apiPost(`/question-imports/${selectedBatchId}/s5/items/bulk-decision`, {
        decision, ...(decision === 'approved' ? { route: bulkDraft.route } : {}),
        reason: bulkDraft.reason.trim() || null,
        items: bulkDraft.selectedIds.map((id) => {
          const item = candidates.find((row) => row.id === id)!
          return { item_id: id, expected_revision: item.revision,
            ...(decision === 'approved' && bulkDraft.route === 'direct_pool' ? {
              answer_review_items: answerUnitsPayload(bulkDraft.units[id] || [defaultAnswerUnit(item)]),
            } : {}) }
        }),
      })
      const count = bulkDraft.selectedIds.length
      setBulkDraft(null)
      await viewCandidates(selectedBatchId)
      message.success(`已整批${decision === 'approved' ? '确认' : '拒绝'} ${count} 题`)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '批量审核失败')
      if (decision === 'approved') await previewBulkReview()
      await viewCandidates(selectedBatchId)
    } finally { setBulkBusy(false) }
  }

  async function saveS5IssueDecision() {
    if (!selectedBatchId || !issueDraft) return
    if (['ignored', 'deferred'].includes(issueDraft.decision) && !issueDraft.reason.trim()) {
      message.warning('忽略或延后需要填写理由'); return
    }
    setIssueBusy(true)
    try {
      await apiPost(`/question-imports/${selectedBatchId}/s5/issues/decision`, {
        issue_key: issueDraft.issue.key, signature: issueDraft.issue.signature,
        decision: issueDraft.decision, reason: issueDraft.reason.trim() || null,
      })
      setIssueDraft(null)
      await refreshS5(selectedBatchId)
      message.success('问题处理决定已保存')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '问题处理失败')
      await refreshS5(selectedBatchId).catch(() => undefined)
    } finally { setIssueBusy(false) }
  }

  async function claimS5Answer(issue: ReviewIssue) {
    if (!selectedBatchId) return
    const target = candidates.find((item) => item.id === answerClaimTargets[issue.key])
    if (!target) { message.warning('请先选择要关联的候选题'); return }
    setAnswerSourceBusy(issue.key)
    try {
      await apiPost(`/question-imports/${selectedBatchId}/s5/issues/claim-answer`, {
        issue_key: issue.key, signature: issue.signature, item_id: target.id,
        expected_revision: target.revision,
      })
      await viewCandidates(selectedBatchId)
      message.success('来源答案已关联；答案内容仍需审核')
    } catch (error) { message.error(error instanceof Error ? error.message : '答案认领失败') }
    finally { setAnswerSourceBusy(null) }
  }

  async function confirmS5AnswerSource() {
    if (!selectedBatchId || !answerSourceDraft) return
    setAnswerSourceBusy(answerSourceDraft.itemId)
    try {
      await apiPost(`/question-imports/${selectedBatchId}/s5/items/${answerSourceDraft.itemId}/confirm-answer-source`, {
        expected_revision: answerSourceDraft.revision, selected_answer_id: answerSourceDraft.answerId,
        reason: answerSourceDraft.reason.trim() || null,
      })
      setAnswerSourceDraft(null)
      await viewCandidates(selectedBatchId)
      message.success('已确认答案归属；答案内容仍需单独审核')
    } catch (error) { message.error(error instanceof Error ? error.message : '答案归属确认失败') }
    finally { setAnswerSourceBusy(null) }
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
      setCatalogReviewResult(await apiGet<CatalogReviewResult>(`/question-imports/${batch.id}/s4/review`))
      await refreshS5(batch.id)
      message.success(linked ? '题图已关联' : '题图已删除')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '题图关联失败')
      setCandidates(await apiGet<Candidate[]>(`/question-imports/${batch.id}/items`).catch(() => candidates))
      await refreshS5(batch.id).catch(() => undefined)
    } finally { setImageBusy(false) }
  }

  function confirmDiscardUnmatchedImages(batch: Batch, count: number) {
    Modal.confirm({
      title: '完成题图核对并删除未匹配图片？',
      content: `将删除当前没有关联到有效候选题的 ${count} 张临时图片（包括未展示的重复图片）。删除后不能再从本批图片列表关联这些图片；已关联图片会保留。`,
      okText: '删除未匹配图片', okButtonProps: { danger: true }, cancelText: '继续核对',
      onOk: async () => {
        setImageBusy(true)
        try {
          const result = await apiPost<{ deleted_count: number }>(`/question-imports/${batch.id}/s5/images/discard-unmatched`, {})
          await refreshImages(batch.id)
          await refreshS5(batch.id)
          message.success(`已删除 ${result.deleted_count} 张未匹配的临时图片`)
        } catch (error) { message.error(error instanceof Error ? error.message : '未匹配图片清理失败') }
        finally { setImageBusy(false) }
      },
    })
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
      setCatalogReviewResult(await apiGet<CatalogReviewResult>(`/question-imports/${batch.id}/s4/review`))
      await refreshS5(batch.id)
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
      content: `将独立答案文件按页段发送给百炼 ${s2Config?.model || ''}，只提取原文标准答案；完成后先按规则关联，再用已提取文字自动辅助核对有歧义的对应。答案不会被标记为教师已确认。请确认文件不含学生个人信息。预算阈值 ¥${budget}；单次调用可能使实际费用超过阈值。`,
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

  function confirmAnswerMatching(batch: Batch) {
    if (!budget || !operatorToken) { message.warning('请先填写预算阈值和 S2 操作口令'); return }
    Modal.confirm({
      title: '启动模型辅助配对答案？',
      content: `将已提取的题目、答案文字、题号和来源位置分组发送给百炼 ${s2Config?.model || ''}。不会重新发送原页；模型无法确定的继续留给教师核对。此次配对任务的预算阈值为 ¥${budget}，此前已产生的配对费用计入该阈值；单次调用可能使实际费用略高。`,
      okText: '开始配对', cancelText: '取消',
      onOk: async () => {
        try {
          await apiPost(`/question-imports/${batch.id}/s3/start-answer-matching`,
            { budget_cny: budget }, undefined, { 'X-Import-S2-Token': operatorToken })
          setLabelEdits({})
          await reload(batch.id)
          message.success('模型辅助配对已排队')
        } catch (error) { message.error(error instanceof Error ? error.message : '启动模型辅助配对失败') }
      },
    })
  }

  function confirmAnswerGeneration(batch: Batch, count: number, itemIds?: string[]) {
    if (!budget || !operatorToken) { message.warning('请先填写预算阈值和 S2 操作口令'); return }
    Modal.confirm({
      title: `为 ${count} 道无答案题生成参考答案？`,
      content: `仅处理${itemIds ? '所选' : '本批'}尚无答案的候选题；已有教师答案不会被覆盖。将题干、选项和已关联题图分组发送给百炼 ${s2Config?.model || ''}。模型无法确定时保留无答案，所有生成结果均须教师核对，不会再次调用模型预审，也不会自动入库或入池。预算阈值 ¥${budget}，此前答案生成费用计入该阈值；单次调用可能超过阈值。`,
      okText: '开始生成答案', cancelText: '取消',
      onOk: async () => {
        try {
          await apiPost(`/question-imports/${batch.id}/s3/start-answer-generation`,
            { budget_cny: budget, ...(itemIds ? { item_ids: itemIds } : {}) }, undefined, { 'X-Import-S2-Token': operatorToken })
          message.success('答案生成已排队')
          await reload(batch.id)
        } catch (error) { message.error(error instanceof Error ? error.message : '启动答案生成失败') }
      },
    })
  }

  function confirmLabels(batch: Batch, rerun = false) {
    if (!budget || !operatorToken) { message.warning('请先填写预算阈值和 S2 操作口令'); return }
    const replan = batch.checkpoint?.s3?.labels?.error_code === 'S3_LABEL_REPLAN_REQUIRED'
    Modal.confirm({
      title: rerun ? '全部重新打标签？' : replan ? '按新规则重新处理整批标签？' : '生成本批候选题的标签建议？',
      content: `${rerun ? '将整批候选题按每组最多 12 题重新发送给模型；新结果会直接覆盖已有题型、难度和主属知识点，必做标记保留。此前已通过的题目将回到待审核；已拒绝或跳过的决定保留。原调用费用仍计入累计预算，本次会产生新费用。' : replan ? '旧分组已停用。本次会按每组最多 12 题重新处理整批候选题；此前已成功的题目也会再次发送并产生费用，历史费用仍计入累计预算。' : '仅处理已去重且已完成资料答案关联的候选题。'}将题干、选项和已关联题图分组发送给百炼 ${s2Config?.model || ''}；模型建议题型、难度和主属 KP，教师仍须核对。预算阈值 ¥${budget}；单次调用可能超过阈值。`,
      okText: rerun ? '全部重新打标签' : '开始生成标签', cancelText: '取消',
      onOk: async () => {
        try {
          await apiPost(`/question-imports/${batch.id}/s3/${rerun ? 'rerun-labels' : 'start-labels'}`,
            { budget_cny: budget }, undefined, { 'X-Import-S2-Token': operatorToken })
          setLabelEdits({})
          setLabelScopes({})
          message.success('标签建议已排队')
          await reload(batch.id)
        } catch (error) { message.error(error instanceof Error ? error.message : '启动标签建议失败') }
      },
    })
  }

  async function saveLabels(item: Candidate) {
    if (!selectedBatch || !canEditCandidates) return
    const changes = labelEdits[item.id]
    if (!changes || !Object.keys(changes).length) return
    const scope = labelScopes[item.id]
    if (scope && !labelCatalog.some((point) => point.code === changes.main_kp_code
      && point.grade === scope.grade && point.chapter_code === scope.chapter_code)) {
      message.warning('请依次选择年级、章和知识点后再保存')
      return
    }
    setLabelSaving(item.id)
    try {
      await apiPatch(`/question-imports/${selectedBatch.id}/items/${item.id}`,
        { expected_revision: item.revision, changes: { tags: changes } })
      setLabelEdits((current) => { const copy = { ...current }; delete copy[item.id]; return copy })
      setLabelScopes((current) => { const copy = { ...current }; delete copy[item.id]; return copy })
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
  const candidateSource = showDuplicateCandidates ? candidates : candidates.filter((item) => !duplicateCandidates.includes(item))
  const visibleCandidates = candidateSource.filter((item) => {
    const review = s5Reviews?.items.find((row) => row.item_id === item.id)
    const catalog = catalogReviewResult?.items.find((row) => row.item_id === item.id)
    const status = review?.valid ? review.decision : item.payload.teacher_decision === 'skipped_duplicate' ? 'skipped_duplicate' : 'pending'
    const hasFigureIssue = !(item.payload.content.asset_ids || []).length
      && !item.payload.figure_text_confirmation
      && !!(item.payload.extraction?.diagram_clues?.length || ['complete', 'uncertain'].includes(item.payload.extraction?.figure_status || ''))
    const match = candidateFilter === 'all' || candidateFilter === status
      || (candidateFilter === 'exact' && !!catalog?.matches.some((row) => row.kind === 'exact'))
      || (candidateFilter === 'nearby' && !!catalog?.matches.some((row) => row.kind === 'nearby'))
      || (candidateFilter === 'stale' && !!catalog && (catalog.candidate_stale || catalog.catalog_stale))
      || (candidateFilter === 'missing_tag' && !hasImportLabels(item))
      || (candidateFilter === 'missing_answer' && !item.payload.answers?.length)
      || (candidateFilter === 'figure' && hasFigureIssue)
      || (candidateFilter === 'issue' && reviewIssues.some((issue) => issue.candidate_item_ids.includes(item.id)
        && !(issue.handling?.valid && ['acknowledged', 'ignored'].includes(issue.handling.decision))))
    const query = candidateQuery.trim().toLowerCase()
    return match && (!query || String(item.ordinal).includes(query)
      || (item.payload.source.original_number || '').toLowerCase().includes(query)
      || item.payload.content.question_text.toLowerCase().includes(query))
  })
  const separateAnswers = selectedBatch?.checkpoint?.s3?.separate_answers
  const sourceAnswers = (selectedBatch?.source_mode === 'separate'
    ? separateAnswers?.units?.flatMap((unit) => unit.source_answers || [])
    : s2?.units?.flatMap((unit) => unit.source_answers || [])) || []
  const pendingAnswers = reconciliation
    ? reviewIssues.filter((issue) => issue.kind === 'answer' && issue.source_answer).map((issue) => issue.source_answer!)
    : sourceAnswers
  const linkedSourceQuestionCount = candidates.filter((item) => item.payload.answers?.some((answer) => answer.answer_source === 'source_extracted')).length
  const teacherReviewIssues = reviewIssues
  const reviewCategories = [
    { kind: 'answer', title: '答案归属待核对' },
    { kind: 'answer_match_disagreement', title: '模型建议更换已关联题目' },
    { kind: 'answer_match_warning', title: '已关联答案模型无法确定' },
    { kind: 'skipped_page', title: '人工跳过页' },
    { kind: 'fragment', title: '提取时未确定的片段' },
    ...(selectedBatch?.source_mode === 'separate' ? [{ kind: 'answer_fragment', title: '答案文件未确定片段' }] : []),
    { kind: 'overlap', title: '疑似重复题' },
  ]
  const dedupeStatus = selectedBatch?.checkpoint?.s3?.global_dedupe?.status
  const answerModelTask = selectedBatch?.checkpoint?.s3?.answer_model_matching
  const answerModelExpected = !!answerModelTask || !!(selectedBatch?.source_mode === 'combined'
    ? s2?.auto_answer_matching : separateAnswers?.auto_answer_matching)
  const labelTaskStatus = selectedBatch?.checkpoint?.s3?.labels?.status
  const generationTask = selectedBatch?.checkpoint?.s3?.answer_generation
  const canEditCandidates = selectedBatch?.status === 'review_pending' && selectedBatch.stage === 's2_completed'
    && dedupeStatus === 'completed' && (selectedBatch.source_mode !== 'separate' || separateAnswers?.status === 'completed')
    && !['queued', 'processing'].includes(labelTaskStatus || '')
    && !['queued', 'processing'].includes(answerModelTask?.status || '')
    && !['queued', 'processing'].includes(generationTask?.status || '')
  const unansweredCandidates = candidates.filter((item) => !item.is_fake && !item.payload.answers?.length
    && (!item.payload.teacher_decision || item.payload.teacher_decision === 'pending'))
  const answerMatchingComplete = !!reconciliation && selectedBatch?.stage === 's2_completed'
    && (!dedupeStatus || dedupeStatus === 'completed')
    && (selectedBatch?.source_mode !== 'separate' || separateAnswers?.status === 'completed')
    && (!answerModelExpected || answerModelTask?.status === 'completed')
  const matchedImageIds = new Set(candidates.flatMap((candidate) => candidate.payload.content.asset_ids || []))
  const unmatchedImageCount = (images?.assets || []).filter((asset) => !matchedImageIds.has(asset.asset_id)).length
  const unmatchedImages = (images?.assets || []).filter((asset) => !matchedImageIds.has(asset.asset_id) && !asset.duplicate_of_kept_image)
  const imageTargetOptions = candidates.filter((candidate) => !candidate.is_fake).map((candidate) => ({
    value: candidate.id,
    label: `第 ${candidate.ordinal} 题${candidate.payload.source.original_number ? `（原题号 ${candidate.payload.source.original_number}）` : ''}${candidate.payload.source.page_start ? ` · 第 ${candidate.payload.source.page_start} 页` : ''} · ${candidate.payload.content.question_text.replace(/\s+/g, ' ').slice(0, 48)}`,
  }))

  function renderCatalogReviewContent(item: Candidate) {
    const catalogRow = catalogReviewResult?.items.find((row) => row.item_id === item.id)
    if (!catalogRow) return <Typography.Text type="secondary">未检查</Typography.Text>
    const catalogStale = catalogRow.candidate_stale || catalogRow.catalog_stale
    return <div className="question-import-catalog-result">
      <div style={{ width: '100%' }}>
        {catalogStale && <Typography.Text type="warning">候选题或正式题内容已变化，查重决定暂不可用；请重新检查题库重复。</Typography.Text>}
        <div><Tag color={catalogRow.matches.some((match) => match.kind === 'exact') ? 'warning' : 'default'}>
          相同题 {catalogRow.matches.filter((match) => match.kind === 'exact').length} 道
        </Tag><Typography.Text type="secondary">可能相似的题 {catalogRow.matches.filter((match) => match.kind === 'nearby').length} 道</Typography.Text></div>
        {!catalogStale && catalogRow.matches.length === 0 && catalogRow.near_review_min_similarity != null &&
          <Typography.Text type="secondary">这次没有找出需要核对的题，可以按新题继续审核。如果题目主要靠图片表达，或题干文字很少，请人工再核对。</Typography.Text>}
        {catalogRow.matches.length > 0 && <details>
          <summary>查看相同或相似的题并处理</summary>
          {catalogRow.matches.map((match) => <Card key={match.id} size="small" style={{ marginTop: 8 }}>
            <Space wrap><Typography.Text strong>题库编号 {match.original_qid}</Typography.Text>
              {match.question_id && <a href={`/questions/${match.question_id}`} target="_blank" rel="noopener noreferrer">打开正式题详情核对</a>}
              <Tag color={match.kind === 'exact' ? 'warning' : 'blue'}>{match.kind === 'exact' ? '相同题' : '可能相似'}</Tag>
              <Tag color={match.decision === 'pending' ? 'default' : 'green'}>{match.decision === 'skip_existing' ? '题库已有此题，不再入库' : match.decision === 'keep_variant' ? '已确认独立变式' : match.decision === 'unrelated' ? '已判为无关题' : '待决定'}</Tag>
              {(match.catalog_stale || match.catalog_unverifiable) && <Tag color="warning">{match.catalog_stale ? '正式题内容已变化' : '正式题内容无法自动核验，请人工核对'}</Tag>}
            </Space>
            {match.question_text && <div><Typography.Text>题干：{match.question_text.replace(/\[ASSET:[^\]]+\]/g, '[题图]').slice(0, 300)}</Typography.Text></div>}
            {catalogOptionRows(match.options).length > 0 && <div><Typography.Text type="secondary">选项：{catalogOptionRows(match.options).map((option) => `${option.key}. ${option.text}`).join('；').slice(0, 300)}</Typography.Text></div>}
            {!!match.reference_answer_raw && <div><Typography.Text type="secondary">题库答案：{match.reference_answer_raw.slice(0, 120)}</Typography.Text></div>}
            {!!match.evidence.answer_text_differs && <Typography.Text type="warning">候选答案与题库答案文本不同，请核对。</Typography.Text>}
            {!!match.evidence.differences?.length && <div><Typography.Text type="warning">差异：{match.evidence.differences.join('；')}</Typography.Text></div>}
            {match.kind === 'nearby' && <div><Typography.Text type="secondary">题图：{match.evidence.image_relation || '未比较'}；文字相近度 {Math.round((match.similarity || 0) * 100)}%（用于筛选和排序，不是重复概率）</Typography.Text></div>}
            {match.reason && <div><Typography.Text type="secondary">判断理由：{match.reason}</Typography.Text></div>}
            {canEditCandidates && !catalogStale && <Space wrap style={{ marginTop: 8 }}>
              {match.decision !== 'skip_existing' && <Button size="small" onClick={() => openCatalogDecision(item, match, 'skip_existing')}>题库已有此题，不再入库</Button>}
              {match.kind === 'nearby' && match.decision !== 'keep_variant' && <Button size="small" onClick={() => openCatalogDecision(item, match, 'keep_variant')}>确认独立变式</Button>}
              {match.kind === 'nearby' && match.decision !== 'unrelated' && <Button size="small" onClick={() => openCatalogDecision(item, match, 'unrelated')}>与此题无关</Button>}
              {match.decision !== 'pending' && <Button size="small" onClick={() => openCatalogDecision(item, match, 'pending')}>撤销决定</Button>}
            </Space>}
          </Card>)}
        </details>}
      </div>
    </div>
  }

  function renderCandidateContent(item: Candidate) {
    const assetIds = item.payload.content.asset_ids || []
    const tags = item.payload.tags || {}
    const suggestion = item.payload.label_suggestion
    const edit = labelEdits[item.id] || {}
    const savedKp = labelCatalog.find((point) => point.code === tags.main_kp_code)
    const scope = labelScopes[item.id]
    const selectedGrade = scope ? scope.grade : savedKp?.grade || tags.grade || null
    const selectedChapterCode = scope ? scope.chapter_code : savedKp?.chapter_code || tags.chapter_code || null
    const gradeOptions = [...new Set(labelCatalog.map((point) => point.grade))].map((grade) => ({ value: grade, label: grade }))
    const chapterOptions = [...new Map(labelCatalog.filter((point) => point.grade === selectedGrade)
      .map((point) => [point.chapter_code, { value: point.chapter_code, label: point.chapter_name }])).values()]
    const knowledgePointOptions = labelCatalog.filter((point) => point.grade === selectedGrade && point.chapter_code === selectedChapterCode)
      .map((point) => ({ value: point.code, label: point.name }))
    const editedKpCode = edit.main_kp_code === undefined ? tags.main_kp_code : edit.main_kp_code
    const visibleKpCode = knowledgePointOptions.some((point) => point.value === editedKpCode) ? editedKpCode : undefined
    const visibleKp = labelCatalog.find((point) => point.code === visibleKpCode)
    const labelTask = selectedBatch?.checkpoint?.s3?.labels
    const skippedByTask = labelTask?.skipped_item_ids?.includes(item.id) || labelTask?.groups?.some((group) => group.skipped_item_ids?.includes(item.id))
    const clues = item.payload.extraction?.diagram_clues || []
    const figureMissing = !assetIds.length && (clues.length > 0 || ['complete', 'uncertain'].includes(item.payload.extraction?.figure_status || ''))
    const figureTextConfirmed = figureMissing && !!item.payload.figure_text_confirmation
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
      {figureMissing && !figureTextConfirmed && <Typography.Text type="warning">题图尚未裁剪；请对照原页复核</Typography.Text>}
      {figureMissing && renderFigureTextAction(item)}
      {removedCandidateImages.length > 0 && <Typography.Text type="warning">去重题目有 {removedCandidateImages.length} 张未自动转移的图片，请在上方“无法匹配到题目的图片”中核对是否缺图。</Typography.Text>}
      {canEditCandidates && <label>添加图片：<input type="file" accept="image/jpeg,image/png,image/webp" disabled={imageBusy || !images?.files_available} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file && selectedBatch) void uploadCandidateImage(selectedBatch, item, file) }} /></label>}
      {item.payload.extraction?.uncertain_fragments?.map((fragment, index) => <Typography.Text type="warning" key={`uncertain-${index}`}>模型不确定：{fragment}</Typography.Text>)}
      {item.payload.answers?.map((answer) => <div key={answer.answer_id} className={`question-import-answer${answer.answer_source === 'model_generated' && answer.answer_id === item.payload.selected_answer_id && !answer.teacher_confirmed ? ' question-import-answer--model-pending' : ''}`}>
        <Typography.Text>{answer.answer_source === 'teacher_entered' ? '教师答案' : answer.answer_source === 'source_extracted' ? '资料答案' : answer.answer_source === 'model_generated' ? '模型生成答案' : '答案'}{answer.answer_id === item.payload.selected_answer_id ? answer.teacher_confirmed ? '（归属已核对，内容仍须审核）' : '（当前选择，待教师确认）' : ''}：</Typography.Text>
        {answer.text ? <RichQuestionText text={answer.text} assets={[]} /> : <Typography.Text type="secondary">未提供</Typography.Text>}
        {canEditCandidates && !item.is_fake && answer.answer_id !== item.payload.selected_answer_id && !!answer.text?.trim() &&
          <Button size="small" disabled={reviewBusy} onClick={() => void selectS5Answer(item, answer.answer_id)}>选为本题答案</Button>}
        {canEditCandidates && !item.is_fake && answer.answer_id === item.payload.selected_answer_id && !answer.teacher_confirmed &&
          <Button size="small" loading={answerSourceBusy === item.id} onClick={() => setAnswerSourceDraft({ itemId: item.id, revision: item.revision, answerId: answer.answer_id, reason: '' })}>确认答案归属</Button>}
      </div>)}
      {item.payload.warnings?.includes('题目已修改，请重新核对答案是否仍适用') && <Typography.Text type="warning">题目已修改，请重新核对答案是否仍适用。</Typography.Text>}
      {canEditCandidates && candidateEditor?.itemId !== item.id && <Space>
        <Button size="small" onClick={() => startEditingCandidate(item)}>编辑题目和答案</Button>
        {selectedBatch && selectedBatch.source_mode === 'questions_only' && !item.payload.answers?.length && !item.is_fake
          && (!item.payload.teacher_decision || item.payload.teacher_decision === 'pending')
          && <Button size="small" disabled={!s2Config?.ready || !!selectedBatch.files_purged_at || !!generationTask?.cost_unknown
            || (['failed', 'paused'].includes(generationTask?.status || '') && !!generationTask?.groups?.some((group) => group.status === 'pending'))}
            onClick={() => confirmAnswerGeneration(selectedBatch, 1, [item.id])}>生成本题答案</Button>}
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
        {suggestion && (suggestion.uncertainty || suggestion.alternative_kp_codes.length > 0) && <Typography.Paragraph type="secondary">{suggestion.uncertainty ? `不确定：${suggestion.uncertainty}` : ''}{suggestion.alternative_kp_codes.length ? `${suggestion.uncertainty ? '；' : ''}备选知识点：${suggestion.alternative_kp_codes.map((code) => labelCatalog.find((point) => point.code === code)?.name || '知识点名称待加载').join('、')}` : ''}</Typography.Paragraph>}
        {!suggestion && <Typography.Paragraph type="warning">{skippedByTask ? '本题在排队后发生变化，未发送给模型；请在任务完成后人工填写，或全部重新打标签。' : ['queued', 'processing'].includes(labelTask?.status || '') ? '标签任务尚未处理本题；请等待任务完成。' : '本题没有可用的模型建议，请人工填写或全部重新打标签。'}</Typography.Paragraph>}
        <Space wrap>
          <label>年级：<Select aria-label={`第 ${item.ordinal} 题年级`} disabled={!canEditCandidates} allowClear showSearch optionFilterProp="label" placeholder="选择年级" style={{ width: 140 }} value={selectedGrade}
            options={gradeOptions}
            onChange={(value: string | undefined) => {
              setLabelScopes((current) => ({ ...current, [item.id]: { grade: value ?? null, chapter_code: null } }))
              setLabelEdits((current) => ({ ...current, [item.id]: { ...current[item.id], main_kp_code: null } }))
            }} /></label>
          <label>章：<Select aria-label={`第 ${item.ordinal} 题章`} disabled={!canEditCandidates || !selectedGrade} allowClear showSearch optionFilterProp="label" placeholder={selectedGrade ? '选择章' : '先选年级'} style={{ width: 210 }} value={selectedChapterCode}
            options={chapterOptions}
            onChange={(value: string | undefined) => {
              setLabelScopes((current) => ({ ...current, [item.id]: { grade: selectedGrade, chapter_code: value ?? null } }))
              setLabelEdits((current) => ({ ...current, [item.id]: { ...current[item.id], main_kp_code: null } }))
            }} /></label>
          <label>主属知识点：<Select aria-label={`第 ${item.ordinal} 题主属知识点`} disabled={!canEditCandidates || !selectedChapterCode} allowClear showSearch optionFilterProp="label" placeholder={selectedChapterCode ? '选择知识点' : '先选章'} style={{ width: 300 }} value={visibleKpCode}
            options={knowledgePointOptions}
            onChange={(value: string | undefined) => setLabelEdits((current) => ({ ...current, [item.id]: { ...current[item.id], main_kp_code: value ?? null } }))} /></label>
          <label>题型：<Select aria-label={`第 ${item.ordinal} 题题型`} disabled={!canEditCandidates} style={{ width: 120 }} value={edit.question_type ?? tags.question_type ?? 'unknown'}
            options={Object.entries(questionTypeLabels).map(([value, label]) => ({ value, label }))}
            onChange={(value: string) => setLabelEdits((current) => ({ ...current, [item.id]: { ...current[item.id], question_type: value } }))} /></label>
          <label>难度：<Select aria-label={`第 ${item.ordinal} 题难度`} disabled={!canEditCandidates} allowClear placeholder="待确认" style={{ width: 105 }} value={edit.difficulty === undefined ? tags.difficulty : edit.difficulty}
            options={[1, 2, 3, 4, 5].map((value) => ({ value, label: `${value} 星` }))}
            onChange={(value: number | undefined) => setLabelEdits((current) => ({ ...current, [item.id]: { ...current[item.id], difficulty: value ?? null } }))} /></label>
          <label>必做：<Select aria-label={`第 ${item.ordinal} 题必做`} disabled={!canEditCandidates} style={{ width: 90 }} value={edit.is_required ?? tags.is_required ?? false}
            options={[{ value: false, label: '否' }, { value: true, label: '是' }]}
            onChange={(value: boolean) => setLabelEdits((current) => ({ ...current, [item.id]: { ...current[item.id], is_required: value } }))} /></label>
          <Button size="small" disabled={!canEditCandidates || !Object.keys(edit).length || (!!scope && !visibleKpCode)} loading={labelSaving === item.id} onClick={() => void saveLabels(item)}>保存调整</Button>
        </Space>
        <div><Typography.Text type="secondary">派生标签：节：{visibleKp?.section || (edit.main_kp_code === undefined ? tags.section : null) || '待定'}；核心素养：{(visibleKp?.core_literacies || (edit.main_kp_code === undefined ? tags.core_literacies : null) || []).join('、') || '待定'}。标签状态：{item.payload.label_status === 'out_of_scope' ? '超出当前知识点范围或无法判断' : '待核对'}</Typography.Text></div>
      </Card>}
      {answerMatchingComplete && !item.payload.answers?.some((answer) => !!answer.text?.trim()) && <Typography.Text type="warning">无答案</Typography.Text>}
      {pendingAnswers.filter((answer) => answer.candidate_item_ids?.length === 1 && answer.candidate_item_ids[0] === item.id).map((answer, index) => <div key={`pending-${index}`} className="question-import-answer"><Typography.Text type="warning">{selectedBatch?.source_mode === 'separate' ? '答案文件' : '原文件'}第 {answer.page_start} 页资料答案（归属待确认）：</Typography.Text><RichQuestionText text={answer.text} assets={[]} /></div>)}
      {item.payload.warnings?.filter((warning) => !['资料答案已关联，须对照原页由教师确认', '题图尚未裁剪；请对照原页复核', '题图已自动关联，须核对原页和图片完整性', '题图已关联，须对照原页核对图片内容和完整性'].includes(warning) && (warning !== '模型报告不确定片段；请人工复核' || !item.payload.extraction?.uncertain_fragments?.length)).map((warning, index) => <Typography.Text type="warning" key={`warning-${index}`}>{warning}</Typography.Text>)}
    </Space>
  }

  const activeReviewItem = candidates.find((item) => item.id === reviewDraft?.itemId)
  const activeReviewCatalog = catalogReviewResult?.items.find((row) => row.item_id === reviewDraft?.itemId)
  const effectiveReview = activeReviewItem && s5Reviews?.items.find((row) => row.item_id === activeReviewItem.id)
  const activeSelectedAnswer = activeReviewItem?.payload.answers?.find((answer) => answer.answer_id === activeReviewItem.payload.selected_answer_id)

  return <Space direction="vertical" size="large" style={{ width: '100%' }}>
    <Modal open={!!catalogDecisionDraft} title="保存题库查重决定"
      okText="保存决定" okButtonProps={{ loading: catalogDecisionBusy }}
      onCancel={() => { if (!catalogDecisionBusy) setCatalogDecisionDraft(null) }}
      onOk={() => void saveCatalogDecision()}>
      {catalogDecisionDraft && <>
        <Typography.Paragraph>候选第 {catalogDecisionDraft.item.ordinal} 题与题库编号 {catalogDecisionDraft.match.original_qid}：
          {catalogDecisionDraft.decision === 'skip_existing' ? '题库已有此题，本批候选题不再作为新题入库；题库中的原题继续保留。' : catalogDecisionDraft.decision === 'keep_variant' ? '确认作为独立变式' : catalogDecisionDraft.decision === 'unrelated' ? '判为无关题' : '撤销原决定'}</Typography.Paragraph>
        {catalogDecisionDraft.decision !== 'pending' && <Input.TextArea value={catalogDecisionReason} maxLength={500}
          rows={3} placeholder="判断理由（可选）"
          onChange={(event) => setCatalogDecisionReason(event.target.value)} />}
      </>}
    </Modal>
    <Modal title="S5 批量审核" open={!!bulkDraft} width={1100}
      onCancel={() => { if (!bulkBusy) setBulkDraft(null) }}
      footer={<Space wrap>
        <Button disabled={bulkBusy} onClick={() => setBulkDraft(null)}>关闭</Button>
        <Button loading={bulkBusy} onClick={() => void previewBulkReview()}>预检所选题目</Button>
        <Button type="primary" loading={bulkBusy}
          disabled={!bulkDraft?.selectedIds.length || bulkDraft.preflight?.summary.selected !== bulkDraft.selectedIds.length || !!bulkDraft.preflight?.summary.blocked}
          onClick={() => void saveBulkReview('approved')}>整批确认审核意向</Button>
        <Button danger loading={bulkBusy} disabled={!bulkDraft?.selectedIds.length || !bulkDraft?.reason.trim()}
          onClick={() => void saveBulkReview('rejected')}>整批拒绝所选题目</Button>
      </Space>}>
      {bulkDraft && <Space direction="vertical" size="middle" style={{ width: '100%', maxHeight: '68vh', overflowY: 'auto' }}>
        <Alert showIcon type="info" message="这里保存教师审核意向；正式入库将在 S6 执行。任一所选题被阻断，本次整批操作都不会保存。" />
        <Space wrap>
          <Typography.Text strong>拟入库路径</Typography.Text>
          <Select aria-label="批量拟入库路径" style={{ width: 280 }} value={bulkDraft.route}
            options={[{ value: 'library_only', label: '仅题目入库' }, { value: 'direct_pool', label: '题目与答案直接入池' }]}
            onChange={(route: S5BulkDraft['route']) => setBulkDraft((current) => current ? { ...current, route, preflight: null, screening: null } : current)} />
          <Button onClick={() => setBulkDraft((current) => current ? { ...current,
            selectedIds: bulkPendingItems().slice(0, 200).map((item) => item.id), preflight: null, screening: null } : current)}>选择全部待审题</Button>
          <Button loading={bulkBusy} onClick={() => void previewBulkReview(true)}>只选当前预检通过的题</Button>
          <Typography.Text type="secondary">自动筛选已完成预检；手动选题或修改答案后需重新预检。每次最多选择 200 题</Typography.Text>
        </Space>
        <div style={{ maxHeight: 190, overflowY: 'auto', border: '1px solid #eee', padding: 8 }}>
          {bulkPendingItems().map((item) => <div key={item.id} style={{ marginBottom: 5 }}>
            <Checkbox checked={bulkDraft.selectedIds.includes(item.id)} disabled={bulkBusy || (!bulkDraft.selectedIds.includes(item.id) && bulkDraft.selectedIds.length >= 200)}
              onChange={(event) => setBulkDraft((current) => current ? { ...current,
                selectedIds: event.target.checked ? [...current.selectedIds, item.id] : current.selectedIds.filter((id) => id !== item.id),
                preflight: null } : current)}>
              第 {item.ordinal} 题 · {item.payload.content.question_text.slice(0, 100)}
            </Checkbox>
            {bulkDraft.screening?.items.find((row) => row.item_id === item.id && !row.batch_eligible)?.blockers.map((problem, index) =>
              <Tag key={`${item.id}-${index}`} color="orange">待处理：{problem.message}</Tag>)}
            {renderFigureTextAction(item)}
          </div>)}
          {!bulkPendingItems().length && <Empty description="没有可批量审核的待审题" />}
        </div>
        <Typography.Text strong>所选 {bulkDraft.selectedIds.length} 题的审核预览；未选 {Math.max(0, bulkPendingItems().length - bulkDraft.selectedIds.length)} 题{bulkDraft.screening ? `（其中预检阻断 ${bulkDraft.screening.summary.blocked} 题）` : ''}</Typography.Text>
        {bulkDraft.selectedIds.map((id) => {
          const item = candidates.find((row) => row.id === id)
          if (!item) return null
          const selectedAnswer = item.payload.answers?.find((answer) => answer.answer_id === item.payload.selected_answer_id)
          const linkedAnswer = item.payload.answers?.length === 1 ? item.payload.answers[0] : null
          const catalog = catalogReviewResult?.items.find((row) => row.item_id === id)
          const check = bulkDraft.preflight?.items.find((row) => row.item_id === id)
          const units = bulkDraft.units[id] || [defaultAnswerUnit(item)]
          return <Card key={id} size="small" title={`第 ${item.ordinal} 题 · 版本 ${item.revision}`}
            extra={check && <Tag color={check.batch_eligible ? 'green' : 'red'}>{check.batch_eligible ? '预检通过' : '有阻断项'}</Tag>}>
            <Typography.Paragraph type="secondary">原题号 {item.payload.source.original_number || '未识别'} · 第 {item.payload.source.page_start ?? '?'} 页 · 题型 {questionTypeLabels[item.payload.tags?.question_type || 'unknown']} · 难度 {item.payload.tags?.difficulty ?? '待确认'} · 知识点 {labelCatalog.find((point) => point.code === item.payload.tags?.main_kp_code)?.name || '待确认'}</Typography.Paragraph>
            <RichQuestionText text={item.payload.content.question_text} assets={[]} />
            {item.payload.content.options.map((option) => <RichQuestionText key={option.key} text={`${option.key}. ${option.text}`} assets={[]} />)}
            {item.payload.content.asset_ids?.map((assetId) => selectedBatch && images?.files_available && <img key={assetId} alt={`第 ${item.ordinal} 题题图`} src={apiUrl(`/question-imports/${selectedBatch.id}/s3/images/${assetId}/content`)} className="question-import-review-image" />)}
            {renderFigureTextAction(item)}
            <Typography.Paragraph>{selectedAnswer ? '当前答案' : linkedAnswer ? '已关联但未选定的答案' : '来源答案'}：{selectedAnswer?.text || linkedAnswer?.text || '无'}</Typography.Paragraph>
            <Typography.Paragraph type="secondary">查重：{!catalog ? '尚未检查' : catalog.candidate_stale || catalog.catalog_stale ? '检查已过期' : catalog.matches.length ? catalog.matches.map((match) => `${match.kind === 'exact' ? '相同' : '相似'} ${match.original_qid}（${match.decision === 'pending' ? '待决定' : match.decision}）`).join('；') : '未发现需人工决定的匹配'}</Typography.Paragraph>
            {bulkDraft.route === 'direct_pool' && <Card size="small" title="逐题答案审核草稿">
              <Typography.Paragraph type="secondary">单道选择题无需填写编号。多个小题或空须逐项填写小题号、空号并核对答案；未填好不能拟直接入池，请改选“仅题目入库”，后续补齐答案再审核入池。</Typography.Paragraph>
              {item.payload.tags?.question_type === 'choice' && <Typography.Paragraph type="secondary">选择题按选项字母判分；本题有效选项：{choiceOptionKeys(item)}。</Typography.Paragraph>}
              {units.map((unit, index) => <Space key={unit.item_id} wrap style={{ marginBottom: 8 }}>
                <InputNumber aria-label={`第 ${item.ordinal} 题答案项 ${index + 1} 小题号`} min={1} max={99} placeholder="小题号" value={unit.subquestion_no} disabled={item.payload.tags?.question_type === 'choice' && units.length === 1 && unit.subquestion_no === null && unit.blank_no === null}
                  onChange={(value) => setBulkDraft((current) => current ? { ...current, preflight: null, screening: null, units: { ...current.units,
                    [id]: units.map((row) => row.item_id === unit.item_id ? { ...row, subquestion_no: value } : row) } } : current)} />
                <InputNumber aria-label={`第 ${item.ordinal} 题答案项 ${index + 1} 空号`} min={1} max={99} placeholder="空号" value={unit.blank_no} disabled={item.payload.tags?.question_type === 'choice' && units.length === 1 && unit.subquestion_no === null && unit.blank_no === null}
                  onChange={(value) => setBulkDraft((current) => current ? { ...current, preflight: null, screening: null, units: { ...current.units,
                    [id]: units.map((row) => row.item_id === unit.item_id ? { ...row, blank_no: value } : row) } } : current)} />
                <Input aria-label={`第 ${item.ordinal} 题答案项 ${index + 1}`} style={{ width: 220 }} placeholder="标准答案" value={unit.answer}
                  onChange={(event) => setBulkDraft((current) => current ? { ...current, preflight: null, screening: null, units: { ...current.units,
                    [id]: units.map((row) => row.item_id === unit.item_id ? { ...row, answer: event.target.value } : row) } } : current)} />
                <Select aria-label={`第 ${item.ordinal} 题判分方式`} style={{ width: 140 }} value={unit.answer_match_type}
                  options={answerMatchOptions(item.payload.tags?.question_type)}
                  onChange={(value: AnswerUnitDraft['answer_match_type']) => setBulkDraft((current) => current ? { ...current, preflight: null, screening: null, units: { ...current.units,
                    [id]: units.map((row) => row.item_id === unit.item_id ? { ...row, answer_match_type: value } : row) } } : current)} />
                <Input aria-label={`第 ${item.ordinal} 题可接受答案`} style={{ width: 220 }} placeholder="其他可接受答案，以换行或分号分隔" value={unit.accepted_answers}
                  onChange={(event) => setBulkDraft((current) => current ? { ...current, preflight: null, screening: null, units: { ...current.units,
                    [id]: units.map((row) => row.item_id === unit.item_id ? { ...row, accepted_answers: event.target.value } : row) } } : current)} />
                <Button size="small" danger disabled={units.length === 1} onClick={() => setBulkDraft((current) => current ? { ...current, preflight: null, screening: null,
                  units: { ...current.units, [id]: units.filter((row) => row.item_id !== unit.item_id) } } : current)}>删除答案项</Button>
              </Space>)}
              <Button size="small" disabled={units.length >= 50} onClick={() => setBulkDraft((current) => current ? { ...current, preflight: null, screening: null,
                units: { ...current.units, [id]: [...units, { ...defaultAnswerUnit(item), item_id: `answer-${crypto.randomUUID().slice(0, 8)}`, answer: '' }] } } : current)}>添加答案项</Button>
            </Card>}
            {check?.blockers.map((problem, index) => <Typography.Paragraph key={`b-${index}`} type="danger">阻断：{problem.message}</Typography.Paragraph>)}
            {check?.warnings.map((problem, index) => <Typography.Paragraph key={`w-${index}`} type="warning">提醒：{problem.message}</Typography.Paragraph>)}
          </Card>
        })}
        {bulkDraft.preflight && <Alert showIcon type={bulkDraft.preflight.summary.blocked ? 'warning' : 'success'}
          message={`预检 ${bulkDraft.preflight.summary.selected} 题：通过 ${bulkDraft.preflight.summary.eligible}，阻断 ${bulkDraft.preflight.summary.blocked}`} />}
        <label>批量拒绝理由（拒绝时必填）<Input.TextArea rows={2} maxLength={500} value={bulkDraft.reason}
          onChange={(event) => setBulkDraft((current) => current ? { ...current, reason: event.target.value } : current)} /></label>
      </Space>}
    </Modal>
    <Modal title="逐题审核与预检" open={!!reviewDraft} width={1050}
      onCancel={() => { if (!reviewBusy) setReviewDraft(null) }}
      footer={<Space wrap>
        <Button onClick={() => setReviewDraft(null)} disabled={reviewBusy}>关闭</Button>
        {effectiveReview?.valid && effectiveReview.decision !== 'pending' && <Button disabled={reviewBusy} onClick={() => void saveS5Review('pending')}>撤回审核</Button>}
        <Button danger disabled={reviewBusy || !reviewDraft?.reason.trim()} onClick={() => void saveS5Review('rejected')}>拒绝此题</Button>
        <Button loading={reviewBusy} onClick={() => void previewS5Review()}>检查所选路径</Button>
        <Button type="primary" disabled={!reviewDraft?.preflight?.batch_eligible || reviewBusy} onClick={() => void saveS5Review('approved')}>确认审核意向</Button>
      </Space>}>
      {reviewDraft && activeReviewItem && <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Alert type="info" showIcon message="确认只保存审核意向，尚不会把题目加入正式题库。" />
        {effectiveReview?.question_event_id && <Typography.Text type={effectiveReview.valid ? 'success' : 'warning'}>
          最近一次决定：{effectiveReview.recorded_decision === 'approved' ? '已确认' : effectiveReview.recorded_decision === 'rejected' ? '已拒绝' : '待审核'}；{effectiveReview.valid ? '当前有效' : '题目或证据变化，须重新核对'}
        </Typography.Text>}
        <div className="question-import-review-compare">
          <Card size="small" title={`本批候选第 ${activeReviewItem.ordinal} 题`}>
            <Typography.Paragraph type="secondary">原题号 {activeReviewItem.payload.source.original_number || '未识别'} · 来源第 {activeReviewItem.payload.source.page_start ?? '?'}–{activeReviewItem.payload.source.page_end ?? '?'} 页 · 版本 {activeReviewItem.revision}</Typography.Paragraph>
            <RichQuestionText text={activeReviewItem.payload.content.question_text} assets={[]} />
            {activeReviewItem.payload.content.options.map((option) => <RichQuestionText key={option.key} text={`${option.key}. ${option.text}`} assets={[]} />)}
            {activeReviewItem.payload.content.asset_ids?.map((assetId) => selectedBatch && images?.files_available && <img key={assetId} alt="候选题图" src={apiUrl(`/question-imports/${selectedBatch.id}/s3/images/${assetId}/content`)} className="question-import-review-image" />)}
            <Typography.Paragraph type="secondary">题型：{questionTypeLabels[activeReviewItem.payload.tags?.question_type || 'unknown']}；难度：{activeReviewItem.payload.tags?.difficulty ?? '待填'}；主属知识点：{labelCatalog.find((point) => point.code === activeReviewItem.payload.tags?.main_kp_code)?.name || '待填'}</Typography.Paragraph>
            <Typography.Text strong>{activeSelectedAnswer ? '当前答案：' : activeReviewItem.payload.answers?.length ? '已关联答案（尚未选定）：' : '当前答案：'}</Typography.Text>
            {activeSelectedAnswer && <RichQuestionText text={activeSelectedAnswer.text || '答案内容为空'} assets={[]} />}
            {!activeSelectedAnswer && !activeReviewItem.payload.answers?.length && <Typography.Text type="secondary">暂无答案</Typography.Text>}
            {!activeSelectedAnswer && activeReviewItem.payload.answers?.map((answer) => <Card key={answer.answer_id} size="small" style={{ marginTop: 8 }}>
              <RichQuestionText text={answer.text || '答案内容为空'} assets={[]} />
              {answer.evidence?.map((evidence, index) => <Typography.Paragraph key={`${evidence.file_id}-${evidence.page}-${index}`} type="secondary">来源第 {evidence.page} 页：{evidence.quote}</Typography.Paragraph>)}
              {!!answer.text?.trim() && <Button size="small" loading={reviewBusy} disabled={!canEditCandidates} onClick={() => void selectS5Answer(activeReviewItem, answer.answer_id)}>选为本题答案</Button>}
            </Card>)}
            {activeSelectedAnswer?.evidence?.map((evidence, index) => <Typography.Paragraph key={`${evidence.file_id}-${evidence.page}-${index}`} type="secondary">
              答案来源第 {evidence.page} 页摘录：{evidence.quote}
            </Typography.Paragraph>)}
            {activeReviewItem.payload.extraction?.uncertain_fragments?.map((fragment, index) => <Typography.Paragraph key={`uncertain-${index}`} type="warning">
              识别不确定片段：{fragment}
            </Typography.Paragraph>)}
          </Card>
          <Card size="small" title="题库查重证据">
            {!activeReviewCatalog && <Typography.Text type="warning">尚未检查题库重复。请先在候选页点击“检查题库重复”。</Typography.Text>}
            {activeReviewCatalog && <>
              {(activeReviewCatalog.candidate_stale || activeReviewCatalog.catalog_stale) && <Alert type="warning" showIcon message="查重记录已过期，请重新检查" />}
              {activeReviewCatalog.matches.length === 0 && <Typography.Text type="secondary">本次检查未发现需要人工决定的匹配。</Typography.Text>}
              {activeReviewCatalog.matches.map((match) => <Card key={match.id} size="small" style={{ marginBottom: 8 }}>
                <Space wrap><Tag color={match.kind === 'exact' ? 'orange' : 'blue'}>{match.kind === 'exact' ? '相同题' : '可能相似'}</Tag><Typography.Text strong>{match.original_qid}</Typography.Text><Typography.Text type="secondary">{match.decision === 'pending' ? '待决定' : match.decision === 'skip_existing' ? '题库已有，不再入库' : match.decision === 'keep_variant' ? '独立变式' : '无关'}</Typography.Text></Space>
                {match.question_text && <RichQuestionText text={match.question_text.replace(/\[ASSET:[^\]]+\]/g, '[题图]')} assets={[]} />}
                {catalogOptionRows(match.options).map((option) => <RichQuestionText key={option.key} text={`${option.key}. ${option.text}`} assets={[]} />)}
                {match.question_id && <a href={`/questions/${match.question_id}`} target="_blank" rel="noopener noreferrer">打开正式题详情核对</a>}
                {match.reason && <Typography.Paragraph type="secondary">判断理由：{match.reason}</Typography.Paragraph>}
              </Card>)}
            </>}
          </Card>
        </div>
        <Space wrap><Typography.Text strong>拟入库路径</Typography.Text><Select aria-label="拟入库路径" style={{ width: 300 }} value={reviewDraft.route}
          onChange={(route: 'library_only' | 'direct_pool') => setReviewDraft((current) => current ? { ...current, route, preflight: null } : current)}
          options={[{ value: 'library_only', label: '仅题目入库，答案后续审核' }, { value: 'direct_pool', label: '题目和答案拟直接入池' }]} /></Space>
        {reviewDraft.route === 'direct_pool' && <Card size="small" title="逐小题／空的答案审核">
          <Typography.Paragraph type="secondary">请核对每项答案。单道选择题无需填写编号；多个小题或空须逐项填写小题号、空号，未填好请改选“仅题目入库”，后续补齐答案再审核入池。只有点击“确认审核意向”才会保存教师决定。</Typography.Paragraph>
          {activeReviewItem.payload.tags?.question_type === 'choice' && <Typography.Paragraph type="secondary">选择题按选项字母判分；本题有效选项：{choiceOptionKeys(activeReviewItem)}。</Typography.Paragraph>}
          {reviewDraft.units.map((unit, index) => <Card key={unit.item_id} size="small" style={{ marginBottom: 8 }} title={`答案项 ${index + 1}`}
            extra={reviewDraft.units.length > 1 && <Button size="small" danger onClick={() => setReviewDraft((current) => current ? { ...current, units: current.units.filter((row) => row.item_id !== unit.item_id), preflight: null } : current)}>删除</Button>}>
            <Space wrap>
              <label>小题号 <InputNumber min={1} max={99} value={unit.subquestion_no} disabled={activeReviewItem.payload.tags?.question_type === 'choice' && reviewDraft.units.length === 1 && unit.subquestion_no === null && unit.blank_no === null} onChange={(value) => setReviewDraft((current) => current ? { ...current, units: current.units.map((row) => row.item_id === unit.item_id ? { ...row, subquestion_no: value } : row), preflight: null } : current)} /></label>
              <label>空号 <InputNumber min={1} max={99} value={unit.blank_no} disabled={activeReviewItem.payload.tags?.question_type === 'choice' && reviewDraft.units.length === 1 && unit.subquestion_no === null && unit.blank_no === null} onChange={(value) => setReviewDraft((current) => current ? { ...current, units: current.units.map((row) => row.item_id === unit.item_id ? { ...row, blank_no: value } : row), preflight: null } : current)} /></label>
              <label>判分方式 <Select style={{ width: 150 }} value={unit.answer_match_type}
                options={answerMatchOptions(activeReviewItem.payload.tags?.question_type)}
                onChange={(value: AnswerUnitDraft['answer_match_type']) => setReviewDraft((current) => current ? { ...current, units: current.units.map((row) => row.item_id === unit.item_id ? { ...row, answer_match_type: value } : row), preflight: null } : current)} /></label>
            </Space>
            <Input aria-label={`答案项 ${index + 1} 标准答案`} placeholder="标准答案" maxLength={2000} value={unit.answer} style={{ marginTop: 8 }}
              onChange={(event) => setReviewDraft((current) => current ? { ...current, units: current.units.map((row) => row.item_id === unit.item_id ? { ...row, answer: event.target.value } : row), preflight: null } : current)} />
            <Input.TextArea aria-label={`答案项 ${index + 1} 可接受答案`} placeholder="其他可接受答案，可用分号或换行分隔" rows={2} value={unit.accepted_answers} style={{ marginTop: 8 }}
              onChange={(event) => setReviewDraft((current) => current ? { ...current, units: current.units.map((row) => row.item_id === unit.item_id ? { ...row, accepted_answers: event.target.value } : row), preflight: null } : current)} />
          </Card>)}
          {reviewDraft.units.length < 50 && <Button onClick={() => setReviewDraft((current) => current ? { ...current, units: [...current.units, { ...defaultAnswerUnit(activeReviewItem), item_id: `answer-${crypto.randomUUID().slice(0, 8)}`, answer: '' }], preflight: null } : current)}>添加答案项</Button>}
        </Card>}
        <label>审核理由（拒绝时必填）<Input.TextArea rows={2} maxLength={500} value={reviewDraft.reason} placeholder="记录判断依据"
          onChange={(event) => setReviewDraft((current) => current ? { ...current, reason: event.target.value } : current)} /></label>
        {reviewDraft.preflight && <Alert showIcon type={reviewDraft.preflight.batch_eligible ? 'success' : 'warning'}
          message={reviewDraft.preflight.batch_eligible ? '当前路径通过预检，可以确认审核意向' : '当前路径有阻断项'}
          description={<Space direction="vertical" size={2}>
            {reviewDraft.preflight.blockers.map((problem, index) => <Typography.Text key={`b-${index}`} type="danger">阻断：{problem.message}</Typography.Text>)}
            {reviewDraft.preflight.warnings.map((problem, index) => <Typography.Text key={`w-${index}`} type="warning">提醒：{problem.message}</Typography.Text>)}
          </Space>} />}
      </Space>}
    </Modal>
    <Modal title="处理待核对问题" open={!!issueDraft} okText="保存决定" okButtonProps={{ loading: issueBusy }}
      onCancel={() => { if (!issueBusy) setIssueDraft(null) }} onOk={() => void saveS5IssueDecision()}>
      {issueDraft && <Space direction="vertical" style={{ width: '100%' }}>
        <Typography.Paragraph>{issueDraft.issue.summary}</Typography.Paragraph>
        <Select aria-label="问题处理决定" value={issueDraft.decision} style={{ width: '100%' }}
          options={[{ value: 'acknowledged', label: '已核对，问题已处理' }, { value: 'deferred', label: '延后处理' }, { value: 'ignored', label: '确认无需处理' }, { value: 'pending', label: '恢复待处理' }]}
          onChange={(decision: S5IssueDraft['decision']) => setIssueDraft((current) => current ? { ...current, decision } : current)} />
        <Input.TextArea aria-label="问题处理理由" rows={3} maxLength={500} value={issueDraft.reason} placeholder="填写核对依据；延后或忽略时必填"
          onChange={(event) => setIssueDraft((current) => current ? { ...current, reason: event.target.value } : current)} />
      </Space>}
    </Modal>
    <Modal title="确认答案归属" open={!!answerSourceDraft} okText="确认答案归属"
      okButtonProps={{ loading: !!answerSourceBusy }} onCancel={() => { if (!answerSourceBusy) setAnswerSourceDraft(null) }}
      onOk={() => void confirmS5AnswerSource()}>
      <Typography.Paragraph>请对照题目与来源答案核对关联。此操作不确认答案内容正确。</Typography.Paragraph>
      <Input.TextArea aria-label="答案关联理由" rows={3} maxLength={500} value={answerSourceDraft?.reason || ''}
        placeholder="若来源配对冲突或未匹配，请填写人工关联依据"
        onChange={(event) => setAnswerSourceDraft((current) => current ? { ...current, reason: event.target.value } : current)} />
    </Modal>
    <Card title="批量题目导入 · 内部识别与候选核对">
      <Typography.Paragraph>上传后先校验文件；S2 按页段提取题目并自动整批去重。同文件场景同时提取答案；分文件场景先读取题目文件，整批去重完成后识别答案文件。来源答案先按规则关联，再对有歧义的对应进行模型辅助判断，仍无法确定的留给教师核对。“只有题目”批次可选择为无答案题生成参考答案，或由教师手填；之后可单独启动标签建议。</Typography.Paragraph>
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
          { title: '状态', render: (_, batch: Batch) => <Space direction="vertical" size={0}><Tag color={batch.status === 'failed' ? 'red' : batch.status === 'cancelled' ? 'default' : batch.status === 'review_pending' ? 'green' : 'blue'}>{stageLabel(batch)}</Tag>{batch.checkpoint?.s2 && <Typography.Text type="secondary">题目识别调用 {batch.checkpoint.s2.calls || 0} 次</Typography.Text>}{batch.checkpoint?.s3?.separate_answers && <Typography.Text type={['failed', 'paused'].includes(batch.checkpoint.s3.separate_answers.status) ? 'warning' : 'secondary'}>答案识别：{batch.checkpoint.s3.separate_answers.status}，调用 {batch.checkpoint.s3.separate_answers.calls || 0} 次{batch.checkpoint.s3.separate_answers.error_message ? `；${batch.checkpoint.s3.separate_answers.error_message}` : ''}</Typography.Text>}{batch.checkpoint?.s3?.answer_model_matching && <Typography.Text type={['failed', 'pending_budget'].includes(batch.checkpoint.s3.answer_model_matching.status) ? 'warning' : 'secondary'}>模型辅助配对：{batch.checkpoint.s3.answer_model_matching.status}，调用 {batch.checkpoint.s3.answer_model_matching.calls || 0} 次{batch.checkpoint.s3.answer_model_matching.error_message ? `；${batch.checkpoint.s3.answer_model_matching.error_message}` : ''}</Typography.Text>}{batch.checkpoint?.s3?.answer_generation && <Typography.Text type={['failed', 'paused'].includes(batch.checkpoint.s3.answer_generation.status) ? 'warning' : 'secondary'}>答案生成：{batch.checkpoint.s3.answer_generation.status}，调用 {batch.checkpoint.s3.answer_generation.calls || 0} 次{batch.checkpoint.s3.answer_generation.error_message ? `；${batch.checkpoint.s3.answer_generation.error_message}` : ''}</Typography.Text>}{batch.checkpoint?.s3?.labels && <Typography.Text type={['failed', 'paused'].includes(batch.checkpoint.s3.labels.status) ? 'warning' : 'secondary'}>标签建议：{batch.checkpoint.s3.labels.status}，调用 {batch.checkpoint.s3.labels.calls || 0} 次{batch.checkpoint.s3.labels.error_message ? `；${batch.checkpoint.s3.labels.error_message}` : ''}</Typography.Text>}{batch.error_message && <Typography.Text type="danger">{batch.error_message}</Typography.Text>}{batch.error_code === 'S2_REVIEW_REQUIRED' && <Typography.Text type="warning">{skippableS2Page(batch) !== null ? '请核对失败页和前一页段的候选题；确认没有新题遗漏后，可人工跳过本页并继续。' : '请检查原文件对应页面并重新上传清晰版，或将批次号发给管理员；已完成的候选题仍可查看。'}批次号：<Typography.Text copyable={{ text: batch.id }}>{batch.id}</Typography.Text></Typography.Text>}{batch.error_code === 'CALL_LIMIT_REACHED' && <Typography.Text type="warning">已达到单批调用次数上限，请将批次号发给管理员：<Typography.Text copyable={{ text: batch.id }}>{batch.id}</Typography.Text></Typography.Text>}</Space> },
           { title: '操作', render: (_, batch: Batch) => <Space wrap>
             {['awaiting_s2', 's2_budget_paused'].includes(batch.stage) && batch.error_code !== 'CALL_LIMIT_REACHED' && <Button disabled={!s2Config?.ready} onClick={() => confirmStart(batch)}>{batch.stage === 's2_budget_paused' ? '调整预算并继续识别' : '开始 S2 识别'}</Button>}
             {batch.checkpoint?.s2 && <Button onClick={() => void viewCandidates(batch.id)}>查看候选与页段</Button>}
             {batch.stage === 's2_completed' && batch.checkpoint?.s3?.global_dedupe?.status !== 'completed' && !['auto_claimed', 'running'].includes(batch.checkpoint?.s3?.global_dedupe?.status || '') && !(batch.checkpoint?.s2?.auto_global_dedupe && !batch.checkpoint?.s3?.global_dedupe?.status) && <Button type="primary" loading={reviewingKey === `batch:${batch.id}`} disabled={!s2Config?.ready || !!batch.files_purged_at} onClick={() => confirmBatchDedup(batch)}>{batch.checkpoint?.s3?.global_dedupe?.status === 'pending_budget' ? '增加预算并整理整批' : batch.source_mode === 'separate' ? '模型整理整批题目' : '模型整理整批题答'}</Button>}
             {batch.stage === 's2_completed' && batch.source_mode === 'separate' && batch.checkpoint?.s3?.global_dedupe?.status === 'completed' && !['queued', 'processing', 'completed'].includes(batch.checkpoint?.s3?.separate_answers?.status || '') && <Button type="primary" disabled={!s2Config?.ready || !!batch.files_purged_at} onClick={() => confirmSeparateAnswers(batch)}>{batch.checkpoint?.s3?.separate_answers?.status === 'paused' ? '增加预算并继续识别答案' : batch.checkpoint?.s3?.separate_answers?.status === 'failed' ? '重试答案识别' : '识别答案文件'}</Button>}
             {batch.stage === 's2_completed' && ['combined', 'separate'].includes(batch.source_mode) && batch.checkpoint?.s3?.global_dedupe?.status === 'completed' && !!batch.checkpoint?.s3?.source_answer_reconciliation && (batch.source_mode !== 'separate' || batch.checkpoint?.s3?.separate_answers?.status === 'completed') && (!batch.checkpoint?.s3?.answer_model_matching || ['pending_budget', 'failed'].includes(batch.checkpoint.s3.answer_model_matching.status)) && (batch.checkpoint?.s3?.answer_model_matching || !(batch.source_mode === 'combined' ? batch.checkpoint?.s2?.auto_answer_matching : batch.checkpoint?.s3?.separate_answers?.auto_answer_matching)) && <Button disabled={!s2Config?.ready || !!batch.files_purged_at || !!batch.checkpoint?.s3?.answer_model_matching?.cost_unknown || ['queued', 'processing'].includes(batch.checkpoint?.s3?.labels?.status || '')} onClick={() => confirmAnswerMatching(batch)}>{batch.checkpoint?.s3?.answer_model_matching?.status === 'pending_budget' ? '增加预算并继续配对' : batch.checkpoint?.s3?.answer_model_matching?.status === 'failed' ? '重试模型配对' : '模型辅助配对答案'}</Button>}
             {batch.stage === 's2_completed' && batch.checkpoint?.s3?.global_dedupe?.status === 'completed' && (batch.source_mode === 'questions_only' || (batch.source_mode === 'combined' && !!batch.checkpoint?.s3?.source_answer_reconciliation) || (batch.source_mode === 'separate' && batch.checkpoint?.s3?.separate_answers?.status === 'completed' && !!batch.checkpoint?.s3?.source_answer_reconciliation)) && (batch.source_mode === 'questions_only' || (!(batch.source_mode === 'combined' ? batch.checkpoint?.s2?.auto_answer_matching : batch.checkpoint?.s3?.separate_answers?.auto_answer_matching) && !batch.checkpoint?.s3?.answer_model_matching) || batch.checkpoint?.s3?.answer_model_matching?.status === 'completed') && !['queued', 'processing', 'completed'].includes(batch.checkpoint?.s3?.labels?.status || '') && !batch.checkpoint?.s3?.labels?.cost_unknown && <Button type="primary" disabled={!s2Config?.ready || !!batch.files_purged_at} onClick={() => confirmLabels(batch)}>{batch.checkpoint?.s3?.labels?.status === 'paused' ? '增加预算并继续打标签' : batch.checkpoint?.s3?.labels?.status === 'failed' ? '重试标签建议' : '生成入库标签建议'}</Button>}
             {batch.checkpoint?.s3?.answer_model_matching?.cost_unknown && <Typography.Text type="warning">配对调用费用未知，请先核对账单后处理</Typography.Text>}
             {batch.stage === 's2_completed' && batch.checkpoint?.s3?.labels?.status === 'completed' && <Button disabled={!s2Config?.ready || !!batch.files_purged_at} onClick={() => confirmLabels(batch, true)}>全部重新打标签</Button>}
             {batch.checkpoint?.s3?.labels?.cost_unknown && <Typography.Text type="warning">标签调用费用未知，请先核对账单后处理</Typography.Text>}
             {batch.status === 'failed' && batch.error_code !== 'S2_REVIEW_REQUIRED' && skippableS2Page(batch) === null && !batch.files_purged_at && <Button disabled={loading} onClick={() => confirmRetry(batch)}>{batch.error_code === 'INCOMPLETE_MODEL_OUTPUT' ? '自动调整页段并重试' : '重试'}</Button>}
             {skippableS2Page(batch) !== null && <Button danger disabled={loading} onClick={() => confirmSkipS2Page(batch, skippableS2Page(batch)!)}>跳过第 {skippableS2Page(batch)} 页并继续</Button>}
             {!['cancelled', 'completed', 'completed_with_unassigned'].includes(batch.status) && <Button disabled={loading} onClick={() => confirmCancel(batch)}>取消</Button>}
             {batch.status === 'cancelled' && <Button danger disabled={loading} onClick={() => confirmDeleteBatch(batch)}>永久删除</Button>}
           </Space> },
        ]} />
    </Card>
    <Modal title="候选题、来源答案与页段记录" open={!!selectedBatchId} width={1300} footer={<Button onClick={closeCandidateModal}>关闭</Button>} onCancel={closeCandidateModal}>
      <Typography.Paragraph>按 PDF 页段自动提取位图，并由模型识别题目和所需题图；题答同文件时还会在同次调用中提取原文答案。缺少独立题图时按模型坐标自动裁剪。已匹配图片直接显示在对应题目处。</Typography.Paragraph>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message={selectedBatch?.source_mode === 'separate'
          ? '先提取题目、整批去重，再识别答案文件；答案识别完成后才能增删改题目和答案。'
          : '先提取题目、整批去重；去重完成后才能增删改题目和答案。'}
        description="题目、答案、题图和标签可在任务完成后编辑；模型配对、答案生成或标签建议排队、运行期间暂不可编辑。修改题目或题图不会自动改变已有标签；主动点击“全部重新打标签”会覆盖已有的题型、难度和主属知识点。" />
      {canEditCandidates && <Space style={{ marginBottom: 12 }}>
        <Button type="primary" onClick={() => { setNewCandidateDraft(emptyCandidateDraft()); setNewCandidateOpen(true) }}>新增题目</Button>
        <Typography.Text type="secondary">新增题目请人工核对重复情况；可在打标签前或后修改。</Typography.Text>
      </Space>}
      {s5Summary && <Card size="small" title="S5 教师审核概况" style={{ marginBottom: 16 }}>
        <Button style={{ marginBottom: 10 }} type="primary" disabled={!canEditCandidates || s5Summary.counts.pending === 0}
          onClick={openBulkReview}>批量预检与审核</Button>
        <Space wrap>
          <Tag color="blue">真实候选 {s5Summary.counts.active_real}</Tag>
          <Tag>待审 {s5Summary.counts.pending}</Tag>
          <Tag color="green">已确认 {s5Summary.counts.approved}</Tag>
          <Tag color="red">已拒绝 {s5Summary.counts.rejected}</Tag>
          <Tag color="orange">跳过已有题 {s5Summary.counts.skipped_duplicate}</Tag>
          <Tag color="cyan">可确认仅入库 {s5Summary.counts.ready_for_library_confirmation}</Tag>
          <Tag color="purple">直接入池待核答案 {s5Summary.counts.needs_answer_review_for_pool}</Tag>
          {s5Summary.counts.invalid_review_records > 0 && <Tag color="warning">旧审核已失效 {s5Summary.counts.invalid_review_records}</Tag>}
        </Space>
        <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 4 }}>
          查重未检查 {s5Summary.counts.missing_catalog_check} 题，已过期 {s5Summary.counts.stale_catalog_check} 题；相同题 {s5Summary.counts.exact_match_items} 题，可能相似 {s5Summary.counts.near_match_items} 题；待认领答案 {s5Summary.counts.unassigned_answer_issues} 条，待认领题图 {s5Summary.counts.unassigned_images} 张。
        </Typography.Paragraph>
        <Typography.Text type="secondary">选择“批量预检与审核”中的“题目与答案直接入池”，系统会预填当前答案；教师核对后可批量确认拟入池意向。正式入库和入池将在 S6 完成。</Typography.Text>
        {s5Reviews && s5Reviews.items.some((row) => row.valid && row.decision === 'approved') && <details style={{ marginTop: 8 }}>
          <summary>查看拟入库清单</summary>
          <Space direction="vertical" size={2} style={{ marginTop: 8 }}>
            {s5Reviews.items.filter((row) => row.valid && row.decision === 'approved').map((row) => {
              const item = candidates.find((candidate) => candidate.id === row.item_id)
              return <Typography.Text key={row.item_id}>第 {item?.ordinal ?? '?'} 题 · {row.route === 'direct_pool' ? '题目与答案拟直接入池' : '拟仅题目入库'} · {item?.payload.content.question_text.slice(0, 70)}</Typography.Text>
            })}
          </Space>
        </details>}
      </Card>}
      {selectedBatch && dedupeStatus === 'completed' && <div style={{ marginBottom: 12 }}>
        <Button loading={exactCatalogBusy} onClick={() => void checkExactCatalog(selectedBatch.id)}>检查题库重复</Button>
        {catalogReviewResult && <Typography.Text type="secondary"> 已检查 {catalogReviewResult.items.length} 道题；{catalogReviewResult.items.filter((row) => row.matches.some((match) => match.kind === 'exact')).length} 道找到相同题，{catalogReviewResult.items.filter((row) => row.candidate_stale || row.catalog_stale).length} 道检查结果已失效。相似题需要人工判断；正式入库前会再次检查。</Typography.Text>}
      </div>}
      {selectedBatch && selectedBatch.source_mode === 'questions_only' && dedupeStatus === 'completed' && <div style={{ marginBottom: 12 }}>
        {generationTask?.cost_unknown && <Alert type="warning" showIcon message="上次答案生成调用费用未知，请先核对账单；当前不能重新发起调用。" style={{ marginBottom: 8 }} />}
        <Button type="primary" disabled={!canEditCandidates || !s2Config?.ready || !!selectedBatch.files_purged_at || !!generationTask?.cost_unknown || unansweredCandidates.length === 0}
          onClick={() => confirmAnswerGeneration(selectedBatch, unansweredCandidates.length)}>
          {generationTask?.status === 'failed' || generationTask?.status === 'paused' ? '继续生成无答案题的答案' : '为无答案题生成答案'}
        </Button>
        <Typography.Text type="secondary"> 当前无答案 {unansweredCandidates.length} 题；也可以逐题手填。模型答案均待教师核对。</Typography.Text>
      </div>}
      {selectedBatch?.stage === 's2_completed' && candidates.some((item) => !hasImportLabels(item)) && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={`还有 ${candidates.filter((item) => !hasImportLabels(item)).length} 道题缺少完整入库标签，请人工打标签；所有题都有标签后才能入库。`} />}
      {selectedBatch?.stage === 's2_completed' && <Card size="small" style={{ marginBottom: 16 }}><details>
        <summary>无法匹配到题目的图片（{unmatchedImages.length}）</summary>
        {!images?.files_available && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message="临时题图已过期，无法显示或修改；请重新上传文件" />}
        {unmatchedImages.length === 0 ? <Typography.Text type="secondary">暂无需要人工关联的图片</Typography.Text> : images?.files_available ? <>
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
        {unmatchedImageCount > 0 && images?.files_available && <div style={{ marginTop: 12 }}>
          <Typography.Paragraph type="secondary">核对结束后，可以删除剩余 {unmatchedImageCount} 张未关联临时图片；其中重复图片不会在上方列表展示。</Typography.Paragraph>
          <Button danger disabled={!canEditCandidates || imageBusy} onClick={() => confirmDiscardUnmatchedImages(selectedBatch, unmatchedImageCount)}>完成题图核对并删除未匹配图片</Button>
        </div>}
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
      {answerModelExpected && !answerModelTask && reconciliation && <Typography.Paragraph type="secondary">规则关联已完成，等待后台模型辅助配对。</Typography.Paragraph>}
      {answerModelTask && <Typography.Paragraph type={['failed', 'pending_budget'].includes(answerModelTask.status) ? 'warning' : 'secondary'}>模型辅助配对：{answerModelTask.status}；已调用 {answerModelTask.calls || 0} 次，估算费用 ¥{answerModelTask.estimated_cny || 0}。{answerModelTask.error_message || ''}</Typography.Paragraph>}
      {!!s2?.units?.length && <details key={`${selectedBatch?.id}:s2-units`}>
        <summary>题目识别页段记录（{s2.units.length} 段）</summary>
        <Typography.Paragraph type="secondary">每页只归一个主体页段；后续页作为跨页上下文重复传入模型。模型报告不完整后，新拆分的页段最多带一页上下文；单页主体仍失败时可核对原页后人工跳过。</Typography.Paragraph>
        {s2.units.map((unit) => <div key={unit.index}>
          <Typography.Paragraph>页段 {unit.index + 1}：主体为原文件第 {unit.core_first_page}–{unit.core_last_page} 页{unit.last_page > unit.core_last_page ? `，第 ${unit.core_last_page + 1}–${unit.last_page} 页仅作跨页上下文` : ''}；{unit.skipped_by_teacher ? '已人工跳过，需核对本页是否漏题' : unit.status === 'completed' ? `S2 当时提取 ${unit.candidate_count || 0} 题（下方展示当前候选）` : '待处理'}</Typography.Paragraph>
          {unit.skipped_by_teacher && unit.skip_error_message && <Typography.Paragraph type="warning">跳过前的模型报错：{unit.skip_error_message}</Typography.Paragraph>}
          {selectedBatch?.source_mode !== 'combined' && unit.source_answer_signals?.map((signal, index) => <Typography.Paragraph key={`answer-signal-${index}`} type="warning">第 {signal.page} 页可能含资料答案，请核对上传场景：{signal.evidence}</Typography.Paragraph>)}
        </div>)}
      </details>}
      {!!teacherReviewIssues.length && <details key={`${selectedBatch?.id}:review-issues`} className="question-import-issues">
        <summary>问题处理清单（{teacherReviewIssues.length} 条，已处理 {teacherReviewIssues.filter((issue) => issue.handling?.valid && ['acknowledged', 'ignored'].includes(issue.handling.decision)).length} 条）</summary>
        <div className="question-import-issues-content">
          <Typography.Paragraph type="secondary">请根据来源页码、原文件和候选题人工核对。</Typography.Paragraph>
          <div className="question-import-issue-types">
            {reviewCategories.map((category) => {
              const categoryIssues = teacherReviewIssues.filter((issue) => issue.kind === category.kind)
              return <details key={category.kind} className="question-import-issue-group">
                <summary>{category.title}（{categoryIssues.length} 条）</summary>
                <div className="question-import-issue-list">
                  {categoryIssues.length === 0 && <Typography.Text type="secondary">暂无此类问题</Typography.Text>}
                  {categoryIssues.map((issue) => {
                    const candidateSummary = (id: string) => {
                      const item = candidates.find((candidate) => candidate.id === id)
                      return item ? `候选第 ${item.ordinal} 题（原题号 ${item.payload.source.original_number || '未识别'}，${selectedBatch?.source_mode === 'separate' ? '题目文件' : '原文件'}第 ${item.payload.source.page_start ?? '未知'} 页）` : `候选 ID ${id}`
                    }
                    const candidateSummaries = issue.candidate_item_ids.map(candidateSummary)
                    return <Card key={issue.key} size="small" className="question-import-issue-card"
                      title={`${selectedBatch?.source_mode === 'separate' ? ['answer', 'answer_fragment', 'answer_match_warning', 'answer_match_disagreement'].includes(issue.kind) ? '答案文件' : '题目文件' : '原文件'}${issue.summary}`}
                      extra={<Tag color={issue.handling?.valid && ['acknowledged', 'ignored'].includes(issue.handling.decision) ? 'green' : issue.handling?.decision === 'deferred' && issue.handling.valid ? 'blue' : 'orange'}>
                        {issue.handling?.valid ? issue.handling.decision === 'acknowledged' ? '已核对' : issue.handling.decision === 'ignored' ? '无需处理' : issue.handling.decision === 'deferred' ? '已延后' : '待处理' : issue.handling ? '处理证据已变化' : '待教师核对'}</Tag>}>
                      {['fragment', 'answer_fragment', 'skipped_page'].includes(issue.kind) && <>
                        <Typography.Paragraph><Typography.Text strong>{issue.kind === 'skipped_page' ? '跳过记录：' : '待核对片段：'}</Typography.Text></Typography.Paragraph>
                        <RichQuestionText text={issue.fragment_text || '内容未记录'} assets={[]} />
                        <Typography.Paragraph type="secondary">问题说明：{issue.fragment_reason || '原因未记录'}</Typography.Paragraph>
                      </>}
                      {['answer', 'answer_match_warning', 'answer_match_disagreement'].includes(issue.kind) && <>
                        <Typography.Paragraph><Typography.Text strong>{issue.kind === 'answer' ? '待认领答案：' : '已关联答案：'}</Typography.Text>原题号 {issue.answer_number || '缺失'}</Typography.Paragraph>
                        <RichQuestionText text={issue.answer_text || '内容未记录'} assets={[]} />
                        {issue.kind === 'answer_match_disagreement' && <Alert type="error" showIcon style={{ margin: '8px 0' }}
                          message="模型明确建议更换关联题，请核对原文件"
                          description={<>
                            <div>当前关联：{issue.current_item_id ? candidateSummary(issue.current_item_id) : '未知'}</div>
                            <div>模型建议：{issue.model_suggestion ? candidateSummary(issue.model_suggestion) : '未知'}</div>
                            {issue.evidence_quote && <div>模型引用的原文：{issue.evidence_quote}</div>}
                            <div>当前关联未自动改动。</div>
                          </>} />}
                        {issue.model_reason && <Typography.Paragraph type="warning">模型判断：{issue.model_reason}</Typography.Paragraph>}
                        {issue.kind === 'answer' && issue.model_suggestion && <Typography.Paragraph type="warning">模型建议候选：{candidateSummary(issue.model_suggestion)}；未自动关联，仍需教师核对。</Typography.Paragraph>}
                      </>}
                      {!!candidateSummaries.length && <Typography.Paragraph>相关候选题：{candidateSummaries.join('；')}</Typography.Paragraph>}
                      <Typography.Paragraph type="secondary">
                        {selectedBatch?.source_mode === 'separate' && ['answer', 'answer_match_warning', 'answer_match_disagreement'].includes(issue.kind)
                          ? `答案文件来源页：${issue.source_answer?.page_start ?? '未知'}${issue.source_answer?.page_end && issue.source_answer.page_end !== issue.source_answer.page_start ? `–${issue.source_answer.page_end}` : ''}；相关候选题的题目文件页码见上方。`
                          : `相关来源页：${issue.page_numbers.join('、') || '未知'}${['fragment', 'skipped_page'].includes(issue.kind) ? '（含邻近题目上下文）' : ''}。`}{['fragment', 'skipped_page'].includes(issue.kind)
                          ? `邻近候选题 ${issue.candidate_item_ids.length} 道，仅供判断归属，不表示这些题有错误。`
                          : issue.kind === 'answer_fragment'
                          ? '答案文件与题目文件页码彼此独立，请对照答案原页和题目内容核对。'
                          : `候选 ${issue.candidate_item_ids.length} 条。`}
                        {issue.candidate_total > issue.candidate_item_ids.length && `共 ${issue.candidate_total} 条候选，此处仅列部分。`}
                      </Typography.Paragraph>
                      {issue.handling?.reason && <Typography.Paragraph type="secondary">上次处理理由：{issue.handling.reason}</Typography.Paragraph>}
                      {issue.kind === 'answer' && <Space wrap style={{ marginBottom: 8 }}>
                        <Select aria-label={`为问题 ${issue.key} 选择候选题`} showSearch optionFilterProp="label" placeholder="选择答案所属题目" style={{ width: 340 }}
                          value={answerClaimTargets[issue.key]} options={imageTargetOptions}
                          onChange={(itemId: string) => setAnswerClaimTargets((current) => ({ ...current, [issue.key]: itemId }))} />
                        <Button type="primary" size="small" disabled={!canEditCandidates || !answerClaimTargets[issue.key]} loading={answerSourceBusy === issue.key}
                          onClick={() => void claimS5Answer(issue)}>认领此答案</Button>
                      </Space>}
                      <Space wrap>
                        <Button size="small" disabled={!canEditCandidates} onClick={() => setIssueDraft({ issue, decision: 'acknowledged', reason: issue.handling?.valid ? issue.handling.reason || '' : '' })}>记录处理决定</Button>
                        {issue.handling && !issue.handling.valid && <Typography.Text type="warning">原决定所依据的内容已变化，请重新核对。</Typography.Text>}
                      </Space>
                    </Card>
                  })}
                </div>
              </details>
            })}
          </div>
        </div>
      </details>}
      {!!duplicateCandidates.length && <Typography.Paragraph><Button size="small" onClick={() => setShowDuplicateCandidates((value) => !value)}>{showDuplicateCandidates ? '收起' : '显示'}疑似重复候选 {duplicateCandidates.length} 条</Button><Typography.Text type="secondary"> 原始记录仍保留；确认不是重复题时可展开核对。</Typography.Text></Typography.Paragraph>}
      <Space wrap style={{ marginBottom: 12 }}>
        <Select aria-label="筛选候选题" value={candidateFilter} style={{ width: 220 }} onChange={setCandidateFilter}
          options={[{ value: 'all', label: '全部候选' }, { value: 'pending', label: '待审核' },
            { value: 'approved', label: '已确认' }, { value: 'rejected', label: '已拒绝' },
            { value: 'skipped_duplicate', label: '题库已有，不再入库' }, { value: 'exact', label: '命中相同题' },
            { value: 'nearby', label: '命中相似题' }, { value: 'stale', label: '查重已过期' },
            { value: 'missing_tag', label: '标签不完整' }, { value: 'missing_answer', label: '缺少答案' },
            { value: 'figure', label: '题图待核对' }, { value: 'issue', label: '问题待处理' }]} />
        <Input.Search aria-label="搜索候选题" allowClear value={candidateQuery} placeholder="题号或题干关键词" style={{ width: 260 }}
          onChange={(event) => setCandidateQuery(event.target.value)} />
        <Typography.Text type="secondary">显示 {visibleCandidates.length} / {candidateSource.length} 道题</Typography.Text>
      </Space>
      <Table className="question-import-candidates-table" rowKey="id" dataSource={visibleCandidates}
        size="small" tableLayout="fixed" scroll={{ x: 1400 }} pagination={{ pageSize: 10 }} columns={[
        { title: '序号', dataIndex: 'ordinal', width: 70 },
        { title: '原题号', render: (_, item: Candidate) => item.payload.source.original_number || '未识别', width: 90 },
        { title: '原页', render: (_, item: Candidate) => `${item.payload.source.page_start ?? '?'}–${item.payload.source.page_end ?? '?'}`, width: 90 },
        { title: '题目、题图与答案', render: (_, item: Candidate) => renderCandidateContent(item) },
        { title: '题库查重结果', render: (_, item: Candidate) => renderCatalogReviewContent(item), width: 380 },
        { title: 'S5 审核', width: 200, render: (_, item: Candidate) => {
          const review = s5Reviews?.items.find((row) => row.item_id === item.id)
          const skipped = item.payload.teacher_decision === 'skipped_duplicate'
          return <Space direction="vertical" size={4}>
            {item.is_fake ? <Tag>测试题，不参与审核</Tag> : skipped ? <Tag color="orange">题库已有，不再入库</Tag>
              : review?.valid && review.decision === 'approved' ? <Tag color="green">{review.route === 'direct_pool' ? '已确认拟直接入池' : '已确认仅题目入库'}</Tag>
                : review?.valid && review.decision === 'rejected' ? <Tag color="red">已拒绝</Tag>
                  : <Tag color="default">待审核</Tag>}
            {review?.question_event_id && !review.valid && <Typography.Text type="warning">旧审核已失效</Typography.Text>}
            {review?.reason && review.valid && <Typography.Text type="secondary">理由：{review.reason}</Typography.Text>}
            {!item.is_fake && !skipped && <Button size="small" disabled={!canEditCandidates} onClick={() => openS5Review(item)}>预检与审核</Button>}
          </Space>
        } },
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
