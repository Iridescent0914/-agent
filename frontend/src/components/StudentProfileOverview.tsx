import { Alert, Button, Card, Space, Statistic, Switch, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useMemo, useState } from 'react'
import { apiGet, queryString } from '../api/client'
import type { ClassOverview } from '../types/analytics'
import type { QuestionCatalogChapter } from '../types'
import type { Profile } from '../types/profile'
import { KnowledgePointAccuracyChart, type AccuracyRow } from './KnowledgePointAccuracyChart'
import { LiteracyRadarChart } from './LiteracyRadarChart'

type LearningStatusRow = {
  code: string
  name: string
  section?: string | null
  status: 'yes' | 'no' | 'weak' | null
}

const statusFlag = (value: boolean | null, color: string) => value === null
  ? <Typography.Text type="secondary">未标记</Typography.Text>
  : value ? <Tag color={color}>是</Tag> : <Tag>否</Tag>

const learningStatusColumns: ColumnsType<LearningStatusRow> = [
  {
    title: '本章知识点',
    dataIndex: 'name',
    render: (_, row) => row.section ? `${row.section} · ${row.name}` : row.name,
  },
  {
    title: '是否为“否”',
    width: 150,
    align: 'center',
    render: (_, row) => statusFlag(
      row.status === null ? null : row.status === 'no' || row.status === 'weak',
      'orange',
    ),
  },
  {
    title: '是否为“弱”',
    width: 150,
    align: 'center',
    render: (_, row) => statusFlag(
      row.status === null ? null : row.status === 'weak',
      'red',
    ),
  },
]

export function StudentProfileOverview({
  profile,
  questionCatalog,
  className,
  grade,
  chapterCode,
}: {
  profile: Profile
  questionCatalog: QuestionCatalogChapter[]
  className: string
  grade: string
  chapterCode: string
}) {
  const literacies = profile.literacies ?? []
  const history = profile.historical_accuracy
  const visibleChapters = useMemo(
    () => questionCatalog.filter((item) => !grade || (item.grades ?? []).includes(grade)),
    [grade, questionCatalog],
  )
  const [showClassAverage, setShowClassAverage] = useState(true)
  const [classOverview, setClassOverview] = useState<ClassOverview | null>(null)
  const [classOverviewLoading, setClassOverviewLoading] = useState(false)
  const [classOverviewError, setClassOverviewError] = useState<string | null>(null)
  const [classOverviewReload, setClassOverviewReload] = useState(0)

  useEffect(() => {
    if (!className || !chapterCode) {
      setClassOverview(null)
      setClassOverviewLoading(false)
      setClassOverviewError(null)
      return
    }
    const controller = new AbortController()
    setClassOverview(null)
    setClassOverviewLoading(true)
    setClassOverviewError(null)
    apiGet<ClassOverview>(`/analytics/class-overview${queryString({ class_name: className, chapter_code: chapterCode, grade })}`, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setClassOverview(value) })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setClassOverviewError(reason.message)
      })
      .finally(() => {
        if (!controller.signal.aborted) setClassOverviewLoading(false)
      })
    return () => controller.abort()
  }, [chapterCode, className, grade, classOverviewReload])

  const chapterCatalog = useMemo(
    () => visibleChapters.find((item) => item.code === chapterCode)?.knowledge_points ?? [],
    [chapterCode, visibleChapters],
  )
  const classByCode = useMemo(
    () => new Map((classOverview?.knowledge_points ?? []).map((item) => [item.knowledge_point_code, item.accuracy])),
    [classOverview],
  )
  const masteryByCode = useMemo(
    () => new Map(profile.masteries.map((item) => [item.knowledge_point_code, item])),
    [profile.masteries],
  )
  const accuracyRows = useMemo<AccuracyRow[]>(() => chapterCatalog.map((point) => {
    const mastery = masteryByCode.get(point.code)
    return {
      code: point.code,
      name: point.name,
      section: point.section,
      accuracy: mastery?.attempt_count ? mastery.correct_count / mastery.attempt_count : null,
      classAverage: classByCode.get(point.code) ?? null,
    }
  }), [chapterCatalog, classByCode, masteryByCode])
  const learningStatusRows = useMemo<LearningStatusRow[]>(() => chapterCatalog.map((point) => ({
    code: point.code,
    name: point.name,
    section: point.section,
    status: masteryByCode.get(point.code)?.learning_status ?? null,
  })), [chapterCatalog, masteryByCode])

  return <Space direction="vertical" size="large" className="full-width">
    <Card title="历史累计正确率" aria-label="历史累计正确率统计">
      {history ? <Space direction="vertical" className="full-width">
        <Space wrap size="large">
          <Statistic title="累计正确率" value={history.accuracy === null ? '—' : history.accuracy * 100}
            precision={history.accuracy === null ? undefined : 1} suffix={history.accuracy === null ? undefined : '%'} />
          <Statistic title="累计已确认题数" value={history.confirmed_attempt_count} />
          <Statistic title="答对" value={history.correct_count} />
          <Statistic title="答错" value={history.incorrect_count} />
        </Space>
        {history.accuracy === null && <Typography.Text type="secondary">暂无已确认作答，暂不计算正确率。</Typography.Text>}
        <Typography.Text type="secondary">覆盖全部年级、全部章节；同一题不同次作答分别计入，每次只取当前判定，多空题按整题组统计。</Typography.Text>
        <Typography.Text type="secondary">共 {history.distinct_question_count} 道不同题；待复核 {history.pending_review_count} 题、失效或无效记录 {history.excluded_attempt_count} 条不计入正确率。</Typography.Text>
      </Space> : <Typography.Text type="secondary">累计统计暂不可用，请刷新后重试。</Typography.Text>}
    </Card>
    <div className="section-heading">
      <div>
        <Typography.Title level={4}>知识点正确率</Typography.Title>
        <Typography.Text type="secondary">按章节查看学生与班级平均的已确认作答正确率。</Typography.Text>
      </div>
      <Space wrap>
        {classOverview ? <Space><Switch checked={showClassAverage} onChange={setShowClassAverage} /><Typography.Text>显示班级平均</Typography.Text></Space> : null}
      </Space>
    </div>
    {classOverviewLoading ? <Alert type="info" showIcon message="正在加载班级平均数据" /> : null}
    {classOverviewError ? <Alert
      type="warning"
      showIcon
      message="班级平均加载失败，当前仅展示学生数据"
      description={classOverviewError}
      action={<Button size="small" onClick={() => setClassOverviewReload((value) => value + 1)}>重新加载</Button>}
    /> : null}
    <KnowledgePointAccuracyChart rows={accuracyRows} showClassAverage={showClassAverage && classOverview !== null} />
    <Card title="知识点回顾标签">
      <Table<LearningStatusRow>
        rowKey="code"
        columns={learningStatusColumns}
        dataSource={learningStatusRows}
        pagination={false}
        size="small"
        scroll={{ x: 620 }}
        locale={{ emptyText: '当前章节暂无知识点' }}
      />
      <Typography.Text type="secondary" className="knowledge-status-note">
        说明：“否”为日常作业中连续错两次，“弱”为周总结中也出错。
      </Typography.Text>
    </Card>
    <Card title="素养能力水平">
      <LiteracyRadarChart student={literacies} classAverage={classOverview?.literacies} />
    </Card>
  </Space>
}
