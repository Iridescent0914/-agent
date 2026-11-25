import { Alert, Button, Card, Checkbox, Input, Select, Space, Tag, Typography } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { apiGet, apiPut } from '../api/client'
import type { PaperRecognitionReviewStatus, PaperRecognitionReviewView } from '../types/paperRecognitionReview'

const labels: Record<PaperRecognitionReviewStatus, string> = {
  pending: '未核对', correct: '确认无误', incorrect: '发现错误', uncertain: '无法判断',
}

export function PaperRecognitionReviewPanel({ scope, refreshKey }: { scope: string; refreshKey: string }) {
  const [data, setData] = useState<PaperRecognitionReviewView | null>(null)
  const [status, setStatus] = useState<PaperRecognitionReviewStatus>('pending')
  const [reviewer, setReviewer] = useState('')
  const [reason, setReason] = useState('')
  const [checked, setChecked] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [refresh, setRefresh] = useState(0)
  const abortRef = useRef<AbortController | null>(null)
  const install = (value: PaperRecognitionReviewView) => {
    setData(value); setStatus(value.current.review_status); setChecked(false)
  }
  useEffect(() => {
    const abort = new AbortController()
    abortRef.current = abort; setData(null); setError(null); setNotice(null)
    setReviewer(''); setReason(''); setChecked(false); setBusy(false)
    apiGet<PaperRecognitionReviewView>(`${scope}/recognition-review`, abort.signal)
      .then(value => { if (!abort.signal.aborted) install(value) })
      .catch(value => { if (!abort.signal.aborted) setError((value as Error).message) })
    return () => abort.abort()
  }, [scope, refreshKey, refresh])
  const definite = status === 'correct' || status === 'incorrect'
  const save = async () => {
    const controller = abortRef.current
    if (!data?.recognition_sha256 || !controller || controller.signal.aborted) return
    setBusy(true); setError(null); setNotice(null)
    try {
      const value = await apiPut<PaperRecognitionReviewView>(`${scope}/recognition-review`, {
        expected_revision_no: data.revision_no, expected_recognition_sha256: data.recognition_sha256,
        review_status: status, all_pages_reviewed: checked, reviewer: reviewer.trim(), reason: reason.trim(),
      }, controller.signal)
      if (!controller.signal.aborted) {
        install(value); setNotice(value.unchanged ? '核对记录未变化。' : '整卷识别核对记录已保存，答案和评分未改变。')
      }
    } catch (value) {
      if (!controller.signal.aborted) { setError((value as Error).message); setChecked(false) }
    } finally { if (!controller.signal.aborted) setBusy(false) }
  }
  return <Card size="small" title="整卷识别质量核对" aria-label="整卷识别质量核对"
    extra={<Button size="small" disabled={busy} onClick={() => setRefresh(v => v + 1)}>刷新识别核对记录</Button>}>
    <Space direction="vertical" className="full-width" size="middle">
      <Typography.Text>核对模型原始抄录是否忠实于照片，和学生是否答对分开记录。修正答案、确认录入不会自动生成质量结论。</Typography.Text>
      {error && <Alert type="error" showIcon message="识别核对记录加载或保存失败" description={error} />}
      {notice && <Alert type="success" showIcon message={notice} />}
      {!data && !error && <Typography.Text type="secondary">正在读取识别核对记录…</Typography.Text>}
      {data && <>
        <Space wrap>
          <Typography.Text>已保存结论：</Typography.Text>
          <Tag color={data.stale ? undefined : ({ correct: 'green', incorrect: 'red', uncertain: 'orange', pending: undefined })[data.current.review_status]}>{labels[data.current.review_status]}</Tag>
          <Typography.Text type="secondary">核对记录 v{data.revision_no} · {data.page_count} 页</Typography.Text>
          {data.synthetic && <Tag>模拟结果，仅供开发核对</Tag>}
        </Space>
        <Typography.Text type="secondary">{data.target_scope === 'submitted' ? '对象为已录入批改时的识别结果，后续补拍不会替换此处的核对对象。' : '对象为当前合并清单中的模型原始抄录；补拍或重提后须重新合并并核对。'}</Typography.Text>
        {data.current.reviewer && <Typography.Text>上次记录：{data.current.reviewer} · {data.current.reason}</Typography.Text>}
        {data.message && <Alert type="info" showIcon message={data.message} />}
        {!data.original_images_available && <Typography.Text type="secondary">对应原照片不可用，不能新增确定结论；可操作的识别版本仍可登记“未核对”或“无法判断”。</Typography.Text>}
        <Select aria-label="整卷识别核对结论" value={status} disabled={busy || !data.can_record} style={{ width: '100%' }}
          options={Object.entries(labels).map(([value, label]) => ({ value, label }))}
          onChange={value => { setStatus(value); setChecked(false); setNotice(null) }} />
        <Input aria-label="识别质量核对人" placeholder="本次识别质量核对人" maxLength={100} value={reviewer}
          disabled={busy || !data.can_record} onChange={e => setReviewer(e.target.value)} />
        <Input.TextArea aria-label="识别质量核对说明" placeholder="核对说明（至少2个字）；发现错误时说明漏抄、误抄或错位等实际情况" rows={2} maxLength={500}
          value={reason} disabled={busy || !data.can_record} onChange={e => setReason(e.target.value)} />
        {definite && <Checkbox checked={checked} disabled={busy || !data.can_record || !data.original_images_available}
          onChange={e => setChecked(e.target.checked)}>已对照全部答题页照片，核对模型原始抄录及页码归属</Checkbox>}
        <Button loading={busy} disabled={!data.can_record || busy || !reviewer.trim() || reason.trim().length < 2
          || (definite && (!checked || !data.original_images_available))} onClick={() => void save()}>保存识别核对记录</Button>
        {data.pages.length > 0 && <details><summary>查看对应提取版本</summary>
          {data.pages.map(p => <Typography.Paragraph key={p.page_no}>答题页 {p.page_no} · 第 {p.generation} 次提取 · {p.provider} · {p.model}</Typography.Paragraph>)}
        </details>}
        <details><summary>查看整卷识别核对历史（最近{data.history.length}条）</summary>
          {data.history.length === 0 ? <Typography.Text type="secondary">尚无人工质量核对记录。</Typography.Text> : data.history.map(h => <Typography.Paragraph key={`${h.recognition_sha256}:${h.revision_no}`}>
            {h.is_current_target ? '当前识别版本' : '历史识别版本'} · 核对 v{h.revision_no} · {labels[h.review_status]}{h.synthetic ? '（模拟）' : ''} · {h.reviewer} · {h.reason} · {new Date(h.created_at).toLocaleString()}
          </Typography.Paragraph>)}
        </details>
      </>}
    </Space>
  </Card>
}
