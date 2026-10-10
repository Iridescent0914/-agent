import { Alert, Button, Card, Checkbox, Form, Input, InputNumber, Select, Space, Table, Tag, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiGet, apiPost } from '../api/client'
import type { ClassSummary, Page, StudentSummary } from '../types'
import type { AssignmentDetail, AssignmentPreview, AssignmentPreviewStudent, AssignmentRequest } from '../types/assignment'
import type { ExamPoint, RecommendationParams } from '../types/profile'

interface FormValues extends RecommendationParams { title: string; class_id: string; student_ids: string[] }

const defaults: Partial<FormValues> = {
  title: '有理数个性化练习', chapter_code: 'math.g6.rational', student_ids: [],
  new_exam_point_codes: [], review_exam_point_codes: [], new_count: 0, review_count: 6,
  question_types: ['choice', 'fill_blank'], base_difficulty: 2, min_difficulty: 1,
  max_difficulty: 5, avoid_recent_days: 7, max_exam_point_ratio: 0.5,
}

export function AssignmentCreatePage() {
  const [form] = Form.useForm<FormValues>()
  const navigate = useNavigate()
  const [students, setStudents] = useState<StudentSummary[]>([])
  const [classes, setClasses] = useState<ClassSummary[]>([])
  const [catalog, setCatalog] = useState<ExamPoint[]>([])
  const [preview, setPreview] = useState<AssignmentPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const key = useRef(crypto.randomUUID())
  useEffect(() => {
    Promise.all([
      apiGet<Page<StudentSummary>>('/students?page_size=100'),
      apiGet<ClassSummary[]>('/classes'),
      apiGet<ExamPoint[]>('/exam-points?chapter_code=math.g6.rational'),
    ])
      .then(([studentPage, classItems, exams]) => {
        setStudents(studentPage.items); setClasses(classItems); setCatalog(exams)
        const initialClass = classItems.find((item) => item.student_count > 0) ?? classItems[0]
        form.setFieldsValue({
          class_id: initialClass?.id,
          student_ids: initialClass
            ? studentPage.items.filter((student) => student.class_id === initialClass.id).map((student) => student.id)
            : [],
        })
      }).catch((reason) => setError((reason as Error).message))
  }, [form])
  const selectedClassId = Form.useWatch('class_id', form)
  const classStudents = useMemo(
    () => students.filter((student) => student.class_id === selectedClassId),
    [students, selectedClassId],
  )
  const available = useMemo(() => catalog.filter((exam) => exam.available_question_count > 0), [catalog])
  const build = (values: FormValues): AssignmentRequest => ({
    class_id: values.class_id,
    student_ids: values.student_ids,
    recommendation_parameters: {
      chapter_code: 'math.g6.rational', new_exam_point_codes: values.new_exam_point_codes,
      review_exam_point_codes: values.review_exam_point_codes, new_count: values.new_count,
      review_count: values.review_count, question_types: values.question_types,
      base_difficulty: values.base_difficulty, min_difficulty: values.min_difficulty,
      max_difficulty: values.max_difficulty, avoid_recent_days: values.avoid_recent_days,
      max_exam_point_ratio: values.max_exam_point_ratio,
    },
  })
  const runPreview = async (values: FormValues) => {
    setLoading(true); setError(null); setPreview(null)
    try { setPreview(await apiPost<AssignmentPreview>('/assignments/preview', build(values))) }
    catch (reason) { setError((reason as Error).message) }
    finally { setLoading(false) }
  }
  const generate = async () => {
    const values = await form.validateFields()
    setLoading(true); setError(null)
    try {
      const created = await apiPost<AssignmentDetail>('/assignments', { ...build(values), title: values.title, idempotency_key: key.current })
      message.success('草稿已生成'); navigate(`/assignments/${created.id}`)
    } catch (reason) { setError((reason as Error).message) }
    finally { setLoading(false) }
  }
  const examOptions = available.map((exam) => ({ value: exam.code, label: `${exam.name}（${exam.available_question_count}题）` }))
  const columns: ColumnsType<AssignmentPreviewStudent> = [
    { title: '学生', render: (_, row) => `${row.student_code} · ${row.student_name}` },
    { title: '结果', render: (_, row) => <Tag color={row.recommendation.shortfall ? 'orange' : 'green'}>{row.recommendation.selected_count} / {row.recommendation.requested_count} 题</Tag> },
    { title: '策略', render: (_, row) => `${row.recommendation.items.filter((item) => item.strategy === 'personalized').length} 道个性化 · ${row.recommendation.items.filter((item) => item.strategy === 'diagnostic').length} 道诊断` },
    { title: '缺口', render: (_, row) => row.recommendation.shortfall ? `新知识 ${row.recommendation.new_shortfall}，回顾 ${row.recommendation.review_shortfall}` : '无' },
  ]
  return <Space direction="vertical" size="large" className="page-stack assignment-workspace">
    <div className="student-heading"><div><Typography.Title level={2}>创建个性化作业</Typography.Title><Typography.Text type="secondary">先选择班级和学生，再预检并生成作业草稿</Typography.Text></div></div>
    {error && <Alert type="error" showIcon message="操作失败" description={error} />}
    <Card>
      <Form form={form} layout="vertical" initialValues={defaults} onFinish={runPreview} onValuesChange={() => setPreview(null)}>
        <div className="assignment-form-grid">
          <Form.Item label="作业标题" name="title" rules={[{ required: true }]}><Input maxLength={255} /></Form.Item>
          <Form.Item label="班级" name="class_id" rules={[{ required: true, message: '请先选择班级' }]}>
            <Select
              placeholder="请选择班级"
              options={classes.map((item) => ({ value: item.id, label: `${item.name}（${item.student_count}人）` }))}
              onChange={(classId) => {
                form.setFieldValue(
                  'student_ids',
                  students.filter((student) => student.class_id === classId).map((student) => student.id),
                )
              }}
            />
          </Form.Item>
          <Form.Item label={`学生（当前班级 ${classStudents.length} 人）`} name="student_ids" rules={[{ type: 'array', min: 1, required: true, message: '请至少选择一名学生' }]}>
            <Select mode="multiple" options={classStudents.map((student) => ({ value: student.id, label: `${student.student_code} · ${student.display_name}` }))} />
          </Form.Item>
          <Form.Item label="新知识范围" name="new_exam_point_codes"><Select mode="multiple" showSearch optionFilterProp="label" options={examOptions} /></Form.Item>
          <Form.Item label="回顾范围" name="review_exam_point_codes"><Select mode="multiple" showSearch optionFilterProp="label" options={examOptions} /></Form.Item>
        </div>
        <div className="recommendation-controls">
          <Form.Item label="新知识题量" name="new_count"><InputNumber min={0} max={30} precision={0} /></Form.Item>
          <Form.Item label="回顾题量" name="review_count"><InputNumber min={0} max={30} precision={0} /></Form.Item>
          <Form.Item label="基础难度" name="base_difficulty"><InputNumber min={1} max={5} /></Form.Item>
          <Form.Item label="难度范围"><Space.Compact><Form.Item name="min_difficulty" noStyle><InputNumber min={1} max={5} /></Form.Item><Form.Item name="max_difficulty" noStyle><InputNumber min={1} max={5} /></Form.Item></Space.Compact></Form.Item>
          <Form.Item label="避免近期重复天数" name="avoid_recent_days"><InputNumber min={0} max={3650} /></Form.Item>
          <Form.Item label="单考点最大占比" name="max_exam_point_ratio"><InputNumber min={0.05} max={1} step={0.05} /></Form.Item>
          <Form.Item label="题型" name="question_types"><Checkbox.Group options={[{ label: '选择题', value: 'choice' }, { label: '填空题', value: 'fill_blank' }]} /></Form.Item>
        </div>
        <Button type="primary" htmlType="submit" loading={loading}>预检全部学生</Button>
      </Form>
    </Card>
    {preview && <Card title="预检结果" extra={<Typography.Text type="secondary">预检不会写入作业</Typography.Text>}>
      <Alert type={preview.shortfall_student_count ? 'warning' : 'success'} showIcon message={`${preview.ready_count} / ${preview.student_count} 名学生可完整生成`} description={`统一基准时间：${new Date(preview.as_of).toLocaleString('zh-CN')}`} />
      <Table className="preview-table" rowKey="student_id" columns={columns} dataSource={preview.students} pagination={false} scroll={{ x: 720 }} />
      <Button type="primary" onClick={generate} loading={loading}>生成草稿</Button>
    </Card>}
  </Space>
}

