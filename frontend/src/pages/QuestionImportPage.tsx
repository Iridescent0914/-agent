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
  checkpoint: { s2?: { calls?: number; units?: Array<{ index: number; first_page: number; last_page: number; status: string; candidate_count?: number; unresolved_fragments?: Array<{ page: number; text: string; reason: string }> }> } }
  error_code: string | null
  error_message: string | null
  attempt_count: number
  created_at: string
  expires_at: string
  files_purged_at: string | null
}

type Candidate = {
  id: string
  ordinal: number
  is_fake: boolean
  payload: {
    source: { original_number: string | null; page_start: number | null; page_end: number | null }
    content: { question_text: string; options: Array<{ key: string; text: string }> }
    extraction?: { math_fragments?: string[]; diagram_clues?: string[]; uncertain_fragments?: string[] }
    warnings?: string[]
  }
}

type S2Config = { ready: boolean; model: string; max_budget_cny: number; max_output_tokens: number; pages_per_unit: number; overlap_pages: number }

const modeLabels: Record<string, string> = {
  questions_only: '只有题目', combined: '题目与答案同文件', separate: '题目与答案分文件',
}

function stageLabel(batch: Batch): string {
  if (batch.stage === 'awaiting_s2') return '文件已校验，等待 S2 解析'
  if (batch.stage === 's2_queued') return '等待 S2 识别'
  if (batch.stage === 's2_processing') return '正在识别 PDF 页段'
  if (batch.stage === 's2_completed') return 'S2 已完成，待人工核对'
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
  const [mode, setMode] = useState('questions_only')
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

  function confirmStart(batch: Batch) {
    if (!budget) { message.warning('请设置本批调用预算阈值'); return }
    if (!operatorToken) { message.warning('请输入 S2 操作口令'); return }
    Modal.confirm({
      title: '确认发送题目文件到百炼？',
      content: `将题目文件按页段发送给百炼 ${s2Config?.model || ''}。请确认文件不含学生个人信息。本批调用预算阈值为 ¥${budget}；这是基于已返回用量的停机阈值，单次调用可能使实际费用略高。`,
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
      const items = await apiGet<Candidate[]>(`/question-imports/${id}/items`)
      setCandidates(items)
      setSelectedBatchId(id)
    } catch (error) { message.error(error instanceof Error ? error.message : '候选题加载失败') }
  }

  const selectedBatch = batches.find((item) => item.id === selectedBatchId)
  const s2 = selectedBatch?.checkpoint?.s2

  return <Space direction="vertical" size="large" style={{ width: '100%' }}>
    <Card title="批量题目导入 · S2 内部识别">
      <Typography.Paragraph>上传后先校验文件；点击“开始 S2 识别”才会将题目页段发送到百炼并产生费用。识别结果只进入候选区，不能直接写入正式题库。</Typography.Paragraph>
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <Select aria-label="文件场景" value={mode} onChange={(value) => { setMode(value); setAnswerFile(null) }}
          options={Object.entries(modeLabels).map(([value, label]) => ({ value, label }))} style={{ width: 260 }} />
        <label>题目文件（PDF/DOCX） <input type="file" accept=".pdf,.docx" onChange={(event) => setQuestionFile(event.target.files?.[0] || null)} /></label>
        {mode === 'separate' && <label>答案文件（PDF/DOCX） <input type="file" accept=".pdf,.docx" onChange={(event) => setAnswerFile(event.target.files?.[0] || null)} /></label>}
        <Typography.Text type="secondary">单文件不超过 30 MB；PDF 不超过 50 页；临时文件最多保留 72 小时、最近 7 批。</Typography.Text>
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
          { title: '状态', render: (_, batch: Batch) => <Space direction="vertical" size={0}><Tag color={batch.status === 'failed' ? 'red' : batch.status === 'cancelled' ? 'default' : batch.status === 'review_pending' ? 'green' : 'blue'}>{stageLabel(batch)}</Tag>{batch.checkpoint?.s2 && <Typography.Text type="secondary">调用 {batch.checkpoint.s2.calls || 0} 次</Typography.Text>}{batch.error_message && <Typography.Text type="danger">{batch.error_message}</Typography.Text>}</Space> },
          { title: '操作', render: (_, batch: Batch) => <Space wrap>{['awaiting_s2', 's2_budget_paused'].includes(batch.stage) && <Button disabled={!s2Config?.ready} onClick={() => confirmStart(batch)}>开始 S2 识别</Button>}{batch.checkpoint?.s2 && <Button onClick={() => void viewCandidates(batch.id)}>查看候选与页段</Button>}{batch.status === 'failed' && !batch.files_purged_at && <Button disabled={loading} onClick={() => void act(batch.id, 'retry')}>重试</Button>}{!['cancelled', 'completed', 'completed_with_unassigned'].includes(batch.status) && <Button disabled={loading} onClick={() => void act(batch.id, 'cancel')}>取消</Button>}</Space> },
        ]} />
    </Card>
    <Modal title="S2 候选题与页段记录" open={!!selectedBatchId} width={900} footer={<Button onClick={() => setSelectedBatchId(null)}>关闭</Button>} onCancel={() => setSelectedBatchId(null)}>
      <Typography.Paragraph>题目与页码是模型建议，请对照本地原文件人工核对；图形仅有线索，尚未完成 S3 题图裁剪。原文件不提供在线公开地址。</Typography.Paragraph>
      {s2?.units?.map((unit) => <Typography.Paragraph key={unit.index}>页段 {unit.index + 1}：原文件第 {unit.first_page}–{unit.last_page} 页，{unit.status === 'completed' ? `候选 ${unit.candidate_count || 0} 题` : '待处理'}{unit.unresolved_fragments?.map((fragment, index) => <Typography.Text type="warning" key={index}>；第 {fragment.page} 页未确定：{fragment.text}（{fragment.reason}）</Typography.Text>)}</Typography.Paragraph>)}
      <Table rowKey="id" dataSource={candidates} size="small" pagination={{ pageSize: 10 }} columns={[
        { title: '序号', dataIndex: 'ordinal', width: 70 },
        { title: '原题号', render: (_, item: Candidate) => item.payload.source.original_number || '未识别', width: 90 },
        { title: '原页', render: (_, item: Candidate) => `${item.payload.source.page_start ?? '?'}–${item.payload.source.page_end ?? '?'}`, width: 90 },
        { title: '题干与选项', render: (_, item: Candidate) => <Space className="question-import-candidate" direction="vertical" style={{ width: '100%' }}><RichQuestionText key="stem" text={item.payload.content.question_text} assets={[]} mathFragments={item.payload.extraction?.math_fragments ?? []} />{item.payload.content.options.map((option, index) => <RichQuestionText key={`option-${index}-${option.key}`} text={`${option.key}. ${option.text}`} assets={[]} mathFragments={item.payload.extraction?.math_fragments ?? []} />)}{item.payload.extraction?.diagram_clues?.map((clue, index) => <Typography.Text type="warning" key={`clue-${index}`}>题图线索：{clue}</Typography.Text>)}{item.payload.extraction?.uncertain_fragments?.map((fragment, index) => <Typography.Text type="warning" key={`uncertain-${index}`}>模型不确定：{fragment}</Typography.Text>)}{item.payload.warnings?.filter((warning) => warning !== '模型报告不确定片段；请人工复核' || !item.payload.extraction?.uncertain_fragments?.length).map((warning, index) => <Typography.Text type="warning" key={`warning-${index}`}>{warning}</Typography.Text>)}</Space> },
      ]} />
    </Modal>
  </Space>
}

export default QuestionImportPage
