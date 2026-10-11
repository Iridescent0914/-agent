import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Empty, Form, Input, Modal, Space, Table, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useState } from 'react'
import { apiDelete, apiGet, apiPatch, apiPost } from '../api/client'
import type { ClassSummary, ClassWriteRequest } from '../types'

type ClassFormValues = ClassWriteRequest

export function ClassManagementPage() {
  const [form] = Form.useForm<ClassFormValues>()
  const [classes, setClasses] = useState<ClassSummary[]>([])
  const [editing, setEditing] = useState<ClassSummary | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    setError(null)
    apiGet<ClassSummary[]>('/classes')
      .then(setClasses)
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  function openCreate() {
    setEditing(null)
    form.setFieldsValue({ name: '', grade: '六年级', school_year: '2026-2027', teacher_name: '' })
    setModalOpen(true)
  }

  function openEdit(item: ClassSummary) {
    setEditing(item)
    form.setFieldsValue({
      name: item.name,
      grade: item.grade,
      school_year: item.school_year,
      teacher_name: item.teacher_name ?? '',
    })
    setModalOpen(true)
  }

  async function save() {
    const values = await form.validateFields()
    setSaving(true)
    setError(null)
    try {
      if (editing) {
        await apiPatch<ClassSummary>(`/classes/${editing.id}`, values)
        message.success('班级信息已更新')
      } else {
        await apiPost<ClassSummary>('/classes', values)
        message.success('班级已创建')
      }
      setModalOpen(false)
      form.resetFields()
      load()
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setSaving(false)
    }
  }

  function remove(item: ClassSummary) {
    Modal.confirm({
      title: `删除班级“${item.name}”？`,
      content: item.student_count > 0
        ? `该班级仍有 ${item.student_count} 名学生，不能删除。请先删除相关学生。`
        : '删除后无法恢复，班级中的历史学生记录不会被删除。',
      okText: '确认删除',
      cancelText: '取消',
      okButtonProps: { danger: true, disabled: item.student_count > 0 },
      onOk: async () => {
        try {
          await apiDelete(`/classes/${item.id}`)
          message.success('班级已删除')
          load()
        } catch (reason) {
          setError((reason as Error).message)
        }
      },
    })
  }

  const columns: ColumnsType<ClassSummary> = [
    { title: '班级名称', dataIndex: 'name' },
    { title: '年级', dataIndex: 'grade', width: 120 },
    { title: '学年', dataIndex: 'school_year', width: 140 },
    { title: '任课教师', dataIndex: 'teacher_name', width: 140, render: (value) => value || '未设置' },
    { title: '学生人数', dataIndex: 'student_count', width: 110, render: (value) => `${value} 人` },
    {
      title: '操作',
      key: 'action',
      width: 180,
      render: (_, item) => <Space>
        <Button type="link" icon={<EditOutlined />} onClick={() => openEdit(item)}>编辑</Button>
        <Button danger type="link" icon={<DeleteOutlined />} onClick={() => remove(item)}>删除</Button>
      </Space>,
    },
  ]

  return <Space direction="vertical" size="large" className="page-stack">
    <div className="student-heading">
      <div>
        <Typography.Title level={2}>班级管理</Typography.Title>
        <Typography.Text type="secondary">独立维护班级、年级、学年和任课教师；空班级也可以预先创建。</Typography.Text>
      </div>
      <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建班级</Button>
    </div>
    {error && <Alert type="error" showIcon message="班级操作失败" description={error} closable onClose={() => setError(null)} />}
    <Card className="table-card">
      <Table
        rowKey="id"
        columns={columns}
        dataSource={classes}
        loading={loading}
        pagination={false}
        locale={{ emptyText: <Empty description="暂无班级，请先新建班级" /> }}
      />
    </Card>
    <Modal
      title={editing ? '编辑班级' : '新建班级'}
      open={modalOpen}
      okText={editing ? '保存' : '确认创建'}
      cancelText="取消"
      confirmLoading={saving}
      onOk={() => void save()}
      onCancel={() => { setModalOpen(false); form.resetFields() }}
      destroyOnClose
    >
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="班级名称" rules={[{ required: true, whitespace: true }]}>
          <Input maxLength={120} placeholder="例如：六年级一班" />
        </Form.Item>
        <Form.Item name="grade" label="年级" rules={[{ required: true, whitespace: true }]}>
          <Input maxLength={40} placeholder="例如：六年级" />
        </Form.Item>
        <Form.Item name="school_year" label="学年" rules={[{ required: true, whitespace: true }]}>
          <Input maxLength={40} placeholder="例如：2026-2027" />
        </Form.Item>
        <Form.Item name="teacher_name" label="任课教师">
          <Input maxLength={80} placeholder="可选" />
        </Form.Item>
      </Form>
    </Modal>
  </Space>
}
