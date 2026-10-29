import { Card, Select, Space, Switch, Typography } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { apiGet, queryString } from '../api/client'
import type { ClassOverview } from '../types/analytics'
import type { QuestionCatalogChapter } from '../types'
import type { Profile } from '../types/profile'
import { KnowledgePointAccuracyChart, type AccuracyRow } from './KnowledgePointAccuracyChart'
import { LiteracyRadarChart } from './LiteracyRadarChart'

export function StudentProfileOverview({
  profile,
  questionCatalog,
  className,
}: {
  profile: Profile
  questionCatalog: QuestionCatalogChapter[]
  className: string
}) {
  const literacies = profile.literacies ?? []
  const gradeOptions = useMemo(
    () => Array.from(new Set(questionCatalog.flatMap((item) => item.grades ?? []))),
    [questionCatalog],
  )
  const [grade, setGrade] = useState(gradeOptions[0] ?? '')
  const visibleChapters = useMemo(
    () => questionCatalog.filter((item) => !grade || (item.grades ?? []).includes(grade)),
    [grade, questionCatalog],
  )
  const [chapterCode, setChapterCode] = useState(visibleChapters[0]?.code ?? '')
  const [showClassAverage, setShowClassAverage] = useState(true)
  const [classOverview, setClassOverview] = useState<ClassOverview | null>(null)

  useEffect(() => {
    if (grade && gradeOptions.includes(grade)) return
    if (gradeOptions[0]) setGrade(gradeOptions[0])
  }, [grade, gradeOptions])

  useEffect(() => {
    if (!visibleChapters.some((item) => item.code === chapterCode)) setChapterCode(visibleChapters[0]?.code ?? '')
  }, [chapterCode, visibleChapters])

  useEffect(() => {
    if (!className || !chapterCode) { setClassOverview(null); return }
    const controller = new AbortController()
    apiGet<ClassOverview>(`/analytics/class-overview${queryString({ class_name: className, chapter_code: chapterCode, grade })}`, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setClassOverview(value) })
      .catch(() => { if (!controller.signal.aborted) setClassOverview(null) })
    return () => controller.abort()
  }, [chapterCode, className, grade])

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

  return <Space direction="vertical" size="large" className="full-width">
    <div className="section-heading">
      <div>
        <Typography.Title level={4}>知识点正确率</Typography.Title>
        <Typography.Text type="secondary">按章节查看学生与班级平均的已确认作答正确率。</Typography.Text>
      </div>
      <Space wrap>
        <Select aria-label="选择画像年级" value={grade || undefined} onChange={setGrade} options={gradeOptions.map((item) => ({ value: item, label: item }))} style={{ width: 180 }} />
        <Select aria-label="选择画像章节" value={chapterCode || undefined} onChange={setChapterCode} options={visibleChapters.map((item) => ({ value: item.code, label: item.name }))} style={{ width: 220 }} />
        <Space><Switch checked={showClassAverage} onChange={setShowClassAverage} /><Typography.Text>显示班级平均</Typography.Text></Space>
      </Space>
    </div>
    <KnowledgePointAccuracyChart rows={accuracyRows} showClassAverage={showClassAverage} />
    <Card title="素养能力水平">
      <LiteracyRadarChart student={literacies} classAverage={classOverview?.literacies ?? literacies} />
    </Card>
  </Space>
}
