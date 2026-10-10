import { BarChartOutlined, DeleteOutlined, PlusOutlined, SwapOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Empty, Form, Input, Modal, Progress, Select, Space, Table, Tag, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiDelete, apiGet, apiPatch, apiPost, queryString } from '../api/client'
import type { ClassSummary, Page, StudentCreateRequest, StudentDetail, StudentSummary } from '../types'

export function StudentListPage() {
  const [form] = Form.useForm<StudentCreateRequest>()
  const [data, setData] = useState<Page<StudentSummary> | null>(null)
  const [classes, setClasses] = useState<ClassSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [selectedClassId, setSelectedClassId] = useState<string>('all')
  const [movingStudent, setMovingStudent] = useState<StudentSummary | null>(null)
  const [targetClassId, setTargetClassId] = useState<string>()

  const load = () => {
    setLoading(true); setError(null)
    Promise.all([
      apiGet<Page<StudentSummary>>(`/students${queryString({ page: 1, page_size: 100, class_id: selectedClassId === 'all' ? undefined : selectedClassId })}`),
      apiGet<ClassSummary[]>('/classes'),
    ])
      .then(([students, classItems]) => { setData(students); setClasses(classItems) })
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setLoading(false))
  }
  useEffect(load, [selectedClassId])

  async function createStudent() {
    const values = await form.validateFields()
    setCreating(true); setError(null)
    try {
      await apiPost<StudentDetail>('/students', values)
      message.success('学生已添加')
      setCreateOpen(false)
      form.resetFields()
      load()
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setCreating(false)
    }
  }

  function deleteStudent(student: StudentSummary) {
    Modal.confirm({
      title: `删除学生 ${student.display_name}（${student.student_code}）？`,
      content: '学生将退出学生列表和后续组卷；历史试卷、作答和画像证据会保留。',
      okText: '确认删除', cancelText: '取消', okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await apiDelete(`/students/${student.id}`, { operator_name: '任课教师' })
          message.success('学生已删除')
          load()
        } catch (reason) {
          setError((reason as Error).message)
        }
      },
    })
  }

  async function moveStudent() {
    if (!movingStudent || !targetClassId) return
    setCreating(true); setError(null)
    try {
      await apiPatch<StudentDetail>(`/students/${movingStudent.id}/class`, { class_id: targetClassId })
      message.success('学生班级已调整')
      setMovingStudent(null)
      setTargetClassId(undefined)
      load()
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setCreating(false)
    }
  }

  const columns: ColumnsType<StudentSummary> = [
    { title: '编号', dataIndex: 'student_code', width: 100, render: (_, row) => <Link to={`/students/${row.id}`}>{row.student_code}</Link> },
    { title: '虚拟学生', dataIndex: 'display_name', width: 130 },
    { title: '班级', dataIndex: 'class_name' },
    { title: '证据量', dataIndex: 'attempt_count', width: 100, render: (value) => `${value} 次` },
    {
      title: '历史正确率',
      width: 240,
      render: (_, row) => {
        const percent = row.attempt_count ? Math.round((row.correct_count / row.attempt_count) * 100) : 0
        return <Progress percent={percent} size="small" />
      },
    },
    { title: '数据类型', width: 110, render: (_, row) => row.is_demo ? <Tag color='purple'>演示数据</Tag> : <Tag color='blue'>教师添加</Tag> },
    {
      title: '操作',
      key: 'action',
      width: 340,
      fixed: 'right',
      render: (_, row) => <Space>
        <Link to={`/students/${row.id}`}>
          <Button type='primary' icon={<BarChartOutlined />}>查看学生画像</Button>
        </Link>
        <Button type='text' icon={<SwapOutlined />} onClick={() => {
          setMovingStudent(row)
          setTargetClassId(row.class_id ?? undefined)
        }}>转班</Button>
        <Button danger type='text' icon={<DeleteOutlined />} onClick={() => deleteStudent(row)}>删除</Button>
      </Space>,
    },
  ]

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <div className='student-heading'>
        <div>
          <Typography.Title level={2}>学生管理</Typography.Title>
          <Typography.Text type='secondary'>新增或删除学生；删除不会破坏历史试卷和作答证据。</Typography.Text>
        </div>
        <Space wrap>
          <Select
            aria-label="按班级筛选学生"
            value={selectedClassId}
            onChange={setSelectedClassId}
            style={{ width: 220 }}
            options={[
              { value: 'all', label: '全部班级' },
              ...classes.map((item) => ({ value: item.id, label: `${item.name}（${item.student_count}人）` })),
            ]}
          />
          <Link to="/classes"><Button>管理班级</Button></Link>
          <Button type='primary' icon={<PlusOutlined />} disabled={!classes.length} onClick={() => setCreateOpen(true)}>添加学生</Button>
        </Space>
      </div>
      {error && <Alert type="error" showIcon message="学生数据加载失败" description={error} />}
      <Card className="table-card">
        <Table rowKey="id" columns={columns} dataSource={data?.items || []} loading={loading} pagination={false} locale={{ emptyText: <Empty description="暂无学生" /> }} />
      </Card>
      <Modal
        title='添加学生'
        open={createOpen}
        okText='确认添加'
        cancelText='取消'
        confirmLoading={creating}
        onOk={() => void createStudent()}
        onCancel={() => { setCreateOpen(false); form.resetFields() }}
        destroyOnClose
      >
        <Form form={form} layout='vertical'>
          <Form.Item
            name='student_code'
            label='学生编号'
            rules={[
              { required: true, whitespace: true },
              { pattern: /^[A-Za-z0-9._-]+$/, message: '仅支持字母、数字、点、下划线和短横线' },
            ]}
          >
            <Input maxLength={32} placeholder='例如：S006' />
          </Form.Item>
          <Form.Item name='display_name' label='学生姓名' rules={[{ required: true, whitespace: true }]}>
            <Input maxLength={80} />
          </Form.Item>
          <Form.Item name='class_id' label='班级' rules={[{ required: true, message: '请选择班级' }]}>
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="请选择已创建的班级"
              options={classes.map((item) => ({
                value: item.id,
                label: `${item.name}（${item.school_year}）`,
              }))}
            />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={movingStudent ? `调整 ${movingStudent.display_name} 的班级` : '调整班级'}
        open={Boolean(movingStudent)}
        okText='确认调整'
        cancelText='取消'
        confirmLoading={creating}
        okButtonProps={{ disabled: !targetClassId || targetClassId === movingStudent?.class_id }}
        onOk={() => void moveStudent()}
        onCancel={() => { setMovingStudent(null); setTargetClassId(undefined) }}
      >
        <Select
          value={targetClassId}
          onChange={setTargetClassId}
          style={{ width: '100%' }}
          options={classes.map((item) => ({
            value: item.id,
            label: `${item.name}（${item.school_year}）`,
          }))}
        />
      </Modal>
    </Space>
  )
}
