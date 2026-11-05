import { ArrowLeftOutlined, CheckOutlined, CloseOutlined, HistoryOutlined, RollbackOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Descriptions, Drawer, Image, Input, Modal, Space, Statistic, Tag, Typography, message } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiGet, apiPatch, apiPost, apiUrl } from '../api/client'
import { AnswerView } from '../components/AnswerView'
import { initialDraft, ManualAnswerEditor } from '../components/ManualAnswerEditor'
import { RichQuestionText } from '../components/RichQuestionText'
import type { AnswerDraft, AttemptResult, AttemptRevision, SubmissionDetail, SubmissionPaperItem } from '../types/submission'
import { answerMatchTypeLabel } from '../utils/gradingLabels'

function gradeTag(attempt: AttemptResult) {
  if (attempt.grade_status === 'needs_review') return <Tag color="orange">待复核</Tag>
  return attempt.is_correct ? <Tag color="green">正确</Tag> : <Tag color="red">错误</Tag>
}

function gradeSource(attempt: AttemptResult) {
  if (attempt.grade_status === 'needs_review') return '待教师复核'
  if (attempt.grade_status === 'auto_confirmed') return '规则自动确认'
  return '教师已确认'
}

interface CorrectionState {
  item: SubmissionPaperItem
  attempt: AttemptResult
  draft: AnswerDraft
  isCorrect: boolean
  reason: string
}

function gradingEditorItem(item: SubmissionPaperItem): SubmissionPaperItem {
  return {
    ...item,
    answer_snapshot: item.grading_answer_snapshot,
    answer_rule_snapshot: item.grading_answer_rule_snapshot,
  }
}

export function PaperGradingPage() {
  const { paperInstanceId } = useParams()
  const [data, setData] = useState<SubmissionDetail | null>(null)
  const [drafts, setDrafts] = useState<Record<string, AnswerDraft>>({})
  const [reasons, setReasons] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [revisions, setRevisions] = useState<AttemptRevision[] | null>(null)
  const [correction, setCorrection] = useState<CorrectionState | null>(null)
  const [correctionSaving, setCorrectionSaving] = useState(false)
  const [regrading, setRegrading] = useState(false)
  const keyRef = useRef(`manual-${paperInstanceId}-${crypto.randomUUID()}`)
  const load = () => {
    if (!paperInstanceId) return
    setLoading(true); setError(null)
    apiGet<SubmissionDetail>(`/paper-instances/${paperInstanceId}/submission`)
      .then((value) => {
        setData(value)
        setDrafts(Object.fromEntries(
          value.items.map((item) => [item.id, initialDraft(gradingEditorItem(item))]),
        ))
      })
      .catch((reason) => setError((reason as Error).message)).finally(() => setLoading(false))
  }
  useEffect(load, [paperInstanceId])
  const submit = async () => {
    if (!data || !paperInstanceId) return
    const unset = data.items.filter((item) => drafts[item.id]?.response_status === 'unset')
    if (unset.length) { message.warning(`请为第 ${unset.map((item) => item.order_no).join('、')} 题录入答案或标记留空`); return }
    setLoading(true); setError(null)
    try {
      const result = await apiPost<SubmissionDetail>(`/paper-instances/${paperInstanceId}/submissions/manual`, {
        idempotency_key: keyRef.current,
        student_id: data.student_id,
        answers: data.items.map((item) => ({ paper_instance_item_id: item.id, order_no: item.order_no, ...drafts[item.id] })),
      })
      setData(result); message.success('答案已提交并完成规则判分')
    } catch (reason) { setError((reason as Error).message) }
    finally { setLoading(false) }
  }
  const review = async (attempt: AttemptResult, action: 'confirm' | 'reopen', isCorrect: boolean | null) => {
    const reason = (reasons[attempt.id] || '').trim()
    if (reason.length < 2) { message.warning('请先填写至少 2 个字的复核原因'); return }
    try {
      const result = await apiPatch<SubmissionDetail>(`/attempts/${attempt.id}/review`, {
        action, expected_revision_no: attempt.revision_no, is_correct: isCorrect,
        corrected_response_status: null, corrected_answer: null, reason, actor: 'demo_teacher',
      })
      setData(result); message.success(result.unchanged ? '当前结果未变化' : '复核结果已保存，画像已重算')
    } catch (reasonValue) { setError((reasonValue as Error).message); load() }
  }
  const confirmRecognition = async (attempt: AttemptResult) => {
    if (!data?.submission_id) return
    const reason = (reasons[attempt.id] || '').trim()
    if (reason.length < 2) { message.warning('请先填写至少 2 个字的核对原因'); return }
    try {
      const result = await apiPost<SubmissionDetail>(`/submissions/${data.submission_id}/recognition-review`, {
        reason, actor: 'demo_teacher', items: [{
          attempt_id: attempt.id,
          expected_revision_no: attempt.revision_no,
          action: 'accept',
          response_status: attempt.recognition_status === 'blank' ? 'blank' : 'answered',
          answer: attempt.recognition_status === 'blank' ? null : attempt.student_answer,
        }],
      })
      setData(result); message.success('已确认抄读结果并按规则判分，画像已重算')
    } catch (reasonValue) { setError((reasonValue as Error).message); load() }
  }
  const confirmAllRecognition = () => {
    if (!data?.submission_id) return
    const candidates = data.items
      .map((item) => item.attempt)
      .filter((attempt): attempt is AttemptResult => Boolean(
        attempt
        && attempt.grade_status === 'needs_review'
        && attempt.recognition_status === 'readable'
        && !attempt.grading_details.recognition_reviewed,
      ))
    if (candidates.length === 0) { message.info('没有可批量确认的清晰抄读项'); return }
    Modal.confirm({
      title: `批量确认 ${candidates.length} 道清晰抄读结果？`,
      content: '系统仍使用确定性规则判分；空白、缺失、模糊和歧义项必须逐题处理。',
      okText: '确认并规则判分', cancelText: '取消',
      onOk: async () => {
        setLoading(true); setError(null)
        try {
          const result = await apiPost<SubmissionDetail>(`/submissions/${data.submission_id}/recognition-review`, {
            reason: '批量对照原图确认清晰抄读', actor: 'demo_teacher',
            items: candidates.map((attempt) => ({
              attempt_id: attempt.id,
              expected_revision_no: attempt.revision_no,
              action: 'accept',
              response_status: 'answered',
              answer: attempt.student_answer,
            })),
          })
          setData(result); message.success(`已批量确认 ${candidates.length} 道，画像仅重算一次`)
        } catch (reasonValue) {
          setError((reasonValue as Error).message); load(); throw reasonValue
        } finally { setLoading(false) }
      },
    })
  }
  const regrade = () => {
    if (!data?.submission_id || data.answer_key_update_count < 1) return
    Modal.confirm({
      title: `使用题库最新标准答案重新评分？`,
      content: `检测到 ${data.answer_key_update_count} 道题的标准答案已经重新发布。重新评分会追加修订记录，并重新计算学生画像。`,
      okText: '重新评分',
      cancelText: '取消',
      onOk: async () => {
        setRegrading(true); setError(null)
        try {
          const result = await apiPost<SubmissionDetail>(`/submissions/${data.submission_id}/regrade`, {
            reason: '题库标准答案重新发布后重新评分',
            actor: 'demo_teacher',
          })
          setData(result)
          message.success(`已按最新标准答案重新评分 ${result.regraded_count} 道题，画像已重算`)
        } catch (reasonValue) {
          setError((reasonValue as Error).message)
          throw reasonValue
        } finally {
          setRegrading(false)
        }
      },
    })
  }
  const beginCorrection = (item: SubmissionPaperItem, attempt: AttemptResult) => {
    setCorrection({
      item,
      attempt,
      draft: attempt.student_answer == null
        ? { response_status: 'blank', answer: null }
        : { response_status: 'answered', answer: attempt.student_answer },
      isCorrect: attempt.is_correct ?? false,
      reason: reasons[attempt.id] || '',
    })
  }
  const saveCorrection = async () => {
    if (!correction) return
    const reason = correction.reason.trim()
    if (reason.length < 2) { message.warning('请填写至少 2 个字的修正原因'); return }
    if (correction.draft.response_status === 'unset') { message.warning('请录入答案或标记整题留空'); return }
    setCorrectionSaving(true); setError(null)
    try {
      const isPendingImage = data?.source === 'image'
        && correction.attempt.grade_status === 'needs_review'
        && !correction.attempt.grading_details.recognition_reviewed
      const result = isPendingImage && data?.submission_id
        ? await apiPost<SubmissionDetail>(`/submissions/${data.submission_id}/recognition-review`, {
            reason, actor: 'demo_teacher', items: [{
              attempt_id: correction.attempt.id,
              expected_revision_no: correction.attempt.revision_no,
              action: 'correct',
              response_status: correction.draft.response_status,
              answer: correction.draft.answer,
            }],
          })
        : await apiPatch<SubmissionDetail>('/attempts/' + correction.attempt.id + '/review', {
            action: 'correct', expected_revision_no: correction.attempt.revision_no,
            is_correct: correction.isCorrect,
            corrected_response_status: correction.draft.response_status,
            corrected_answer: correction.draft.answer, reason, actor: 'demo_teacher',
          })
      setData(result); setCorrection(null); message.success('录入答案已修正，画像已重算')
    } catch (reasonValue) {
      setError((reasonValue as Error).message)
    } finally {
      setCorrectionSaving(false)
    }
  }
  const showHistory = async (attempt: AttemptResult) => {
    try { setRevisions(await apiGet<AttemptRevision[]>(`/attempts/${attempt.id}/revisions`)) }
    catch (reason) { setError((reason as Error).message) }
  }
  return <Space direction="vertical" size="large" className="page-stack grading-workspace">
    <Button type="link" icon={<ArrowLeftOutlined />}><Link to="/grading">返回批改工作台</Link></Button>
    {error && <Alert type="error" showIcon message="操作失败" description={error} action={<Button onClick={load}>刷新</Button>} />}
    {data && <><div className="student-heading"><div><Typography.Title level={2}>{data.student_code} · {data.student_name}</Typography.Title><Typography.Text type="secondary">{data.assignment_title} · v{data.version_no} · {data.instance_code}</Typography.Text></div><Space wrap>{data.can_submit && <Button type="primary" loading={loading} onClick={submit}>提交并自动判分</Button>}{data.submission_id && data.answer_key_update_count > 0 && <Button type="primary" danger loading={regrading} onClick={regrade}>重新评分（{data.answer_key_update_count} 题）</Button>}</Space></div>
      {!data.is_active && <Alert type="warning" showIcon message="历史失效版本" description={data.submission_id ? '已有提交仍可查看和复核，但不能创建新提交。' : '该版本不能创建新提交。'} />}
      {data.answer_key_update_count > 0 && <Alert type="warning" showIcon message="题库标准答案已重新发布" description="当前结果仍按原评分标准保留。请点击“重新评分”，确认后系统才会追加修订并更新画像。" />}
      {data.source === 'image' && data.recognition_image_url && <Card title="答题区原图"><div className="recognition-source-grid"><Image src={apiUrl(data.recognition_image_url)} /><Descriptions size="small" column={1}><Descriptions.Item label="来源">图片识别，教师确认前不进入画像</Descriptions.Item><Descriptions.Item label="模型">{data.recognition_provider} · {data.recognition_model}</Descriptions.Item><Descriptions.Item label="提示版本">{data.recognition_prompt_version}</Descriptions.Item></Descriptions></div></Card>}
      {data.source === 'image' && !data.recognition_image_url && <Alert type="info" showIcon message="答题原图已清理" description="识别结果、教师修订和评分证据仍保留；原图已按保留策略删除。" />}
      {data.submission_id && <Card><Space size="large" wrap><Statistic title="题目" value={data.summary.total_count} /><Statistic title="正确" value={data.summary.correct_count} valueStyle={{ color: '#389e0d' }} /><Statistic title="错误" value={data.summary.incorrect_count} valueStyle={{ color: '#cf1322' }} /><Statistic title="待复核" value={data.summary.needs_review_count} valueStyle={{ color: '#d46b08' }} />{data.source === 'image' && data.summary.needs_review_count > 0 && <Button type="primary" loading={loading} onClick={confirmAllRecognition}>批量确认清晰抄读项</Button>}<Button><Link to={`/students/${data.student_id}`}>查看最新画像</Link></Button></Space></Card>}
      {data.items.map((item) => <Card key={item.id} title={`第 ${item.order_no} 题 · ${item.question_snapshot.original_qid ?? ''}`} extra={item.attempt && gradeTag(item.attempt)}>
        <Space wrap><Tag>{item.question_snapshot.question_type === 'choice' ? '选择题' : item.question_snapshot.question_type === 'true_false' ? '判断题' : '填空题'}</Tag><Tag>{item.knowledge_point_snapshot.name}</Tag><Tag>规则：{answerMatchTypeLabel(item.grading_answer_rule_snapshot.answer_match_type)}</Tag>{item.answer_key_update_available && <Tag color="orange">标准答案待更新</Tag>}</Space>
        <RichQuestionText text={item.question_snapshot.question_text ?? ''} assets={item.assets} />
        {!data.submission_id && <ManualAnswerEditor item={gradingEditorItem(item)} value={drafts[item.id] ?? initialDraft(gradingEditorItem(item))} onChange={(value) => setDrafts((current) => ({ ...current, [item.id]: value }))} />}
        {item.attempt && <Space direction="vertical" className="attempt-result" size="middle">
          <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
            <Descriptions.Item label="学生原始答案"><AnswerView answer={item.attempt.student_answer} /></Descriptions.Item>
            <Descriptions.Item label="当前评分标准答案"><AnswerView answer={item.grading_answer_snapshot} /></Descriptions.Item>
            <Descriptions.Item label="归一化结果"><pre className="json-block compact-json">{JSON.stringify(item.attempt.normalized_answer, null, 2)}</pre></Descriptions.Item>
            <Descriptions.Item label="自动判定">{String(item.attempt.grading_details.reason_code ?? '—')} · {String(item.attempt.grading_details.auto_status ?? '—')}</Descriptions.Item>
            <Descriptions.Item label="当前状态">{gradeSource(item.attempt)}</Descriptions.Item>
            <Descriptions.Item label="评分标准来源">{item.standard_answer_source === 'question_revision' ? '题库当前发布答案' : '试卷发布快照'}</Descriptions.Item>
            <Descriptions.Item label="当前修订">v{item.attempt.revision_no}</Descriptions.Item>
            <Descriptions.Item label="画像证据">{item.attempt.used_in_profile ? '已计入' : data.is_active ? '待复核，不计入' : '历史失效版本，不计入'}</Descriptions.Item>
            {data.source === 'image' && <Descriptions.Item label="识别状态">{item.attempt.recognition_status ?? '—'} · 置信度 {item.attempt.ocr_confidence == null ? '—' : item.attempt.ocr_confidence.toFixed(3)}（模型自报）</Descriptions.Item>}
          </Descriptions>
          <Input.TextArea value={reasons[item.attempt.id] ?? ''} onChange={(event) => setReasons((current) => ({ ...current, [item.attempt!.id]: event.target.value }))} placeholder="复核或改判原因（至少 2 个字）" rows={2} />
          <Space wrap>
            {item.attempt.grade_status === 'needs_review' && (data.source === 'image' && !item.attempt.grading_details.recognition_reviewed ? <>
              {['readable', 'blank'].includes(item.attempt.recognition_status ?? '') && <Button type="primary" icon={<CheckOutlined />} onClick={() => confirmRecognition(item.attempt!)}>确认抄读并规则判分</Button>}
              {!['readable', 'blank'].includes(item.attempt.recognition_status ?? '') && <Tag color="orange">请对照原图修正答案</Tag>}
            </> : <><Button type="primary" icon={<CheckOutlined />} onClick={() => review(item.attempt!, 'confirm', true)}>确认正确</Button><Button danger icon={<CloseOutlined />} onClick={() => review(item.attempt!, 'confirm', false)}>确认错误</Button></>)}
            {item.attempt.grade_status === 'auto_confirmed' && <Button
              danger={item.attempt.is_correct === true}
              type={item.attempt.is_correct === false ? 'primary' : 'default'}
              icon={item.attempt.is_correct === true ? <CloseOutlined /> : <CheckOutlined />}
              onClick={() => review(item.attempt!, 'confirm', !item.attempt!.is_correct)}
            >人工改判为{item.attempt.is_correct ? '错误' : '正确'}</Button>}
            {item.attempt.grade_status === 'teacher_confirmed' && <Button
              danger={item.attempt.is_correct === true}
              type={item.attempt.is_correct === false ? 'primary' : 'default'}
              icon={item.attempt.is_correct === true ? <CloseOutlined /> : <CheckOutlined />}
              onClick={() => review(item.attempt!, 'confirm', !item.attempt!.is_correct)}
            >改判为{item.attempt.is_correct ? '错误' : '正确'}</Button>}
            <Button onClick={() => beginCorrection(item, item.attempt!)}>修正学生答案</Button>
            {item.attempt.grade_status === 'teacher_confirmed' && <Button icon={<RollbackOutlined />} onClick={() => review(item.attempt!, 'reopen', null)}>撤销教师确认</Button>}
            <Button icon={<HistoryOutlined />} onClick={() => showHistory(item.attempt!)}>修订历史</Button>
          </Space>
        </Space>}
      </Card>)}
    </>}
    <Drawer title="判分修订历史" width={720} open={revisions !== null} onClose={() => setRevisions(null)}>
      <Space direction="vertical" className="revision-list">
        {revisions?.map((revision) => <Card key={revision.revision_no} size="small" title={`修订 ${revision.revision_no} · ${revision.operator_type} · ${revision.operator_id ?? '历史记录'}`}><Typography.Paragraph>{revision.reason}</Typography.Paragraph><Typography.Text type="secondary">{new Date(revision.created_at).toLocaleString()}</Typography.Text><details><summary>查看修订后状态</summary><pre className="json-block">{JSON.stringify(revision.after_state, null, 2)}</pre></details></Card>)}
      </Space>
    </Drawer>
    <Modal
      className="grading-correction-modal"
      title={correction ? '修正第 ' + correction.item.order_no + ' 题学生答案' : '修正学生答案'}
      width={720}
      open={correction !== null}
      okText="保存修正"
      cancelText="取消"
      confirmLoading={correctionSaving}
      onCancel={() => setCorrection(null)}
      onOk={() => void saveCorrection()}
    >
      {correction && <Space direction="vertical" className="modal-form-stack" size="middle">
        <Typography.Text type="secondary">按原题结构逐空修正；数值和 LaTeX 公式可直接使用公式键盘。</Typography.Text>
        <ManualAnswerEditor
          item={{
            ...correction.item,
            answer_snapshot: correction.item.grading_answer_snapshot,
            answer_rule_snapshot: correction.item.grading_answer_rule_snapshot,
          }}
          value={correction.draft}
          disabled={correctionSaving}
          onChange={(draft) => setCorrection((current) => current ? { ...current, draft } : current)}
        />
        {!(data?.source === 'image' && correction.attempt.grade_status === 'needs_review' && !correction.attempt.grading_details.recognition_reviewed) && <RadioCorrect value={correction.isCorrect} onChange={(isCorrect) => setCorrection((current) => current ? { ...current, isCorrect } : current)} />}
        <Input.TextArea
          placeholder="修正原因（至少 2 个字）"
          value={correction.reason}
          rows={3}
          onChange={(event) => setCorrection((current) => current ? { ...current, reason: event.target.value } : current)}
        />
      </Space>}
    </Modal>
  </Space>
}

function RadioCorrect({ value, onChange }: { value: boolean; onChange: (value: boolean) => void }) {
  return <Space><Typography.Text>最终判定：</Typography.Text><Button type={value ? 'primary' : 'default'} onClick={() => onChange(true)}>正确</Button><Button danger type={!value ? 'primary' : 'default'} onClick={() => onChange(false)}>错误</Button></Space>
}
