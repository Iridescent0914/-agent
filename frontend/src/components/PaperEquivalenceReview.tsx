import { Alert, Button, Card, Drawer, Input, Modal, Space, Tag, Typography, message } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { apiGet, apiPost } from '../api/client'
import type { PaperEquivalenceReport, PaperEquivalenceUnit, Stage6Status } from '../types/assessment'
import type { SubmissionDetail } from '../types/submission'
import { AnswerView } from './AnswerView'
import { AnswerJudgmentContext } from './AnswerJudgmentContext'
import { AdoptedEquivalenceReview } from './AdoptedEquivalenceReview'

const verdictLabel = { equivalent: '等价', not_equivalent: '不等价', uncertain: '无法确定' }
const contextualLabel = { equivalent: '本空可接受', not_equivalent: '本空不可接受', uncertain: '无法确定' }
const taskLabel = { queued: '排队中', running: '判断中', succeeded: '已完成', failed: '失败', stale: '已过期', cancelled: '已取消' }

function adoptionBlockReason(unit: PaperEquivalenceUnit): string | null {
  const task = unit.task
  if (!task) return '尚未生成当前有效建议，请检查或发起整卷判断'
  if (task.adoptable) return null
  if (task.adoption_blocker) return task.adoption_blocker.message
  // Older responses may not include the detailed server-side reason.
  if (task.synthetic) return '模拟结果不能用于正式评分'
  if (['queued', 'running'].includes(task.status)) return '等价判断尚未完成，请等待或刷新整卷'
  if (task.result?.verdict === 'uncertain') return '模型返回“无法确定”，请使用本题原有人工复核'
  if (task.status !== 'succeeded') return '任务未成功完成，请查看本题调用记录'
  return '建议尚未满足采用条件，请刷新或查看本题调用记录'
}

export function PaperEquivalenceReview({ data, onAdopted }: { data: SubmissionDetail; onAdopted: (value: SubmissionDetail) => void }) {
  const [open, setOpen] = useState(false)
  const [report, setReport] = useState<PaperEquivalenceReport | null>(null)
  const [status, setStatus] = useState<Stage6Status | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [reviewer, setReviewer] = useState('')
  const [reason, setReason] = useState('')
  const [pending, setPending] = useState<'schedule' | number | null>(null)
  const generation = useRef(0)
  const inputKey = JSON.stringify(data.items.map(i => [i.id, i.attempt?.revision_no, i.grading_answer_snapshot, i.grading_answer_rule_snapshot]))
  const base = `/internal/stage6/paper-instances/${data.paper_instance_id}`
  const audited = !!reviewer.trim() && reason.trim().length >= 2

  useEffect(() => { setReport(null); setPending(null); generation.current++ }, [inputKey, data.paper_instance_id])
  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    apiGet<Stage6Status>('/internal/stage6/status', controller.signal).then(setStatus)
      .catch(value => { if (!controller.signal.aborted) setError((value as Error).message) })
    return () => controller.abort()
  }, [open, report?.new_task_count])

  const inspect = async () => {
    const current = generation.current
    setBusy(true); setError(null)
    try {
      const value = await apiGet<PaperEquivalenceReport>(`${base}/equivalence`)
      if (current === generation.current) setReport(value)
    } catch (value) { if (current === generation.current) setError((value as Error).message) }
    finally { setBusy(false) }
  }

  const hasRunning = report?.units.some(u => u.task && ['queued', 'running'].includes(u.task.status)) ?? false
  useEffect(() => {
    if (!open || !hasRunning || busy) return
    const controller = new AbortController()
    const current = generation.current
    let timer: ReturnType<typeof setTimeout>
    let polls = 0
    const poll = async () => {
      try {
        const value = await apiGet<PaperEquivalenceReport>(`${base}/equivalence`, controller.signal)
        if (controller.signal.aborted || current !== generation.current) return
        setReport(value)
        if (value.units.some(u => u.task && ['queued', 'running'].includes(u.task.status)) && ++polls < 30) timer = setTimeout(poll, 2000)
      } catch (value) { if (!controller.signal.aborted) setError((value as Error).message) }
    }
    timer = setTimeout(poll, 1500)
    return () => { controller.abort(); clearTimeout(timer) }
  }, [open, hasRunning, busy, base, inputKey])

  const perform = async () => {
    if (!report || pending === null || !audited) return
    const current = generation.current
    const payload = { expected_input_sha256: report.input_sha256, reviewer: reviewer.trim(), reason: reason.trim() }
    setBusy(true); setError(null)
    try {
      if (pending === 'schedule') {
        const value = await apiPost<PaperEquivalenceReport>(`${base}/equivalence-tasks`, payload)
        if (current === generation.current) setReport(value)
      } else {
        const taskIds = report.units.filter(u => u.order_no === pending && u.eligible && u.task?.adoptable).map(u => u.task!.id)
        const value = await apiPost<SubmissionDetail>(`${base}/equivalence-adoptions`, { ...payload, task_ids: taskIds })
        if (current === generation.current) { onAdopted(value); setReport(null); message.success('已采用本题建议，评分与画像已更新') }
      }
      setPending(null)
    } catch (value) {
      if (current === generation.current) { setError((value as Error).message); setReport(null); setPending(null) }
    } finally { setBusy(false) }
  }

  const numbers = [...new Set(report?.units.filter(u => u.eligible).map(u => u.order_no) ?? [])]
  const configured = status?.enabled && (status.provider === 'fake' || status.real_provider_available)
  return <>
    <Button disabled={!data.is_active} onClick={() => { setOpen(true); setError(null) }}>整卷等价判断</Button>
    <Drawer title="整卷等价判断" width="min(900px, 100vw)" open={open} onClose={() => setOpen(false)}>
      <Space direction="vertical" className="full-width" size="middle">
        <Alert type="info" showIcon message="先检查整卷，再统一发起必要的逐空判断"
          description="规则已确认的答案直接保留；结合发布题干和目标空位，判断学生答案在本空是否可接受。模型建议经教师按题确认后，才更新评分与画像。" />
        {status && <Typography.Text>{status.provider === 'fake' ? '模拟模式：结果不能用于正式评分' : `文本模型：${status.model ?? '尚未配置'}`}</Typography.Text>}
        {status?.budget && <Typography.Text>本库累计调用 {status.budget.calls}/{status.budget.max_calls} 次，估算 ¥{status.budget.estimated_cost_cny.toFixed(4)} / ¥{status.budget.max_budget_cny}。
          {status.budget.unknown_calls > 0 && '存在用量未知的调用，已暂停新调用，请核对账单。'}</Typography.Text>}
        {error && <Alert type="error" showIcon message={error} />}
        {data.items.map(item => <AdoptedEquivalenceReview key={item.id} item={item} />)}
        <Button onClick={() => void inspect()} loading={busy}>检查或刷新整卷</Button>
        <Input aria-label="整卷判断核对人" placeholder="核对人" value={reviewer} disabled={busy} onChange={e => setReviewer(e.target.value)} />
        <Input.TextArea aria-label="整卷判断原因" placeholder="发起或采用原因（至少两个字）" value={reason} disabled={busy} onChange={e => setReason(e.target.value)} />
        {report && <>
          <Typography.Text>共 {report.units.length} 空，需等价判断 {report.candidate_count} 空，需新建任务 {report.new_task_count} 个。</Typography.Text>
          {report.created_count !== undefined && <Typography.Text>本次新建 {report.created_count} 个，复用 {report.reused_count} 个。</Typography.Text>}
          <Button type="primary" disabled={busy || !audited || !configured || !report.new_task_count || !!status?.budget?.unknown_calls}
            onClick={() => setPending('schedule')}>{status?.provider === 'fake' ? '统一生成模拟建议' : '统一发起等价判断（调用模型）'}</Button>
          {report.candidate_count === 0 && <Alert type="success" message="本卷当前没有需要模型处理的候选空位" description="无需为规则已确认的答案重复调用模型；受约束或无法处理的答案仍可人工复核。" />}
          {numbers.map(number => {
            const units = report.units.filter(u => u.order_no === number && u.eligible)
            const ready = units.length > 0 && units.every(u => u.task?.adoptable)
            const blockers = units.flatMap(u => {
              const reason = adoptionBlockReason(u)
              return reason ? [`第 ${u.subquestion_no} 小题 · 第 ${u.blank_no} 空：${reason}`] : []
            })
            if (!audited) blockers.unshift('请填写核对人及至少两个字的原因')
            if (busy) blockers.unshift('正在处理，请稍候')
            return <Card key={number} size="small" title={`第 ${number} 题 · ${units.length} 个候选空`}>
              <Space direction="vertical" className="full-width" size="middle">
                {units.map(u => <div key={u.item_id}>
                  <Typography.Paragraph>第 {u.subquestion_no} 小题 · 第 {u.blank_no} 空</Typography.Paragraph>
                  <Typography.Paragraph>标答：<AnswerView answer={u.reference_answer} />；学生答案：<AnswerView answer={u.student_answer} /></Typography.Paragraph>
                  <Typography.Paragraph type="secondary">{u.message}</Typography.Paragraph>
                  <AnswerJudgmentContext context={u.answer_context} />
                  {u.task && <Space direction="vertical">
                    <Space><Tag>{taskLabel[u.task.status]}</Tag>{u.task.synthetic && <Tag color="orange">模拟结果</Tag>}</Space>
                    {u.task.result && <Typography.Text>建议：{(u.task.result.schema_version === 'stage6-equivalence-response-v2' ? contextualLabel : verdictLabel)[u.task.result.verdict]} · {u.task.result.explanation}</Typography.Text>}
                    {u.task.error_code && <Typography.Text type="danger">{u.task.error_code}；可在本题“答案等价建议”查看调用及重试。</Typography.Text>}
                  </Space>}
                </div>)}
                <Button disabled={busy || !audited || !ready} onClick={() => setPending(number)}>采用第 {number} 题建议</Button>
                {blockers.length > 0 && <Alert type="warning" showIcon message="本题暂不能采用建议"
                  description={<>{blockers.map(reason => <Typography.Paragraph key={reason}>{reason}</Typography.Paragraph>)}</>} />}
              </Space>
            </Card>
          })}
          <details><summary>查看跳过的空位与原因</summary>
            {report.units.filter(u => !u.eligible).map(u => <Typography.Paragraph key={`${u.paper_instance_item_id}:${u.item_id}`}>
              第 {u.order_no} 题 · 小题 {u.subquestion_no} · 第 {u.blank_no} 空：{u.message}
            </Typography.Paragraph>)}
          </details>
        </>}
      </Space>
    </Drawer>
    <Modal title={pending === 'schedule' ? '确认统一发起等价判断' : `确认采用第 ${pending ?? ''} 题建议`}
      open={pending !== null} onCancel={() => setPending(null)} onOk={() => void perform()} confirmLoading={busy}
      okText={pending === 'schedule' ? '确认发起' : '确认采用并更新评分'} cancelText="返回核对" okButtonProps={{ disabled: !audited }}>
      {pending === 'schedule'
        ? <Typography.Paragraph>将新建 {report?.new_task_count ?? 0} 个逐空任务，已有任务复用。{status?.provider === 'fake' ? '当前为模拟模式。' : '后台会调用已配置的文本模型并产生费用，沿用现有调用次数和费用上限。'}</Typography.Paragraph>
        : <Typography.Paragraph>请确认本题全部候选空的建议。采用后保留学生原始答案和其他空的规则结果，追加评分修订，并更新画像。</Typography.Paragraph>}
    </Modal>
  </>
}
