import { BarChartOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Empty, Progress, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiGet } from '../api/client'
import type { Page, StudentSummary } from '../types'

export function StudentListPage() {
  const [data, setData] = useState<Page<StudentSummary> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const controller = new AbortController()
    apiGet<Page<StudentSummary>>('/students?page=1&page_size=100', controller.signal)
      .then(setData)
      .catch((reason: Error) => {
        if (reason.name !== 'AbortError') setError(reason.message)
      })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [])

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
    { title: '数据类型', width: 100, render: () => <Tag color="purple">演示数据</Tag> },
    {
      title: '操作',
      key: 'action',
      width: 170,
      fixed: 'right',
      render: (_, row) => (
        <Link to={`/students/${row.id}`}>
          <Button type='primary' icon={<BarChartOutlined />}>查看学生画像</Button>
        </Link>
      ),
    },
  ]

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <div>
        <Typography.Title level={2}>虚拟学生</Typography.Title>
        <Typography.Text type="secondary">学生差异来自可追溯的历史 Attempt；M2 再计算掌握度和薄弱点。</Typography.Text>
      </div>
      {error && <Alert type="error" showIcon message="学生数据加载失败" description={error} />}
      <Card className="table-card">
        <Table rowKey="id" columns={columns} dataSource={data?.items || []} loading={loading} pagination={false} locale={{ emptyText: <Empty description="暂无学生" /> }} />
      </Card>
    </Space>
  )
}
