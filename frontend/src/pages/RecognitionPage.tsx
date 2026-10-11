import { ArrowLeftOutlined, CameraOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Descriptions, Image, Input, Space, Table, Tag, Typography, Upload, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { apiDelete, apiGet, apiPost, apiPostForm, apiUrl } from '../api/client'
import { AnswerView } from '../components/AnswerView'
import type { RecognitionRun } from '../types/recognition'
import type { SubmissionDetail } from '../types/submission'

const labels = { readable: '清晰', blank: '确认空白', missing: '缺失题号', illegible: '无法辨认', ambiguous: '存在歧义' }

export function RecognitionPage() {
  const { paperInstanceId } = useParams()
  const navigate = useNavigate()
  const [paper, setPaper] = useState<SubmissionDetail | null>(null)
  const [code, setCode] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [run, setRun] = useState<RecognitionRun | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const key = useRef('m6-image-' + crypto.randomUUID())
  useEffect(() => {
    if (!paperInstanceId) return
    const controller = new AbortController()
    Promise.all([
      apiGet<SubmissionDetail>(`/paper-instances/${paperInstanceId}/submission`, controller.signal),
      apiGet<RecognitionRun | null>(`/paper-instances/${paperInstanceId}/recognition-runs/current`, controller.signal),
    ])
      .then(([value, currentRun]) => {
        if (controller.signal.aborted) return
        setPaper(value)
        setCode(value.instance_code ?? '')
        setRun(currentRun)
      })
      .catch((reason: Error) => setError(reason.message))
    return () => controller.abort()
  }, [paperInstanceId])
  const upload = async () => {
    if (!file || !paperInstanceId) { message.warning('请先选择答题区照片'); return }
    const form = new FormData()
    form.append('file', file)
    form.append('instance_code', code)
    form.append('idempotency_key', key.current)
    setBusy(true); setError(null)
    try { setRun(await apiPostForm<RecognitionRun>(`/paper-instances/${paperInstanceId}/recognition-runs`, form)) }
    catch (reason) { setError((reason as Error).message) } finally { setBusy(false) }
  }
  const extract = async () => {
    if (!run) return
    setBusy(true); setError(null)
    try { setRun(await apiPost<RecognitionRun>(`/recognition-runs/${run.id}/extract`, {})) }
    catch (reason) { setError((reason as Error).message) } finally { setBusy(false) }
  }
  const refreshRun = async () => {
    if (!run) return
    setBusy(true); setError(null)
    try { setRun(await apiGet<RecognitionRun>(`/recognition-runs/${run.id}`)) }
    catch (reason) { setError((reason as Error).message) } finally { setBusy(false) }
  }
  const commit = async () => {
    if (!run) return
    setBusy(true); setError(null)
    try {
      await apiPost(`/recognition-runs/${run.id}/commit`, {})
      message.success('识别结果已进入待复核，教师确认前不会更新画像')
      navigate(`/paper-instances/${paperInstanceId}/grading`)
    } catch (reason) { setError((reason as Error).message) } finally { setBusy(false) }
  }
  const discard = async () => {
    if (!run) return
    setBusy(true); setError(null)
    try {
      await apiDelete<void>(`/recognition-runs/${run.id}`)
      setRun(null)
      key.current = 'm6-image-' + crypto.randomUUID()
      message.success('本次识别已放弃，可重新上传或改用手动录入')
    } catch (reason) { setError((reason as Error).message) } finally { setBusy(false) }
  }
  const columns: ColumnsType<RecognitionRun['items'][number]> = [
    { title: '题号', dataIndex: 'order_no', width: 70 },
    { title: '状态', render: (_, row) => <Tag color={row.recognition_status === 'readable' ? 'green' : row.recognition_status === 'blank' ? 'blue' : 'orange'}>{labels[row.recognition_status]}</Tag> },
    { title: '模型抄读', render: (_, row) => <AnswerView answer={row.recognized_answer} /> },
    { title: '模型自报置信度', render: (_, row) => row.confidence == null ? '—' : row.confidence.toFixed(3) },
  ]
  return <Space direction="vertical" size="large" className="page-stack recognition-workspace">
    <Button type="link" icon={<ArrowLeftOutlined />}><Link to={`/paper-instances/${paperInstanceId}`}>返回试卷</Link></Button>
    <div><Typography.Title level={2}>上传答题区照片</Typography.Title><Typography.Text type="secondary">VLM 只抄读答案；所有结果先进入待复核。</Typography.Text></div>
    {error && <Alert type="error" showIcon message="图片识别操作失败" description={error} />}
    {paper && <Card><Descriptions column={{ xs: 1, md: 3 }}><Descriptions.Item label="学生">{paper.student_code}</Descriptions.Item><Descriptions.Item label="版本">v{paper.version_no}</Descriptions.Item><Descriptions.Item label="系统短码">{paper.instance_code}</Descriptions.Item></Descriptions><Alert type="info" showIcon message="隐私提示" description="请优先上传裁剪后的答题区，不要包含真实姓名、学号或其他身份信息。" /></Card>}
    {paper?.submission_id && !run && <Alert type="warning" showIcon message="这份试卷已经提交" description="同一试卷只保留一份 Submission，请进入批改页查看或修正现有作答。" action={<Button><Link to={`/paper-instances/${paperInstanceId}/grading`}>查看批改</Link></Button>} />}
    {!run && !paper?.submission_id && <Card title="1. 核对编号并选择图片"><Space direction="vertical" className="full-width"><Input value={code} onChange={(event) => setCode(event.target.value.toUpperCase())} addonBefore="纸面短码" /><Upload beforeUpload={(value) => { setFile(value); return false }} maxCount={1} accept="image/jpeg,image/png,image/webp"><Button icon={<CameraOutlined />}>选择 JPEG / PNG / WebP</Button></Upload>{file && <Typography.Text>{file.name} · {(file.size / 1024).toFixed(1)} KB</Typography.Text>}<Button type="primary" loading={busy} onClick={upload}>上传图片</Button></Space></Card>}
    {run && <><Card title="2. 原图与模型"><Space direction="vertical">
      {run.image_url ? <Image src={apiUrl(run.image_url)} className="recognition-preview" /> : <Alert type="warning" showIcon message="原图已按保留策略清理" description="识别与批改记录仍保留，但无法再查看或重新提取原图。" />}
      <Typography.Text type="secondary">{run.provider} · {run.model} · {run.prompt_version}</Typography.Text>
      {run.status === 'uploaded' && run.image_url && <Button type="primary" loading={busy} onClick={extract}>开始提取答案</Button>}
      {run.status === 'processing' && !run.processing_stale && <Alert type="info" showIcon message="正在提取答案，请稍候" action={<Button loading={busy} onClick={refreshRun}>刷新状态</Button>} />}
      {run.status === 'processing' && run.processing_stale && <Alert type="warning" showIcon message="识别任务已超时" description="上一次处理可能因服务重启而中断，可安全重新发起。" action={run.image_url ? <Button loading={busy} onClick={extract}>恢复识别</Button> : undefined} />}
      {run.status === 'failed' && <Alert type="error" message={run.error_code} description={run.error_message} />}
      <Space wrap>{run.status === 'failed' && run.image_url && <Button loading={busy} onClick={extract}>重试识别</Button>}{['uploaded', 'failed', 'ready_for_review'].includes(run.status) && <Button danger loading={busy} onClick={discard}>放弃本次识别</Button>}<Button><Link to={`/paper-instances/${paperInstanceId}/grading`}>改用手动录入</Link></Button></Space>
    </Space></Card>{run.items.length > 0 && <Card title="3. 识别结果"><Table rowKey="id" columns={columns} dataSource={run.items} pagination={false} /><Alert type="warning" showIcon message="提交后仍需教师逐题确认" description="缺失、无法辨认和歧义不会被视为答错；模型置信度不触发自动确认。" /><Button type="primary" loading={busy} onClick={commit}>提交为待复核作答</Button></Card>}</>}
  </Space>
}
