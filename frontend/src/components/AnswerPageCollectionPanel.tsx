import { Alert, Button, Card, Checkbox, Image, Input, Select, Space, Tag, Typography } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiGet, apiPost, apiPostForm, apiPut, apiUrl } from '../api/client'
import type { AnswerCardArtifact } from '../types/recognition'
import type { AnswerPageCollection } from '../types/answerPages'
import { VisionPageTasksPanel } from './VisionPageTasksPanel'
import { AnswerTranscriptionPanel } from './AnswerTranscriptionPanel'

interface PaperIdentity {
  student_code: string
  instance_code: string
  version_no: number
  is_active: boolean
}
interface PendingPhoto { id: string; file: File; pageNo: number }

function PhotoPreview({ file }: { file: File }) {
  const [url, setUrl] = useState('')
  useEffect(() => {
    const value = URL.createObjectURL(file)
    setUrl(value)
    return () => URL.revokeObjectURL(value)
  }, [file])
  return url ? <Image src={url} width={130} alt="待上传答题页照片" /> : null
}

export function AnswerPageCollectionPanel({ paperId }: { paperId: string }) {
  const [paper, setPaper] = useState<PaperIdentity | null>(null)
  const [artifacts, setArtifacts] = useState<AnswerCardArtifact[]>([])
  const [artifactId, setArtifactId] = useState<string>()
  const [collection, setCollection] = useState<AnswerPageCollection | null>(null)
  const [code, setCode] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [photosConfirmed, setPhotosConfirmed] = useState(false)
  const [reviewer, setReviewer] = useState('')
  const [reason, setReason] = useState('收集答题页照片')
  const [pending, setPending] = useState<PendingPhoto[]>([])
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const controller = useRef(new AbortController())
  const artifact = artifacts.find(a => a.id === artifactId)
  const base = `/internal/stage6/paper-instances/${paperId}/answer-card-artifacts`
  const scope = `${base}/${artifactId}/page-collection`

  useEffect(() => {
    const abort = new AbortController()
    controller.current = abort
    setLoading(true)
    Promise.all([
      apiGet<PaperIdentity>(`/paper-instances/${paperId}/submission`, abort.signal),
      apiGet<AnswerCardArtifact[]>(base, abort.signal),
    ]).then(([identity, rows]) => {
      if (abort.signal.aborted) return
      setPaper(identity); setCode(identity.instance_code); setArtifacts(rows)
    }).catch(e => { if (!abort.signal.aborted) setError((e as Error).message) })
      .finally(() => { if (!abort.signal.aborted) setLoading(false) })
    return () => abort.abort()
  }, [base, paperId])

  useEffect(() => {
    const abort = new AbortController()
    setCollection(null); setPending([]); setConfirmed(false); setPhotosConfirmed(false); setError(null); setNotice(null)
    if (!artifactId) return () => abort.abort()
    setLoading(true)
    apiGet<AnswerPageCollection | null>(scope, abort.signal).then(value => {
      if (!abort.signal.aborted) setCollection(value)
    }).catch(e => { if (!abort.signal.aborted) setError((e as Error).message) })
      .finally(() => { if (!abort.signal.aborted) setLoading(false) })
    return () => abort.abort()
  }, [artifactId, scope])

  const perform = async (action: () => Promise<void>) => {
    setBusy(true); setError(null); setNotice(null)
    try { await action() }
    catch (e) { if (!controller.current.signal.aborted) setError((e as Error).message) }
    finally { if (!controller.current.signal.aborted) setBusy(false) }
  }
  const refresh = () => perform(async () => {
    setCollection(await apiGet<AnswerPageCollection | null>(scope, controller.current.signal))
  })
  const create = () => perform(async () => {
    if (!artifact || !paper || !confirmed) return
    setCollection(await apiPost<AnswerPageCollection>(scope, {
      artifact_code: artifact.artifact_code, instance_code: code, expected_version_no: paper.version_no,
    }, controller.current.signal))
  })
  const upload = () => perform(async () => {
    if (!collection || !photosConfirmed) return
    if (new Set(pending.map(p => p.pageNo)).size !== pending.length) throw new Error('同一批照片的答题页编号重复，请核对后上传')
    let current = collection
    // Persist successes immediately. If a later upload fails, only unfinished
    // files remain queued and retry uses the last confirmed collection revision.
    for (const photo of pending) {
      const form = new FormData()
      form.append('file', photo.file)
      form.append('expected_revision_no', String(current.revision_no))
      form.append('reviewer', reviewer.trim())
      form.append('reason', reason.trim())
      current = await apiPostForm<AnswerPageCollection>(`${scope}/pages/${photo.pageNo}/images`, form, controller.current.signal)
      if (controller.current.signal.aborted) return
      setCollection(current)
      setPending(rows => rows.filter(p => p.id !== photo.id))
      if (current.notice) setNotice(current.notice)
    }
    setPhotosConfirmed(false)
  })
  const select = (pageNo: number, imageId: string | null) => perform(async () => {
    if (!collection) return
    setCollection(await apiPut<AnswerPageCollection>(`${scope}/pages/${pageNo}/effective-image`, {
      expected_revision_no: collection.revision_no, image_id: imageId,
      reviewer: reviewer.trim(), reason: reason.trim(),
    }, controller.current.signal))
  })
  const writable = Boolean(paper?.is_active && collection?.is_active)
  const auditReady = reviewer.trim().length > 0 && reason.trim().length >= 2
  return <Space direction="vertical" size="large" className="page-stack" aria-label="答题页照片管理">
    <Button><Link to={`/paper-instances/${paperId}`}>返回试卷</Link></Button>
    <div><Typography.Title level={2}>答题页照片管理</Typography.Title>
      <Typography.Paragraph>上传学生版试卷后面的答题区域，每页拍一张，保留页眉、页脚编号和四角标记。题目页无需上传。</Typography.Paragraph></div>
    <Alert type="info" showIcon message="先核对并收齐答题页照片，再运行提取" description="照片与提取结果分别管理，请在下方逐页任务中查看处理结果。缺页不会当作留白或答错。" />
    {error && <Alert type="error" showIcon message="照片操作失败" description={error} action={artifactId && <Button disabled={busy} onClick={refresh}>刷新照片集合</Button>} />}
    {notice && <Alert type="info" showIcon message={notice} />}
    <Card title="1. 核对实际打印的试卷" loading={loading && !paper}>
      <Space direction="vertical" className="full-width">
        {paper && <Typography.Text>{paper.student_code} · 试卷短码 {paper.instance_code} · v{paper.version_no}</Typography.Text>}
        {!loading && artifacts.length === 0 && <Alert type="warning" showIcon message="尚无固定分页的答题页记录" description="请使用现有“打印 / 保存 PDF”生成新版学生试卷，再回到这里刷新；试卷仍是前面题目、后面答题区。" action={<Button><Link to={`/paper-instances/${paperId}/print`}>打开学生试卷</Link></Button>} />}
        <Select aria-label="纸面产物编号" className="full-width" placeholder="按答题页页脚的产物编号选择，不默认选最新版本" value={artifactId} disabled={busy || loading}
          options={artifacts.map(a => ({ value: a.id, label: `${a.artifact_code} · r${a.revision_no} · ${a.total_pages} 个答题页` }))} onChange={setArtifactId} />
        {artifact && !collection && !loading && <>
          <Input aria-label="纸面试卷短码" value={code} maxLength={100} onChange={e => setCode(e.target.value.toUpperCase())} addonBefore="纸面短码" disabled={busy} />
          <Checkbox checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)}>已对照纸面核对学生、试卷短码、版本和产物编号</Checkbox>
          <Button type="primary" disabled={!confirmed || !paper?.is_active} loading={busy} onClick={create}>建立照片集合</Button>
        </>}
      </Space>
    </Card>
    {collection && <>
      <Alert type={collection.missing_page_nos.length ? 'warning' : 'success'} showIcon
        message={collection.missing_page_nos.length ? `待补拍：答题页 ${collection.missing_page_nos.join('、')}` : '答题页照片已收齐，请查看下方提取任务'}
        description={`共 ${collection.total_pages} 个答题页；这里只使用答题区自己的页码，不使用整卷PDF页码。`} />
      {!collection.is_active && <Alert type="warning" message="此试卷版本已失效，照片历史仅供查看" />}
      <Card title="2. 上传或补拍照片">
        <Space direction="vertical" className="full-width">
          <Input aria-label="核对人" placeholder="填写核对人" value={reviewer} maxLength={100} onChange={e => setReviewer(e.target.value)} disabled={busy} />
          <Input aria-label="操作原因" placeholder="上传、补拍或调整原因（至少2字）" value={reason} maxLength={500} onChange={e => setReason(e.target.value)} disabled={busy} />
          <input aria-label="选择多页照片" type="file" multiple accept="image/jpeg,image/png,image/webp" disabled={busy || !writable} onChange={e => {
            const files = Array.from(e.target.files ?? [])
            if (files.length === 0) return
            if (files.length > collection.total_pages) { setError('一次选择的照片数不能超过答题页数'); e.target.value = ''; return }
            const missing = collection.missing_page_nos
            setPending(files.map((file, i) => ({ id: crypto.randomUUID(), file, pageNo: missing[i] ?? i + 1 })))
            setPhotosConfirmed(false); e.target.value = ''
          }} />
          {pending.map(photo => <Card key={photo.id} size="small"><Space wrap>
            <PhotoPreview file={photo.file} />
            <Typography.Text>{photo.file.name}</Typography.Text>
            <Select aria-label={`照片 ${photo.file.name} 的答题页编号`} value={photo.pageNo} disabled={busy} style={{ minWidth: 140 }}
              options={collection.pages.map(p => ({ value: p.page_no, label: `答题页 ${p.page_no}` }))}
              onChange={value => { setPending(rows => rows.map(p => p.id === photo.id ? { ...p, pageNo: value } : p)); setPhotosConfirmed(false) }} />
            <Button disabled={busy} onClick={() => setPending(rows => rows.filter(p => p.id !== photo.id))}>移除待上传照片</Button>
          </Space></Card>)}
          {pending.length > 0 && <>
            <Checkbox disabled={busy} checked={photosConfirmed} onChange={e => setPhotosConfirmed(e.target.checked)}>已逐张对照照片核对产物编号及答题页编号</Checkbox>
            <Button type="primary" aria-label={`上传所选照片（${pending.length} 张）`} loading={busy} disabled={!writable || !auditReady || !photosConfirmed} onClick={upload}>上传所选照片（{pending.length} 张）</Button>
          </>}
        </Space>
      </Card>
      <Card title="3. 当前有效页与历史照片" extra={<Button disabled={busy} onClick={refresh}>刷新</Button>}>
        <Space direction="vertical" className="full-width">
          {collection.pages.map(p => {
            const image = collection.images.find(i => i.id === p.effective_image_id)
            const ids = new Set(collection.history.filter(h => h.page_no === p.page_no).flatMap(h => [h.before_image_id, h.after_image_id]))
            const history = collection.images.filter(i => ids.has(i.id))
            return <Card key={p.page_no} size="small" title={`答题页 ${p.page_no}`} extra={<Tag color={p.status === 'uploaded' ? 'green' : 'orange'}>{p.status === 'uploaded' ? '已上传' : p.status === 'expired' ? '照片已过期，请补拍' : '缺页'}</Tag>}>
              <Space direction="vertical" className="full-width">
                {image?.image_url && <Image width={180} src={apiUrl(image.image_url)} alt={`答题页 ${p.page_no} 当前有效照片`} />}
                {image?.retention_until && <Typography.Text type="secondary">照片保留至 {new Date(image.retention_until).toLocaleString()}</Typography.Text>}
                {p.effective_image_id && <Button danger disabled={!writable || busy || !auditReady} onClick={() => select(p.page_no, null)}>移除本页归属</Button>}
                {history.map((old, i) => <Space key={old.id} wrap>
                  {old.image_url && <Image width={70} src={apiUrl(old.image_url)} alt={`答题页 ${p.page_no} 历史照片 ${i + 1}`} />}
                  <Typography.Text>照片 {i + 1}{old.id === p.effective_image_id ? '（当前有效）' : ''}{!old.available ? '（已过期）' : ''}</Typography.Text>
                  {old.available && old.id !== p.effective_image_id && <Button disabled={!writable || busy || !auditReady} onClick={() => select(p.page_no, old.id)}>设为本页有效照片</Button>}
                </Space>)}
              </Space>
            </Card>
          })}
        </Space>
      </Card>
      <VisionPageTasksPanel scope={scope} collection={collection} reviewer={reviewer} reason={reason} />
      <AnswerTranscriptionPanel key={`${scope}:${collection.revision_no}`} scope={scope} collection={collection} paperId={paperId}
        reviewer={reviewer} reason={reason} onReviewerChange={setReviewer} onReasonChange={setReason} />
      <Card title="操作记录"><Space direction="vertical">
        {collection.history.length === 0 && <Typography.Text type="secondary">尚未上传照片</Typography.Text>}
        {collection.history.map(h => <Typography.Text key={h.revision_no}>v{h.revision_no} · 答题页 {h.page_no} · {({ upload: '上传/补拍', select: '切换有效照片', remove: '移除归属', restore: '重新上传过期照片' } as Record<string, string>)[h.action] ?? h.action} · {h.reviewer} · {h.reason}</Typography.Text>)}
      </Space></Card>
    </>}
  </Space>
}
