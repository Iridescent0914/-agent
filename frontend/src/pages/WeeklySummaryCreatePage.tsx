import { CalendarOutlined, CheckCircleOutlined, ExclamationCircleOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Form, Input, Select, Space, Statistic, Table, Tag, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { apiGet, apiPost } from '../api/client'
import type { ClassSummary } from '../types'
import type {
  AssignmentDetail, AssignmentListItem, WeeklySummaryPreview,
  WeeklySummaryStudentPreview,
} from '../types/assignment'

interface WeeklySummaryForm {
  title: string
  class_id: string
  source_assignment_ids: string[]
}

function defaultTitle() {
  return `${new Date().toLocaleDateString('zh-CN')} 周总结`
}

export function WeeklySummaryCreatePage() {
  const [form] = Form.useForm<WeeklySummaryForm>()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const classId = Form.useWatch('class_id', form)
  const [classes, setClasses] = useState<ClassSummary[]>([])
  const [sources, setSources] = useState<AssignmentListItem[]>([])
  const [preview, setPreview] = useState<WeeklySummaryPreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [sourceLoading, setSourceLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiGet<ClassSummary[]>('/classes').then((items) => {
      setClasses(items)
      const requested = searchParams.get('class_id')
      const selected = items.find((item) => item.id === requested)
        ?? items.find((item) => item.student_count > 0)
        ?? items[0]
      if (selected) form.setFieldsValue({ title: defaultTitle(), class_id: selected.id, source_assignment_ids: [] })
    }).catch((reason) => setError((reason as Error).message))
  }, [])

  useEffect(() => {
    setPreview(null)
    form.setFieldValue('source_assignment_ids', [])
    if (!classId) { setSources([]); return }
    setSourceLoading(true)
    apiGet<AssignmentListItem[]>(`/assignments/recent-completed?limit=6&class_id=${encodeURIComponent(classId)}`)
      .then(setSources)
      .catch((reason) => { setSources([]); setError((reason as Error).message) })
      .finally(() => setSourceLoading(false))
  }, [classId])

  const payload = async () => {
    const values = await form.validateFields()
    return { class_id: values.class_id, source_assignment_ids: values.source_assignment_ids }
  }
  const runPreview = async () => {
    setLoading(true); setError(null)
    try {
      setPreview(await apiPost<WeeklySummaryPreview>('/assignments/weekly-summary/preview', await payload()))
    } catch (reason) {
      setError((reason as Error).message)
    } finally { setLoading(false) }
  }
  const create = async () => {
    setLoading(true); setError(null)
    try {
      const values = await form.validateFields()
      const result = await apiPost<AssignmentDetail>('/assignments/weekly-summary', {
        class_id: values.class_id,
        source_assignment_ids: values.source_assignment_ids,
        title: values.title,
        idempotency_key: `weekly-${crypto.randomUUID()}`,
      })
      const generatedCount = result.papers.length
      const classStudentCount = classes.find((item) => item.id === values.class_id)?.student_count ?? generatedCount
      const skippedCount = Math.max(classStudentCount - generatedCount, 0)
      if (generatedCount === 0) {
        message.info({
          content: '周总结记录已创建，但全班暂无标“否”的知识点，本次未生成学生草稿。',
          duration: 6,
        })
      } else if (skippedCount > 0) {
        message.success({
          content: `已生成 ${generatedCount} 份周总结草稿；另有 ${skippedCount} 名学生暂无标“否”的知识点，因此无需生成。`,
          duration: 6,
        })
      } else {
        message.success(`已生成 ${generatedCount} 份周总结草稿`)
      }
      navigate(`/assignments/${result.id}`)
    } catch (reason) {
      setError((reason as Error).message)
    } finally { setLoading(false) }
  }

  const columns: ColumnsType<WeeklySummaryStudentPreview> = useMemo(() => [
    { title: '学生', render: (_, row) => `${row.student_code} · ${row.student_name}`, width: 190 },
    {
      title: '标“否”的知识点',
      render: (_, row) => row.false_knowledge_points.length
        ? <Space wrap>{row.false_knowledge_points.map((item) => <Tag color="orange" key={item.knowledge_point_code}>{item.knowledge_point_name}</Tag>)}</Space>
        : <Typography.Text type="secondary">无，不生成周总结试卷</Typography.Text>,
    },
    { title: '计划 / 已选', width: 120, render: (_, row) => `${row.requested_count} / ${row.selected_count}` },
    {
      title: '状态', width: 120, render: (_, row) => row.status === 'no_review_needed'
        ? <Tag>无需回顾</Tag>
        : row.shortfall
          ? <Tag color="orange" icon={<ExclamationCircleOutlined />}>缺 {row.shortfall} 题</Tag>
          : <Tag color="green" icon={<CheckCircleOutlined />}>可生成</Tag>,
    },
  ], [])

  return <Space direction="vertical" size="large" className="page-stack">
    <div>
      <Typography.Title level={2}>创建周总结</Typography.Title>
      <Typography.Text type="secondary">周总结是独立于当天作业的第二套卷子；每个“否”知识点暂按 3 星生成两道题。</Typography.Text>
    </div>
    {error && <Alert type="error" showIcon message="周总结处理失败" description={error} />}
    <Alert type="info" showIcon message="判定规则" description="所选来源作业中的历史回顾题再次答错，该知识点标为“否”；周总结两题中任一题答错，标为“弱”。库存不足会显示缺口，但仍允许生成草稿。" />
    <Card>
      <Form form={form} layout="vertical">
        <Form.Item name="title" label="周总结标题" rules={[{ required: true, whitespace: true, message: '请输入周总结标题' }]}>
          <Input maxLength={255} />
        </Form.Item>
        <Form.Item name="class_id" label="班级" rules={[{ required: true, message: '请选择班级' }]}>
          <Select options={classes.map((item) => ({ value: item.id, label: `${item.name}（${item.student_count}人）` }))} />
        </Form.Item>
        <Form.Item name="source_assignment_ids" label="来源作业（最近六次，可多选）" rules={[
          { required: true, type: 'array', min: 1, message: '请至少选择一份已批改作业' },
          { validator: (_, value: string[]) => value?.length <= 6 ? Promise.resolve() : Promise.reject(new Error('最多选择六份作业')) },
        ]}>
          <Select
            mode="multiple"
            loading={sourceLoading}
            placeholder={sources.length ? '请选择本次周总结覆盖的作业' : '当前班级没有可选的已批改作业'}
            options={sources.map((item) => ({
              value: item.id,
              label: `${item.title} · ${new Date(item.created_at).toLocaleString('zh-CN')}`,
            }))}
          />
        </Form.Item>
        <Space><Button onClick={runPreview} loading={loading}>预检</Button><Button type="primary" icon={<CalendarOutlined />} onClick={create} loading={loading}>生成周总结草稿</Button></Space>
      </Form>
    </Card>
    {preview && <>
      <Space wrap size="large">
        <Card size="small"><Statistic title="班级学生" value={preview.student_count} /></Card>
        <Card size="small"><Statistic title="需要周总结" value={preview.paper_student_count} /></Card>
        <Card size="small"><Statistic title="涉及知识点" value={preview.involved_knowledge_point_codes.length} /></Card>
        <Card size="small"><Statistic title="库存不足学生" value={preview.shortfall_student_count} valueStyle={{ color: preview.shortfall_student_count ? '#d46b08' : undefined }} /></Card>
      </Space>
      {preview.paper_student_count === 0
        ? <Alert type="info" showIcon message="本次无需生成学生草稿" description="所选来源作业中，没有学生出现历史回顾再次答错的情况，因此全班暂无标“否”的知识点。" />
        : preview.paper_student_count < preview.student_count
          ? <Alert type="info" showIcon message="仅为部分学生生成" description={`本次有 ${preview.paper_student_count} 名学生需要周总结；其余 ${preview.student_count - preview.paper_student_count} 名学生暂无标“否”的知识点，不生成草稿。`} />
          : null}
      {preview.shortfall_student_count > 0 && <Alert type="warning" showIcon message="部分知识点的 3 星题不足" description="系统将保留缺口并生成当前可用题目的草稿，不会跨知识点或跨难度补题。" />}
      <Card title="逐学生预检"><Table rowKey="student_id" columns={columns} dataSource={preview.students} pagination={false} scroll={{ x: 850 }} /></Card>
    </>}
  </Space>
}
