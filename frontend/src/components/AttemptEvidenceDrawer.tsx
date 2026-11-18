import { Alert, Descriptions, Divider, Drawer, Skeleton, Space, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiGet } from '../api/client'
import type { AttemptEvidence } from '../types/profile'
import { AnswerView } from './AnswerView'
import { RichQuestionText } from './RichQuestionText'
import { QuestionOptions } from './QuestionOptions'

const exclusionReasonLabels: Record<string, string> = {
  invalidated_paper: '试卷已撤回或被新版替代',
  unsupported_profile_question_type: '该题型保留判分结果，不参与当前画像评分',
  needs_review: '作答仍待教师复核',
  unpublished_paper: '试卷尚未发布',
  missing_ownership_record: '作答归属记录缺失',
  ownership_mismatch: '作答归属不一致',
  unmapped_knowledge_point: '知识点无法映射',
  invalid_question_snapshot: '发布时题目快照无效',
  missing_current_revision: '当前修订记录缺失',
}

const exclusionReasonText = (reason: string | null) => reason
  ? exclusionReasonLabels[reason] ?? reason
  : '未形成有效画像证据'

export function AttemptEvidenceDrawer({ studentId, attemptId, onClose }: {
  studentId: string; attemptId: string | null; onClose: () => void
}) {
  const [value, setValue] = useState<AttemptEvidence | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    setValue(null); setError(null)
    if (!attemptId) return
    const controller = new AbortController()
    apiGet<AttemptEvidence>(`/students/${studentId}/attempts/${attemptId}`, controller.signal)
      .then((data) => { if (!controller.signal.aborted) setValue(data) })
      .catch((reason: Error) => { if (!controller.signal.aborted) setError(reason.message) })
    return () => controller.abort()
  }, [studentId, attemptId])
  return <Drawer title="历史作答证据" open={attemptId !== null} onClose={onClose} width={720}>
    {error ? <Alert type="error" message={error} /> : !value ? <Skeleton active /> : <Space direction="vertical" size="middle" className="full-width">
      <Space wrap><Typography.Text strong>{value.question_snapshot.original_qid}</Typography.Text>
        <Tag>修订 {value.revision_no}</Tag><Tag color={value.is_correct === null ? 'default' : value.is_correct ? 'success' : 'error'}>{value.is_correct === null ? '待复核' : value.is_correct ? '正确' : '错误'}</Tag></Space>
      <Descriptions size="small" bordered column={1}>
        <Descriptions.Item label="原作答时间">{new Date(value.answered_at).toLocaleString('zh-CN')}</Descriptions.Item>
        <Descriptions.Item label="试卷编号">{value.instance_code ?? '缺失'}</Descriptions.Item>
        <Descriptions.Item label="当时主知识点">{value.knowledge_point_snapshot.name ?? '缺失'}</Descriptions.Item>
        <Descriptions.Item label="确认状态">{({ teacher_confirmed: '教师确认', auto_confirmed: '自动确认', needs_review: '待复核' } as Record<string, string>)[value.grade_status] ?? value.grade_status}</Descriptions.Item>
        <Descriptions.Item label="计入画像">{value.used_in_profile ? '是' : `否：${exclusionReasonText(value.exclusion_reason)}`}</Descriptions.Item>
      </Descriptions>
      <Typography.Title level={5}>原题快照</Typography.Title>
      <RichQuestionText text={value.question_snapshot.question_text ?? '题目快照缺失'} assets={value.assets} />
      {value.options_snapshot != null && <QuestionOptions options={value.options_snapshot} assets={value.assets} />}
      <Divider className="compact-divider" />
      <Typography.Text strong>学生答案</Typography.Text><AnswerView answer={value.student_answer} referenceAnswer={value.answer_snapshot} assets={value.assets} />
      <Typography.Text strong>当时标准答案</Typography.Text><AnswerView answer={value.answer_snapshot} assets={value.assets} />
      <Typography.Text type="secondary">修订原因：{value.revision?.reason ?? '修订记录缺失'}</Typography.Text>
      <details className="evidence-details"><summary>判分规则与修订记录</summary><pre className="json-block">{JSON.stringify({ rules: value.answer_rule_snapshot, revision: value.revision }, null, 2)}</pre></details>
      <Link to={`/questions/${value.question_id}`}>查看当前题库题目</Link>
    </Space>}
  </Drawer>
}
