import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Modal, Space, Table, Tag, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiDelete, apiGet } from '../api/client'
import type { AssignmentListItem, AssignmentPage } from '../types/assignment'

export function AssignmentListPage() {
  const [data, setData] = useState<AssignmentPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const load = () => {
    setLoading(true); setError(null)
    apiGet<AssignmentPage>(`/assignments?page=${page}&page_size=${pageSize}`)
      .then(setData).catch((reason) => setError((reason as Error).message)).finally(() => setLoading(false))
  }
  useEffect(load, [page, pageSize])
  const deleteAssignment = (assignment: AssignmentListItem) => {
    Modal.confirm({
      title: `删除作业“${assignment.title}”？`,
      content: '该作业没有任何试卷，删除后无法恢复。',
      okText: '确认删除', cancelText: '取消', okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await apiDelete(`/assignments/${assignment.id}`)
          message.success('作业已删除')
          load()
        } catch (reason) {
          setError((reason as Error).message)
        }
      },
    })
  }
  const columns: ColumnsType<AssignmentListItem> = [
    { title: '作业', dataIndex: 'title', render: (value, row) => <Link to={`/assignments/${row.id}`}>{value}</Link> },
    { title: '班级', dataIndex: 'class_name', width: 150, render: (value) => value || '未设置' },
    { title: '学生', dataIndex: 'student_count', width: 90 },
    { title: '草稿', dataIndex: 'draft_count', width: 90, render: (value) => <Tag color={value ? 'gold' : 'default'}>{value}</Tag> },
    { title: '有效发布', dataIndex: 'published_count', width: 100, render: (value) => <Tag color={value ? 'green' : 'default'}>{value}</Tag> },
    { title: '已作废', dataIndex: 'invalidated_count', width: 100, render: (value) => <Tag color={value ? 'red' : 'default'}>{value}</Tag> },
    { title: '创建时间', dataIndex: 'created_at', width: 190, render: (value) => new Date(value).toLocaleString('zh-CN') },
    { title: '操作', width: 160, render: (_, row) => <Space>
      <Link to={`/assignments/${row.id}`}>查看</Link>
      {row.student_count === 0 && row.draft_count === 0 && row.published_count === 0 && row.invalidated_count === 0 &&
        <Button danger type="link" size="small" icon={<DeleteOutlined />} onClick={() => deleteAssignment(row)}>删除</Button>}
    </Space> },
  ]
  return <Space direction="vertical" size="large" className="page-stack">
    <div className="student-heading">
      <div><Typography.Title level={2}>个性化作业</Typography.Title><Typography.Text type="secondary">为每名学生生成、调整并发布独立试卷</Typography.Text></div>
      <Button type="primary" icon={<PlusOutlined />}><Link to="/assignments/new">创建作业</Link></Button>
    </div>
    {error && <Alert type="error" showIcon message="作业加载失败" description={error} action={<Button onClick={load}>重试</Button>} />}
    <Card className="table-card"><Table rowKey="id" loading={loading} columns={columns} dataSource={data?.items ?? []} pagination={{
      current: page, pageSize, total: data?.total ?? 0, showSizeChanger: true,
      showTotal: (total) => `共 ${total} 个作业`,
    }} onChange={(pagination) => {
      setPage(pagination.current ?? 1); setPageSize(pagination.pageSize ?? 20)
    }} scroll={{ x: 760 }} /></Card>
  </Space>
}
