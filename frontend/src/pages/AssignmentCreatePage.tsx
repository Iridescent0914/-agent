import {
  Alert, Button, Card, Divider, Empty, Form, Input,
  InputNumber, Select, Space, Table, Tag, Typography, message,
} from 'antd'
import {
  BookOutlined, BulbOutlined, CheckCircleFilled, HistoryOutlined,
  SettingOutlined, ThunderboltOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
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

const expandChapterSelection = (values: string[], chapters: QuestionCatalogChapter[]) => {
  const selection = Array.isArray(values) ? values : []
  if (selection.includes(ALL_CHAPTERS)) return chapters.map((chapter) => chapter.code)
  const expanded = new Set(selection.filter((value) => !value.startsWith(ALL_GRADE_CHAPTERS)))
  selection.filter((value) => value.startsWith(ALL_GRADE_CHAPTERS)).forEach((value) => {
    const grade = value.slice(ALL_GRADE_CHAPTERS.length)
    chapters.filter((chapter) => chapter.grades?.includes(grade)).forEach((chapter) => expanded.add(chapter.code))
  })
  return Array.from(expanded)
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
    ])
      .then(([classItems, chapterItems]) => {
        setClasses(classItems); setChapters(chapterItems)
        const initialClass = classItems.find((item) => item.student_count > 0) ?? classItems[0]
        form.setFieldsValue({
          class_id: initialClass?.id,
          grade_codes: [ALL_GRADES],
          chapter_codes: [ALL_CHAPTERS],
          difficulty_counts: { '1': 0, '2': 6, '3': 0, '4': 0, '5': 0 },
        })
      })
      .catch((reason) => setError((reason as Error).message))
  }, [form])

  const selectedClassId = Form.useWatch('class_id', form)
  const selectedChapterSelection = Form.useWatch('chapter_codes', form) ?? []
  const selectedChapterCodes = useMemo(
    () => expandChapterSelection(selectedChapterSelection, chapters),
    [selectedChapterSelection, chapters],
  )
  const selectedRequiredKps = Form.useWatch('required_knowledge_point_codes', form) ?? []
  const selectedPersonalizedKps = Form.useWatch('personalized_knowledge_point_codes', form) ?? []
  const selectedHistoryAssignmentId = Form.useWatch('history_assignment_id', form)
  const difficultyCounts = Form.useWatch('difficulty_counts', form) ?? defaults.difficulty_counts ?? {}

  useEffect(() => {
    setRecentAssignments([])
    form.setFieldValue('history_assignment_id', undefined)
    if (!selectedClassId) {
      return
    }
    const controller = new AbortController()
    apiGet<AssignmentListItem[]>(
      `/assignments/recent-completed?limit=5&class_id=${encodeURIComponent(selectedClassId)}`,
      controller.signal,
    )
      .then((items) => {
        setRecentAssignments(items.filter((item) => item.class_id === selectedClassId))
      })
      .catch((reason: Error) => { if (!controller.signal.aborted) setError(reason.message) })
    return () => controller.abort()
  }, [form, selectedClassId])

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
        const requiredAllowed = new Set(
          merged
            .filter((item) => item.required_available_question_count > 0)
            .map((item) => item.code),
        )
        const retainKnowledgePointSelection = (values: string[], codes: Set<string>) => values.filter((value) => {
          if (value === ALL_KNOWLEDGE_POINTS) return true
          if (value.startsWith(ALL_CHAPTER_KNOWLEDGE_POINTS)) {
            return selectedChapterCodes.includes(value.slice(ALL_CHAPTER_KNOWLEDGE_POINTS.length))
          }
          return codes.has(value)
        })
        form.setFieldsValue({
          required_knowledge_point_codes: retainKnowledgePointSelection(
            form.getFieldValue('required_knowledge_point_codes') ?? [], requiredAllowed,
          ),
          personalized_knowledge_point_codes: retainKnowledgePointSelection(
            form.getFieldValue('personalized_knowledge_point_codes') ?? [], allowed,
          ),
        })
      })
      .catch((reason: Error) => { if (!controller.signal.aborted) setError(reason.message) })
    return () => controller.abort()
  }, [form, selectedChapterCodes])

  const selectedClass = classes.find((item) => item.id === selectedClassId)
  const selectedChapters = chapters.filter((item) => selectedChapterCodes.includes(item.code))
  const available = useMemo(() => catalog.filter((point) => point.available_question_count > 0), [catalog])
  const requiredAvailable = useMemo(
    () => catalog.filter((point) => point.required_available_question_count > 0),
    [catalog],
  )
  const buildKnowledgePointOptions = (
    points: KnowledgePoint[], count: (point: KnowledgePoint) => number,
  ) => [
    { value: ALL_KNOWLEDGE_POINTS, label: `选择全部知识点（${points.length}个）` },
    ...selectedChapters.map((chapter) => {
      const chapterPoints = points.filter((point) => (
        chapter.knowledge_points.some((candidate) => candidate.code === point.code)
      ))
      return {
        label: <span className="knowledge-point-chapter-label">{chapter.name}</span>,
        options: [
          { value: `${ALL_CHAPTER_KNOWLEDGE_POINTS}${chapter.code}`, label: `选择本章全部知识点（${chapterPoints.length}个）` },
          ...chapterPoints.map((point) => ({ value: point.code, label: `${point.name}（${count(point)}题）` })),
        ],
      }
    }),
  ]
  const requiredKnowledgePointSelectOptions = buildKnowledgePointOptions(
    requiredAvailable, (point) => point.required_available_question_count,
  )
  const personalizedKnowledgePointSelectOptions = buildKnowledgePointOptions(
    available, (point) => point.available_question_count,
  )
  const sectionOptions = Array.from(new Set(selectedChapters.flatMap((item) => item.sections))).map((value) => ({ value, label: value }))
  const coreLiteracyOptions = CORE_LITERACIES.map((value) => ({ value, label: value }))
  const gradeValues = Array.from(new Set(
    chapters.flatMap((item) => item.grades ?? []).filter(isConfiguredGrade),
  ))
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

  const expandKnowledgePointSelection = (
    values: string[], points: KnowledgePoint[] = available,
  ) => {
    const selection = Array.isArray(values) ? values : []
    if (selection.includes(ALL_KNOWLEDGE_POINTS)) {
      return points.map((point) => point.code)
    }
    const expanded = new Set(selection.filter((value) => !value.startsWith(ALL_CHAPTER_KNOWLEDGE_POINTS)))
    selection.filter((value) => value.startsWith(ALL_CHAPTER_KNOWLEDGE_POINTS)).forEach((value) => {
      const chapterCode = value.slice(ALL_CHAPTER_KNOWLEDGE_POINTS.length)
      const chapter = selectedChapters.find((item) => item.code === chapterCode)
      chapter?.knowledge_points.forEach((point) => {
        if (points.some((candidate) => candidate.code === point.code)) expanded.add(point.code)
      })
    })
    return Array.from(expanded)
  }

  const compactKnowledgePointSelection = (values: string[]) => {
    const selection = Array.isArray(values) ? values : []
    if (selection.includes(ALL_KNOWLEDGE_POINTS)) return [ALL_KNOWLEDGE_POINTS]
    const chapterSelections = selection.filter((value) => value.startsWith(ALL_CHAPTER_KNOWLEDGE_POINTS))
    const chapterKnowledgePointCodes = new Set(chapterSelections.flatMap((value) => {
      const chapter = selectedChapters.find((item) => item.code === value.slice(ALL_CHAPTER_KNOWLEDGE_POINTS.length))
      return chapter?.knowledge_points.map((point) => point.code) ?? []
    }))
    return [
      ...chapterSelections,
      ...selection.filter((value) => !value.startsWith(ALL_CHAPTER_KNOWLEDGE_POINTS) && !chapterKnowledgePointCodes.has(value)),
    ]
  }
  const selectedRequiredKpCount = expandKnowledgePointSelection(selectedRequiredKps, requiredAvailable).length
  const selectedPersonalizedKpCount = expandKnowledgePointSelection(selectedPersonalizedKps, available).length
  const selectedPersonalizedKpCodes = expandKnowledgePointSelection(selectedPersonalizedKps, available)
  const personalizedCompetencies = Array.from(new Set(
    selectedChapters.flatMap((chapter) => chapter.knowledge_points)
      .filter((point) => selectedPersonalizedKpCodes.includes(point.code))
      .flatMap((point) => point.core_competencies ?? []),
  ))
  const competencyOptions = personalizedCompetencies.map((value) => ({ value, label: value }))

  const handleKnowledgePointChange = (values: string[]) => {
    form.setFieldValue('required_knowledge_point_codes', compactKnowledgePointSelection(values))
  }

  const handlePersonalizedKnowledgePointChange = (values: string[]) => {
    const compacted = compactKnowledgePointSelection(values)
    form.setFieldValue('personalized_knowledge_point_codes', compacted)
    const expanded = expandKnowledgePointSelection(compacted, available)
    const allowed = new Set(
      selectedChapters.flatMap((chapter) => chapter.knowledge_points)
        .filter((point) => expanded.includes(point.code))
        .flatMap((point) => point.core_competencies ?? []),
    )
    if (!allowed.has(form.getFieldValue('primary_competency'))) {
      form.setFieldValue('primary_competency', undefined)
    }
  }

  const handleGradeChange = (values: string[]) => {
    const selection = Array.isArray(values) ? values : []
    form.setFieldValue('grade_codes', selection.includes(ALL_GRADES) ? [ALL_GRADES] : selection.filter(isConfiguredGrade))
  }

  const handleChapterChange = (values: string[]) => {
    const selection = Array.isArray(values) ? values : []
    if (selection.includes(ALL_CHAPTERS)) {
      form.setFieldValue('chapter_codes', [ALL_CHAPTERS])
      return
    }
    const gradeSelections = selection.filter((value) => value.startsWith(ALL_GRADE_CHAPTERS))
    const gradeChapterCodes = new Set(expandChapterSelection(gradeSelections, chapters))
    form.setFieldValue('chapter_codes', [
      ...gradeSelections,
      ...selection.filter((value) => !value.startsWith(ALL_GRADE_CHAPTERS) && !gradeChapterCodes.has(value)),
    ])
  }

  const expandGradeSelection = (values: string[]) => (
    (Array.isArray(values) && values.includes(ALL_GRADES)) ? gradeValues : (Array.isArray(values) ? values.filter(isConfiguredGrade) : [])
  )

  const buildFilterConfig = (values: FormValues): AssignmentFilterConfig => ({
    grade_codes: expandGradeSelection(values.grade_codes), chapter_codes: expandChapterSelection(values.chapter_codes, chapters),
    required: {
      knowledge_point_codes: expandKnowledgePointSelection(
        values.required_knowledge_point_codes ?? [], requiredAvailable,
      ),
      core_literacies: values.required_core_literacies ?? [],
      difficulty_counts: values.difficulty_counts ?? defaults.difficulty_counts ?? {},
    },
    personalized: {
      knowledge_point_codes: expandKnowledgePointSelection(
        values.personalized_knowledge_point_codes ?? [], available,
      ),
      primary_competency: values.primary_competency || null, question_count: 3,
    },
    history: { assignment_id: values.history_assignment_id || null },
  })

  const build = (values: FormValues): AssignmentRequest => {
    const requiredKnowledgePoints = values.required_knowledge_point_codes ?? []
    const selectedCodes = requiredKnowledgePoints.length
      ? expandKnowledgePointSelection(requiredKnowledgePoints, requiredAvailable)
      : requiredAvailable.map((point) => point.code)
    return {
      class_id: values.class_id, student_scope: 'all_class', student_ids: [],
      filter_config: buildFilterConfig(values),
      recommendation_parameters: {
        chapter_code: expandChapterSelection(values.chapter_codes, chapters)[0], chapter_codes: expandChapterSelection(values.chapter_codes, chapters),
        grade_codes: expandGradeSelection(values.grade_codes), new_knowledge_point_codes: selectedCodes,
        review_knowledge_point_codes: [], new_count: totalRequiredCount, review_count: 0,
        question_types: values.question_types, base_difficulty: values.base_difficulty,
        min_difficulty: values.min_difficulty, max_difficulty: values.max_difficulty,
        avoid_recent_days: values.avoid_recent_days, max_knowledge_point_ratio: values.max_knowledge_point_ratio,
      },
    }
  }

  const validateNewFilters = (values: FormValues) => {
    if (!values.title?.trim()) return '请填写作业标题'
    if (!values.class_id) return '请选择班级'
    if (!values.grade_codes?.length || !values.chapter_codes?.length) return '请选择至少一个年级和章节'
    if (totalRequiredCount < 1) return '请设置至少 1 道必做题'
    if (!expandKnowledgePointSelection(values.personalized_knowledge_point_codes ?? [], available).length) return '请选择个性化模块的知识点'
    if (!values.primary_competency) return '请选择所选知识点对应的主要能力'
    if (values.history_assignment_id && !recentAssignments.some((item) => item.id === values.history_assignment_id)) return '历史回顾作业无效，请重新选择'
    return null
  }

  const handleFinishFailed = () => {
    const values = form.getFieldsValue(true) as FormValues
    const validationError = validateNewFilters(values)
    if (
      validationError === '请选择个性化模块的知识点'
      || validationError === '请选择所选知识点对应的主要能力'
    ) {
      setActiveModule('personalized')
    }
    setError(validationError ?? '请检查表单中的必填项')
  }

  const runPreview = async () => {
    const values = form.getFieldsValue(true) as FormValues
    const validationError = validateNewFilters(values)
    if (validationError) { setError(validationError); return }
    setLoading(true); setError(null); setPreview(null)
    try { setPreview(await apiPost<AssignmentPreview>('/assignments/preview', build(values))) }
    catch (reason) { setError((reason as Error).message) }
    finally { setLoading(false) }
  }

  const generate = async () => {
    await form.validateFields()
    const values = form.getFieldsValue(true) as FormValues
    const validationError = validateNewFilters(values)
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
    { title: '题目来源', render: (_, row) => `${row.recommendation.items.filter((item) => item.source_module === 'required').length} 道必做 · ${row.recommendation.items.filter((item) => item.source_module === 'history').length} 道历史回顾 · 个性化待接入` },
    { title: '缺口', render: (_, row) => row.recommendation.shortfall ? `缺 ${row.recommendation.shortfall} 题` : '无' },
  ]
  const requiredQuestionColumns: ColumnsType<RecommendedQuestion> = [
    { title: '题号', dataIndex: 'original_qid', width: 180 },
    { title: '知识点', dataIndex: 'knowledge_point_name' },
    { title: '难度', dataIndex: 'difficulty', width: 80 },
    { title: '题型', dataIndex: 'question_type', width: 100 },
  ]
  const moduleButton = (
    module: Exclude<FilterModule, null>, label: string, summary: string,
    icon: ReactNode, tone: 'required' | 'personalized' | 'history',
  ) => (
    <button type="button" className={`assignment-filter-slider ${activeModule === module ? 'active' : ''}`} onClick={() => setActiveModule(module)}>
      <span className={`assignment-filter-slider-icon ${tone}`}>{icon}</span>
      <span className="assignment-filter-slider-copy">
        <span className="assignment-filter-slider-title">{label}</span>
        <span className="assignment-filter-slider-summary">{summary}</span>
      </span>
      <span className="assignment-filter-slider-state">{activeModule === module ? '配置中' : '查看'}</span>
    </button>
  )
  const sharedRequiredItems = preview?.students[0]?.recommendation.items.filter(
    (item) => item.source_module === 'required',
  ) ?? []

  return <Space direction="vertical" size="large" className="page-stack assignment-workspace">
    <Typography.Title level={2}>创建班级作业</Typography.Title>
    <Card className="assignment-builder-card">
      <Form
        form={form}
        preserve
        layout="vertical"
        initialValues={defaults}
        onFinish={runPreview}
        onFinishFailed={handleFinishFailed}
        onValuesChange={() => { setPreview(null); setError(null) }}
      >
        <div className="assignment-section-heading">
          <span className="assignment-section-index">01</span>
          <div><Typography.Title level={4}>作业基本信息</Typography.Title><Typography.Text type="secondary">选择班级和本次教学覆盖的年级、章节</Typography.Text></div>
        </div>
        <div className="assignment-form-grid assignment-scope-grid">
          <Form.Item label="作业标题" name="title" rules={[{ required: true }]}><Input maxLength={255} /></Form.Item>
          <Form.Item label="班级" name="class_id" rules={[{ required: true, message: '请选择班级' }]}><Select placeholder="请选择班级" options={classes.map((item) => ({ value: item.id, label: `${item.name}（${item.student_count}人）` }))} /></Form.Item>
          <Form.Item label="年级（可多选）" name="grade_codes" rules={[{ required: true, type: 'array', min: 1, message: '请选择至少一个年级' }]}><Select mode="multiple" options={gradeSelectOptions} onChange={handleGradeChange} placeholder="请选择年级" /></Form.Item>
          <Form.Item label="章（可多选）" name="chapter_codes" rules={[{ required: true, type: 'array', min: 1, message: '请选择至少一个章节' }]}><Select mode="multiple" options={chapterSelectOptions} onChange={handleChapterChange} placeholder="请选择章节" /></Form.Item>
          <Form.Item label="学生范围"><div className="assignment-scope-readonly">{selectedClass ? <><CheckCircleFilled /> 当前班级全部学生（{selectedClass.student_count} 人）</> : '请先选择班级'}</div></Form.Item>
        </div>

        <Divider />
        <div className="assignment-section-heading">
          <span className="assignment-section-index">02</span>
          <Typography.Title level={4}>配置题目模块</Typography.Title>
        </div>
        <div className="assignment-filter-sliders">
          {moduleButton('required', '必做题', `${totalRequiredCount} 道 · ${selectedRequiredKpCount ? `${selectedRequiredKpCount} 个知识点` : '全章节'}`, <BookOutlined />, 'required')}
          {moduleButton('personalized', '个性化', `${selectedPersonalizedKpCount ? `${selectedPersonalizedKpCount} 个知识点` : '待配置'} · 3 道`, <BulbOutlined />, 'personalized')}
          {moduleButton('history', '历史回顾', selectedHistoryAssignmentId ? '已选择来源作业' : '本次可不选择', <HistoryOutlined />, 'history')}
        </div>

        <div hidden={activeModule !== 'required'}>
          <Card size="small" className="assignment-filter-panel" title="必做题筛选" extra={<Button type="link" onClick={() => setActiveModule(null)}>收起</Button>}>
          <div className="assignment-filter-grid">
            <Form.Item label="节（知识点，可选）" name="required_knowledge_point_codes"><Select mode="multiple" showSearch optionFilterProp="label" options={requiredKnowledgePointSelectOptions} onChange={handleKnowledgePointChange} placeholder="不选表示覆盖所选章节" /></Form.Item>
            <Form.Item label="核心素养（可选，可多选）" name="required_core_literacies"><Select mode="multiple" options={coreLiteracyOptions} placeholder="可独立选择核心素养，与知识点取交集" /></Form.Item>
          </div>
          {sectionOptions.length > 0 && <Typography.Text type="secondary">已按所选章节提供节与知识点选项</Typography.Text>}
          <Divider plain><SettingOutlined /> 按难度设置必做题数量</Divider>
          <div className="difficulty-count-grid">{[1, 2, 3, 4, 5].map((difficulty) => <Form.Item key={difficulty} label={`${difficulty} 星`} name={['difficulty_counts', String(difficulty)]} initialValue={difficulty === 2 ? 6 : 0}><InputNumber min={0} max={30} precision={0} /></Form.Item>)}</div>
          </Card>
        </div>

        <div hidden={activeModule !== 'personalized'}>
          <Card size="small" className="assignment-filter-panel" title="个性化筛选" extra={<Button type="link" onClick={() => setActiveModule(null)}>收起</Button>}>
          <Alert type="info" showIcon message="当前阶段只保存筛选条件，个性化出题算法将在后续接入" />
          <Form.Item label="节（知识点，必选）" name="personalized_knowledge_point_codes" rules={[{ required: true, type: 'array', min: 1, message: '请选择至少一个知识点' }]}><Select mode="multiple" showSearch optionFilterProp="label" options={personalizedKnowledgePointSelectOptions} onChange={handlePersonalizedKnowledgePointChange} placeholder="请先选择知识点" /></Form.Item>
          <Form.Item label="主要能力（任选其一）" name="primary_competency" rules={[{ required: true, message: '请选择主要能力' }]}><Select allowClear disabled={!personalizedCompetencies.length} options={competencyOptions} placeholder={personalizedCompetencies.length ? '请选择所选知识点对应的一项能力' : '请先选择知识点'} /></Form.Item>
          <Typography.Text type="secondary">个性化模块固定预留 3 道题。</Typography.Text>
          </Card>
        </div>

        <div hidden={activeModule !== 'history'}>
          <Card size="small" className="assignment-filter-panel" title="历史回顾筛选" extra={<Button type="link" onClick={() => setActiveModule(null)}>收起</Button>}>
          <Alert type="info" showIcon message="选择后，将按每名学生在该作业必做题中的三星及以下错题，生成同知识点、同难度回顾题" description="历史回顾不受本次年级和章节限制；系统不会根据“昨天”等相对时间自动猜测，没有符合条件的错题时不生成历史回顾题。" />
          <Form.Item label="当前班级最近 5 次已批改作业" name="history_assignment_id"><Select allowClear options={recentAssignments.map((item) => ({ value: item.id, label: `${item.title} · ${item.class_name ?? '未命名班级'} · ${new Date(item.created_at).toLocaleString('zh-CN')}` }))} placeholder={recentAssignments.length ? '请选择作业' : '暂无已全部批改的作业'} notFoundContent={<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无已全部批改的作业" />} /></Form.Item>
          </Card>
        </div>

        <div className="assignment-preview-action">
          <div><Typography.Text strong>配置完成后先预检</Typography.Text><Typography.Text type="secondary">检查每名学生的实际题量与库存缺口</Typography.Text></div>
          <Button type="primary" size="large" htmlType="submit" loading={loading} icon={<ThunderboltOutlined />}>预检全部学生</Button>
        </div>
        {error && <Alert type="error" showIcon message="操作失败" description={error} />}
      </Form>
    </Card>
    {preview && <Card className="assignment-preview-card" title="预检结果" extra={<Tag color="green">只读预检</Tag>}>
      <div className="assignment-preview-summary">
        <div><span>班级学生</span><strong>{preview.student_count}</strong><small>人</small></div>
        <div><span>可完整生成</span><strong>{preview.ready_count}</strong><small>人</small></div>
        <div className={preview.shortfall_student_count ? 'warning' : ''}><span>存在缺口</span><strong>{preview.shortfall_student_count}</strong><small>人</small></div>
        <div><span>统一必做题</span><strong>{sharedRequiredItems.length}</strong><small>道</small></div>
      </div>
      <Alert type={preview.shortfall_student_count ? 'warning' : 'success'} showIcon message={`${preview.ready_count} / ${preview.student_count} 名学生可完整生成`} description={`统一基准时间：${new Date(preview.as_of).toLocaleString('zh-CN')}`} />
      <Table className="preview-table" rowKey="student_id" columns={columns} dataSource={preview.students} pagination={{ pageSize: 20, showSizeChanger: true }} scroll={{ x: 720 }} />
      {sharedRequiredItems.length > 0 && <Card size="small" title={`必做题草稿（${sharedRequiredItems.length} 题，所有学生相同）`}>
        <Table
          size="small"
          rowKey="question_id"
          columns={requiredQuestionColumns}
          dataSource={sharedRequiredItems}
          pagination={false}
        />
      </Card>}
      {sharedRequiredItems.length === 0 && <Alert type="warning" showIcon message="当前筛选条件没有可用的必做题" description="请减少难度题量，或放宽年级、章节、知识点和核心素养筛选范围。" />}
      <div className="assignment-generate-action"><Typography.Text type="secondary">确认预检结果后，为当前班级生成可继续编辑的试卷草稿。</Typography.Text><Button type="primary" size="large" onClick={generate} loading={loading}>确认并生成草稿</Button></div>
    </Card>}
  </Space>
}
