import {
  Alert, Button, Card, Divider, Empty, Form, Input,
  InputNumber, Select, Space, Table, Tag, Typography, message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiGet, apiPost } from '../api/client'
import type { ClassSummary, QuestionCatalogChapter } from '../types'
import type {
  AssignmentDetail, AssignmentFilterConfig, AssignmentListItem, AssignmentPreview,
  AssignmentPreviewStudent, AssignmentRequest,
} from '../types/assignment'
import type { KnowledgePoint, RecommendedQuestion, RecommendationParams } from '../types/profile'

interface FormValues extends RecommendationParams {
  title: string
  class_id: string
  grade_codes: string[]
  chapter_codes: string[]
  required_knowledge_point_codes: string[]
  required_core_literacies: string[]
  difficulty_counts: Record<string, number>
  personalized_knowledge_point_codes: string[]
  primary_competency?: string
  history_assignment_id?: string
}

const defaults: Partial<FormValues> = {
  title: '数学作业', class_id: '',
  grade_codes: [], chapter_codes: [], required_knowledge_point_codes: [],
  required_core_literacies: [], difficulty_counts: { '1': 0, '2': 6, '3': 0, '4': 0, '5': 0 },
  personalized_knowledge_point_codes: [], primary_competency: undefined,
  history_assignment_id: undefined, new_knowledge_point_codes: [],
  review_knowledge_point_codes: [], new_count: 6, review_count: 0,
  question_types: ['choice', 'fill_blank'], base_difficulty: 2,
  min_difficulty: 1, max_difficulty: 5, avoid_recent_days: 7,
  max_knowledge_point_ratio: 0.5,
}

type FilterModule = 'required' | 'personalized' | 'history' | null

const CORE_LITERACIES = [
  '抽象能力', '运算能力', '几何直观', '空间观念',
  '推理能力', '数据观念', '模型观念', '应用意识',
]
const ALL_KNOWLEDGE_POINTS = '__all_knowledge_points__'
const ALL_CHAPTER_KNOWLEDGE_POINTS = '__all_chapter_knowledge_points__:'
const ALL_GRADES = '__all_grades__'
const ALL_CHAPTERS = '__all_chapters__'
const ALL_GRADE_CHAPTERS = '__all_grade_chapters__:'

const isConfiguredGrade = (value: string | null | undefined): value is string => {
  const normalized = value?.trim()
  return Boolean(normalized) && normalized !== '未设置'
}

export function AssignmentCreatePage() {
  const [form] = Form.useForm<FormValues>()
  const navigate = useNavigate()
  const [classes, setClasses] = useState<ClassSummary[]>([])
  const [chapters, setChapters] = useState<QuestionCatalogChapter[]>([])
  const [catalog, setCatalog] = useState<KnowledgePoint[]>([])
  const [recentAssignments, setRecentAssignments] = useState<AssignmentListItem[]>([])
  const [activeModule, setActiveModule] = useState<FilterModule>('required')
  const [preview, setPreview] = useState<AssignmentPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const key = useRef(crypto.randomUUID())

  useEffect(() => {
    Promise.all([
      apiGet<ClassSummary[]>('/classes'),
      apiGet<QuestionCatalogChapter[]>('/questions/catalog'),
      apiGet<AssignmentListItem[]>('/assignments/recent-completed?limit=5'),
    ])
      .then(([classItems, chapterItems, recentItems]) => {
        setClasses(classItems); setChapters(chapterItems); setRecentAssignments(recentItems)
        const initialClass = classItems.find((item) => item.student_count > 0) ?? classItems[0]
        const grades = Array.from(new Set([
          ...classItems.map((item) => item.grade).filter(Boolean),
          ...chapterItems.flatMap((item) => item.grades ?? []),
        ].filter(isConfiguredGrade)))
        form.setFieldsValue({
          class_id: initialClass?.id,
          grade_codes: grades,
          chapter_codes: chapterItems.map((item) => item.code),
        })
      })
      .catch((reason) => setError((reason as Error).message))
  }, [form])

  const selectedClassId = Form.useWatch('class_id', form)
  const selectedChapterCodes = Form.useWatch('chapter_codes', form) ?? []
  const selectedRequiredKps = Form.useWatch('required_knowledge_point_codes', form) ?? []
  const selectedPersonalizedKps = Form.useWatch('personalized_knowledge_point_codes', form) ?? []
  const selectedHistoryAssignmentId = Form.useWatch('history_assignment_id', form)
  const difficultyCounts = Form.useWatch('difficulty_counts', form) ?? {}

  useEffect(() => {
    if (!selectedChapterCodes.length) {
      setCatalog([])
      form.setFieldsValue({ required_knowledge_point_codes: [], personalized_knowledge_point_codes: [] })
      return
    }
    const controller = new AbortController()
    Promise.all(selectedChapterCodes.map((chapter) => apiGet<KnowledgePoint[]>(
      `/knowledge-points?chapter_code=${encodeURIComponent(chapter)}`, controller.signal,
    )))
      .then((results) => {
        const merged = Array.from(new Map(results.flat().map((item) => [item.code, item])).values())
        setCatalog(merged)
        const allowed = new Set(merged.map((item) => item.code))
        form.setFieldsValue({
          required_knowledge_point_codes: (form.getFieldValue('required_knowledge_point_codes') ?? []).filter((code: string) => allowed.has(code)),
          personalized_knowledge_point_codes: (form.getFieldValue('personalized_knowledge_point_codes') ?? []).filter((code: string) => allowed.has(code)),
        })
      })
      .catch((reason: Error) => { if (!controller.signal.aborted) setError(reason.message) })
    return () => controller.abort()
  }, [form, selectedChapterCodes])

  const selectedClass = classes.find((item) => item.id === selectedClassId)
  const selectedChapters = chapters.filter((item) => selectedChapterCodes.includes(item.code))
  const available = useMemo(() => catalog.filter((point) => point.available_question_count > 0), [catalog])
  const knowledgePointOptions = selectedChapters.map((chapter) => {
    const chapterPoints = available.filter((point) => chapter.knowledge_points.some((candidate) => candidate.code === point.code))
    return {
      label: <span className="knowledge-point-chapter-label">{chapter.name}</span>,
      options: [
        { value: `${ALL_CHAPTER_KNOWLEDGE_POINTS}${chapter.code}`, label: `选择本章全部知识点（${chapterPoints.length}个）` },
        ...chapterPoints.map((point) => ({ value: point.code, label: `${point.name}（${point.available_question_count}题）` })),
      ],
    }
  })
  const knowledgePointSelectOptions = [
    { value: ALL_KNOWLEDGE_POINTS, label: `选择全部知识点（${available.length}个）` },
    ...knowledgePointOptions,
  ]
  const sectionOptions = Array.from(new Set(selectedChapters.flatMap((item) => item.sections))).map((value) => ({ value, label: value }))
  const coreLiteracyOptions = CORE_LITERACIES.map((value) => ({ value, label: value }))
  const competencyOptions = Array.from(new Set(selectedPersonalizedKps.flatMap((code) => {
    const point = selectedChapters.flatMap((item) => item.knowledge_points).find((candidate) => candidate.code === code)
    return point?.core_competencies ?? []
  }))).map((value) => ({ value, label: value }))
  const gradeValues = Array.from(new Set([
    ...classes.map((item) => item.grade).filter(Boolean), ...chapters.flatMap((item) => item.grades ?? []),
  ].filter(isConfiguredGrade)))
  const gradeOptions = gradeValues.map((value) => ({ value, label: value }))
  const gradeSelectOptions = [{ value: ALL_GRADES, label: '全部年级' }, ...gradeOptions]
  const chapterGroups = gradeValues.map((grade) => {
    const gradeChapters = chapters.filter((chapter) => chapter.grades?.includes(grade))
    return {
      label: <span className="chapter-grade-label">{grade}</span>,
      options: [
        { value: `${ALL_GRADE_CHAPTERS}${grade}`, label: `选择本年级全部章节（${gradeChapters.length}章）` },
        ...gradeChapters.map((chapter) => ({ value: chapter.code, label: chapter.name })),
      ],
    }
  }).filter((group) => group.options.length > 1)
  const groupedChapterCodes = new Set(chapterGroups.flatMap((group) => group.options.slice(1).map((option) => option.value)))
  const ungroupedChapters = chapters.filter((chapter) => !groupedChapterCodes.has(chapter.code))
  const chapterSelectOptions = [
    { value: ALL_CHAPTERS, label: `选择全部章节（${chapters.length}章）` },
    ...chapterGroups,
    ...(ungroupedChapters.length ? [{
      label: <span className="chapter-grade-label">其他章节</span>,
      options: ungroupedChapters.map((chapter) => ({ value: chapter.code, label: chapter.name })),
    }] : []),
  ]
  const totalRequiredCount = Object.values(difficultyCounts).reduce((sum, value) => sum + Number(value || 0), 0)

  const handleKnowledgePointChange = (values: string[]) => {
    if (values.includes(ALL_KNOWLEDGE_POINTS)) {
      form.setFieldValue('required_knowledge_point_codes', available.map((point) => point.code))
      return
    }
    const expanded = new Set(values.filter((value) => !value.startsWith(ALL_CHAPTER_KNOWLEDGE_POINTS)))
    values.filter((value) => value.startsWith(ALL_CHAPTER_KNOWLEDGE_POINTS)).forEach((value) => {
      const chapterCode = value.slice(ALL_CHAPTER_KNOWLEDGE_POINTS.length)
      const chapter = selectedChapters.find((item) => item.code === chapterCode)
      chapter?.knowledge_points.forEach((point) => {
        if (available.some((candidate) => candidate.code === point.code)) expanded.add(point.code)
      })
    })
    form.setFieldValue('required_knowledge_point_codes', Array.from(expanded))
  }

  const handleGradeChange = (values: string[]) => {
    form.setFieldValue('grade_codes', values.includes(ALL_GRADES) ? gradeValues : values.filter(isConfiguredGrade))
  }

  const handleChapterChange = (values: string[]) => {
    if (values.includes(ALL_CHAPTERS)) {
      form.setFieldValue('chapter_codes', chapters.map((chapter) => chapter.code))
      return
    }
    const expanded = new Set(values.filter((value) => !value.startsWith(ALL_GRADE_CHAPTERS)))
    values.filter((value) => value.startsWith(ALL_GRADE_CHAPTERS)).forEach((value) => {
      const grade = value.slice(ALL_GRADE_CHAPTERS.length)
      chapters.filter((chapter) => chapter.grades?.includes(grade)).forEach((chapter) => expanded.add(chapter.code))
    })
    form.setFieldValue('chapter_codes', Array.from(expanded))
  }

  const buildFilterConfig = (values: FormValues): AssignmentFilterConfig => ({
    grade_codes: values.grade_codes, chapter_codes: values.chapter_codes,
    required: {
      knowledge_point_codes: values.required_knowledge_point_codes,
      core_literacies: values.required_core_literacies,
      difficulty_counts: values.difficulty_counts,
    },
    personalized: {
      knowledge_point_codes: values.personalized_knowledge_point_codes,
      primary_competency: values.primary_competency || null, question_count: 3,
    },
    history: { assignment_id: values.history_assignment_id || null },
  })

  const build = (values: FormValues): AssignmentRequest => {
    const selectedCodes = values.required_knowledge_point_codes.length
      ? values.required_knowledge_point_codes : available.map((point) => point.code)
    return {
      class_id: values.class_id, student_scope: 'all_class', student_ids: [],
      filter_config: buildFilterConfig(values),
      recommendation_parameters: {
        chapter_code: values.chapter_codes[0], chapter_codes: values.chapter_codes,
        grade_codes: values.grade_codes, new_knowledge_point_codes: selectedCodes,
        review_knowledge_point_codes: [], new_count: totalRequiredCount, review_count: 0,
        question_types: values.question_types, base_difficulty: values.base_difficulty,
        min_difficulty: values.min_difficulty, max_difficulty: values.max_difficulty,
        avoid_recent_days: values.avoid_recent_days, max_knowledge_point_ratio: values.max_knowledge_point_ratio,
      },
    }
  }

  const validateNewFilters = (values: FormValues) => {
    if (!values.grade_codes?.length || !values.chapter_codes?.length) return '请选择至少一个年级和章节'
    if (totalRequiredCount < 1) return '请设置至少 1 道必做题'
    if (values.history_assignment_id && !recentAssignments.some((item) => item.id === values.history_assignment_id)) return '历史回顾作业无效，请重新选择'
    return null
  }

  const runPreview = async (values: FormValues) => {
    const validationError = validateNewFilters(values)
    if (validationError) { setError(validationError); return }
    setLoading(true); setError(null); setPreview(null)
    try { setPreview(await apiPost<AssignmentPreview>('/assignments/preview', build(values))) }
    catch (reason) { setError((reason as Error).message) }
    finally { setLoading(false) }
  }

  const generate = async () => {
    const values = await form.validateFields(); const validationError = validateNewFilters(values)
    if (validationError) { setError(validationError); return }
    setLoading(true); setError(null)
    try {
      const created = await apiPost<AssignmentDetail>('/assignments', { ...build(values), title: values.title, idempotency_key: key.current })
      message.success('草稿已生成'); navigate(`/assignments/${created.id}`)
    } catch (reason) { setError((reason as Error).message) }
    finally { setLoading(false) }
  }

  const columns: ColumnsType<AssignmentPreviewStudent> = [
    { title: '学生', render: (_, row) => `${row.student_code} · ${row.student_name}` },
    { title: '结果', render: (_, row) => <Tag color={row.recommendation.shortfall ? 'orange' : 'green'}>{row.recommendation.selected_count} / {row.recommendation.requested_count} 题</Tag> },
    { title: '策略', render: (_, row) => `${row.recommendation.items.filter((item) => item.strategy === 'personalized').length} 道个性化 · ${row.recommendation.items.filter((item) => item.strategy === 'diagnostic').length} 道诊断` },
    { title: '缺口', render: (_, row) => row.recommendation.shortfall ? `缺 ${row.recommendation.shortfall} 题` : '无' },
  ]
  const requiredQuestionColumns: ColumnsType<RecommendedQuestion> = [
    { title: '题号', dataIndex: 'original_qid', width: 180 },
    { title: '知识点', dataIndex: 'knowledge_point_name' },
    { title: '难度', dataIndex: 'difficulty', width: 80 },
    { title: '题型', dataIndex: 'question_type', width: 100 },
  ]
  const moduleButton = (module: Exclude<FilterModule, null>, label: string, summary: string) => (
    <button type="button" className={`assignment-filter-slider ${activeModule === module ? 'active' : ''}`} onMouseEnter={() => setActiveModule(module)} onClick={() => setActiveModule(activeModule === module ? null : module)}>
      <span className="assignment-filter-slider-title">{label}</span><span className="assignment-filter-slider-summary">{summary}</span>
    </button>
  )

  return <Space direction="vertical" size="large" className="page-stack assignment-workspace">
    <div className="student-heading"><div><Typography.Title level={2}>创建个性化作业</Typography.Title><Typography.Text type="secondary">先确定作业范围，再配置必做题、个性化和历史回顾模块</Typography.Text></div></div>
    {error && <Alert type="error" showIcon message="操作失败" description={error} />}
    <Card>
      <Form
        form={form}
        layout="vertical"
        initialValues={defaults}
        onFinish={runPreview}
        onFinishFailed={() => setError('请先完成作业标题、班级、年级和章节选择')}
        onValuesChange={() => { setPreview(null); setError(null) }}
      >
        <div className="assignment-form-grid">
          <Form.Item label="作业标题" name="title" rules={[{ required: true }]}><Input maxLength={255} /></Form.Item>
          <Form.Item label="班级" name="class_id" rules={[{ required: true, message: '请选择班级' }]}><Select placeholder="请选择班级" options={classes.map((item) => ({ value: item.id, label: `${item.name}（${item.student_count}人）` }))} /></Form.Item>
          <Form.Item label="年级（可多选）" name="grade_codes" rules={[{ required: true, type: 'array', min: 1, message: '请选择至少一个年级' }]}><Select mode="multiple" options={gradeSelectOptions} onChange={handleGradeChange} placeholder="请选择年级" /></Form.Item>
          <Form.Item label="章（可多选）" name="chapter_codes" rules={[{ required: true, type: 'array', min: 1, message: '请选择至少一个章节' }]}><Select mode="multiple" options={chapterSelectOptions} onChange={handleChapterChange} placeholder="请选择章节" /></Form.Item>
          <Form.Item label="学生范围"><Typography.Text type="secondary">{selectedClass ? `当前班级全部学生（${selectedClass.student_count}人）` : '请先选择班级'}</Typography.Text></Form.Item>
        </div>

        <Divider orientation="left">题目筛选模块</Divider>
        <div className="assignment-filter-sliders">
          {moduleButton('required', '必做题', `${totalRequiredCount} 道 · ${selectedRequiredKps.length ? `${selectedRequiredKps.length} 个知识点` : '全章节'}`)}
          {moduleButton('personalized', '个性化', `${selectedPersonalizedKps.length ? `${selectedPersonalizedKps.length} 个知识点` : '待配置'} · 3 道`)}
          {moduleButton('history', '历史回顾', selectedHistoryAssignmentId ? '已选择作业' : '待选择作业')}
        </div>

        {activeModule === 'required' && <Card size="small" className="assignment-filter-panel" title="必做题筛选" extra={<Button type="link" onClick={() => setActiveModule(null)}>收起</Button>}>
          <div className="assignment-filter-grid">
            <Form.Item label="节（知识点，可选）" name="required_knowledge_point_codes"><Select mode="multiple" showSearch optionFilterProp="label" options={knowledgePointSelectOptions} onChange={handleKnowledgePointChange} placeholder="不选表示覆盖所选章节" /></Form.Item>
            <Form.Item label="核心素养（可选，可多选）" name="required_core_literacies"><Select mode="multiple" options={coreLiteracyOptions} placeholder="可独立选择核心素养，与知识点取交集" /></Form.Item>
          </div>
          {sectionOptions.length > 0 && <Typography.Text type="secondary">已按所选章节提供节与知识点选项</Typography.Text>}
          <Divider plain>按难度设置必做题数量</Divider>
          <div className="difficulty-count-grid">{[1, 2, 3, 4, 5].map((difficulty) => <Form.Item key={difficulty} label={`${difficulty} 星`} name={['difficulty_counts', String(difficulty)]}><InputNumber min={0} max={30} precision={0} /></Form.Item>)}</div>
        </Card>}

        {activeModule === 'personalized' && <Card size="small" className="assignment-filter-panel" title="个性化筛选" extra={<Button type="link" onClick={() => setActiveModule(null)}>收起</Button>}>
          <Alert type="info" showIcon message="当前阶段只保存筛选条件，个性化出题算法将在后续接入" />
          <Form.Item label="节（知识点，后续启用）" name="personalized_knowledge_point_codes"><Select mode="multiple" showSearch optionFilterProp="label" options={knowledgePointOptions} placeholder="后续配置个性化知识点" /></Form.Item>
          <Form.Item label="主要能力（任选其一）" name="primary_competency"><Select allowClear options={competencyOptions} placeholder={selectedPersonalizedKps.length ? '请选择主要能力' : '先选择知识点'} /></Form.Item>
          <Typography.Text type="secondary">个性化模块固定预留 3 道题。</Typography.Text>
        </Card>}

        {activeModule === 'history' && <Card size="small" className="assignment-filter-panel" title="历史回顾筛选" extra={<Button type="link" onClick={() => setActiveModule(null)}>收起</Button>}>
          <Alert type="info" showIcon message="请选择要回顾的已完成作业，系统不会根据“昨天”等相对时间自动猜测" />
          <Form.Item label="最近 5 次已完成作业" name="history_assignment_id"><Select allowClear options={recentAssignments.map((item) => ({ value: item.id, label: `${item.title} · ${new Date(item.created_at).toLocaleString('zh-CN')}` }))} placeholder={recentAssignments.length ? '请选择作业' : '暂无已完成作业'} notFoundContent={<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无已完成作业" />} /></Form.Item>
        </Card>}

        <Button type="primary" htmlType="submit" loading={loading}>预检全部学生</Button>
      </Form>
    </Card>
    {preview && <Card title="预检结果" extra={<Typography.Text type="secondary">预检不会写入作业</Typography.Text>}>
      <Alert type={preview.shortfall_student_count ? 'warning' : 'success'} showIcon message={`${preview.ready_count} / ${preview.student_count} 名学生可完整生成`} description={`统一基准时间：${new Date(preview.as_of).toLocaleString('zh-CN')}`} />
      <Table className="preview-table" rowKey="student_id" columns={columns} dataSource={preview.students} pagination={{ pageSize: 20, showSizeChanger: true }} scroll={{ x: 720 }} />
      {preview.students[0]?.recommendation.items.length > 0 && <Card size="small" title={`必做题草稿（${preview.students[0].recommendation.items.length} 题，所有学生相同）`}>
        <Table
          size="small"
          rowKey="question_id"
          columns={requiredQuestionColumns}
          dataSource={preview.students[0].recommendation.items}
          pagination={false}
        />
      </Card>}
      {preview.students[0]?.recommendation.items.length === 0 && <Alert type="warning" showIcon message="当前筛选条件没有可用的必做题" description="请减少难度题量，或放宽年级、章节、知识点和核心素养筛选范围。" />}
      <Button type="primary" onClick={generate} loading={loading}>生成草稿</Button>
    </Card>}
  </Space>
}
