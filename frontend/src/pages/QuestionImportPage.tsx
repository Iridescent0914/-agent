import { Button, Card, Empty, Input, InputNumber, Modal, Progress, Select, Space, Table, Tag, Typography, message } from 'antd'
import { useEffect, useState } from 'react'
import { apiPost, apiGet, apiUrl } from '../api/client'
import { RichQuestionText } from '../components/RichQuestionText'

type Batch = {
  id: string
  source_mode: string
  status: string
  stage: string
  files: Array<{ file_id: string; original_name: string; size_bytes: number; page_count: number | null; role: string }>
  checkpoint: {
    s2?: { calls?: number; prompt_version?: string; auto_global_dedupe?: boolean; units?: Array<{ index: number; first_page: number; last_page: number; core_first_page: number; core_last_page: number; status: string; candidate_count?: number; source_answers?: Array<{ original_number: string | null; text: string; page_start: number; candidate_item_ids?: string[] }>; source_answer_signals?: Array<{ page: number; evidence: string }>; overlap_suspects?: Array<{ candidate_item_id: string; other_item_id: string }>; unresolved_fragments?: Array<{ page: number; text: string; reason: string }> }> }
    s3?: { global_dedupe?: { status: string; reviewed_candidate_count?: number; removed_duplicate_count?: number; error_message?: string; estimated_cny?: number }; source_answer_reconciliation?: { schema_version: string; source_answer_count: number; linked_answer_count: number; linked_question_count: number; overlap_duplicate_answers?: Array<{ text: string; page_start: number; matched_original_number: string; matched_page: number }>; unassigned_answers: Array<{ original_number: string | null; text: string; page_start: number; reason: string; candidate_item_ids?: string[] }> } }
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
  is_fake: boolean
  payload: {
    source: { original_number: string | null; page_start: number | null; page_end: number | null }
    content: { question_text: string; options: Array<{ key: string; text: string }> }
    extraction?: { math_fragments?: string[]; diagram_clues?: string[]; uncertain_fragments?: string[]; source_classification?: string; classification_evidence?: string }
    answers?: Array<{ answer_id: string; answer_source: string; text: string | null; teacher_confirmed: boolean }>
    answer_match_status?: string
    duplicate_status?: string
    warnings?: string[]
  }
}

type S2Config = { ready: boolean; model: string; max_budget_cny: number; max_output_tokens: number; pages_per_unit: number; overlap_pages: number }

const modeLabels: Record<string, string> = {
  questions_only: '只有题目', combined: '题目与答案同文件', separate: '题目与答案分文件',
}

const reviewVerdictLabels: Record<string, string> = {
  same_question: '可能是同一题', different_questions: '可能是不同题',
  match_candidate: '可能归属所列候选', no_match: '未找到可信归属', uncertain: '无法判断',
}

function stageLabel(batch: Batch): string {
  if (batch.stage === 'awaiting_s2') return '文件已校验，等待 S2 解析'
  if (batch.stage === 's2_queued') return '等待 S2 识别'
  if (batch.stage === 's2_processing') return '正在识别 PDF 页段'
  if (batch.stage === 's2_completed') {
    const dedupe = batch.checkpoint?.s3?.global_dedupe?.status
    if (dedupe === 'auto_claimed' || dedupe === 'running') return '正在整批去重和关联答案'
    if (dedupe === 'pending_budget') return '整批去重待补预算'
    if (dedupe === 'failed') return '整批去重失败，可重试'
    if (!dedupe && batch.checkpoint?.s2?.auto_global_dedupe) return '等待整批去重'
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
      content: `将题目文件按页段发送给百炼 ${s2Config?.model || ''}，页段识别完成后自动把整批候选题发送给模型去重并关联原文答案。请确认文件不含学生个人信息。本批调用预算阈值为 ¥${budget}；这是基于已返回用量的停机阈值，单次调用可能使实际费用略高。`,
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
      const [items, issues, units] = await Promise.all([
        apiGet<Candidate[]>(`/question-imports/${id}/items`),
        apiGet<ReviewIssue[]>(`/question-imports/${id}/s3/review-issues`),
        apiGet<ReviewUnit[]>(`/question-imports/${id}/s3/review-units`),
      ])
      setCandidates(items)
      setShowDuplicateCandidates(false)
      setReviewIssues(issues)
      setReviewUnits(units)
      setSelectedBatchId(id)
    } catch (error) { message.error(error instanceof Error ? error.message : '候选题加载失败') }
  }

  function confirmIssueReview(batch: Batch, issue: ReviewIssue) {
    if (!budget || !operatorToken) { message.warning('请先填写预算阈值和 S2 操作口令'); return }
    Modal.confirm({
      title: '确认发送问题原页给百炼复核？',
      content: `问题：${issue.summary}。将原文件第 ${issue.page_numbers.join('、')} 页及相关候选题文字发送给百炼 ${s2Config?.model || ''}。本批 S3 复核预算阈值为 ¥${budget}，单次调用仍可能略超阈值；模型确认同题或答案归属后会自动处理，未确定片段仍只记录建议。`,
      okText: '发送并复核', cancelText: '取消',
      onOk: async () => {
        setReviewingKey(issue.key)
        try {
          const result = await apiPost<ReviewIssue['review']>(`/question-imports/${batch.id}/s3/review-issues/${issue.key}`,
            { budget_cny: budget }, undefined, { 'X-Import-S2-Token': operatorToken })
          message[result?.status === 'completed' ? 'success' : 'warning'](result?.status === 'completed' ? '模型复核已保存，确定的重复题或答案归属已处理' : result?.error_message || '复核尚未完成')
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
      content: `将原文件第 ${unit.page_numbers.join('、')} 页和相关候选文字一次发送给百炼 ${s2Config?.model || ''}，同时复核 ${unit.issue_keys.length} 个问题${unit.skipped_count ? `；另有 ${unit.skipped_count} 个问题需单独处理` : ''}。本批 S3 预算阈值为 ¥${budget}；单次调用可能略超阈值。模型确认同题或答案归属后会自动处理。`,
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
      content: `将整批候选题文字、题号、来源页码和提取到的资料答案发送给百炼 ${s2Config?.model || ''}，跨页段判断重复题与答案区重述题，移除重复候选后自动关联资料答案。预算阈值 ¥${budget}；模型可能产生费用。`,
      okText: '整理整批', cancelText: '取消',
      onOk: async () => {
        setReviewingKey(`batch:${batch.id}`)
        try {
          const result = await apiPost<{ status: string; removed_duplicate_count?: number; error_message?: string }>(
            `/question-imports/${batch.id}/s3/dedupe-batch`, { budget_cny: budget }, undefined,
            { 'X-Import-S2-Token': operatorToken })
          message[result.status === 'completed' ? 'success' : 'warning'](result.status === 'completed'
            ? `整批判重完成，移除 ${result.removed_duplicate_count || 0} 道重复候选，已重新关联资料答案`
            : result.error_message || '整批判重未完成')
          await reload(batch.id)
          if (selectedBatchId === batch.id) await viewCandidates(batch.id)
        } catch (error) { message.error(error instanceof Error ? error.message : '整批判重失败') }
        finally { setReviewingKey(null) }
      },
    })
  }

  async function reconcileAnswers(batch: Batch) {
    try {
      const result = await apiPost<{ linked_answer_count: number; overlap_duplicate_answers: unknown[]; unassigned_answers: unknown[] }>(
        `/question-imports/${batch.id}/s3/reconcile-source-answers`, {})
      message[result.linked_answer_count ? 'success' : 'warning'](`原文件提取答案 ${result.linked_answer_count + result.unassigned_answers.length} 条，已关联 ${result.linked_answer_count} 条；跨页重复 ${result.overlap_duplicate_answers.length} 条；待认领 ${result.unassigned_answers.length} 条`)
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
  const sourceAnswers = s2?.units?.flatMap((unit) => unit.source_answers || []) || []
  const pendingAnswers = reconciliation?.unassigned_answers || sourceAnswers
  const answerNumber = (value: string | null) => value?.normalize('NFKC').trim().replace(/^第\s*/, '').replace(/\s*题?[.．、:：\s]*$/, '') || ''

  return <Space direction="vertical" size="large" style={{ width: '100%' }}>
    <Card title="批量题目导入 · 内部识别与候选核对">
      <Typography.Paragraph>上传后先校验文件；点击“开始 S2 识别”会按页段提取题目与原文答案，接着自动整批去重和关联答案。请先确认文件场景；题答同文件已设为默认选项。</Typography.Paragraph>
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
          { title: '状态', render: (_, batch: Batch) => <Space direction="vertical" size={0}><Tag color={batch.status === 'failed' ? 'red' : batch.status === 'cancelled' ? 'default' : batch.status === 'review_pending' ? 'green' : 'blue'}>{stageLabel(batch)}</Tag>{batch.checkpoint?.s2 && <Typography.Text type="secondary">调用 {batch.checkpoint.s2.calls || 0} 次</Typography.Text>}{batch.error_message && <Typography.Text type="danger">{batch.error_message}</Typography.Text>}{batch.error_code === 'S2_REVIEW_REQUIRED' && <Typography.Text type="warning">请检查原文件对应页面并重新上传清晰版，或将批次号发给管理员；已完成的候选题仍可查看。批次号：<Typography.Text copyable={{ text: batch.id }}>{batch.id}</Typography.Text></Typography.Text>}{batch.error_code === 'CALL_LIMIT_REACHED' && <Typography.Text type="warning">已达到单批调用次数上限，请将批次号发给管理员：<Typography.Text copyable={{ text: batch.id }}>{batch.id}</Typography.Text></Typography.Text>}</Space> },
           { title: '操作', render: (_, batch: Batch) => <Space wrap>
             {['awaiting_s2', 's2_budget_paused'].includes(batch.stage) && batch.error_code !== 'CALL_LIMIT_REACHED' && <Button disabled={!s2Config?.ready} onClick={() => confirmStart(batch)}>{batch.stage === 's2_budget_paused' ? '调整预算并继续识别' : '开始 S2 识别'}</Button>}
             {batch.checkpoint?.s2 && <Button onClick={() => void viewCandidates(batch.id)}>查看候选与页段</Button>}
             {batch.stage === 's2_completed' && batch.checkpoint?.s3?.global_dedupe?.status !== 'completed' && !['auto_claimed', 'running'].includes(batch.checkpoint?.s3?.global_dedupe?.status || '') && !(batch.checkpoint?.s2?.auto_global_dedupe && !batch.checkpoint?.s3?.global_dedupe?.status) && <Button type="primary" loading={reviewingKey === `batch:${batch.id}`} disabled={!s2Config?.ready || !!batch.files_purged_at} onClick={() => confirmBatchDedup(batch)}>{batch.checkpoint?.s3?.global_dedupe?.status === 'pending_budget' ? '增加预算并整理整批' : '模型整理整批题答'}</Button>}
             {batch.stage === 's2_completed' && batch.source_mode === 'combined' && <Button onClick={() => void reconcileAnswers(batch)}>重新匹配答案</Button>}
             {batch.status === 'failed' && batch.error_code !== 'S2_REVIEW_REQUIRED' && !batch.files_purged_at && <Button disabled={loading} onClick={() => confirmRetry(batch)}>{batch.error_code === 'INCOMPLETE_MODEL_OUTPUT' ? '自动调整页段并重试' : '重试'}</Button>}
             {!['cancelled', 'completed', 'completed_with_unassigned'].includes(batch.status) && <Button disabled={loading} onClick={() => void act(batch.id, 'cancel')}>取消</Button>}
           </Space> },
        ]} />
    </Card>
    <Modal title="候选题、来源答案与页段记录" open={!!selectedBatchId} width={900} footer={<Button onClick={() => setSelectedBatchId(null)}>关闭</Button>} onCancel={() => setSelectedBatchId(null)}>
      <Typography.Paragraph>整批模型整理会比较所有候选题，移除答案区重述题和重复题，然后自动关联资料答案。图形仅有线索，尚未完成 S3 题图裁剪；原文件不提供在线公开地址。</Typography.Paragraph>
      {selectedBatch?.source_mode === 'combined' && <Typography.Paragraph>
        S2 已提取原文答案 {sourceAnswers.length} 条；{reconciliation
          ? `S3 已为 ${reconciliation.linked_question_count}/${candidates.length} 道题关联答案，答案显示在下方对应题目内。`
          : selectedBatch?.stage === 's2_completed' && selectedBatch?.checkpoint?.s2?.auto_global_dedupe
            ? '后台正在整批去重并关联题答，题目内的答案会在完成后自动刷新。'
            : '尚未完成题答关联。'}
      </Typography.Paragraph>}
      {selectedBatch?.checkpoint?.s3?.global_dedupe?.status === 'completed' && <Typography.Paragraph>整批模型判重：比较 {selectedBatch.checkpoint.s3.global_dedupe.reviewed_candidate_count || 0} 道候选，移除 {selectedBatch.checkpoint.s3.global_dedupe.removed_duplicate_count || 0} 道重复题。</Typography.Paragraph>}
      {['auto_claimed', 'running'].includes(selectedBatch?.checkpoint?.s3?.global_dedupe?.status || '') && <Typography.Paragraph>正在自动整批去重；请稍后刷新。</Typography.Paragraph>}
      {selectedBatch?.checkpoint?.s3?.global_dedupe?.status === 'pending_budget' && <Typography.Paragraph type="warning">S2 预算不足以进行整批判重；请在上方提高预算阈值后点击“增加预算并整理整批”。</Typography.Paragraph>}
      {selectedBatch?.checkpoint?.s3?.global_dedupe?.status === 'failed' && <Typography.Paragraph type="warning">整批模型判重失败：{selectedBatch.checkpoint.s3.global_dedupe.error_message}</Typography.Paragraph>}
      <Typography.Paragraph type="secondary">每页只归一个主体页段；后续页作为跨页上下文重复传入模型。模型报告不完整后，重试会拆小主体页并向后多带上下文，所以传送范围会重叠。</Typography.Paragraph>
        {s2?.units?.map((unit) => <div key={unit.index}>
          <Typography.Paragraph>页段 {unit.index + 1}：主体为原文件第 {unit.core_first_page}–{unit.core_last_page} 页{unit.last_page > unit.core_last_page ? `，第 ${unit.core_last_page + 1}–${unit.last_page} 页仅作跨页上下文` : ''}；{unit.status === 'completed' ? `S2 当时提取 ${unit.candidate_count || 0} 题（下方展示当前候选）` : '待处理'}</Typography.Paragraph>
          {selectedBatch?.source_mode !== 'combined' && unit.source_answer_signals?.map((signal, index) => <Typography.Paragraph key={`answer-signal-${index}`} type="warning">第 {signal.page} 页可能含资料答案，请核对上传场景：{signal.evidence}</Typography.Paragraph>)}
          {!!unit.overlap_suspects?.length && <details><summary>S2 当时检出的疑似跨页重复 {unit.overlap_suspects.length} 对</summary>{unit.overlap_suspects.map((pair, index) => <Typography.Paragraph key={index}>候选 {pair.candidate_item_id} 与 {pair.other_item_id}；当前结果以整批模型判重为准。</Typography.Paragraph>)}</details>}
        </div>)}
      {selectedBatch?.source_mode === 'combined' && <details open={!reconciliation && sourceAnswers.length > 0}><summary>原文件提取答案 {sourceAnswers.length} 条（始终可查看）</summary>{sourceAnswers.length === 0 && <Typography.Paragraph type="warning">S2 未提取到资料答案，请核对答案页和页段记录。</Typography.Paragraph>}{sourceAnswers.map((answer, index) => <div key={index} className="question-import-answer"><Typography.Text type="secondary">第 {answer.page_start} 页，题号 {answer.original_number || '缺失'}：</Typography.Text><RichQuestionText text={answer.text} assets={[]} /></div>)}</details>}
      {reconciliation && <div>
         <Typography.Paragraph>资料答案 {reconciliation.source_answer_count} 条，已关联 {reconciliation.linked_answer_count} 条，跨页重复 {reconciliation.overlap_duplicate_answers?.length || 0} 条，待认领 {reconciliation.unassigned_answers.length} 条；答案均未由教师确认。</Typography.Paragraph>
         {reconciliation.overlap_duplicate_answers?.map((answer, index) => <div key={`overlap-${index}`} className="question-import-answer"><Typography.Text type="secondary">第 {answer.page_start} 页重复了第 {answer.matched_page} 页第 {answer.matched_original_number} 题的答案：</Typography.Text><RichQuestionText text={answer.text} assets={[]} /></div>)}
        {reconciliation.unassigned_answers.map((answer, index) => <div key={index} className="question-import-answer">
          <Typography.Text type="warning">第 {answer.page_start} 页，题号 {answer.original_number || '缺失'}（{answer.reason}）：</Typography.Text>
          <RichQuestionText text={answer.text} assets={[]} />
        </div>)}
      </div>}
      {!!reviewIssues.length && <div>
        <Typography.Title level={5}>按问题复核原页</Typography.Title>
        <Typography.Paragraph type="secondary">同一页段有多个问题时，可一次发送相关原页。模型确认同题或答案归属后会自动处理；无法确定的片段保留复核记录。</Typography.Paragraph>
        {reviewUnits.filter((unit) => unit.issue_keys.length > 1).map((unit) => <Typography.Paragraph key={`unit-${unit.unit_index}`}>
          第 {unit.unit_index + 1} 页段：待复核 {unit.issue_keys.length} 个问题，原页 {unit.page_numbers.join('、')}，候选 {unit.candidate_count} 条。
          {selectedBatch && <Button size="small" type="primary" loading={reviewingKey === `unit:${unit.unit_index}`} disabled={!unit.available || !s2Config?.ready || selectedBatch.status !== 'review_pending' || !!selectedBatch.files_purged_at} onClick={() => confirmUnitReview(selectedBatch, unit)}>一次复核本页段</Button>}
          {!unit.available && <Typography.Text type="warning"> 本页段超过单次范围，请逐条复核。</Typography.Text>}
        </Typography.Paragraph>)}
        {reviewIssues.map((issue) => <Typography.Paragraph key={issue.key}>
          {issue.summary}；原页 {issue.page_numbers.join('、') || '不可复核'}；候选 {issue.candidate_item_ids.length} 条{issue.candidate_total > issue.candidate_item_ids.length ? `（共 ${issue.candidate_total} 条，仅列部分供模型参考）` : ''}。
          {issue.review?.status === 'completed' && issue.review.advice && <span> 模型建议：{reviewVerdictLabels[issue.review.advice.verdict] || issue.review.advice.verdict}；依据第 {issue.review.advice.source_page} 页“{issue.review.advice.evidence_quote}”；{issue.review.advice.reason}{issue.review.advice.candidate_item_id ? `；候选 ${issue.review.advice.candidate_item_id}` : ''}；估算 ¥{issue.review.estimated_cny ?? '未知'}。</span>}
          {issue.review?.stale && <Typography.Text type="warning"> 候选内容已变化，此建议已过时，请以当前原页和候选为准。</Typography.Text>}
          {issue.review?.status === 'failed' && <Typography.Text type="warning"> 复核失败：{issue.review.error_message}；可能已产生费用。</Typography.Text>}
          {issue.review?.status === 'running' && <Typography.Text type="secondary"> 复核进行中，请稍后刷新。</Typography.Text>}
          {!issue.review && selectedBatch && <Button size="small" loading={reviewingKey === issue.key} disabled={!issue.page_numbers.length || !s2Config?.ready || selectedBatch.status !== 'review_pending' || !!selectedBatch.files_purged_at || (issue.kind === 'answer' && (!issue.candidate_item_ids.length || (issue.recall_method === 'number' && issue.candidate_total > issue.candidate_item_ids.length)))} onClick={() => confirmIssueReview(selectedBatch, issue)}>发送原页复核</Button>}
        </Typography.Paragraph>)}
      </div>}
      {!!duplicateCandidates.length && <Typography.Paragraph><Button size="small" onClick={() => setShowDuplicateCandidates((value) => !value)}>{showDuplicateCandidates ? '收起' : '显示'}疑似重复候选 {duplicateCandidates.length} 条</Button><Typography.Text type="secondary"> 原始记录仍保留；确认不是重复题时可展开核对。</Typography.Text></Typography.Paragraph>}
      <Table rowKey="id" dataSource={visibleCandidates} size="small" pagination={{ pageSize: 10 }} columns={[
        { title: '序号', dataIndex: 'ordinal', width: 70 },
        { title: '原题号', render: (_, item: Candidate) => item.payload.source.original_number || '未识别', width: 90 },
        { title: '原页', render: (_, item: Candidate) => `${item.payload.source.page_start ?? '?'}–${item.payload.source.page_end ?? '?'}`, width: 90 },
        ...(candidates.some((item) => item.payload.extraction?.classification_evidence)
          ? [{ title: '模型分类依据', render: (_: unknown, item: Candidate) => item.payload.extraction?.classification_evidence || '未记录', width: 180 }]
          : []),
        { title: '题干与选项', render: (_, item: Candidate) => <Space className="question-import-candidate" direction="vertical" style={{ width: '100%' }}><RichQuestionText key="stem" text={item.payload.content.question_text} assets={[]} mathFragments={item.payload.extraction?.math_fragments ?? []} />{item.payload.content.options.map((option, index) => <RichQuestionText key={`option-${index}-${option.key}`} text={`${option.key}. ${option.text}`} assets={[]} mathFragments={item.payload.extraction?.math_fragments ?? []} />)}{item.payload.extraction?.diagram_clues?.map((clue, index) => <Typography.Text type="warning" key={`clue-${index}`}>题图线索：{clue}</Typography.Text>)}{item.payload.extraction?.uncertain_fragments?.map((fragment, index) => <Typography.Text type="warning" key={`uncertain-${index}`}>模型不确定：{fragment}</Typography.Text>)}{item.payload.answers?.map((answer) => <div key={answer.answer_id} className="question-import-answer"><Typography.Text>资料答案（待确认）：</Typography.Text>{answer.text ? <RichQuestionText text={answer.text} assets={[]} /> : <Typography.Text type="secondary">未提供</Typography.Text>}</div>)}{pendingAnswers.filter((answer) => (answer.candidate_item_ids?.includes(item.id) || (!answer.candidate_item_ids?.length && answerNumber(answer.original_number) && answerNumber(answer.original_number) === answerNumber(item.payload.source.original_number)))).map((answer, index) => <div key={`pending-${index}`} className="question-import-answer"><Typography.Text type="warning">第 {answer.page_start} 页资料答案（同题号，归属待确认）：</Typography.Text><RichQuestionText text={answer.text} assets={[]} /></div>)}{item.payload.warnings?.filter((warning) => warning !== '模型报告不确定片段；请人工复核' || !item.payload.extraction?.uncertain_fragments?.length).map((warning, index) => <Typography.Text type="warning" key={`warning-${index}`}>{warning}</Typography.Text>)}</Space> },
      ]} />
    </Modal>
  </Space>
}

export default QuestionImportPage
