import { ReloadOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Radio, Select, Skeleton, Space, Statistic, Table, Tag, Tooltip, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { apiGet, queryString } from '../api/client'
import { AssignmentProgressPanel } from '../components/AssignmentProgressPanel'
import { ClassExamPointChart } from '../components/ClassExamPointChart'
import { ExamPointStudentDrawer } from '../components/ExamPointStudentDrawer'
import type { AssignmentListItem, AssignmentPage } from '../types/assignment'
import type { AssignmentAnalytics, ClassExamPointAnalytics, ClassFocusPoint, ClassOption, ClassOverview } from '../types/analytics'

const CHAPTER_CODE = 'math.g6.rational'
const focusLabels = { practice: '重点练习', recent_errors: '近期关注', observe: '补充证据', diagnostic: '安排诊断' }

export function ClassAnalyticsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [classes, setClasses] = useState<ClassOption[]>([])
  const [assignments, setAssignments] = useState<AssignmentListItem[]>([])
  const [className, setClassName] = useState(searchParams.get('class_name') ?? '')
  const [assignmentId, setAssignmentId] = useState(searchParams.get('assignment_id') ?? '')
  const [overview, setOverview] = useState<ClassOverview | null>(null)
  const [assignment, setAssignment] = useState<AssignmentAnalytics | null>(null)
  const [examPointCode, setExamPointCode] = useState<string | null>(null)
  const [metric, setMetric] = useState<'accuracy' | 'mastery'>('accuracy')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refresh, setRefresh] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError(null)
    apiGet<ClassOption[]>('/analytics/classes', controller.signal).then((classData) => {
      if (controller.signal.aborted) return
      setClasses(classData)
      setClassName((current) => classData.some((item) => item.class_name === current) ? current : classData[0]?.class_name ?? '')
    }).catch((reason: Error) => {
      if (!controller.signal.aborted) setError(reason.message)
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [refresh])

  useEffect(() => {
    if (!className) { setOverview(null); return }
    const controller = new AbortController()
    setOverview(null); setError(null)
    apiGet<ClassOverview>(`/analytics/class-overview${queryString({ class_name: className, chapter_code: CHAPTER_CODE })}`, controller.signal)
      .then(setOverview).catch((reason: Error) => { if (!controller.signal.aborted) setError(reason.message) })
    return () => controller.abort()
  }, [className, refresh])

  useEffect(() => {
    if (!className) { setAssignments([]); setAssignmentId(''); return }
    const controller = new AbortController()
    setAssignments([])
    setAssignmentId('')
    setAssignment(null)
    apiGet<AssignmentPage>(`/assignments${queryString({ page_size: 100, class_name: className })}`, controller.signal)
      .then((assignmentData) => {
        const usable = assignmentData.items.filter((item) => item.assignment_type !== 'historical_seed' && item.published_count > 0)
        setAssignments(usable)
        setAssignmentId(usable[0]?.id ?? '')
      })
      .catch((reason: Error) => { if (!controller.signal.aborted) setError(reason.message) })
    return () => controller.abort()
  }, [className, refresh])

  useEffect(() => {
    if (!assignmentId || !className) { setAssignment(null); return }
    const controller = new AbortController()
    setAssignment(null)
    apiGet<AssignmentAnalytics>(`/analytics/assignments/${assignmentId}${queryString({ class_name: className })}`, controller.signal)
      .then(setAssignment).catch((reason: Error) => { if (!controller.signal.aborted) setError(reason.message) })
    return () => controller.abort()
  }, [assignmentId, className, refresh])

  useEffect(() => {
    const next = new URLSearchParams()
    if (className) next.set('class_name', className)
    if (assignmentId) next.set('assignment_id', assignmentId)
    setSearchParams(next, { replace: true })
  }, [className, assignmentId, setSearchParams])

  const examColumns: ColumnsType<ClassExamPointAnalytics> = useMemo(() => [
    { title: '考点', dataIndex: 'exam_point_name', render: (value, row) => <Button className="table-link" type="link" onClick={() => setExamPointCode(row.exam_point_code)}>{value}</Button> },
    { title: '证据覆盖', width: 150, render: (_, row) => `${row.evidence_student_count}/${row.student_count} 名学生` },
    { title: '证据状态', width: 120, render: (_, row) => row.evidence_student_count === 0 ? <Tag color="blue">未评估</Tag> : row.stable_student_count < row.evidence_student_count ? <Tag>证据较少</Tag> : <Tag color="green">较稳定</Tag> },
    { title: '正确率', width: 150, render: (_, row) => row.accuracy === null ? '—' : `${(row.accuracy * 100).toFixed(1)}%（${row.correct_attempt_count}/${row.confirmed_attempt_count}）` },
    { title: '掌握度参考均值', width: 140, render: (_, row) => row.average_mastery === null ? '未评估' : row.average_mastery.toFixed(4) },
    { title: '最近错误', dataIndex: 'recent_wrong_count', width: 90 },
  ], [])

  const focusColumns: ColumnsType<ClassFocusPoint> = [
    { title: '考点', dataIndex: 'exam_point_name', render: (value, row) => <Button className="table-link" type="link" onClick={() => setExamPointCode(row.exam_point_code)}>{value}</Button> },
    { title: '建议', width: 110, render: (_, row) => <Tag color={row.category === 'practice' ? 'red' : row.category === 'recent_errors' ? 'orange' : row.category === 'diagnostic' ? 'blue' : 'default'}>{focusLabels[row.category]}</Tag> },
    { title: '依据', dataIndex: 'reason' },
  ]

  return <Space direction="vertical" size="large" className="page-stack analytics-workspace">
    <div className="student-heading">
      <div><Typography.Title level={2}>班级学情</Typography.Title><Typography.Text type="secondary">从班级概览下钻到学生、作答证据和批改操作</Typography.Text></div>
      <Space wrap>
        <Select aria-label="选择班级" value={className || undefined} placeholder="选择班级" onChange={setClassName} options={classes.map((item) => ({ value: item.class_name, label: `${item.class_name}（${item.student_count}人）` }))} style={{ width: 230 }} />
        <Tooltip title="刷新学情"><Button aria-label="刷新学情" icon={<ReloadOutlined />} onClick={() => setRefresh((value) => value + 1)} /></Tooltip>
      </Space>
    </div>
    {error && <Alert type="error" showIcon message="学情加载失败" description={error} action={<Button onClick={() => setRefresh((value) => value + 1)}>重试</Button>} />}
    {loading || (className && !overview && !error) ? <Skeleton active /> : !overview ? <Card><Typography.Text type="secondary">暂无可展示的班级数据，请先创建学生并导入确认作答。</Typography.Text></Card> : <>
      <div className="analytics-summary-grid">
        <Card size="small"><Statistic title="在册学生" value={overview.summary.student_count} suffix="人" /></Card>
        <Card size="small"><Statistic title="有有效证据" value={overview.summary.evidence_student_count} suffix="人" /></Card>
        <Card size="small"><Statistic title="确认作答" value={overview.summary.confirmed_attempt_count} suffix="次" /></Card>
        <Card size="small"><Statistic title="待复核" value={overview.summary.pending_review_count} suffix="题" valueStyle={overview.summary.pending_review_count ? { color: '#b45309' } : undefined} /></Card>
        <Card size="small"><Statistic title="已覆盖考点" value={overview.summary.covered_exam_point_count} suffix={`/ ${overview.summary.covered_exam_point_count + overview.summary.uncovered_exam_point_count}`} /></Card>
        <Card size="small"><Statistic title="异常证据" value={overview.summary.invalid_evidence_count} suffix="条" valueStyle={overview.summary.invalid_evidence_count ? { color: '#b42318' } : undefined} /></Card>
      </div>
      {(overview.summary.pending_review_count > 0 || assignment?.summary.unsubmitted_count) && <Alert type="warning" showIcon message="有待处理事项" description={<Space wrap>{overview.summary.pending_review_count > 0 && <Link to="/grading?status=needs_review">处理 {overview.summary.pending_review_count} 道待复核题</Link>}{Boolean(assignment?.summary.unsubmitted_count) && <Link to="/grading?status=unsubmitted">查看 {assignment?.summary.unsubmitted_count} 份未提交试卷</Link>}</Space>} />}
      <Card title="考点学情" extra={<Radio.Group value={metric} onChange={(event) => setMetric(event.target.value)} options={[{ label: '正确率', value: 'accuracy' }, { label: '掌握度参考值', value: 'mastery' }]} optionType="button" />}>
        <ClassExamPointChart items={overview.exam_points} metric={metric} onSelect={setExamPointCode} />
        <Typography.Paragraph type="secondary">{overview.evidence_note} 图表中的灰色表示证据较少，不表示能力弱。</Typography.Paragraph>
        <Table rowKey="exam_point_code" columns={examColumns} dataSource={overview.exam_points} pagination={false} scroll={{ x: 900 }} size="small" />
      </Card>
      <Card title="需关注考点"><Table rowKey="exam_point_code" columns={focusColumns} dataSource={overview.focus_points} pagination={{ pageSize: 6, hideOnSinglePage: true }} /></Card>
      <Card title={`${className} · 作业进度`} extra={assignments.length > 0 && <Select aria-label="选择作业" value={assignmentId || undefined} onChange={setAssignmentId} options={assignments.map((item) => ({ value: item.id, label: item.title }))} style={{ width: 280, maxWidth: '70vw' }} />}>
        <AssignmentProgressPanel data={assignment} />
      </Card>
      <Alert
        type="info"
        showIcon
        message="这些数字是怎么计算的？"
        description={<div>
          <Typography.Paragraph>
            <Typography.Text strong>正确率：</Typography.Text>
            把本班在该考点下所有已经确认的作答合在一起，用“答对次数 ÷ 作答次数”。例如确认作答 20 次、答对 15 次，正确率就是 75%。
          </Typography.Paragraph>
          <Typography.Paragraph>
            <Typography.Text strong>掌握度参考值：</Typography.Text>
            每名学生每个考点从 50% 起步。每次确认作答后按“旧值 × 80% + 本次结果 × 20%”更新，答对时本次结果为 100%，答错为 0%。例如 50% 后答对一次变为 60%，随后答错一次变为 48%；因此越近的作答影响越大。
          </Typography.Paragraph>
          <Typography.Paragraph className="no-margin">
            <Typography.Text strong>班级掌握度：</Typography.Text>
            先分别计算每名有作答记录学生的掌握度，再求平均。没有做过该考点题目的学生不参与平均；待复核答案在确认前既不算对，也不算错。
          </Typography.Paragraph>
        </div>}
      />
      <Typography.Text type="secondary">计算时间：{new Date(overview.computed_at).toLocaleString('zh-CN')} · 规则版本：{overview.rule_version}</Typography.Text>
    </>}
    <ExamPointStudentDrawer examPointCode={examPointCode} className={className} chapterCode={CHAPTER_CODE} onClose={() => setExamPointCode(null)} />
  </Space>
}
