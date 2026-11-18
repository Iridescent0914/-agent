import { Alert, Button, Card, Drawer, Empty, Input, Modal, Select, Space, Tag, Typography } from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import { apiGet, apiPost, apiPut, queryString } from '../api/client'
import type { AssessmentTask, EquivalencePreview, EquivalenceVerdict, QualityFeedback, QualityReview } from '../types/assessment'
import type { AnswerDraft, SubmissionPaperItem } from '../types/submission'
import { AnswerView } from './AnswerView'

const prefix = '/internal/stage6'
const verdictLabels: Record<EquivalenceVerdict, string> = { equivalent: '等价', not_equivalent: '不等价', uncertain: '无法确定' }
const statusLabels = { queued: '排队中', running: '处理中', succeeded: '建议已返回', failed: '处理失败', cancelled: '已取消', stale: '输入已失效' }
const reviewLabels = { unreviewed: '未核对', correct: '确认正确', incorrect: '确认错误', uncertain: '暂无法确定' }

interface AnswerUnit { item_id: string; subquestion_no?: number; blank_no?: number; answer?: unknown }

function answerUnits(reference: unknown): AnswerUnit[] {
  const group = reference as { kind?: string; items?: AnswerUnit[] } | null
  if (group?.kind === 'group' && Array.isArray(group.items)) return group.items.map((part, index) => {
    const canonical = /^sub_([1-9]\d*)\.blank_([1-9]\d*)$/.exec(part.item_id)
    return { ...part,
      subquestion_no: canonical ? Number(canonical[1]) : part.subquestion_no ?? (/^[1-9]\d*$/.test(part.item_id) ? Number(part.item_id) : index + 1),
      blank_no: canonical ? Number(canonical[2]) : part.blank_no ?? 1,
    }
  }).sort((a, b) => a.subquestion_no - b.subquestion_no || a.blank_no - b.blank_no)
  return [{ item_id: 'answer', subquestion_no: 1, blank_no: 1 }]
}

function draftText(draft: AnswerDraft | undefined, itemId: string): string | null {
  if (!draft || draft.response_status !== 'answered') return null
  const group = draft.answer as { kind?: string; items?: AnswerUnit[] } | null
  const value = group?.kind === 'group' ? group.items?.find((part) => part.item_id === itemId)?.answer : draft.answer
  return typeof value === 'string' ? value : null
}

export function EquivalenceReview({ item, draft, active }: { item: SubmissionPaperItem; draft?: AnswerDraft; active: boolean }) {
  const units = useMemo(() => answerUnits(item.grading_answer_snapshot), [item.grading_answer_snapshot])
  const [open, setOpen] = useState(false)
  const [unitId, setUnitId] = useState(units[0]?.item_id ?? 'answer')
  const [preview, setPreview] = useState<EquivalencePreview | null>(null)
  const [tasks, setTasks] = useState<AssessmentTask[]>([])
  const [task, setTask] = useState<AssessmentTask | null>(null)
  const [feedback, setFeedback] = useState<QualityFeedback | null>(null)
  const [feedbackTaskId, setFeedbackTaskId] = useState<string | null>(null)
  const [reviewStatus, setReviewStatus] = useState<'correct' | 'incorrect' | 'uncertain'>('uncertain')
  const [gold, setGold] = useState<EquivalenceVerdict>('equivalent')
  const [errorType, setErrorType] = useState('evidence_insufficient')
  const [reviewer, setReviewer] = useState('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [action, setAction] = useState<'retry' | 'rerun' | 'cancel' | null>(null)
  const [actionReason, setActionReason] = useState('')
  const generation = useRef(0)
  const inputKey = JSON.stringify([
    item.attempt ? [item.attempt.id, item.attempt.revision_no] : draft,
    item.grading_answer_snapshot, item.grading_answer_rule_snapshot,
  ])

  useEffect(() => { setPreview(null); generation.current += 1 }, [inputKey, unitId, active])

  const restoreFeedback = (value: QualityFeedback, targetId: string) => {
    setFeedback(value)
    setFeedbackTaskId(targetId)
    setReviewStatus(value.current.review_status === 'unreviewed' ? 'uncertain' : value.current.review_status)
    setGold(value.current.gold?.verdict ?? 'equivalent')
    setErrorType(value.current.error_type ?? 'evidence_insufficient')
    setReviewer(value.current.reviewer ?? '')
    setReason(value.current.reason ?? '')
  }
  const feedbackUrl = (target: AssessmentTask) => `${prefix}/quality-feedback/equivalence/${target.id}/${encodeURIComponent(target.snapshot.item_id)}`
  const loadTasks = async (preferredId?: string) => {
    const values = await apiGet<AssessmentTask[]>(`${prefix}/equivalence-tasks${queryString({ paper_instance_item_id: item.id })}`)
    setTasks(values)
    setTask(values.find((value) => value.id === preferredId) ?? values.find((value) => value.snapshot.item_id === unitId) ?? null)
  }
  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    setError(null); setTask(null); setFeedback(null)
    apiGet<AssessmentTask[]>(`${prefix}/equivalence-tasks${queryString({ paper_instance_item_id: item.id })}`, controller.signal)
      .then((values) => { setTasks(values); setTask(values.find((value) => value.snapshot.item_id === unitId) ?? null) })
      .catch((value) => { if (!controller.signal.aborted) setError((value as Error).message) })
    return () => controller.abort()
  }, [open, unitId, item.id, inputKey, active])
  useEffect(() => {
    setFeedback(null)
    if (!open || !task?.result) return
    const controller = new AbortController()
    apiGet<QualityFeedback>(feedbackUrl(task), controller.signal).then((value) => restoreFeedback(value, task.id))
      .catch((value) => { if (!controller.signal.aborted) setError((value as Error).message) })
    return () => controller.abort()
  }, [open, task?.id, task?.status])
  useEffect(() => {
    if (!open || !task || !['queued', 'running'].includes(task.status)) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    let polls = 0
    const poll = async () => {
      try {
        const value = await apiGet<AssessmentTask>(`${prefix}/equivalence-tasks/${task.id}`, controller.signal)
        if (controller.signal.aborted) return
        setTask(value); setTasks((current) => current.map((entry) => entry.id === value.id ? value : entry))
        if (['queued', 'running'].includes(value.status) && ++polls < 30) timer = setTimeout(poll, 2000)
      } catch (value) { if (!controller.signal.aborted) setError((value as Error).message) }
    }
    timer = setTimeout(poll, 1000)
    return () => { controller.abort(); clearTimeout(timer) }
  }, [open, task?.id, task?.status])

  const requestBody = () => ({
    paper_instance_item_id: item.id, item_id: unitId,
    student_answer: item.attempt ? null : draftText(draft, unitId),
    expected_attempt_revision: item.attempt?.revision_no ?? null,
    expected_input_revision: preview?.input_revision ?? 0,
  })
  const inspect = async () => {
    const current = generation.current
    setBusy(true); setError(null); setPreview(null)
    try {
      const value = await apiPost<EquivalencePreview>(`${prefix}/equivalence-preview`, requestBody())
      if (current === generation.current) setPreview(value)
    } catch (value) { if (current === generation.current) setError((value as Error).message) }
    finally { setBusy(false) }
  }
  const create = async () => {
    setBusy(true); setError(null)
    try {
      const value = await apiPost<AssessmentTask>(`${prefix}/equivalence-tasks`, requestBody())
      setTask(value); setPreview(null)
      await loadTasks(value.id)
    } catch (value) { setError((value as Error).message); setPreview(null) }
    finally { setBusy(false) }
  }
  const saveFeedback = async () => {
    if (!task || !feedback || feedbackTaskId !== task.id) return
    setBusy(true); setError(null)
    try {
      const value = await apiPut<QualityFeedback>(feedbackUrl(task), {
        expected_revision: feedback.revision_no, review_status: reviewStatus,
        error_type: reviewStatus === 'incorrect' ? 'equivalence_error' : reviewStatus === 'uncertain' ? errorType : null,
        gold: reviewStatus === 'incorrect' ? { verdict: gold } : null, reviewer: reviewer.trim(), reason: reason.trim(),
      })
      restoreFeedback(value, task.id)
    } catch (value) {
      setError((value as Error).message)
      // Reload the saved revision and reset the form after a conflict; do not
      // silently submit the old edits with a new revision number.
      try { restoreFeedback(await apiGet<QualityFeedback>(feedbackUrl(task)), task.id) } catch { /* Keep the original error. */ }
    } finally { setBusy(false) }
  }
  const runAction = async () => {
    if (!task || !action || !actionReason.trim()) return
    setBusy(true); setError(null)
    try {
      const value = await apiPost<AssessmentTask>(`${prefix}/equivalence-tasks/${task.id}/${action}`, { reason: actionReason.trim() })
      setTask(value); setAction(null); setActionReason(''); await loadTasks(value.id)
    } catch (value) { setError((value as Error).message) }
    finally { setBusy(false) }
  }

  return <div className="equivalence-entry">
    <Button onClick={() => setOpen(true)}>答案等价建议</Button>
    <Drawer title={`第 ${item.order_no} 题 · 答案等价建议`} width="min(760px, 100vw)" open={open} onClose={() => setOpen(false)}>
      <Space direction="vertical" size="middle" className="equivalence-panel">
        <Alert type="info" showIcon message="模拟模式：验证逐空建议和质量核对流程" description="当前未执行真实数学判断。正式判分仍使用批改页原有复核操作。" />
        {error && <Alert type="error" showIcon message={error} />}
        <Typography.Text>选择作答空位</Typography.Text>
        <Select aria-label="选择作答空位" value={unitId} disabled={busy} style={{ width: '100%' }} onChange={setUnitId}
          options={units.map((unit, index) => ({ value: unit.item_id, label: `小题 ${unit.subquestion_no ?? index + 1} · 第 ${unit.blank_no ?? 1} 空` }))} />
        {!active && <Alert type="warning" message="该试卷已失效，可查看历史建议和反馈" />}
        <Space wrap>
          <Button onClick={() => void inspect()} loading={busy} disabled={!active || (!item.attempt && draft?.response_status === 'unset')}>检查本空是否需要建议</Button>
          <Button type="primary" onClick={() => void create()} loading={busy} disabled={!active || !preview?.eligible}>生成模拟建议</Button>
        </Space>
        {preview && <Card size="small" title="本空预检">
          <Alert type={preview.eligible ? 'info' : 'warning'} message={preview.message} />
          <Typography.Paragraph>参考答案：<AnswerView answer={preview.reference_answer} /></Typography.Paragraph>
          <Typography.Paragraph>学生答案：<AnswerView answer={preview.student_answer} /></Typography.Paragraph>
        </Card>}
        <Typography.Text>已保存的运行记录</Typography.Text>
        <Select aria-label="选择建议记录" value={task?.id} placeholder="暂无该空运行记录" style={{ width: '100%' }} disabled={busy}
          options={tasks.filter((entry) => entry.snapshot.item_id === unitId).map((entry) => ({ value: entry.id,
            label: `${new Date(entry.created_at).toLocaleString()} · ${statusLabels[entry.status]} · 输入版本 ${entry.input_revision}` }))}
          onChange={(id) => setTask(tasks.find((entry) => entry.id === id) ?? null)} />
        {!task && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本空尚无建议记录" />}
        {task && <Card size="small" title={<Space wrap><Tag>{statusLabels[task.status]}</Tag>{task.synthetic && <Tag color="orange">模拟结果</Tag>}</Space>}>
          <Space direction="vertical" className="equivalence-panel">
            <Typography.Text>运行时参考答案：<AnswerView answer={task.request_payload.reference_answer} /></Typography.Text>
            <Typography.Text>运行时学生答案：<AnswerView answer={task.request_payload.student_answer} /></Typography.Text>
            {task.result && <Alert type="info" message={`建议：${verdictLabels[task.result.verdict]}`} description={task.result.explanation} />}
            {task.status === 'stale' && <Alert type="warning" message="该建议的输入依据已失效，仅供追溯核对" />}
            {task.error_code && task.status === 'failed' && <Alert type="error" message={`建议任务失败：${task.error_code}`} />}
            <Typography.Text type="secondary">调用 {task.attempt_count} / {task.max_attempts}</Typography.Text>
            <Space wrap>
              <Button onClick={() => void loadTasks(task.id).catch((value) => setError((value as Error).message))} disabled={busy}>刷新记录</Button>
              {task.retryable && <Button onClick={() => setAction('retry')} disabled={busy}>重试</Button>}
              {active && ['succeeded', 'failed', 'cancelled'].includes(task.status) && <Button onClick={() => setAction('rerun')} disabled={busy}>重新运行</Button>}
              {['queued', 'running'].includes(task.status) && <Button onClick={() => setAction('cancel')} disabled={busy}>取消运行</Button>}
            </Space>
          </Space>
        </Card>}
        {task?.result && feedback && feedbackTaskId === task.id && <Card size="small" title="人工质量核对">
          <Space direction="vertical" className="equivalence-panel">
            <Typography.Text>当前：{reviewLabels[feedback.current.review_status]} · 反馈修订 {feedback.revision_no}</Typography.Text>
            <Select aria-label="模型质量核对状态" value={reviewStatus} style={{ width: '100%' }} disabled={busy}
              onChange={(value) => { setReviewStatus(value); if (value === 'uncertain') setErrorType('evidence_insufficient') }}
              options={(['correct', 'incorrect', 'uncertain'] as const).map((value) => ({ value, label: reviewLabels[value] }))} />
            {reviewStatus === 'incorrect' && <Select aria-label="人工正确等价结论" value={gold} style={{ width: '100%' }} disabled={busy} onChange={setGold}
              options={Object.entries(verdictLabels).map(([value, label]) => ({ value, label, disabled: value === task.result?.verdict }))} />}
            {reviewStatus === 'uncertain' && <Select aria-label="无法确定原因" value={errorType} style={{ width: '100%' }} disabled={busy} onChange={setErrorType}
              options={[{ value: 'evidence_insufficient', label: '证据不足' }, { value: 'reference_issue', label: '参考答案有待核对' }, { value: 'input_changed', label: '输入已经改变' }]} />}
            <Input aria-label="质量核对人" placeholder="核对人" value={reviewer} disabled={busy} onChange={(event) => setReviewer(event.target.value)} />
            <Input.TextArea aria-label="质量核对原因" placeholder="请填写核对原因" value={reason} disabled={busy} onChange={(event) => setReason(event.target.value)} rows={3} />
            <Button type="primary" onClick={() => void saveFeedback()} loading={busy}
              disabled={!reviewer.trim() || !reason.trim() || (reviewStatus === 'incorrect' && gold === task.result.verdict)}>保存质量反馈</Button>
            {feedback.history.length > 0 && <details><summary>查看质量反馈历史（{feedback.history.length}）</summary>
              {feedback.history.map((entry: QualityReview & { revision_no: number }) => <Typography.Paragraph key={entry.revision_no}>
                修订 {entry.revision_no} · {reviewLabels[entry.review_status]} · {entry.reviewer}：{entry.reason}
              </Typography.Paragraph>)}
            </details>}
          </Space>
        </Card>}
      </Space>
    </Drawer>
    <Modal title={action === 'cancel' ? '取消运行' : action === 'retry' ? '重试建议' : '重新运行建议'} open={action !== null}
      okText="保存操作" cancelText="返回" confirmLoading={busy} okButtonProps={{ disabled: !actionReason.trim() }} onOk={() => void runAction()} onCancel={() => { setAction(null); setActionReason('') }}>
      <Input.TextArea aria-label="运行操作原因" value={actionReason} placeholder="请填写操作原因" onChange={(event) => setActionReason(event.target.value)} />
    </Modal>
  </div>
}
