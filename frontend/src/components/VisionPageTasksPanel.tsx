import { Alert, Button, Card, Drawer, Space, Tag, Typography } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { apiGet, apiPost } from '../api/client'
import type { AnswerPageCollection } from '../types/answerPages'
import type { PageVisionTask, PageVisionTaskDetail, PageVisionTasks } from '../types/visionPages'

const statusLabels = {
  queued: '等待模拟提取', running: '模拟处理中', needs_review: '模拟完成 · 待核对',
  failed: '处理失败', stale: '照片已变化 · 结果失效', cancelled: '已取消',
}
const errorLabels: Record<string, string> = {
  PAGE_IDENTITY_MISMATCH: '照片中观察到的试卷或答题页编号不符，请核对并补拍',
  PAGE_INPUT_CHANGED: '本页有效照片已变化', INPUT_STALE: '试卷或学生已失效',
  MODEL_SCHEMA_INVALID: '返回格式、空位或选项字母不符合要求', MODEL_TIMEOUT: '处理超时',
  MODEL_PROVIDER_FAILED: '提取调用失败', MAX_ATTEMPTS_REACHED: '已达到重试上限',
  IMAGE_INTEGRITY_ERROR: '图片校验失败，请补拍或重新上传', MODEL_RESPONSE_TOO_LARGE: '返回结果超过长度限制',
}

export function VisionPageTasksPanel({ scope, collection, reviewer, reason }: {
  scope: string; collection: AnswerPageCollection; reviewer: string; reason: string
}) {
  const [data, setData] = useState<PageVisionTasks | null>(null)
  const [detail, setDetail] = useState<PageVisionTaskDetail | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const abort = useRef(new AbortController())
  const path = `${scope}/vision-tasks`
  useEffect(() => {
    const controller = new AbortController()
    abort.current = controller
    setData(null); setDetail(null); setError(null); setBusy(false)
    apiGet<PageVisionTasks>(path, controller.signal).then(value => {
      if (!controller.signal.aborted) setData(value)
    }).catch(e => { if (!controller.signal.aborted) setError((e as Error).message) })
    return () => controller.abort()
  }, [path, collection.revision_no, collection.effective_input_sha256])

  const perform = async (action: (signal: AbortSignal) => Promise<void>) => {
    const signal = abort.current.signal
    setBusy(true); setError(null)
    try { await action(signal) }
    catch (e) { if (!signal.aborted) setError((e as Error).message) }
    finally { if (!signal.aborted) setBusy(false) }
  }
  const refresh = () => perform(async signal => {
    const value = await apiGet<PageVisionTasks>(path, signal)
    if (!signal.aborted) setData(value)
  })
  const start = () => perform(async signal => {
    const value = await apiPost<PageVisionTasks>(path, {
      expected_collection_revision: collection.revision_no,
      expected_input_sha256: collection.effective_input_sha256,
      reviewer: reviewer.trim(), reason: reason.trim(),
    }, signal)
    if (!signal.aborted) setData(value)
  })
  const action = (task: PageVisionTask, kind: 'retry' | 'cancel' | 'rerun') => perform(async signal => {
    await apiPost(`${path}/${task.id}/${kind}`, {
      expected_revision_no: task.revision_no, reviewer: reviewer.trim(), reason: reason.trim(),
    }, signal)
    const value = await apiGet<PageVisionTasks>(path, signal)
    if (!signal.aborted) setData(value)
  })
  const showDetail = (task: PageVisionTask) => perform(async signal => {
    const value = await apiGet<PageVisionTaskDetail>(`${path}/${task.id}`, signal)
    if (!signal.aborted) setDetail(value)
  })
  const auditReady = reviewer.trim().length > 0 && reason.trim().length >= 2
  const enabled = collection.is_active && data?.configuration.provider === 'fake' && !data.configuration.error_code
  const history = data?.history.filter(t => !t.is_current) ?? []
  const row = (task: PageVisionTask) => <Card key={task.id} size="small" title={`答题页 ${task.page_no} · 第 ${task.generation} 次任务`}>
    <Space direction="vertical" className="full-width">
      <Space wrap><Tag color={task.effective_status === 'failed' ? 'red' : 'orange'}>{statusLabels[task.effective_status]}</Tag>
        <Typography.Text>已调用 {task.attempt_count} / {task.max_attempts} 次</Typography.Text></Space>
      {(task.binding_error || task.error_code) && <Typography.Text type="warning">{errorLabels[task.binding_error || task.error_code || ''] ?? task.binding_error ?? task.error_code}</Typography.Text>}
      {task.identity_check?.status === 'unconfirmed' && <Typography.Text type="secondary">模拟输出未读取纸面编号，页身份尚未确认。</Typography.Text>}
      <Space wrap>
        <Button disabled={busy} onClick={() => showDetail(task)}>查看本页记录</Button>
        {task.is_current && enabled && ['failed', 'cancelled'].includes(task.status) && task.attempt_count < task.max_attempts && <Button disabled={busy || !auditReady} onClick={() => action(task, 'retry')}>重试本页</Button>}
        {task.is_current && enabled && ['queued', 'running'].includes(task.status) && <Button disabled={busy || !auditReady} onClick={() => action(task, 'cancel')}>取消本页任务</Button>}
        {task.is_current && enabled && !['queued', 'running'].includes(task.status) && <Button disabled={busy || !auditReady} onClick={() => action(task, 'rerun')}>重新模拟本页</Button>}
      </Space>
    </Space>
  </Card>
  return <Card title="4. 逐页提取任务（模拟）" extra={<Button aria-label="刷新提取任务" disabled={busy} onClick={refresh}>刷新任务</Button>}>
    <Space direction="vertical" className="full-width">
      <Alert type="info" showIcon message="当前验证任务流程，尚未读取手写答案" description="模拟结果统一标为无法辨认、纸面编号未确认，不用于判分。补拍后重新建立任务，未变化的页面可以复用。" />
      {error && <Alert type="error" message="提取任务操作失败" description={error} />}
      {data?.configuration.error_code && <Alert type="warning" message="多页视觉供应商尚不可用" description="当前只支持离线模拟，不会自动切换到其他模型。" />}
      <Button type="primary" aria-label="运行模拟提取" loading={busy} disabled={!enabled || !collection.collection_ready || !auditReady} onClick={start}>运行模拟提取</Button>
      {!collection.collection_ready && <Typography.Text type="secondary">请先补齐有效答题页照片，再启动提取。</Typography.Text>}
      {data?.current_tasks.map(row)}
      {data && data.current_tasks.length === 0 && <Typography.Text type="secondary">当前有效照片尚无提取任务。</Typography.Text>}
      {history.length > 0 && <details><summary>历史任务（{history.length}）</summary><Space direction="vertical" className="full-width">{history.map(row)}</Space></details>}
    </Space>
    <Drawer title="本页模拟提取记录" open={detail !== null} onClose={() => setDetail(null)} width={640}>
      {detail && <Space direction="vertical" className="full-width">
        <Alert type="warning" message="模拟记录不能作为真实识别或质量验收证据" />
        <Typography.Text>答题页 {detail.page_no} · {statusLabels[detail.effective_status]}</Typography.Text>
        {detail.result?.answers.map(answer => {
          const slot = detail.snapshot.slots.find(s => s.slot_token === answer.slot_token)
          return <Typography.Text key={answer.slot_token}>第 {slot?.order_no} 题 · 第 {slot?.subquestion_no} 小题 · 第 {slot?.blank_no} 空：{({ readable: '清晰', blank: '留白', missing: '缺失', illegible: '无法辨认', ambiguous: '歧义' } as Record<string, string>)[answer.recognition_status]} {answer.answer}</Typography.Text>
        })}
        {detail.calls.map(call => <details key={call.id}><summary>调用 {call.call_no} · {call.status}{call.late_response ? ' · 迟到响应，未采用' : ''} · 模拟费用 {call.usage.cost_cny} 元</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{call.raw_response ?? '尚无返回内容'}</pre></details>)}
        {detail.action_history.map((event, i) => <Typography.Text key={i}>{event.reviewer} · {event.reason}</Typography.Text>)}
      </Space>}
    </Drawer>
  </Card>
}
