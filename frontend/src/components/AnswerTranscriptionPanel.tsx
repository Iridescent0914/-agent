import { Alert, Button, Card, Checkbox, Image, Input, Select, Space, Tag, Typography, message } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiGet, apiPost, apiPut, apiUrl } from '../api/client'
import type { AnswerPageCollection } from '../types/answerPages'
import { AnswerView } from './AnswerView'
import { PaperRecognitionReviewPanel } from './PaperRecognitionReviewPanel'

type Status = 'readable' | 'blank' | 'missing' | 'illegible' | 'ambiguous'
interface Unit {
  slot_key: string; order_no: number; subquestion_no: number; blank_no: number; page_no: number
  source_module: string; recognition_status: Status; answer: string | null
  original_status: Status; original_answer: string | null; confidence: number | null
}
interface Transcription {
  id: string | null; revision_no: number; input_sha256?: string; stale?: boolean; stale_message?: string
  submission_id?: string | null; existing_submission_id?: string | null; can_submit: boolean
  blockers?: string[]; units: Unit[]; confirmed_page_nos?: number[]
  pages?: Array<{ page_no: number; image_url: string | null; synthetic: boolean; identity_check: { status: string } }>
  history: Array<{ revision_no: number; action: string; reviewer: string; reason: string }>
}
const statusLabels = { readable: '已作答', blank: '确认留白', missing: '缺失', illegible: '无法辨认', ambiguous: '存在歧义' }

export function AnswerTranscriptionPanel({ scope, collection, paperId, reviewer, reason, onReviewerChange, onReasonChange }: {
  scope: string; collection: AnswerPageCollection; paperId: string; reviewer: string; reason: string
  onReviewerChange: (value: string) => void; onReasonChange: (value: string) => void
}) {
  const [data, setData] = useState<Transcription | null>(null)
  const [units, setUnits] = useState<Unit[]>([])
  const [pages, setPages] = useState<number[]>([])
  const [dirty, setDirty] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitted, setSubmitted] = useState(false)
  const abort = useRef(new AbortController())
  const install = (value: Transcription) => {
    setData(value); setUnits(value.units); setPages(value.confirmed_page_nos ?? [])
    setDirty(false); setConfirmed(false); setSubmitted(Boolean(value.submission_id))
  }
  useEffect(() => {
    const controller = new AbortController()
    abort.current = controller
    setData(null); setUnits([]); setPages([]); setDirty(false); setConfirmed(false); setError(null); setBusy(false)
    apiGet<Transcription>(`${scope}/transcription`, controller.signal)
      .then(value => { if (!controller.signal.aborted) install(value) })
      .catch(value => { if (!controller.signal.aborted) setError((value as Error).message) })
    return () => controller.abort()
  }, [scope, collection.revision_no, collection.effective_input_sha256])
  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError(null)
    try { await action() } catch (value) {
      if (!abort.current.signal.aborted) { setError((value as Error).message); setConfirmed(false) }
    } finally { if (!abort.current.signal.aborted) setBusy(false) }
  }
  const audit = { reviewer: reviewer.trim(), reason: reason.trim(), expected_revision_no: data?.revision_no ?? 0 }
  const auditReady = audit.reviewer.length > 0 && audit.reason.length >= 2
  const mergeBlockers = [
    ...(!audit.reviewer ? ['请填写本次整卷核对人。'] : []),
    ...(audit.reason.length < 2 ? ['请填写至少2个字的整卷核对原因。'] : []),
    ...(!data ? [error ? '核对清单未加载成功，请点击刷新核对清单重试。' : '正在加载核对清单，请稍候。'] : []),
    ...(!collection.collection_ready ? ['请先补齐当前有效答题页照片；如已补齐，请刷新上方照片集合。'] : []),
    ...(submitted || data?.existing_submission_id ? ['本卷已有作答，请进入批改页查看或修正。'] : []),
  ]
  const locked = busy || submitted || Boolean(data?.existing_submission_id) || Boolean(data?.stale)
  const mutate = (key: string, patch: Partial<Unit>) => {
    setUnits(values => values.map(u => u.slot_key === key ? { ...u, ...patch } : u))
    setDirty(true); setConfirmed(false)
    const pageNo = units.find(u => u.slot_key === key)?.page_no
    setPages(values => values.filter(n => n !== pageNo))
  }
  return <Card title="5. 整卷答案核对与录入" aria-label="整卷答案核对">
    <Space direction="vertical" className="full-width" size="middle">
      <Typography.Paragraph>逐页提取完成后，合并整卷答案并对照照片核对。保存核对清单不会判分；确认录入后才进入批改并更新画像。</Typography.Paragraph>
      {error && <Alert type="error" showIcon message="答案核对操作失败" description={error} />}
      <Input aria-label="整卷核对人" placeholder="本次核对人（必填，刷新后需重新填写）" value={reviewer} maxLength={100}
        disabled={busy || submitted || Boolean(data?.existing_submission_id)} onChange={e => onReviewerChange(e.target.value)} />
      <Input aria-label="整卷核对原因" placeholder="整卷核对原因（至少2个字）" value={reason} maxLength={500}
        disabled={busy || submitted || Boolean(data?.existing_submission_id)} onChange={e => onReasonChange(e.target.value)} />
      {!busy && mergeBlockers.length > 0 && <Alert type="info" showIcon message="合并前请完成以下事项"
        description={<>{mergeBlockers.map(value => <div key={value}>{value}</div>)}</>} />}
      <Space wrap>
        <Button loading={busy} disabled={busy} onClick={() => run(async () => install(await apiGet<Transcription>(`${scope}/transcription`, abort.current.signal)))}>刷新核对清单（放弃未保存修改）</Button>
        <Button disabled={busy || mergeBlockers.length > 0}
          onClick={() => run(async () => install(await apiPost<Transcription>(`${scope}/transcription/merge`, {
            ...audit, expected_collection_revision: collection.revision_no, expected_input_sha256: collection.effective_input_sha256,
          }, abort.current.signal)))}>合并最新整卷提取结果</Button>
      </Space>
      {data?.stale && <Alert type="warning" showIcon message="核对清单已过期" description={data.stale_message} />}
      {(submitted || data?.existing_submission_id) && <Alert type="success" showIcon message="这份试卷已有作答记录" description="后续修正请在批改页操作。原提取结果和核对记录会保留。"
        action={<Button type="primary"><Link to={`/paper-instances/${paperId}/grading`}>查看批改结果</Link></Button>} />}
      {data?.id && <>
        {data.pages?.some(p => p.synthetic) && <Alert type="warning" showIcon message="模拟结果不能录入正式批改" />}
        {data.pages?.map(p => <Card size="small" key={p.page_no} title={`核对答题页 ${p.page_no}`}>
          <div className="transcription-page-grid">
            <div className="transcription-photo">{p.image_url ? <Image width="100%" src={apiUrl(p.image_url)} alt={`核对答题页 ${p.page_no} 原图`} /> : <Alert type="warning" message="原图已过期，请补拍后重新合并" />}</div>
            <Space direction="vertical" className="full-width transcription-units">
            {p.identity_check.status === 'unconfirmed' && <Alert type="warning" message="模型未完整识别纸面编号，请人工核对短码、版本、产物编号及页码" />}
            {units.filter(u => u.page_no === p.page_no).map(u => <Card size="small" key={u.slot_key}>
              <Space direction="vertical" className="full-width">
                <Typography.Text strong>第 {u.order_no} 题 · 第 {u.subquestion_no} 小题 · 第 {u.blank_no} 空 <Tag>{({ required: '必做题', personalized: '个性化', history: '历史回顾' } as Record<string, string>)[u.source_module] ?? u.source_module}</Tag></Typography.Text>
                <Typography.Text type="secondary">模型原始抄读：{statusLabels[u.original_status]}</Typography.Text>
                <AnswerView answer={u.original_answer} />
                <Select aria-label={`第${u.order_no}题第${u.subquestion_no}小题第${u.blank_no}空状态`} value={u.recognition_status} disabled={locked}
                  style={{ minWidth: 150 }} options={Object.entries(statusLabels).map(([value, label]) => ({ value, label }))}
                  onChange={(status: Status) => mutate(u.slot_key, { recognition_status: status, answer: status === 'readable' ? u.answer ?? '' : null })} />
                {u.recognition_status === 'readable' && <>
                  <Input.TextArea aria-label={`第${u.order_no}题第${u.subquestion_no}小题第${u.blank_no}空答案`} value={u.answer ?? ''} disabled={locked} autoSize={{ minRows: 1, maxRows: 5 }} maxLength={16000}
                    onChange={e => mutate(u.slot_key, { answer: e.target.value })} />
                  <AnswerView answer={u.answer} />
                </>}
              </Space>
            </Card>)}
            <Checkbox disabled={locked} checked={pages.includes(p.page_no)} onChange={e => {
              setPages(values => e.target.checked ? [...values, p.page_no] : values.filter(n => n !== p.page_no))
              setDirty(true); setConfirmed(false)
            }}>已对照照片核对答题页 {p.page_no} 的编号和全部答案</Checkbox>
            </Space>
          </div>
        </Card>)}
        {!submitted && !data.existing_submission_id && <>
          <Button disabled={locked || !auditReady || !dirty || units.some(u => u.recognition_status === 'readable' && !u.answer?.trim())}
            loading={busy} onClick={() => run(async () => {
              install(await apiPut<Transcription>(`${scope}/transcription`, { ...audit, expected_input_sha256: data.input_sha256,
                confirmed_page_nos: pages, units: units.map(u => ({ slot_key: u.slot_key, recognition_status: u.recognition_status, answer: u.answer })),
              }, abort.current.signal)); message.success('核对清单已保存，尚未录入批改')
            })}>保存核对清单</Button>
          {dirty && <Typography.Text type="warning">存在未保存修改，请先保存。</Typography.Text>}
          {data.blockers?.map(value => <Typography.Text key={value} type="secondary">{value}</Typography.Text>)}
          <Checkbox disabled={locked || dirty || !data.can_submit} checked={confirmed} onChange={e => setConfirmed(e.target.checked)}>确认已核对整卷，按保存的答案录入批改</Checkbox>
          <Button type="primary" loading={busy} disabled={locked || dirty || !confirmed || !auditReady || !data.can_submit}
            onClick={() => run(async () => {
              await apiPost(`${scope}/transcription/confirm`, { ...audit, expected_input_sha256: data.input_sha256, transcription_confirmed: true }, abort.current.signal)
              setSubmitted(true); setConfirmed(false); message.success('整卷答案已录入批改')
            })}>确认录入并判分</Button>
        </>}
        <PaperRecognitionReviewPanel scope={scope} refreshKey={`${data.revision_no}:${collection.revision_no}:${collection.effective_input_sha256}`} />
        <details><summary>查看核对记录（{data.history.length}）</summary>{data.history.map(h => <Typography.Paragraph key={h.revision_no}>
          v{h.revision_no} · {({ merge: '合并', save: '保存修正', submit: '确认录入' } as Record<string, string>)[h.action] ?? h.action} · {h.reviewer} · {h.reason}
        </Typography.Paragraph>)}</details>
      </>}
    </Space>
  </Card>
}
