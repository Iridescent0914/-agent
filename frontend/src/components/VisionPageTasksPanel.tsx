import { Alert, Button, Card, Checkbox, Drawer, Space, Tag, Typography } from 'antd'
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
  VISION_CONFIG_INCOMPLETE: '请检查新版视觉模型的Key、接口地址和模型名称', CONFIG_CHANGED: '模型配置已改变，请重新建立任务',
  VISION_BUDGET_EXHAUSTED: '已达到视觉模型调用次数或金额限制',
  MODEL_USAGE_UNKNOWN: '存在用量待核对的调用，已暂停后续真实提取，请核对百炼账单',
  MODEL_USAGE_BOUND_EXCEEDED: '用量超过预估范围，已暂停后续真实提取',
  MODEL_AUTH_FAILED: '模型Key无效或与地域、业务空间不匹配', MODEL_PERMISSION_DENIED: '没有调用模型的权限',
  MODEL_NOT_FOUND: '模型不存在或当前地域不可用', MODEL_RATE_LIMITED: '调用限流或配额不足',
  MODEL_REQUEST_REJECTED: '模型拒绝请求，请检查模型和输入参数', MODEL_OUTPUT_TRUNCATED: '返回内容未完整生成，请检查输出长度限制',
  VISION_INPUT_TOO_LARGE: '本页信息超过当前支持范围，请减少单页题量或调整图片像素上限',
}
const taskStatus = (task: PageVisionTask) => task.synthetic ? statusLabels[task.effective_status]
  : ({ queued: '等待提取', running: '提取处理中', needs_review: '提取完成 · 待核对',
       failed: '处理失败', stale: '依据已变化 · 结果失效', cancelled: '已取消' })[task.effective_status]

export function VisionPageTasksPanel({ scope, collection, reviewer, reason }: {
  scope: string; collection: AnswerPageCollection; reviewer: string; reason: string
}) {
  const [data, setData] = useState<PageVisionTasks | null>(null)
  const [detail, setDetail] = useState<PageVisionTaskDetail | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [externalReviewed, setExternalReviewed] = useState(false)
  const abort = useRef(new AbortController())
  const path = `${scope}/vision-tasks`
  useEffect(() => {
    const controller = new AbortController()
    abort.current = controller
    setData(null); setDetail(null); setError(null); setBusy(false); setExternalReviewed(false)
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
      external_images_reviewed: externalReviewed,
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
  const real = data?.configuration.provider === 'bailian'
  const enabled = collection.is_active && !data?.configuration.error_code
    && (data?.configuration.provider === 'fake' || data?.configuration.real_provider_available === true)
  const history = data?.history.filter(t => !t.is_current) ?? []
  const row = (task: PageVisionTask) => <Card key={task.id} size="small" title={`答题页 ${task.page_no} · 第 ${task.generation} 次任务`}>
    <Space direction="vertical" className="full-width">
      <Space wrap><Tag color={task.effective_status === 'failed' ? 'red' : 'orange'}>{taskStatus(task)}</Tag>
        <Tag>{task.synthetic ? '模拟' : `真实 · ${task.model}`}</Tag>
        <Typography.Text>已调用 {task.attempt_count} / {task.max_attempts} 次</Typography.Text></Space>
      {(task.binding_error || task.error_code) && <Typography.Text type="warning">{errorLabels[task.binding_error || task.error_code || ''] ?? task.binding_error ?? task.error_code}</Typography.Text>}
      {task.identity_check?.status === 'unconfirmed' && <Typography.Text type="secondary">{task.synthetic ? '模拟输出未读取纸面编号，页身份尚未确认。' : '纸面编号未完整识别，页身份尚未确认，请对照原图核对。'}</Typography.Text>}
      <Space wrap>
        <Button disabled={busy} onClick={() => showDetail(task)}>查看本页记录</Button>
        {task.is_current && enabled && ['failed', 'cancelled'].includes(task.status) && task.attempt_count < task.max_attempts && <Button disabled={busy || !auditReady} onClick={() => action(task, 'retry')}>重试本页</Button>}
        {task.is_current && enabled && ['queued', 'running'].includes(task.status) && <Button disabled={busy || !auditReady} onClick={() => action(task, 'cancel')}>取消本页任务</Button>}
        {task.is_current && enabled && !['queued', 'running'].includes(task.status) && <Button disabled={busy || !auditReady} onClick={() => action(task, 'rerun')}>{task.synthetic ? '重新模拟本页' : '重新提取本页'}</Button>}
      </Space>
    </Space>
  </Card>
  return <Card title={`4. 逐页提取任务${real ? '' : '（模拟）'}`} extra={<Button aria-label="刷新提取任务" disabled={busy} onClick={refresh}>刷新任务</Button>}>
    <Space direction="vertical" className="full-width">
      {real ? <Alert type="info" showIcon message={`真实视觉提取 · ${data?.configuration.model ?? ''}`} description="将当前有效答题页照片发送至已配置的视觉模型，按页提取答案；结果待核对，尚不进入成绩或画像。重试和重新提取可能产生费用。" />
        : <Alert type="info" showIcon message="当前验证任务流程，尚未读取手写答案" description="模拟结果统一标为无法辨认、纸面编号未确认，不用于判分。补拍后重新建立任务，未变化的页面可以复用。" />}
      {error && <Alert type="error" message="提取任务操作失败" description={error} />}
      {data?.configuration.error_code && <Alert type="warning" message="多页视觉配置不可用" description={errorLabels[data.configuration.error_code] ?? '供应商不受支持，不会自动切换到其他模型。'} />}
      {real && data?.budget && <Typography.Text type={data.budget.unknown_calls ? 'warning' : 'secondary'}>
        累计调用 {data.budget.calls} / {data.budget.max_calls} 次 · 预估费用 {data.budget.estimated_cost_cny} 元 · 预占 {data.budget.reserved_cost_cny} 元 · 上限 {data.budget.max_budget_cny} 元{data.budget.unknown_calls > 0 ? `；${data.budget.unknown_calls} 次用量待核对，已暂停后续真实调用` : ''}。此限额不会按月重置。
      </Typography.Text>}
      {real && <Checkbox checked={externalReviewed} onChange={event => setExternalReviewed(event.target.checked)}>已核对当前照片中的姓名、学号等个人信息已遮挡，可发送至已配置的模型</Checkbox>}
      <Button type="primary" aria-label={real ? '运行真实提取' : '运行模拟提取'} loading={busy} disabled={!enabled || !collection.collection_ready || !auditReady || (real && (!externalReviewed || Boolean(data?.budget?.unknown_calls)))} onClick={start}>{real ? '运行真实提取' : '运行模拟提取'}</Button>
      {!collection.collection_ready && <Typography.Text type="secondary">请先补齐有效答题页照片，再启动提取。</Typography.Text>}
      {data?.current_tasks.map(row)}
      {data && data.current_tasks.length === 0 && <Typography.Text type="secondary">当前有效照片尚无提取任务。</Typography.Text>}
      {history.length > 0 && <details><summary>历史任务（{history.length}）</summary><Space direction="vertical" className="full-width">{history.map(row)}</Space></details>}
    </Space>
    <Drawer title={detail?.synthetic === false ? '本页提取记录' : '本页模拟提取记录'} open={detail !== null} onClose={() => setDetail(null)} width={640}>
      {detail && <Space direction="vertical" className="full-width">
        <Alert type="info" message={detail.synthetic ? '模拟记录不能作为真实识别或质量验收证据' : '逐页任务仅保存提取结果，请在下方整卷答案核对中确认录入；已录入的成绩请到批改页查看'} />
        <Typography.Text>答题页 {detail.page_no} · {taskStatus(detail)}</Typography.Text>
        {detail.result?.answers.map(answer => {
          const slot = detail.snapshot.slots.find(s => s.slot_token === answer.slot_token)
          return <Typography.Text key={answer.slot_token}>第 {slot?.order_no} 题 · 第 {slot?.subquestion_no} 小题 · 第 {slot?.blank_no} 空：{({ readable: '清晰', blank: '留白', missing: '缺失', illegible: '无法辨认', ambiguous: '歧义' } as Record<string, string>)[answer.recognition_status]} {answer.answer}</Typography.Text>
        })}
        {detail.calls.map(call => <details key={call.id}><summary>调用 {call.call_no} · {call.status}{call.late_response ? ' · 迟到响应，未采用' : ''} · {detail.synthetic ? `模拟费用 ${call.usage.cost_cny} 元` : call.usage.cost_cny === null ? '费用待核对' : `预估费用 ${call.usage.cost_cny} 元`}</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{call.raw_response ?? '尚无返回内容'}</pre></details>)}
        {detail.action_history.map((event, i) => <Typography.Text key={i}>{event.reviewer} · {event.reason}</Typography.Text>)}
      </Space>}
    </Drawer>
  </Card>
}
