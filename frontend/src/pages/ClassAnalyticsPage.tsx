import { ReloadOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Select, Skeleton, Space, Tooltip, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { apiGet, queryString } from '../api/client'
import { AssignmentProgressPanel } from '../components/AssignmentProgressPanel'
import { KnowledgePointAccuracyChart } from '../components/KnowledgePointAccuracyChart'
import { LiteracyRadarChart } from '../components/LiteracyRadarChart'
import type { QuestionCatalogChapter } from '../types'
import type { AssignmentListItem, AssignmentPage } from '../types/assignment'
import type { AssignmentAnalytics, ClassOption, ClassOverview } from '../types/analytics'

export function ClassAnalyticsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [classes, setClasses] = useState<ClassOption[]>([])
  const [chapters, setChapters] = useState<QuestionCatalogChapter[]>([])
  const [assignments, setAssignments] = useState<AssignmentListItem[]>([])
  const [className, setClassName] = useState(searchParams.get('class_name') ?? '')
  const [grade, setGrade] = useState(searchParams.get('grade') ?? '')
  const [chapterCode, setChapterCode] = useState(searchParams.get('chapter_code') ?? '')
  const [assignmentId, setAssignmentId] = useState(searchParams.get('assignment_id') ?? '')
  const [overview, setOverview] = useState<ClassOverview | null>(null)
  const [assignment, setAssignment] = useState<AssignmentAnalytics | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refresh, setRefresh] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError(null)
    Promise.all([
      apiGet<ClassOption[]>('/analytics/classes', controller.signal),
      apiGet<QuestionCatalogChapter[]>('/questions/catalog', controller.signal),
    ]).then(([classData, chapterData]) => {
      if (controller.signal.aborted) return
      setClasses(classData)
      setChapters(chapterData)
      setClassName((current) => classData.some((item) => item.class_name === current) ? current : classData[0]?.class_name ?? '')
      const grades = Array.from(new Set(chapterData.flatMap((item) => item.grades ?? [])))
      setGrade((current) => grades.includes(current) ? current : grades[0] ?? '')
    }).catch((reason: Error) => {
      if (!controller.signal.aborted) setError(reason.message)
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [refresh])

  const gradeOptions = Array.from(new Set(chapters.flatMap((item) => item.grades ?? [])))
  const visibleChapters = chapters.filter((item) => !grade || (item.grades ?? []).includes(grade))

  useEffect(() => {
    setChapterCode((current) => visibleChapters.some((item) => item.code === current)
      ? current
      : visibleChapters[0]?.code ?? '')
  }, [grade, chapters])

  useEffect(() => {
    if (!className || !chapterCode) { setOverview(null); return }
    const controller = new AbortController()
    setOverview(null); setError(null)
    apiGet<ClassOverview>(`/analytics/class-overview${queryString({ class_name: className, chapter_code: chapterCode, grade })}`, controller.signal)
      .then(setOverview).catch((reason: Error) => { if (!controller.signal.aborted) setError(reason.message) })
    return () => controller.abort()
  }, [className, chapterCode, grade, refresh])

  useEffect(() => {
    if (!className || !chapterCode) { setAssignments([]); setAssignmentId(''); return }
    const controller = new AbortController()
    setAssignments([])
    setAssignmentId('')
    setAssignment(null)
    const loadAssignments = async () => {
      const first = await apiGet<AssignmentPage>(`/assignments${queryString({ page: 1, page_size: 100, class_name: className, chapter_code: chapterCode })}`, controller.signal)
      const pages = Math.ceil(first.total / first.page_size)
      const rest = pages > 1 ? await Promise.all(Array.from({ length: pages - 1 }, (_, index) =>
        apiGet<AssignmentPage>(`/assignments${queryString({ page: index + 2, page_size: 100, class_name: className, chapter_code: chapterCode })}`, controller.signal))) : []
      return [first, ...rest].flatMap((item) => item.items)
    }
    loadAssignments()
      .then((items) => {
        const usable = items.filter((item) => item.assignment_type !== 'historical_seed' && item.published_count > 0)
        setAssignments(usable)
        setAssignmentId(usable[0]?.id ?? '')
      })
      .catch((reason: Error) => { if (!controller.signal.aborted) setError(reason.message) })
    return () => controller.abort()
  }, [className, chapterCode, refresh])

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
    if (grade) next.set('grade', grade)
    if (chapterCode) next.set('chapter_code', chapterCode)
    if (assignmentId) next.set('assignment_id', assignmentId)
    setSearchParams(next, { replace: true })
  }, [className, grade, chapterCode, assignmentId, setSearchParams])

  return <Space direction="vertical" size="large" className="page-stack analytics-workspace">
      <div className="student-heading">
      <div><Typography.Title level={2}>班级学情</Typography.Title><Typography.Text type="secondary">查看知识点平均正确率、素养能力和作业进度</Typography.Text></div>
      <Space wrap>
        <Select aria-label="选择班级" value={className || undefined} placeholder="选择班级" onChange={setClassName} options={classes.map((item) => ({ value: item.class_name, label: `${item.class_name}（${item.student_count}人）` }))} style={{ width: 230 }} />
        <Select aria-label="选择年级" value={grade || undefined} placeholder="选择年级" onChange={setGrade} options={gradeOptions.map((item) => ({ value: item, label: item }))} style={{ width: 180 }} />
        <Select aria-label="选择章节" value={chapterCode || undefined} placeholder="选择章节" onChange={setChapterCode} options={visibleChapters.map((item) => ({ value: item.code, label: item.name }))} style={{ width: 230 }} />
        <Tooltip title="刷新学情"><Button aria-label="刷新学情" icon={<ReloadOutlined />} onClick={() => setRefresh((value) => value + 1)} /></Tooltip>
      </Space>
    </div>
    {error && <Alert type="error" showIcon message="学情加载失败" description={error} action={<Button onClick={() => setRefresh((value) => value + 1)}>重试</Button>} />}
    {loading || (className && !overview && !error) ? <Skeleton active /> : !overview ? <Card><Typography.Text type="secondary">暂无可展示的班级数据，请先创建学生并导入确认作答。</Typography.Text></Card> : <>
      <Card title="知识点平均正确率">
        <KnowledgePointAccuracyChart rows={overview.knowledge_points.map((item) => ({
          code: item.knowledge_point_code,
          name: item.knowledge_point_name,
          section: item.section,
          accuracy: item.accuracy,
          classAverage: null,
        }))} showClassAverage={false} label="班级知识点平均正确率柱状图" />
      </Card>
      <Card title="素养能力平均水平">
        <LiteracyRadarChart student={overview.literacies ?? []} classAverage={overview.literacies ?? []} label="班级素养能力平均水平雷达图" />
      </Card>
      <Card title={`${className} · 作业进度`} extra={assignments.length > 0 && <Select aria-label="选择作业" value={assignmentId || undefined} onChange={setAssignmentId} options={assignments.map((item) => ({ value: item.id, label: item.title }))} style={{ width: 280, maxWidth: '70vw' }} />}>
        <AssignmentProgressPanel data={assignment} />
      </Card>
      <Typography.Text type="secondary">计算时间：{new Date(overview.computed_at).toLocaleString('zh-CN')} · 规则版本：{overview.rule_version}</Typography.Text>
    </>}
  </Space>
}
