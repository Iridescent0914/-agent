import { PlusOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiGet } from '../api/client'
import type { AssignmentListItem, AssignmentPage } from '../types/assignment'

export function AssignmentListPage() {
  const [data, setData] = useState<AssignmentPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const load = () => {
    setLoading(true); setError(null)
    apiGet<AssignmentPage>('/assignments?page_size=100')
      .then(setData).catch((reason) => setError((reason as Error).message)).finally(() => setLoading(false))
  }
  useEffect(load, [])
  const columns: ColumnsType<AssignmentListItem> = [
    { title: '作业', dataIndex: 'title', render: (value, row) => <Link to={`/assignments/${row.id}`}>{value}</Link> },
    { title: '学生', dataIndex: 'student_count', width: 90 },
    { title: '草稿', dataIndex: 'draft_count', width: 90, render: (value) => <Tag color={value ? 'gold' : 'default'}>{value}</Tag> },
    { title: '有效发布', dataIndex: 'published_count', width: 100, render: (value) => <Tag color={value ? 'green' : 'default'}>{value}</Tag> },
    { title: '已作废', dataIndex: 'invalidated_count', width: 100, render: (value) => <Tag color={value ? 'red' : 'default'}>{value}</Tag> },
    { title: '创建时间', dataIndex: 'created_at', width: 190, render: (value) => new Date(value).toLocaleString('zh-CN') },
    { title: '操作', width: 90, render: (_, row) => <Link to={`/assignments/${row.id}`}>查看</Link> },
  ]
  return <Space direction="vertical" size="large" className="page-stack">
    <div className="student-heading">
      <div><Typography.Title level={2}>个性化作业</Typography.Title><Typography.Text type="secondary">为每名学生生成、调整并发布独立试卷</Typography.Text></div>
      <Button type="primary" icon={<PlusOutlined />}><Link to="/assignments/new">创建作业</Link></Button>
    </div>
    {error && <Alert type="error" showIcon message="作业加载失败" description={error} action={<Button onClick={load}>重试</Button>} />}
    <Card className="table-card"><Table rowKey="id" loading={loading} columns={columns} dataSource={data?.items ?? []} pagination={false} scroll={{ x: 760 }} /></Card>
  </Space>
}
